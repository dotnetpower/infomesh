"""Port accessibility check and cloud firewall auto-open.

Detects whether the P2P listen port is reachable from outside,
identifies the cloud provider (CSP), and offers to auto-open
the port via the provider's CLI tools.

Usage::

    from infomesh.resources.port_check import check_port_and_offer_fix

    # Interactive — warns and offers auto-fix
    check_port_and_offer_fix(port=4001)
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

import click
import structlog

logger = structlog.get_logger()

# ── Constants ────────────────────────────────────────────────────────

# Known cloud metadata endpoints (IMDS)
_IMDS_TIMEOUT = 2.0  # seconds


class CloudProvider(StrEnum):
    """Detected cloud service provider."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PortCheckResult:
    """Result of a port accessibility check."""

    port: int
    is_listening: bool
    is_blocked: bool  # True if listening but unreachable from outside
    provider: CloudProvider
    message: str


@dataclass(frozen=True)
class NsgInfo:
    """Azure NSG identity — name + resource group + origin."""

    name: str
    resource_group: str
    source: str  # e.g. "NIC myNic" or "Subnet mySubnet"

    @staticmethod
    def from_resource_id(resource_id: str, source: str) -> NsgInfo:
        """Parse an ARM resource ID into NSG name and resource group."""
        parts = resource_id.strip().split("/")
        # Standard ARM ID has resourceGroups at index 4, name at index 8
        rg = ""
        name = parts[-1]
        for i, part in enumerate(parts):
            if part.lower() == "resourcegroups" and i + 1 < len(parts):
                rg = parts[i + 1]
                break
        return NsgInfo(name=name, resource_group=rg, source=source)


# ── Cloud detection ──────────────────────────────────────────────────


def _http_get(
    url: str, headers: dict[str, str] | None = None, timeout: float = _IMDS_TIMEOUT
) -> str | None:
    """Minimal HTTP GET using urllib (no external deps)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def detect_cloud_provider() -> CloudProvider:
    """Detect which cloud provider we're running on via IMDS."""

    # Azure IMDS
    body = _http_get(
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        headers={"Metadata": "true"},
    )
    if body and "compute" in body:
        logger.debug("cloud_detected", provider="azure")
        return CloudProvider.AZURE

    # AWS IMDS v2 (token-based)
    token = _http_get(
        "http://169.254.169.254/latest/api/token",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    if token:
        body = _http_get(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token},
        )
        if body and body.startswith("i-"):
            logger.debug("cloud_detected", provider="aws")
            return CloudProvider.AWS

    # AWS IMDS v1 fallback
    body = _http_get("http://169.254.169.254/latest/meta-data/instance-id")
    if body and body.startswith("i-"):
        logger.debug("cloud_detected", provider="aws")
        return CloudProvider.AWS

    # GCP IMDS
    body = _http_get(
        "http://metadata.google.internal/computeMetadata/v1/instance/id",
        headers={"Metadata-Flavor": "Google"},
    )
    if body and body.strip().isdigit():
        logger.debug("cloud_detected", provider="gcp")
        return CloudProvider.GCP

    logger.debug("cloud_detected", provider="unknown")
    return CloudProvider.UNKNOWN


# ── WSL detection ────────────────────────────────────────────────────


def _is_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux (WSL)."""
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _get_wsl_host_ip() -> str | None:
    """Get the Windows host IP as seen from WSL2.

    WSL2 stores the host IP in ``/etc/resolv.conf`` (the nameserver line)
    and also in the ``WSL_HOST_IP`` variable on newer builds.
    """
    # Method 1: /etc/resolv.conf nameserver (most reliable)
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("nameserver"):
                    parts = stripped.split()
                    if len(parts) >= 2:
                        return parts[1]
    except OSError:
        pass
    return None


def _get_wsl_ip() -> str | None:
    """Get the WSL2 VM's own IP address (eth0)."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # First IP is typically the eth0 address
            return result.stdout.strip().split()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ── Port check ───────────────────────────────────────────────────────


def is_port_listening(port: int) -> bool:
    """Check if *port* is bound locally (i.e., some process is listening)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except (OSError, TimeoutError):
        return False


def is_port_open_externally(port: int) -> bool:
    """Best-effort check: can traffic reach this port from outside?

    Tries to bind to the port on 0.0.0.0 and checks for common
    indicators of firewall blocking. This is a heuristic — a true
    external probe would require an outside server.
    """
    # Strategy: if we can bind, it means nothing is blocking the bind.
    # But we can't truly test inbound without an external probe.
    # So we use iptables/nftables inspection as a fallback on Linux.
    try:
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if f":{port}" in result.stdout:
            # Process is listening — check iptables
            return _check_iptables_allows(port)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return True  # Assume open if we can't determine


def _check_iptables_allows(port: int) -> bool:
    """Check if iptables/nftables allows inbound traffic on *port*.

    Returns True if allowed or if we can't determine.
    """
    for cmd in (
        ["iptables", "-L", "INPUT", "-n", "--line-numbers"],
        ["nft", "list", "ruleset"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                output = result.stdout
                # Look for explicit DROP/REJECT on this port
                if f"dpt:{port}" in output and ("DROP" in output or "REJECT" in output):
                    return False
            # Can't determine — assume open
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            continue
    return True


# ── Cloud-specific metadata helpers ──────────────────────────────────


def _get_azure_metadata() -> dict | None:
    """Fetch Azure VM instance metadata."""
    body = _http_get(
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        headers={"Metadata": "true"},
    )
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return None


def _get_aws_metadata() -> dict[str, str]:
    """Fetch AWS EC2 instance metadata (instance-id, security groups)."""
    info: dict[str, str] = {}
    token = _http_get(
        "http://169.254.169.254/latest/api/token",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    headers = {"X-aws-ec2-metadata-token": token} if token else {}
    for key in ("instance-id", "security-groups", "placement/region"):
        body = _http_get(
            f"http://169.254.169.254/latest/meta-data/{key}",
            headers=headers,
        )
        if body:
            info[key.split("/")[-1]] = body.strip()
    return info


def _get_gcp_metadata() -> dict[str, str]:
    """Fetch GCP instance metadata."""
    info: dict[str, str] = {}
    headers = {"Metadata-Flavor": "Google"}
    for key in ("name", "zone"):
        body = _http_get(
            f"http://metadata.google.internal/computeMetadata/v1/instance/{key}",
            headers=headers,
        )
        if body:
            info[key] = body.strip()
    return info


# ── Auto-open implementations ────────────────────────────────────────


def _discover_azure_nsgs(
    vm_name: str,
    rg: str,
    subscription_id: str,
) -> list[NsgInfo]:
    """Discover all NSGs for a VM — NIC-level and subnet-level.

    Azure traffic must pass **both** NIC-level and subnet-level NSGs
    (if present). This function collects all unique NSGs so that the
    inbound rule is added to every one of them.
    """
    nsgs: list[NsgInfo] = []
    seen_ids: set[str] = set()  # deduplicate by full resource ID

    # Step 1: Get all NIC IDs for this VM
    vm_show = subprocess.run(
        [
            "az",
            "vm",
            "show",
            "-g",
            rg,
            "-n",
            vm_name,
            "--query",
            "networkProfile.networkInterfaces[].id",
            "-o",
            "json",
            "--subscription",
            subscription_id,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if vm_show.returncode != 0:
        logger.warning(
            "azure_nsg_discovery_failed",
            step="vm_show",
            error=vm_show.stderr.strip(),
        )
        return nsgs

    nic_ids: list[str] = json.loads(vm_show.stdout)

    for nic_id in nic_ids:
        nic_name = nic_id.split("/")[-1]

        # ── NIC-level NSG ────────────────────────────────────────
        nic_nsg = subprocess.run(
            [
                "az",
                "network",
                "nic",
                "show",
                "--ids",
                nic_id,
                "--query",
                "networkSecurityGroup.id",
                "-o",
                "tsv",
                "--subscription",
                subscription_id,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if nic_nsg.returncode == 0 and nic_nsg.stdout.strip():
            nsg_full_id = nic_nsg.stdout.strip()
            if nsg_full_id not in seen_ids:
                seen_ids.add(nsg_full_id)
                nsgs.append(NsgInfo.from_resource_id(nsg_full_id, f"NIC {nic_name}"))

        # ── Subnet-level NSGs (check ALL ipConfigurations) ───────
        subnet_query = subprocess.run(
            [
                "az",
                "network",
                "nic",
                "show",
                "--ids",
                nic_id,
                "--query",
                "ipConfigurations[].subnet.id",
                "-o",
                "json",
                "--subscription",
                subscription_id,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if subnet_query.returncode != 0:
            continue

        subnet_ids: list[str] = json.loads(subnet_query.stdout or "[]")
        seen_subnets: set[str] = set()

        for subnet_id in subnet_ids:
            if not subnet_id or subnet_id in seen_subnets:
                continue
            seen_subnets.add(subnet_id)
            subnet_name = subnet_id.split("/")[-1]

            subnet_nsg = subprocess.run(
                [
                    "az",
                    "network",
                    "vnet",
                    "subnet",
                    "show",
                    "--ids",
                    subnet_id,
                    "--query",
                    "networkSecurityGroup.id",
                    "-o",
                    "tsv",
                    "--subscription",
                    subscription_id,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if subnet_nsg.returncode == 0 and subnet_nsg.stdout.strip():
                nsg_full_id = subnet_nsg.stdout.strip()
                if nsg_full_id not in seen_ids:
                    seen_ids.add(nsg_full_id)
                    nsgs.append(
                        NsgInfo.from_resource_id(nsg_full_id, f"Subnet {subnet_name}")
                    )

    return nsgs


def _auto_open_azure(port: int) -> tuple[bool, str]:
    """Attempt to open *port* in all Azure NSGs (NIC + subnet level)."""
    if not shutil.which("az"):
        return False, (
            "Azure CLI ('az') is not installed.\n"
            "  Install: https://learn.microsoft.com/cli/azure/install-azure-cli\n"
            "  Then run: az login"
        )

    meta = _get_azure_metadata()
    if not meta:
        return False, "Could not retrieve Azure VM metadata (IMDS unavailable)."

    try:
        compute = meta.get("compute", {})
        rg = compute.get("resourceGroupName", "")
        vm_name = compute.get("name", "")
        subscription_id = compute.get("subscriptionId", "")
        if not rg or not vm_name:
            return False, "Could not determine VM resource group or name from IMDS."

        click.echo(f"    Detecting NSGs for VM '{vm_name}' in resource group '{rg}'...")
        nsgs = _discover_azure_nsgs(vm_name, rg, subscription_id)

        if not nsgs:
            return False, (
                "No NSG found attached to this VM's NICs or subnets.\n"
                "  You may need to create an NSG or open"
                " the port manually in the Azure Portal."
            )

        # Show discovered NSGs
        for nsg in nsgs:
            rg_note = f" (RG: {nsg.resource_group})" if nsg.resource_group != rg else ""
            click.echo(f"    Found NSG '{nsg.name}'{rg_note} via {nsg.source}")

        rule_name = f"Allow-InfoMesh-{port}"
        success_count = 0
        errors: list[str] = []

        for nsg in nsgs:
            nsg_rg = nsg.resource_group or rg
            click.echo(
                f"    Adding rule '{rule_name}' to NSG '{nsg.name}' (RG: {nsg_rg})..."
            )
            result = subprocess.run(
                [
                    "az",
                    "network",
                    "nsg",
                    "rule",
                    "create",
                    "-g",
                    nsg_rg,
                    "--nsg-name",
                    nsg.name,
                    "-n",
                    rule_name,
                    "--priority",
                    "1010",
                    "--direction",
                    "Inbound",
                    "--access",
                    "Allow",
                    "--protocol",
                    "Tcp",
                    "--destination-port-ranges",
                    str(port),
                    "--subscription",
                    subscription_id,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                success_count += 1
                click.echo(f"    \u2713 Rule added to NSG '{nsg.name}'")
            else:
                err = result.stderr.strip()
                errors.append(
                    f"NSG '{nsg.name}' (RG: {nsg_rg}, via {nsg.source}): {err}"
                )

        nsg_summary = ", ".join(f"{n.name}({n.source})" for n in nsgs)
        if success_count > 0 and not errors:
            return (
                True,
                f"Port {port}/TCP opened in {success_count} NSG(s): {nsg_summary}",
            )
        elif success_count > 0:
            return True, (
                f"Port {port}/TCP opened in {success_count} NSG(s), "
                f"but failed in others:\n  " + "\n  ".join(errors)
            )
        else:
            return False, "Failed to add rules:\n  " + "\n  ".join(errors)

    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"Azure CLI command failed: {exc}"


def _auto_open_aws(port: int) -> tuple[bool, str]:
    """Attempt to open *port* in all AWS Security Groups for this instance."""
    if not shutil.which("aws"):
        return False, (
            "AWS CLI ('aws') is not installed.\n"
            "  Install: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html\n"
            "  Then run: aws configure"
        )

    meta = _get_aws_metadata()
    instance_id = meta.get("instance-id")
    region = meta.get("region")

    if not instance_id:
        return False, "Could not determine EC2 instance ID from IMDS."

    try:
        # Get ALL security group IDs for this instance
        sg_result = subprocess.run(
            [
                "aws",
                "ec2",
                "describe-instances",
                "--instance-ids",
                instance_id,
                "--query",
                "Reservations[0].Instances[0].SecurityGroups[*].[GroupId,GroupName]",
                "--output",
                "json",
                *(["--region", region] if region else []),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if sg_result.returncode != 0:
            return False, (f"Failed to describe instance: {sg_result.stderr.strip()}")

        sg_pairs: list[list[str]] = json.loads(sg_result.stdout)
        if not sg_pairs:
            return False, "No security groups found for this instance."

        click.echo(
            f"    Found {len(sg_pairs)} security group(s) for instance '{instance_id}'"
        )

        success_count = 0
        errors: list[str] = []
        opened_sgs: list[str] = []

        for sg_pair in sg_pairs:
            sg_id = sg_pair[0] if isinstance(sg_pair, list) else sg_pair
            sg_name = sg_pair[1] if isinstance(sg_pair, list) else ""
            label = f"{sg_id} ({sg_name})" if sg_name else sg_id
            click.echo(f"    Adding inbound rule to security group '{label}'...")

            result = subprocess.run(
                [
                    "aws",
                    "ec2",
                    "authorize-security-group-ingress",
                    "--group-id",
                    sg_id,
                    "--protocol",
                    "tcp",
                    "--port",
                    str(port),
                    "--cidr",
                    "0.0.0.0/0",
                    "--tag-specifications",
                    f"ResourceType=security-group-rule,"
                    f"Tags=[{{Key=Name,Value=InfoMesh-{port}}}]",
                    *(["--region", region] if region else []),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                success_count += 1
                opened_sgs.append(label)
                click.echo(f"    ✓ Rule added to '{label}'")
            elif "InvalidPermission.Duplicate" in result.stderr:
                success_count += 1
                opened_sgs.append(label)
                click.echo(f"    ✓ Already open in '{label}'")
            else:
                errors.append(f"SG '{label}': {result.stderr.strip()}")

        if success_count > 0 and not errors:
            return True, (
                f"Port {port}/TCP opened in"
                f" {success_count} SG(s):"
                f" {', '.join(opened_sgs)}"
            )
        elif success_count > 0:
            return True, (
                f"Port {port}/TCP opened in {success_count} SG(s),"
                f" but failed in others:\n  " + "\n  ".join(errors)
            )
        else:
            return False, ("Failed to add rules:\n  " + "\n  ".join(errors))

    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"AWS CLI command failed: {exc}"


def _auto_open_gcp(port: int) -> tuple[bool, str]:
    """Attempt to open *port* via gcloud CLI and auto-add network tag."""
    if not shutil.which("gcloud"):
        return False, (
            "Google Cloud CLI ('gcloud') is not installed.\n"
            "  Install: https://cloud.google.com/sdk/docs/install\n"
            "  Then run: gcloud auth login"
        )

    meta = _get_gcp_metadata()
    vm_name = meta.get("name", "unknown")
    zone = meta.get("zone", "")
    # zone is like "projects/1234/zones/us-central1-a"
    project = zone.split("/")[1] if "/" in zone else ""
    zone_short = zone.split("/")[-1] if "/" in zone else ""

    try:
        rule_name = f"allow-infomesh-{port}"
        tag_name = f"infomesh-{port}"
        click.echo(f"    Creating firewall rule '{rule_name}'...")

        cmd = [
            "gcloud",
            "compute",
            "firewall-rules",
            "create",
            rule_name,
            "--allow",
            f"tcp:{port}",
            "--direction",
            "INGRESS",
            "--description",
            f"Allow InfoMesh P2P traffic on port {port}",
            "--target-tags",
            tag_name,
        ]
        if project:
            cmd.extend(["--project", project])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        rule_created = False
        if result.returncode == 0:
            rule_created = True
            click.echo(f"    ✓ Firewall rule '{rule_name}' created")
        elif "already exists" in result.stderr:
            rule_created = True
            click.echo(f"    ✓ Firewall rule '{rule_name}' already exists")
        else:
            return False, (f"Failed to create firewall rule: {result.stderr.strip()}")

        # Auto-add network tag to the VM
        if rule_created and vm_name != "unknown" and zone_short:
            click.echo(f"    Adding network tag '{tag_name}' to VM '{vm_name}'...")
            tag_cmd = [
                "gcloud",
                "compute",
                "instances",
                "add-tags",
                vm_name,
                f"--tags={tag_name}",
                f"--zone={zone_short}",
            ]
            if project:
                tag_cmd.extend(["--project", project])

            tag_result = subprocess.run(
                tag_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if tag_result.returncode == 0:
                click.echo(f"    ✓ Network tag '{tag_name}' added to VM")
                return True, (
                    f"Firewall rule '{rule_name}' created and"
                    f" tag '{tag_name}' added to VM '{vm_name}'"
                )
            else:
                return True, (
                    f"Firewall rule '{rule_name}' created, but"
                    f" failed to add network tag:\n"
                    f"  {tag_result.stderr.strip()}\n"
                    f"  Run manually: gcloud compute instances"
                    f" add-tags {vm_name}"
                    f" --tags={tag_name}"
                    f" --zone={zone_short}"
                )

        return True, (
            f"Firewall rule '{rule_name}' created.\n"
            f"  NOTE: Add the network tag '{tag_name}' to your VM:\n"
            f"    gcloud compute instances"
            f" add-tags {vm_name}"
            f" --tags={tag_name}"
        )

    except subprocess.TimeoutExpired as exc:
        return False, f"gcloud command timed out: {exc}"


def _auto_open_wsl(port: int) -> tuple[bool, str]:
    """Attempt to open *port* in Windows Firewall and set up port forwarding.

    WSL2 uses a NAT'd virtual network, so two things are needed:
    1. A Windows Firewall inbound rule allowing TCP traffic on *port*.
    2. A ``netsh portproxy`` rule forwarding from the Windows host
       to the WSL2 VM's IP address.

    Both operations require Administrator privileges on the Windows side.
    We invoke them via ``powershell.exe`` which is accessible from WSL.
    """
    ps = shutil.which("powershell.exe")
    if not ps:
        return False, (
            "Cannot find 'powershell.exe' from WSL.\n"
            "  Ensure Windows interop is enabled "
            "(check /etc/wsl.conf [interop] appendWindowsPath)."
        )

    wsl_ip = _get_wsl_ip()
    if not wsl_ip:
        return False, "Could not determine WSL2 IP address."

    rule_name = f"InfoMesh-P2P-{port}"
    errors: list[str] = []

    # Step 1: Add Windows Firewall inbound rule
    click.echo(f"    Adding Windows Firewall rule '{rule_name}'...")
    fw_cmd = (
        f"New-NetFirewallRule -DisplayName '{rule_name}' "
        f"-Direction Inbound -Action Allow "
        f"-Protocol TCP -LocalPort {port} "
        f"-Profile Any "
        f"-ErrorAction SilentlyContinue; "
        f"if (-not $?) {{ "
        f"Set-NetFirewallRule -DisplayName '{rule_name}' "
        f"-Direction Inbound -Action Allow "
        f"-Protocol TCP -LocalPort {port} }}"
    )
    fw_result = subprocess.run(
        [ps, "-NoProfile", "-Command", fw_cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    fw_ok = False
    if fw_result.returncode == 0:
        fw_ok = True
        click.echo(f"    \u2713 Windows Firewall rule '{rule_name}' created")
    else:
        err = fw_result.stderr.strip()
        if "Access" in err or "denied" in err.lower() or "administrator" in err.lower():
            errors.append(
                "Windows Firewall: Access denied — "
                "run WSL terminal as Administrator, or add the rule manually."
            )
        else:
            errors.append(f"Windows Firewall: {err}")

    # Step 2: Set up port forwarding (netsh portproxy)
    click.echo(f"    Setting port proxy: 0.0.0.0:{port} → {wsl_ip}:{port}...")
    proxy_cmd = (
        f"netsh interface portproxy add v4tov4 "
        f"listenport={port} listenaddress=0.0.0.0 "
        f"connectport={port} connectaddress={wsl_ip}"
    )
    proxy_result = subprocess.run(
        [ps, "-NoProfile", "-Command", proxy_cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    proxy_ok = False
    if proxy_result.returncode == 0:
        proxy_ok = True
        click.echo(f"    \u2713 Port proxy set: 0.0.0.0:{port} → {wsl_ip}:{port}")
    else:
        err = proxy_result.stderr.strip()
        if "Access" in err or "denied" in err.lower():
            errors.append(
                "Port proxy: Access denied — run WSL terminal as Administrator."
            )
        else:
            errors.append(f"Port proxy: {err}")

    if fw_ok and proxy_ok:
        return True, (
            f"Windows Firewall rule '{rule_name}' created and "
            f"port proxy {port} → {wsl_ip}:{port} configured.\n"
            f"  ⚠ WSL2 IP may change on reboot. "
            f"Re-run 'infomesh start' to refresh the port proxy."
        )
    elif fw_ok or proxy_ok:
        done = "Firewall rule" if fw_ok else "Port proxy"
        return True, (
            f"{done} configured, but some steps failed:\n  " + "\n  ".join(errors)
        )
    else:
        return False, "\n  ".join(errors)


# ── Orchestrator ─────────────────────────────────────────────────────


def _get_manual_instructions(provider: CloudProvider, port: int) -> str:
    """Return human-readable manual fix instructions per provider."""
    match provider:
        case CloudProvider.AZURE:
            return (
                f"To open port {port}/TCP on Azure manually:\n"
                f"  Azure applies BOTH NIC-level and subnet-level NSGs.\n"
                f"  You must add the rule to ALL relevant NSGs:\n"
                f"\n"
                f"  1. Go to Azure Portal → Your VM → Networking\n"
                f"  2. Check 'Network interface' NSG and 'Subnet' NSG\n"
                f"  3. For EACH NSG, add an inbound port rule:\n"
                f"     Destination port: {port}, Protocol: TCP,"
                f" Action: Allow\n"
                f"     Name: Allow-InfoMesh-{port}, Priority: 1010\n"
                f"\n"
                f"  ⚠ If the NSG is in a different resource group than"
                f" the VM,\n"
                f"    use the NSG's own resource group in the CLI command.\n"
                f"\n"
                f"  Via Azure CLI:\n"
                f"    az network nsg rule create"
                f" -g <nsg-resource-group>"
                f" --nsg-name <nsg-name> \\\n"
                f"      -n Allow-InfoMesh-{port}"
                f" --priority 1010"
                f" --direction Inbound \\\n"
                f"      --access Allow --protocol Tcp"
                f" --destination-port-ranges {port}"
            )
        case CloudProvider.AWS:
            return (
                f"To open port {port}/TCP on AWS manually:\n"
                f"  An instance can have multiple security groups.\n"
                f"  Add the rule to ALL security groups:\n"
                f"\n"
                f"  1. Go to AWS Console → EC2 → Your instance\n"
                f"  2. Click 'Security' tab → view all security groups\n"
                f"  3. For EACH security group, edit inbound rules:\n"
                f"     Type: Custom TCP, Port: {port},"
                f" Source: 0.0.0.0/0\n"
                f"\n"
                f"  ⚠ Also check VPC Network ACLs (Subnets → Network ACL)\n"
                f"\n"
                f"  Via AWS CLI:\n"
                f"    aws ec2 authorize-security-group-ingress"
                f" --group-id <sg-id> \\\n"
                f"      --protocol tcp --port {port}"
                f" --cidr 0.0.0.0/0"
            )
        case CloudProvider.GCP:
            return (
                f"To open port {port}/TCP on GCP manually:\n"
                f"  1. Go to GCP Console → VPC Network → Firewall\n"
                f"  2. Create a firewall rule:\n"
                f"     Direction: Ingress, Action: Allow, Protocol: tcp:{port}\n"
                f"  3. Target: your VM's network tags or 'All instances'\n"
                f"\n"
                f"  Or via gcloud CLI:\n"
                f"    gcloud compute firewall-rules create allow-infomesh-{port} \\\n"
                f"      --allow=tcp:{port} --direction=INGRESS"
            )
        case _:
            return (
                f"Port {port}/TCP appears to be blocked by a firewall.\n"
                f"  Common fixes:\n"
                f"  • Linux firewall:  sudo ufw allow {port}/tcp\n"
                f"  • iptables:        sudo iptables"
                f" -A INPUT -p tcp"
                f" --dport {port} -j ACCEPT\n"
                f"  • Router/NAT:      forward port"
                f" {port}/TCP to this machine's"
                f" local IP\n"
                f"  • Cloud firewall:  check your"
                f" cloud provider's security group"
                f" / firewall rules"
            )


def _get_wsl_manual_instructions(port: int) -> str:
    """Return manual fix instructions for WSL2 users."""
    return (
        f"To open port {port}/TCP on WSL2 manually:\n"
        f"\n"
        f"  WSL2 needs TWO things for inbound P2P traffic:\n"
        f"  1. A Windows Firewall inbound rule allowing port {port}\n"
        f"  2. A port proxy forwarding from Windows host → WSL2 VM\n"
        f"\n"
        f"  Step 1 — Windows Firewall (run in admin PowerShell):\n"
        f"    New-NetFirewallRule -DisplayName 'InfoMesh-P2P-{port}' `\n"
        f"      -Direction Inbound -Action Allow `\n"
        f"      -Protocol TCP -LocalPort {port}\n"
        f"\n"
        f"  Step 2 — Port proxy (run in admin PowerShell):\n"
        f"    # Get WSL IP: wsl hostname -I\n"
        f"    netsh interface portproxy add v4tov4 `\n"
        f"      listenport={port} listenaddress=0.0.0.0 `\n"
        f"      connectport={port} connectaddress=<WSL_IP>\n"
        f"\n"
        f"  ⚠ WSL2 IP changes on reboot — re-run Step 2 after restart.\n"
        f"\n"
        f"  To verify port proxy:\n"
        f"    netsh interface portproxy show v4tov4\n"
        f"\n"
        f"  To remove later:\n"
        f"    Remove-NetFirewallRule -DisplayName 'InfoMesh-P2P-{port}'\n"
        f"    netsh interface portproxy delete v4tov4 "
        f"listenport={port} listenaddress=0.0.0.0"
    )


def _wsl_firewall_exists(port: int) -> bool:
    """Check if a Windows Firewall rule for *port* already exists."""
    ps = shutil.which("powershell.exe")
    if not ps:
        return False
    rule_name = f"InfoMesh-P2P-{port}"
    try:
        result = subprocess.run(
            [
                ps,
                "-NoProfile",
                "-Command",
                f"Get-NetFirewallRule -DisplayName '{rule_name}'"
                f" -ErrorAction SilentlyContinue"
                f" | Select-Object -ExpandProperty Enabled",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except (subprocess.TimeoutExpired, OSError):
        return False


def _wsl_portproxy_target(port: int) -> str | None:
    """Return the current portproxy connect-address for *port*, or None."""
    ps = shutil.which("powershell.exe")
    if not ps:
        return None
    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-Command", "netsh interface portproxy show v4tov4"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split()
            # Expected columns: listen-addr listen-port connect-addr connect-port
            if len(parts) >= 4 and parts[1] == str(port):
                return parts[2]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _wsl_update_portproxy(port: int, wsl_ip: str) -> bool:
    """Silently update the portproxy connect-address to *wsl_ip*."""
    ps = shutil.which("powershell.exe")
    if not ps:
        return False
    proxy_cmd = (
        f"netsh interface portproxy set v4tov4 "
        f"listenport={port} listenaddress=0.0.0.0 "
        f"connectport={port} connectaddress={wsl_ip}"
    )
    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-Command", proxy_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_port_wsl(port: int) -> bool:
    """WSL-specific port check and auto-fix flow.

    Checks whether the Windows Firewall rule and port proxy are already
    configured.  Only prompts the user when something is missing.
    If the port proxy exists but points to a stale WSL2 IP, it is
    silently updated.
    """
    wsl_ip = _get_wsl_ip()
    host_ip = _get_wsl_host_ip()

    click.echo(f"  ℹ P2P port: {port}/TCP (detected: WSL2)")
    if wsl_ip:
        click.echo(f"    WSL2 IP: {wsl_ip}")
    if host_ip:
        click.echo(f"    Windows host IP: {host_ip}")

    # ── Check existing configuration ──────────────────────────────
    fw_ok = _wsl_firewall_exists(port)
    proxy_target = _wsl_portproxy_target(port)
    proxy_ok = proxy_target is not None

    if fw_ok and proxy_ok:
        # Both exist — check if port proxy IP is current
        if proxy_target == wsl_ip:
            click.secho(
                f"  ✓ Firewall rule + port proxy already configured"
                f" (→ {wsl_ip}:{port})",
                fg="green",
            )
            return True
        # Stale IP — silently update
        click.echo(
            f"    Port proxy target is stale ({proxy_target} → {wsl_ip}), updating..."
        )
        if _wsl_update_portproxy(port, wsl_ip):
            click.secho(
                f"  ✓ Port proxy updated: 0.0.0.0:{port} → {wsl_ip}:{port}",
                fg="green",
            )
            return True
        click.secho(
            "  ⚠ Could not update port proxy (need Administrator?).",
            fg="yellow",
        )
        # Fall through to manual instructions
    elif fw_ok and not proxy_ok:
        click.echo("    Firewall rule exists, but port proxy is missing.")
    elif not fw_ok and proxy_ok:
        click.echo("    Port proxy exists, but firewall rule is missing.")

    # ── Something is missing — prompt for auto-fix ────────────────
    click.echo()
    click.secho(
        "  ⚠ WSL2 uses NAT — Windows Firewall + port proxy required for peering.",
        fg="yellow",
    )

    if not sys.stdin.isatty():
        click.echo(
            f"    Run interactively to auto-configure Windows Firewall"
            f" and port proxy for port {port}/TCP."
        )
        return True

    if click.confirm(
        f"    Attempt to auto-open port {port}/TCP in Windows Firewall"
        f" and set up port proxy?",
        default=True,
    ):
        click.echo()
        success, message = _auto_open_wsl(port)
        if success:
            click.secho(f"  ✓ {message}", fg="green")
            return True
        else:
            click.secho("  ✗ Auto-configuration failed.", fg="red")
            click.echo(f"    {message}")
            click.echo()
            click.secho("  Manual steps:", fg="yellow", bold=True)
            for line in _get_wsl_manual_instructions(port).split("\n"):
                click.echo(f"    {line}")
            click.echo()
            return False
    else:
        click.echo()
        click.secho("  Manual steps:", fg="yellow", bold=True)
        for line in _get_wsl_manual_instructions(port).split("\n"):
            click.echo(f"    {line}")
        click.echo()
        return True


def check_port_accessibility(port: int) -> PortCheckResult:
    """Check if the P2P port is accessible and return a result.

    This does NOT prompt the user — it only inspects.
    """
    provider = detect_cloud_provider()

    # On cloud VMs, the OS firewall usually allows all traffic —
    # the blocking is at the cloud firewall (NSG/SG/VPC) level.
    # We can't truly test inbound from here, so we check local
    # listening + iptables heuristic.
    is_blocked = not _check_iptables_allows(port)

    return PortCheckResult(
        port=port,
        is_listening=is_port_listening(port),
        is_blocked=is_blocked,
        provider=provider,
        message=""
        if not is_blocked
        else f"Port {port}/TCP may be blocked by firewall.",
    )


def check_port_and_offer_fix(port: int) -> bool:
    """Check port accessibility and interactively offer to fix it.

    Shows warnings if the port may be blocked and offers
    cloud-provider-specific auto-fix.

    Args:
        port: The TCP port to check (typically config.node.listen_port).

    Returns:
        True if port appears accessible (or was fixed), False if user
        declined or fix failed.
    """
    provider = detect_cloud_provider()

    if provider == CloudProvider.UNKNOWN:
        # Check if running under WSL
        if _is_wsl():
            return _check_port_wsl(port)
        # On non-cloud machines, just give a brief info message
        click.echo(f"  ℹ P2P port: {port}/TCP")
        click.echo(f"    Ensure port {port}/TCP is open in your firewall for peering.")
        return True

    # Cloud VM detected — check and warn
    click.echo(f"  ℹ P2P port: {port}/TCP (detected: {provider.value.upper()} VM)")
    click.echo("    Cloud firewalls often block inbound ports by default.")

    click.secho(
        f"  ⚠ Port {port}/TCP may be blocked by {provider.value.upper()} firewall.",
        fg="yellow",
    )
    click.echo("    P2P peering requires this port to be open for inbound TCP traffic.")

    # Ask for consent (skip if non-interactive)
    auto_open_label = {
        CloudProvider.AZURE: "Azure NSG",
        CloudProvider.AWS: "AWS Security Group",
        CloudProvider.GCP: "GCP Firewall",
    }[provider]

    if not sys.stdin.isatty():
        # Non-interactive — just warn and continue
        click.echo(
            f"    Run interactively to auto-open port {port}/TCP in {auto_open_label}."
        )
        return True

    if click.confirm(
        f"    Attempt to auto-open port {port}/TCP in {auto_open_label}?",
        default=True,
    ):
        click.echo()
        auto_fn = {
            CloudProvider.AZURE: _auto_open_azure,
            CloudProvider.AWS: _auto_open_aws,
            CloudProvider.GCP: _auto_open_gcp,
        }[provider]

        success, message = auto_fn(port)
        if success:
            click.secho(f"  ✓ {message}", fg="green")
            return True
        else:
            click.secho("  ✗ Auto-open failed.", fg="red")
            click.echo(f"    {message}")
            click.echo()
            click.secho("  Manual steps:", fg="yellow", bold=True)
            for line in _get_manual_instructions(provider, port).split("\n"):
                click.echo(f"    {line}")
            click.echo()
            return False
    else:
        click.echo()
        click.secho("  Manual steps:", fg="yellow", bold=True)
        for line in _get_manual_instructions(provider, port).split("\n"):
            click.echo(f"    {line}")
        click.echo()
        return True  # User chose manual — don't block startup
