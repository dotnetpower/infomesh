"""Tests for infomesh.resources.port_check – port accessibility & CSP auto-open."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from infomesh.resources.port_check import (
    CloudProvider,
    PortCheckResult,
    _check_iptables_allows,
    _get_manual_instructions,
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
            if "instance-id" in url and headers and "X-aws-ec2-metadata-token" in headers:
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
            patch("infomesh.resources.port_check.detect_cloud_provider", return_value=CloudProvider.UNKNOWN),
            patch("infomesh.resources.port_check._check_iptables_allows", return_value=True),
        ):
            result = check_port_accessibility(4001)
            assert isinstance(result, PortCheckResult)
            assert result.port == 4001
            assert result.provider == CloudProvider.UNKNOWN
            assert result.is_blocked is False

    def test_blocked_port(self) -> None:
        with (
            patch("infomesh.resources.port_check.detect_cloud_provider", return_value=CloudProvider.AZURE),
            patch("infomesh.resources.port_check._check_iptables_allows", return_value=False),
        ):
            result = check_port_accessibility(4001)
            assert result.is_blocked is True
            assert "blocked" in result.message.lower()


# ── _get_manual_instructions ─────────────────────────────────────────


class TestManualInstructions:
    """Test manual instruction generation per CSP."""

    @pytest.mark.parametrize("provider", [CloudProvider.AZURE, CloudProvider.AWS, CloudProvider.GCP, CloudProvider.UNKNOWN])
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
        with patch("infomesh.resources.port_check.detect_cloud_provider", return_value=CloudProvider.UNKNOWN):
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_cloud_user_declines_returns_true(self) -> None:
        """User declines auto-open — still returns True (don't block startup)."""
        with (
            patch("infomesh.resources.port_check.detect_cloud_provider", return_value=CloudProvider.AZURE),
            patch("click.confirm", return_value=False),
        ):
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_cloud_auto_open_success(self) -> None:
        with (
            patch("infomesh.resources.port_check.detect_cloud_provider", return_value=CloudProvider.AWS),
            patch("click.confirm", return_value=True),
            patch("infomesh.resources.port_check._auto_open_aws", return_value=(True, "Port opened")),
        ):
            result = check_port_and_offer_fix(4001)
            assert result is True

    def test_cloud_auto_open_failure(self) -> None:
        with (
            patch("infomesh.resources.port_check.detect_cloud_provider", return_value=CloudProvider.GCP),
            patch("click.confirm", return_value=True),
            patch("infomesh.resources.port_check._auto_open_gcp", return_value=(False, "CLI not found")),
        ):
            result = check_port_and_offer_fix(4001)
            assert result is False


# ── Auto-open functions ──────────────────────────────────────────────


class TestAutoOpenAzure:
    """Test Azure auto-open port logic."""

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
            patch("infomesh.resources.port_check._get_azure_metadata", return_value=None),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is False
            assert "metadata" in msg.lower() or "IMDS" in msg

    def test_success_path(self) -> None:
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
            elif "nic" in cmd and "show" in cmd and "networkSecurityGroup" in str(cmd):
                result.stdout = "/subscriptions/sub/nsg/myNSG\n"
            elif "nic" in cmd and "show" in cmd and "subnet" in str(cmd):
                result.stdout = ""
            elif "subnet" in cmd and "show" in cmd:
                result.stdout = ""
            elif "nsg" in cmd and "rule" in cmd:
                result.stdout = "{}"
            else:
                result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/az"),
            patch("infomesh.resources.port_check._get_azure_metadata", return_value=meta),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_azure(4001)
            assert success is True
            assert "4001" in msg


class TestAutoOpenAWS:
    """Test AWS auto-open port logic."""

    def test_no_aws_cli(self) -> None:
        from infomesh.resources.port_check import _auto_open_aws

        with patch("shutil.which", return_value=None):
            success, msg = _auto_open_aws(4001)
            assert success is False
            assert "aws" in msg.lower()

    def test_success_path(self) -> None:
        from infomesh.resources.port_check import _auto_open_aws

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            if "describe-instances" in cmd:
                result.stdout = json.dumps(["sg-abc123"])
            elif "authorize-security-group-ingress" in cmd:
                result.stdout = "{}"
            result.stderr = ""
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/aws"),
            patch("infomesh.resources.port_check._get_aws_metadata", return_value={"instance-id": "i-abc", "region": "us-east-1"}),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_aws(4001)
            assert success is True
            assert "sg-abc123" in msg

    def test_duplicate_permission(self) -> None:
        from infomesh.resources.port_check import _auto_open_aws

        def fake_run(cmd: list, **kw: object) -> MagicMock:
            result = MagicMock()
            if "describe-instances" in cmd:
                result.returncode = 0
                result.stdout = json.dumps(["sg-abc123"])
                result.stderr = ""
            else:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "InvalidPermission.Duplicate"
            return result

        with (
            patch("shutil.which", return_value="/usr/bin/aws"),
            patch("infomesh.resources.port_check._get_aws_metadata", return_value={"instance-id": "i-abc", "region": "us-east-1"}),
            patch("subprocess.run", side_effect=fake_run),
        ):
            success, msg = _auto_open_aws(4001)
            assert success is True
            assert "already open" in msg


class TestAutoOpenGCP:
    """Test GCP auto-open port logic."""

    def test_no_gcloud_cli(self) -> None:
        from infomesh.resources.port_check import _auto_open_gcp

        with patch("shutil.which", return_value=None):
            success, msg = _auto_open_gcp(4001)
            assert success is False
            assert "gcloud" in msg.lower()

    def test_success_path(self) -> None:
        from infomesh.resources.port_check import _auto_open_gcp

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/bin/gcloud"),
            patch("infomesh.resources.port_check._get_gcp_metadata", return_value={"name": "myvm", "zone": "projects/123/zones/us-central1-a"}),
            patch("subprocess.run", return_value=mock_result),
        ):
            success, msg = _auto_open_gcp(4001)
            assert success is True

    def test_already_exists(self) -> None:
        from infomesh.resources.port_check import _auto_open_gcp

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "already exists"

        with (
            patch("shutil.which", return_value="/usr/bin/gcloud"),
            patch("infomesh.resources.port_check._get_gcp_metadata", return_value={"name": "myvm", "zone": ""}),
            patch("subprocess.run", return_value=mock_result),
        ):
            success, msg = _auto_open_gcp(4001)
            assert success is True
            assert "already exists" in msg
