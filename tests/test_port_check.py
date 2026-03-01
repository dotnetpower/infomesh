"""Tests for infomesh.resources.port_check – port accessibility & CSP auto-open."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from infomesh.resources.port_check import (
    CloudProvider,
    NsgInfo,
    PortCheckResult,
    _auto_open_wsl,
    _check_iptables_allows,
    _check_port_wsl,
    _get_manual_instructions,
    _get_wsl_manual_instructions,
    _is_wsl,
    check_port_accessibility,
    check_port_and_offer_fix,
    detect_cloud_provider,
    is_port_listening,
)

# ── detect_cloud_provider ────────────────────────────────────────────


class TestDetectCloudProvider:
    """Test cloud provider detection via IMDS."""

    def test_azure_detected(self) -> None:
        def fake_get(url: str, **kw: object) -> str | None:
            if "169.254.169.254/metadata/instance" in url:
                return json.dumps({"compute": {"name": "myvm"}})
            return None

        with patch("infomesh.resources.port_check._http_get", side_effect=fake_get):
            assert detect_cloud_provider() == CloudProvider.AZURE

    def test_aws_detected_v2(self) -> None:
        def fake_get(url: str, headers: dict | None = None, **kw: object) -> str | None:
            if "api/token" in url:
                return "mytoken"
            if "instance-id" in url:
                return "i-1234567890abcdef0"
            return None

        with patch("infomesh.resources.port_check._http_get", side_effect=fake_get):
            assert detect_cloud_provider() == CloudProvider.AWS

    def test_aws_detected_v1_fallback(self) -> None:
        call_count = 0

        def fake_get(url: str, headers: dict | None = None, **kw: object) -> str | None:
            nonlocal call_count
            call_count += 1
            # Azure fails
            if "metadata/instance" in url and "api-version" in url:
                return None
            # AWS v2 token fails
            if "api/token" in url:
                return None
            # AWS v2 instance-id with token fails (no token)
            if (
                "instance-id" in url
                and headers
                and "X-aws-ec2-metadata-token" in headers
            ):
                return None
            # AWS v1 fallback works
            if "instance-id" in url:
                return "i-abcd1234"
            return None

        with patch("infomesh.resources.port_check._http_get", side_effect=fake_get):
            assert detect_cloud_provider() == CloudProvider.AWS

    def test_gcp_detected(self) -> None:
        def fake_get(url: str, headers: dict | None = None, **kw: object) -> str | None:
            if "metadata.google.internal" in url:
                return "123456789"
            return None

        with patch("infomesh.resources.port_check._http_get", side_effect=fake_get):
            assert detect_cloud_provider() == CloudProvider.GCP

    def test_unknown_when_all_fail(self) -> None:
        with patch("infomesh.resources.port_check._http_get", return_value=None):
            assert detect_cloud_provider() == CloudProvider.UNKNOWN


# ── is_port_listening ────────────────────────────────────────────────


class TestIsPortListening:
    """Test local port listening check."""

    def test_not_listening(self) -> None:
        # Port 59999 is unlikely to be in use
        assert is_port_listening(59999) is False

    def test_listening_mock(self) -> None:
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock()
            assert is_port_listening(4001) is True


# ── _check_iptables_allows ───────────────────────────────────────────


class TestCheckIptables:
    """Test iptables/nftables check."""

    def test_allows_when_no_drop(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Chain INPUT (policy ACCEPT)\nACCEPT all"

        with patch("subprocess.run", return_value=mock_result):
            assert _check_iptables_allows(4001) is True

    def test_blocked_when_drop_rule_exists(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "DROP tcp -- 0.0.0.0/0 0.0.0.0/0 tcp dpt:4001"

        with patch("subprocess.run", return_value=mock_result):
            assert _check_iptables_allows(4001) is False

    def test_allows_when_command_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _check_iptables_allows(4001) is True


# ── check_port_accessibility ─────────────────────────────────────────


class TestCheckPortAccessibility:
    """Test port accessibility check."""

    def test_returns_result_on_unknown_provider(self) -> None:
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.UNKNOWN,
            ),
            patch(
                "infomesh.resources.port_check._check_iptables_allows",
                return_value=True,
            ),
        ):
            result = check_port_accessibility(4001)
            assert isinstance(result, PortCheckResult)
            assert result.port == 4001
            assert result.provider == CloudProvider.UNKNOWN
            assert result.is_blocked is False

    def test_blocked_port(self) -> None:
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.AZURE,
            ),
            patch(
                "infomesh.resources.port_check._check_iptables_allows",
                return_value=False,
            ),
        ):
            result = check_port_accessibility(4001)
            assert result.is_blocked is True
            assert "blocked" in result.message.lower()


# ── _get_manual_instructions ─────────────────────────────────────────


class TestManualInstructions:
    """Test manual instruction generation per CSP."""

    @pytest.mark.parametrize(
        "provider",
        [
            CloudProvider.AZURE,
            CloudProvider.AWS,
            CloudProvider.GCP,
            CloudProvider.UNKNOWN,
        ],
    )
    def test_instructions_contain_port(self, provider: CloudProvider) -> None:
        instructions = _get_manual_instructions(provider, 4001)
        assert "4001" in instructions

    def test_azure_instructions_mention_nsg(self) -> None:
        text = _get_manual_instructions(CloudProvider.AZURE, 4001)
        assert "NSG" in text or "nsg" in text

    def test_aws_instructions_mention_sg(self) -> None:
        text = _get_manual_instructions(CloudProvider.AWS, 4001)
        assert "security" in text.lower() or "sg" in text.lower()

    def test_gcp_instructions_mention_firewall(self) -> None:
        text = _get_manual_instructions(CloudProvider.GCP, 4001)
        assert "firewall" in text.lower()

    def test_unknown_instructions_mention_ufw(self) -> None:
        text = _get_manual_instructions(CloudProvider.UNKNOWN, 4001)
        assert "ufw" in text or "iptables" in text


# ── check_port_and_offer_fix ─────────────────────────────────────────


class TestCheckPortAndOfferFix:
    """Test the interactive port check + auto-fix flow."""

    def test_non_cloud_shows_info_and_returns_true(self) -> None:
        with patch(
            "infomesh.resources.port_check.detect_cloud_provider",
            return_value=CloudProvider.UNKNOWN,
        ):
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_non_interactive_skips_prompt(self) -> None:
        """When stdin is not a tty, skip prompt and return True."""
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.AZURE,
            ),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = False
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_cloud_user_declines_returns_true(self) -> None:
        """User declines auto-open — still returns True (don't block startup)."""
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.AZURE,
            ),
            patch("sys.stdin") as mock_stdin,
            patch("click.confirm", return_value=False),
        ):
            mock_stdin.isatty.return_value = True
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_cloud_auto_open_success(self) -> None:
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.AWS,
            ),
            patch("sys.stdin") as mock_stdin,
            patch("click.confirm", return_value=True),
            patch(
                "infomesh.resources.port_check._auto_open_aws",
                return_value=(True, "Port opened"),
            ),
        ):
            mock_stdin.isatty.return_value = True
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_cloud_auto_open_failure(self) -> None:
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.GCP,
            ),
            patch("sys.stdin") as mock_stdin,
            patch("click.confirm", return_value=True),
            patch(
                "infomesh.resources.port_check._auto_open_gcp",
                return_value=(False, "CLI not found"),
            ),
        ):
            mock_stdin.isatty.return_value = True
            result = check_port_and_offer_fix(4001)
            assert result is False


# ── Auto-open functions ──────────────────────────────────────────────


class TestAutoOpenAzure:
    """Test Azure auto-open port logic with multi-NSG support."""

    def test_no_az_cli(self) -> None:
        from infomesh.resources.port_check import _auto_open_azure

        with patch("shutil.which", return_value=None):
            success, msg = _auto_open_azure(4001)
            assert success is False
            assert "az" in msg.lower()

    def test_no_imds(self) -> None:
        from infomesh.resources.port_check import _auto_open_azure

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=None
            ),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is False
            assert "metadata" in msg.lower() or "IMDS" in msg

    def test_success_single_nic_nsg(self) -> None:
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "myRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(["/subscriptions/sub/nic1"])
            elif "nic" in cmd and "networkSecurityGroup" in str(cmd):
                result.stdout = (
                    "/subscriptions/sub/resourceGroups/myRG"
                    "/providers/Microsoft.Network"
                    "/networkSecurityGroups/myNSG\n"
                )
            elif "nic" in cmd and "subnet" in str(cmd):
                result.stdout = "[]"
            elif "nsg" in cmd and "rule" in cmd:
                result.stdout = "{}"
            else:
                result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is True
            assert "4001" in msg

    def test_success_nic_and_subnet_nsgs(self) -> None:
        """Both NIC-level and subnet-level NSGs should get rules."""
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "vmRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }
        nic_nsg_id = (
            "/subscriptions/sub/resourceGroups/vmRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/nicNSG"
        )
        subnet_id = (
            "/subscriptions/sub/resourceGroups/netRG"
            "/providers/Microsoft.Network"
            "/virtualNetworks/myVNet/subnets/default"
        )
        subnet_nsg_id = (
            "/subscriptions/sub/resourceGroups/netRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/subnetNSG"
        )

        call_log: list[str] = []

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            joined = " ".join(str(c) for c in cmd)
            call_log.append(joined)

            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(["/subscriptions/sub/nic1"])
            elif "nic" in cmd and "networkSecurityGroup" in joined:
                result.stdout = nic_nsg_id + "\n"
            elif "nic" in cmd and "subnet" in joined:
                result.stdout = json.dumps([subnet_id])
            elif "vnet" in cmd and "subnet" in cmd and "show" in cmd:
                result.stdout = subnet_nsg_id + "\n"
            elif "nsg" in cmd and "rule" in cmd:
                result.stdout = "{}"
            else:
                result.stdout = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is True
            assert "2 NSG(s)" in msg
            # Verify both NSG rule create commands were called
            nsg_rule_calls = [c for c in call_log if "nsg" in c and "rule" in c]
            assert len(nsg_rule_calls) == 2

    def test_cross_rg_nsg_uses_correct_rg(self) -> None:
        """NSG in a different RG than the VM should use its own RG."""
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "vmRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }
        # NSG is in "networkRG", not "vmRG"
        nic_nsg_id = (
            "/subscriptions/sub/resourceGroups/networkRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/sharedNSG"
        )

        nsg_rule_rgs: list[str] = []

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(["/subscriptions/sub/nic1"])
            elif "nic" in cmd and "networkSecurityGroup" in str(cmd):
                result.stdout = nic_nsg_id + "\n"
            elif "nic" in cmd and "subnet" in str(cmd):
                result.stdout = "[]"
            elif "nsg" in cmd and "rule" in cmd:
                # Capture the -g parameter
                g_idx = cmd.index("-g")
                nsg_rule_rgs.append(cmd[g_idx + 1])
                result.stdout = "{}"
            else:
                result.stdout = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is True
            # Must use "networkRG", NOT "vmRG"
            assert nsg_rule_rgs == ["networkRG"]

    def test_multiple_ip_configs_different_subnets(self) -> None:
        """VM with multiple IP configs on different subnets."""
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "vmRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }
        subnet1_id = (
            "/subscriptions/sub/resourceGroups/netRG"
            "/providers/Microsoft.Network"
            "/virtualNetworks/vnet1/subnets/sub1"
        )
        subnet2_id = (
            "/subscriptions/sub/resourceGroups/netRG"
            "/providers/Microsoft.Network"
            "/virtualNetworks/vnet1/subnets/sub2"
        )
        nsg1_id = (
            "/subscriptions/sub/resourceGroups/netRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/nsg-sub1"
        )
        nsg2_id = (
            "/subscriptions/sub/resourceGroups/netRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/nsg-sub2"
        )

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            joined = " ".join(str(c) for c in cmd)

            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(["/subscriptions/sub/nic1"])
            elif "nic" in cmd and "networkSecurityGroup" in joined:
                # No NIC-level NSG
                result.stdout = ""
            elif "nic" in cmd and "subnet" in joined:
                # Two IP configs → two different subnets
                result.stdout = json.dumps([subnet1_id, subnet2_id])
            elif "vnet" in cmd and "subnet" in cmd:
                if "sub1" in joined:
                    result.stdout = nsg1_id + "\n"
                else:
                    result.stdout = nsg2_id + "\n"
            elif "nsg" in cmd and "rule" in cmd:
                result.stdout = "{}"
            else:
                result.stdout = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is True
            assert "2 NSG(s)" in msg

    def test_dedup_same_nsg_on_nic_and_subnet(self) -> None:
        """Same NSG on both NIC and subnet should not create duplicate rules."""
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "vmRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }
        same_nsg_id = (
            "/subscriptions/sub/resourceGroups/vmRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/sharedNSG"
        )
        subnet_id = (
            "/subscriptions/sub/resourceGroups/vmRG"
            "/providers/Microsoft.Network"
            "/virtualNetworks/vnet/subnets/default"
        )

        rule_create_count = 0

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            nonlocal rule_create_count
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""

            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(["/subscriptions/sub/nic1"])
            elif "nic" in cmd and "networkSecurityGroup" in str(cmd):
                result.stdout = same_nsg_id + "\n"
            elif "nic" in cmd and "subnet" in str(cmd):
                result.stdout = json.dumps([subnet_id])
            elif "vnet" in cmd and "subnet" in cmd:
                result.stdout = same_nsg_id + "\n"
            elif "nsg" in cmd and "rule" in cmd:
                rule_create_count += 1
                result.stdout = "{}"
            else:
                result.stdout = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is True
            # Should only create ONE rule (dedup by full resource ID)
            assert rule_create_count == 1
            assert "1 NSG(s)" in msg

    def test_no_nsgs_found(self) -> None:
        """VM with no NSGs should return helpful error."""
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "vmRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(["/subscriptions/sub/nic1"])
            elif "nic" in cmd and "networkSecurityGroup" in str(cmd):
                result.stdout = ""
            elif "nic" in cmd and "subnet" in str(cmd):
                result.stdout = "[]"
            else:
                result.stdout = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is False
            assert "no nsg" in msg.lower()

    def test_partial_failure(self) -> None:
        """Some NSG rules succeed, some fail → partial success."""
        from infomesh.resources.port_check import _auto_open_azure

        meta = {
            "compute": {
                "resourceGroupName": "vmRG",
                "name": "myVM",
                "subscriptionId": "sub-123",
            }
        }
        nsg1_id = (
            "/subscriptions/sub/resourceGroups/rg1"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/nsg1"
        )
        nsg2_id = (
            "/subscriptions/sub/resourceGroups/rg2"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/nsg2"
        )

        call_count = 0

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            nonlocal call_count
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""

            if "vm" in cmd and "show" in cmd:
                result.stdout = json.dumps(
                    ["/subscriptions/sub/nic1", "/subscriptions/sub/nic2"]
                )
            elif "nic" in cmd and "networkSecurityGroup" in str(cmd):
                call_count += 1
                if call_count == 1:
                    result.stdout = nsg1_id + "\n"
                else:
                    result.stdout = nsg2_id + "\n"
            elif "nic" in cmd and "subnet" in str(cmd):
                result.stdout = "[]"
            elif "nsg" in cmd and "rule" in cmd:
                if "nsg1" in str(cmd):
                    result.stdout = "{}"
                else:
                    result.returncode = 1
                    result.stderr = "AuthorizationFailed"
            else:
                result.stdout = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch(
                "infomesh.resources.port_check._get_azure_metadata", return_value=meta
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            # Partial success — at least one NSG was updated
            assert success is True
            assert "failed" in msg.lower()


class TestAutoOpenAWS:
    """Test AWS auto-open port logic with multi-SG support."""

    def test_no_aws_cli(self) -> None:
        from infomesh.resources.port_check import _auto_open_aws

        with patch("shutil.which", return_value=None):
            success, msg = _auto_open_aws(4001)
            assert success is False
            assert "aws" in msg.lower()

    def test_success_single_sg(self) -> None:
        from infomesh.resources.port_check import _auto_open_aws

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            if "describe-instances" in cmd:
                result.stdout = json.dumps([["sg-abc123", "my-sg"]])
            elif "authorize-security-group-ingress" in cmd:
                result.stdout = "{}"
            result.stderr = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/aws"),
            patch(
                "infomesh.resources.port_check._get_aws_metadata",
                return_value={"instance-id": "i-abc", "region": "us-east-1"},
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_aws(4001)
            assert success is True
            assert "sg-abc123" in msg

    def test_success_multiple_sgs(self) -> None:
        """All security groups should get the inbound rule."""
        from infomesh.resources.port_check import _auto_open_aws

        authorize_calls: list[str] = []

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if "describe-instances" in cmd:
                result.stdout = json.dumps(
                    [
                        ["sg-111", "web-sg"],
                        ["sg-222", "app-sg"],
                        ["sg-333", "db-sg"],
                    ]
                )
            elif "authorize-security-group-ingress" in cmd:
                sg_idx = cmd.index("--group-id")
                authorize_calls.append(cmd[sg_idx + 1])
                result.stdout = "{}"
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/aws"),
            patch(
                "infomesh.resources.port_check._get_aws_metadata",
                return_value={"instance-id": "i-abc", "region": "us-east-1"},
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_aws(4001)
            assert success is True
            assert "3 SG(s)" in msg
            assert authorize_calls == ["sg-111", "sg-222", "sg-333"]

    def test_duplicate_permission(self) -> None:
        from infomesh.resources.port_check import _auto_open_aws

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            if "describe-instances" in cmd:
                result.returncode = 0
                result.stdout = json.dumps([["sg-abc123", "my-sg"]])
                result.stderr = ""
            else:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "InvalidPermission.Duplicate"
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/aws"),
            patch(
                "infomesh.resources.port_check._get_aws_metadata",
                return_value={"instance-id": "i-abc", "region": "us-east-1"},
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_aws(4001)
            assert success is True
            assert "1 SG(s)" in msg


class TestAutoOpenGCP:
    """Test GCP auto-open port logic with auto-tagging."""

    def test_no_gcloud_cli(self) -> None:
        from infomesh.resources.port_check import _auto_open_gcp

        with patch("shutil.which", return_value=None):
            success, msg = _auto_open_gcp(4001)
            assert success is False
            assert "gcloud" in msg.lower()

    def test_success_with_auto_tag(self) -> None:
        from infomesh.resources.port_check import _auto_open_gcp

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/gcloud"),
            patch(
                "infomesh.resources.port_check._get_gcp_metadata",
                return_value={
                    "name": "myvm",
                    "zone": "projects/123/zones/us-central1-a",
                },
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_gcp(4001)
            assert success is True
            assert "tag" in msg.lower()
            assert "myvm" in msg

    def test_already_exists(self) -> None:
        from infomesh.resources.port_check import _auto_open_gcp

        call_count = 0

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # firewall-rules create — already exists
                result.returncode = 1
                result.stderr = "already exists"
            else:
                # add-tags — success
                result.returncode = 0
                result.stderr = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/gcloud"),
            patch(
                "infomesh.resources.port_check._get_gcp_metadata",
                return_value={
                    "name": "myvm",
                    "zone": "projects/123/zones/us-central1-a",
                },
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_gcp(4001)
            assert success is True

    def test_tag_failure_still_succeeds(self) -> None:
        """Firewall rule created but tagging fails — partial success."""
        from infomesh.resources.port_check import _auto_open_gcp

        call_count = 0

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # firewall-rules create — success
                result.returncode = 0
                result.stderr = ""
            else:
                # add-tags — failure
                result.returncode = 1
                result.stderr = "Permission denied"
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/gcloud"),
            patch(
                "infomesh.resources.port_check._get_gcp_metadata",
                return_value={
                    "name": "myvm",
                    "zone": "projects/123/zones/us-central1-a",
                },
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_gcp(4001)
            # Still success (rule was created)
            assert success is True
            assert "failed to add network tag" in msg.lower()
            assert "Run manually" in msg


# ── NsgInfo ──────────────────────────────────────────────────────────


class TestNsgInfo:
    """Test Azure NsgInfo dataclass and ARM resource ID parsing."""

    def test_from_standard_resource_id(self) -> None:
        rid = (
            "/subscriptions/abc-123/resourceGroups/myRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/myNSG"
        )
        nsg = NsgInfo.from_resource_id(rid, "NIC nic1")
        assert nsg.name == "myNSG"
        assert nsg.resource_group == "myRG"
        assert nsg.source == "NIC nic1"

    def test_cross_rg_parsing(self) -> None:
        rid = (
            "/subscriptions/abc-123/resourceGroups/networkRG"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/sharedNSG"
        )
        nsg = NsgInfo.from_resource_id(rid, "Subnet default")
        assert nsg.name == "sharedNSG"
        assert nsg.resource_group == "networkRG"

    def test_case_insensitive_rg_key(self) -> None:
        """Azure ARM IDs can have mixed case for 'resourceGroups'."""
        rid = (
            "/subscriptions/abc/ResourceGroups/MixedCase"
            "/providers/Microsoft.Network"
            "/networkSecurityGroups/nsg1"
        )
        nsg = NsgInfo.from_resource_id(rid, "NIC nic1")
        assert nsg.resource_group == "MixedCase"

    def test_minimal_id(self) -> None:
        """Handles edge case of simple name-only input."""
        nsg = NsgInfo.from_resource_id("myNSG", "NIC nic1")
        assert nsg.name == "myNSG"
        assert nsg.resource_group == ""

    def test_frozen(self) -> None:
        """NsgInfo should be immutable."""
        nsg = NsgInfo(name="nsg1", resource_group="rg1", source="NIC nic1")
        with pytest.raises(AttributeError):
            nsg.name = "changed"  # type: ignore[misc]


# ── WSL detection ─────────────────────────────────────────────────────

_PS_EXE = "/mnt/c/Windows/System32/powershell.exe"


def _mock_open_read(content: str) -> MagicMock:
    """Create a mock for ``open()`` that returns *content* on read."""
    m = MagicMock()
    ctx = MagicMock(read=MagicMock(return_value=content))
    m.__enter__ = MagicMock(return_value=ctx)
    m.__exit__ = MagicMock(return_value=False)
    return m


class TestWSLDetection:
    """Test WSL detection and auto-open functionality."""

    def test_is_wsl_true(self, tmp_path: object) -> None:
        """Detect WSL from /proc/version containing 'microsoft'."""
        content = (
            "Linux version 5.15.153.1-microsoft-standard-WSL2 (root@buildhost) (gcc)"
        )
        with patch("builtins.open", return_value=_mock_open_read(content)):
            assert _is_wsl() is True

    def test_is_wsl_false(self) -> None:
        """Non-WSL Linux kernel."""
        content = "Linux version 6.5.0-44-generic (buildd@lcy02)"
        with patch("builtins.open", return_value=_mock_open_read(content)):
            assert _is_wsl() is False

    def test_is_wsl_no_proc(self) -> None:
        """Returns False when /proc/version is unavailable."""
        with patch("builtins.open", side_effect=OSError("No such file")):
            assert _is_wsl() is False

    def test_wsl_manual_instructions_content(self) -> None:
        """WSL manual instructions contain firewall and portproxy steps."""
        text = _get_wsl_manual_instructions(4001)
        assert "New-NetFirewallRule" in text
        assert "netsh interface portproxy" in text
        assert "4001" in text
        assert "WSL2 IP changes on reboot" in text

    def test_auto_open_wsl_no_powershell(self) -> None:
        """Fail gracefully when powershell.exe is not found."""
        with patch("shutil.which", return_value=None):
            ok, msg = _auto_open_wsl(4001)
        assert not ok
        assert "powershell.exe" in msg

    def test_auto_open_wsl_no_ip(self) -> None:
        """Fail when WSL IP cannot be determined."""
        with (
            patch("shutil.which", return_value=_PS_EXE),
            patch(
                "infomesh.resources.port_check._get_wsl_ip",
                return_value=None,
            ),
        ):
            ok, msg = _auto_open_wsl(4001)
        assert not ok
        assert "WSL2 IP" in msg

    def test_auto_open_wsl_success(self) -> None:
        """Both firewall rule and port proxy succeed."""
        mock_result = MagicMock(returncode=0, stderr="", stdout="")
        with (
            patch("shutil.which", return_value=_PS_EXE),
            patch(
                "infomesh.resources.port_check._get_wsl_ip",
                return_value="172.20.0.2",
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            ok, msg = _auto_open_wsl(4001)
        assert ok
        assert "Firewall rule" in msg
        assert "port proxy" in msg
        assert "172.20.0.2" in msg

    def test_auto_open_wsl_firewall_access_denied(self) -> None:
        """Firewall fails with access denied, port proxy succeeds."""
        call_count = 0

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:  # Firewall call
                r.returncode = 1
                r.stderr = "Access is denied."
                r.stdout = ""
            else:  # Port proxy call
                r.returncode = 0
                r.stderr = ""
                r.stdout = ""
            return r

        with (
            patch("shutil.which", return_value=_PS_EXE),
            patch(
                "infomesh.resources.port_check._get_wsl_ip",
                return_value="172.20.0.2",
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            ok, msg = _auto_open_wsl(4001)
        assert ok  # partial success
        assert "failed" in msg.lower()

    def test_auto_open_wsl_both_fail(self) -> None:
        """Both firewall and port proxy fail."""
        mock_result = MagicMock(returncode=1, stderr="Access is denied.", stdout="")
        with (
            patch("shutil.which", return_value=_PS_EXE),
            patch(
                "infomesh.resources.port_check._get_wsl_ip",
                return_value="172.20.0.2",
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            ok, msg = _auto_open_wsl(4001)
        assert not ok

    def test_check_port_wsl_noninteractive(self) -> None:
        """Non-interactive mode prints info and returns True."""
        with (
            patch(
                "infomesh.resources.port_check._get_wsl_ip",
                return_value="172.20.0.2",
            ),
            patch(
                "infomesh.resources.port_check._get_wsl_host_ip",
                return_value="172.20.0.1",
            ),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = False
            result = _check_port_wsl(4001)
        assert result is True

    def test_check_port_wsl_user_declines(self) -> None:
        """User declines auto-fix — shows manual instructions."""
        with (
            patch(
                "infomesh.resources.port_check._get_wsl_ip",
                return_value="172.20.0.2",
            ),
            patch(
                "infomesh.resources.port_check._get_wsl_host_ip",
                return_value="172.20.0.1",
            ),
            patch("sys.stdin") as mock_stdin,
            patch("click.confirm", return_value=False),
        ):
            mock_stdin.isatty.return_value = True
            result = _check_port_wsl(4001)
        assert result is True  # doesn't block startup

    def test_check_port_and_offer_fix_wsl(self) -> None:
        """check_port_and_offer_fix delegates to WSL flow on WSL."""
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.UNKNOWN,
            ),
            patch("infomesh.resources.port_check._is_wsl", return_value=True),
            patch(
                "infomesh.resources.port_check._check_port_wsl",
                return_value=True,
            ) as mock_wsl,
        ):
            result = check_port_and_offer_fix(4001)
        assert result is True
        mock_wsl.assert_called_once_with(4001)

    def test_check_port_and_offer_fix_non_wsl_unknown(self) -> None:
        """Non-WSL unknown provider skips WSL flow."""
        with (
            patch(
                "infomesh.resources.port_check.detect_cloud_provider",
                return_value=CloudProvider.UNKNOWN,
            ),
            patch("infomesh.resources.port_check._is_wsl", return_value=False),
        ):
            result = check_port_and_offer_fix(4001)
        assert result is True
