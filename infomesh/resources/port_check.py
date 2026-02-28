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


# ── Cloud detection ──────────────────────────────────────────────────


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = _IMDS_TIMEOUT) -> str | None:
    """Minimal HTTP GET using urllib (no external deps)."""
    import urllib.request
    import urllib.error

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


def _auto_open_azure(port: int) -> tuple[bool, str]:
    """Attempt to open *port* via Azure CLI (az network nsg rule create)."""
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

        # Find NSGs attached to this VM's NICs
        click.echo(f"    Detecting NSGs for VM '{vm_name}' in resource group '{rg}'...")

        # Get VM NIC IDs
        vm_show = subprocess.run(
            ["az", "vm", "show", "-g", rg, "-n", vm_name,
             "--query", "networkProfile.networkInterfaces[].id", "-o", "json",
             "--subscription", subscription_id],
            capture_output=True, text=True, timeout=30,
        )
        if vm_show.returncode != 0:
            return False, f"Failed to query VM NICs: {vm_show.stderr.strip()}"

        nic_ids = json.loads(vm_show.stdout)
        if not nic_ids:
            return False, "No NICs found for this VM."

        nsg_names: set[str] = set()

        for nic_id in nic_ids:
            nic_name = nic_id.split("/")[-1]
            # Check NIC-level NSG
            nic_nsg = subprocess.run(
                ["az", "network", "nic", "show", "--ids", nic_id,
                 "--query", "networkSecurityGroup.id", "-o", "tsv",
                 "--subscription", subscription_id],
                capture_output=True, text=True, timeout=30,
            )
            if nic_nsg.returncode == 0 and nic_nsg.stdout.strip():
                nsg_names.add(nic_nsg.stdout.strip().split("/")[-1])

            # Check subnet-level NSG
            subnet_query = subprocess.run(
                ["az", "network", "nic", "show", "--ids", nic_id,
                 "--query", "ipConfigurations[0].subnet.id", "-o", "tsv",
                 "--subscription", subscription_id],
                capture_output=True, text=True, timeout=30,
            )
            if subnet_query.returncode == 0 and subnet_query.stdout.strip():
                subnet_id = subnet_query.stdout.strip()
                subnet_nsg = subprocess.run(
                    ["az", "network", "vnet", "subnet", "show",
                     "--ids", subnet_id,
                     "--query", "networkSecurityGroup.id", "-o", "tsv",
                     "--subscription", subscription_id],
                    capture_output=True, text=True, timeout=30,
                )
                if subnet_nsg.returncode == 0 and subnet_nsg.stdout.strip():
                    nsg_names.add(subnet_nsg.stdout.strip().split("/")[-1])

        if not nsg_names:
            return False, (
                "No NSG found attached to this VM's NICs or subnets.\n"
                "  You may need to create an NSG or open the port manually in the Azure Portal."
            )

        rule_name = f"Allow-InfoMesh-{port}"
        success_count = 0
        errors: list[str] = []

        for nsg_name in sorted(nsg_names):
            click.echo(f"    Adding rule '{rule_name}' to NSG '{nsg_name}'...")
            result = subprocess.run(
                ["az", "network", "nsg", "rule", "create",
                 "-g", rg, "--nsg-name", nsg_name,
                 "-n", rule_name,
                 "--priority", "1010",
                 "--direction", "Inbound",
                 "--access", "Allow",
                 "--protocol", "Tcp",
                 "--destination-port-ranges", str(port),
                 "--subscription", subscription_id],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                success_count += 1
                click.echo(f"    ✓ Rule added to NSG '{nsg_name}'")
            else:
                errors.append(f"NSG '{nsg_name}': {result.stderr.strip()}")

        if success_count > 0 and not errors:
            return True, f"Port {port}/TCP opened in {success_count} NSG(s): {', '.join(sorted(nsg_names))}"
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
    """Attempt to open *port* via AWS CLI (authorize-security-group-ingress)."""
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
        # Get security group IDs for this instance
        sg_result = subprocess.run(
            ["aws", "ec2", "describe-instances",
             "--instance-ids", instance_id,
             "--query", "Reservations[0].Instances[0].SecurityGroups[*].GroupId",
             "--output", "json",
             *(["--region", region] if region else [])],
            capture_output=True, text=True, timeout=30,
        )
        if sg_result.returncode != 0:
            return False, f"Failed to describe instance: {sg_result.stderr.strip()}"

        sg_ids = json.loads(sg_result.stdout)
        if not sg_ids:
            return False, "No security groups found for this instance."

        # Add ingress rule to the first (primary) security group
        sg_id = sg_ids[0]
        click.echo(f"    Adding inbound rule to security group '{sg_id}'...")

        result = subprocess.run(
            ["aws", "ec2", "authorize-security-group-ingress",
             "--group-id", sg_id,
             "--protocol", "tcp",
             "--port", str(port),
             "--cidr", "0.0.0.0/0",
             "--tag-specifications", f"ResourceType=security-group-rule,Tags=[{{Key=Name,Value=InfoMesh-{port}}}]",
             *(["--region", region] if region else [])],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, f"Port {port}/TCP opened in security group '{sg_id}'"
        elif "InvalidPermission.Duplicate" in result.stderr:
            return True, f"Port {port}/TCP is already open in security group '{sg_id}'"
        else:
            return False, f"Failed to add rule: {result.stderr.strip()}"

    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"AWS CLI command failed: {exc}"


def _auto_open_gcp(port: int) -> tuple[bool, str]:
    """Attempt to open *port* via gcloud CLI (compute firewall-rules create)."""
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

    try:
        rule_name = f"allow-infomesh-{port}"
        click.echo(f"    Creating firewall rule '{rule_name}'...")

        cmd = [
            "gcloud", "compute", "firewall-rules", "create", rule_name,
            "--allow", f"tcp:{port}",
            "--direction", "INGRESS",
            "--description", f"Allow InfoMesh P2P traffic on port {port}",
            "--target-tags", f"infomesh-{port}",
        ]
        if project:
            cmd.extend(["--project", project])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True, (
                f"Firewall rule '{rule_name}' created.\n"
                f"  NOTE: You may also need to add the network tag 'infomesh-{port}' "
                f"to your VM:\n"
                f"    gcloud compute instances add-tags {vm_name} --tags=infomesh-{port}"
            )
        elif "already exists" in result.stderr:
            return True, f"Firewall rule '{rule_name}' already exists."
        else:
            return False, f"Failed to create firewall rule: {result.stderr.strip()}"

    except subprocess.TimeoutExpired as exc:
        return False, f"gcloud command timed out: {exc}"


# ── Orchestrator ─────────────────────────────────────────────────────


def _get_manual_instructions(provider: CloudProvider, port: int) -> str:
    """Return human-readable manual fix instructions per provider."""
    match provider:
        case CloudProvider.AZURE:
            return (
                f"To open port {port}/TCP on Azure manually:\n"
                f"  1. Go to Azure Portal → Your VM → Networking → Inbound port rules\n"
                f"  2. Click 'Add inbound port rule'\n"
                f"  3. Set Destination port: {port}, Protocol: TCP, Action: Allow\n"
                f"  4. Name: Allow-InfoMesh-{port}, Priority: 1010\n"
                f"  5. Click 'Add'\n"
                f"  Also check subnet-level NSG if applicable.\n"
                f"\n"
                f"  Or via Azure CLI:\n"
                f"    az network nsg rule create -g <resource-group> --nsg-name <nsg-name> \\\n"
                f"      -n Allow-InfoMesh-{port} --priority 1010 --direction Inbound \\\n"
                f"      --access Allow --protocol Tcp --destination-port-ranges {port}"
            )
        case CloudProvider.AWS:
            return (
                f"To open port {port}/TCP on AWS manually:\n"
                f"  1. Go to AWS Console → EC2 → Security Groups\n"
                f"  2. Select the security group for your instance\n"
                f"  3. Edit inbound rules → Add rule:\n"
                f"     Type: Custom TCP, Port: {port}, Source: 0.0.0.0/0\n"
                f"  4. Save rules\n"
                f"\n"
                f"  Or via AWS CLI:\n"
                f"    aws ec2 authorize-security-group-ingress --group-id <sg-id> \\\n"
                f"      --protocol tcp --port {port} --cidr 0.0.0.0/0"
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
                f"  • iptables:        sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT\n"
                f"  • Router/NAT:      forward port {port}/TCP to this machine's local IP\n"
                f"  • Cloud firewall:  check your cloud provider's security group / firewall rules"
            )


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
        message="" if not is_blocked else f"Port {port}/TCP may be blocked by firewall.",
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
        # On non-cloud machines, just give a brief info message
        click.echo(f"  ℹ P2P port: {port}/TCP")
        click.echo(f"    Ensure port {port}/TCP is open in your firewall for peering.")
        return True

    # Cloud VM detected — check and warn
    click.echo(f"  ℹ P2P port: {port}/TCP (detected: {provider.value.upper()} VM)")
    click.echo(f"    Cloud firewalls often block inbound ports by default.")

    click.secho(
        f"  ⚠ Port {port}/TCP may be blocked by {provider.value.upper()} firewall.",
        fg="yellow",
    )
    click.echo(f"    P2P peering requires this port to be open for inbound TCP traffic.")

    # Ask for consent
    auto_open_label = {
        CloudProvider.AZURE: "Azure NSG",
        CloudProvider.AWS: "AWS Security Group",
        CloudProvider.GCP: "GCP Firewall",
    }[provider]

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
            click.secho(f"  ✗ Auto-open failed.", fg="red")
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
