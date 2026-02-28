"""Tests for preflight checks — disk space and network connectivity."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infomesh.resources.preflight import (
    IssueSeverity,
    check_disk_space,
    check_outbound_connectivity,
    get_disk_free_mb,
    is_disk_critically_low,
    run_preflight_checks,
)

# ── Disk space tests ───────────────────────────────────────────────────


class TestGetDiskFreeMb:
    """Tests for get_disk_free_mb."""

    def test_returns_float(self, tmp_path: Path) -> None:
        result = get_disk_free_mb(tmp_path)
        assert isinstance(result, float)
        assert result > 0

    def test_oserror_propagates(self) -> None:
        with pytest.raises(OSError):
            get_disk_free_mb(Path("/nonexistent/path/that/does/not/exist"))


class TestCheckDiskSpace:
    """Tests for check_disk_space."""

    def test_sufficient_space_no_issues(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.resources.preflight.get_disk_free_mb", return_value=5000.0
        ):
            issues = check_disk_space(tmp_path)
        assert len(issues) == 0

    def test_low_space_warning(self, tmp_path: Path) -> None:
        # Between MIN_DISK_SPACE_MB and 2*MIN_DISK_SPACE_MB
        with patch("infomesh.resources.preflight.get_disk_free_mb", return_value=800.0):
            issues = check_disk_space(tmp_path)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.WARNING
        assert issues[0].check == "disk_space"

    def test_critically_low_space_error(self, tmp_path: Path) -> None:
        with patch("infomesh.resources.preflight.get_disk_free_mb", return_value=100.0):
            issues = check_disk_space(tmp_path)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.ERROR
        assert "minimum" in issues[0].message.lower()

    def test_oserror_returns_error(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.resources.preflight.get_disk_free_mb",
            side_effect=OSError("disk error"),
        ):
            issues = check_disk_space(tmp_path)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.ERROR


class TestIsDiskCriticallyLow:
    """Tests for is_disk_critically_low."""

    def test_above_threshold(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.resources.preflight.get_disk_free_mb", return_value=5000.0
        ):
            assert not is_disk_critically_low(tmp_path)

    def test_below_threshold(self, tmp_path: Path) -> None:
        with patch("infomesh.resources.preflight.get_disk_free_mb", return_value=100.0):
            assert is_disk_critically_low(tmp_path)

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.resources.preflight.get_disk_free_mb",
            side_effect=OSError,
        ):
            assert not is_disk_critically_low(tmp_path)


# ── Network connectivity tests ─────────────────────────────────────────


class TestCheckOutboundConnectivity:
    """Tests for check_outbound_connectivity."""

    def test_all_reachable_no_issues(self) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("socket.create_connection", return_value=mock_conn):
            issues = check_outbound_connectivity()
        assert len(issues) == 0

    def test_all_unreachable_error(self) -> None:
        with patch(
            "socket.create_connection",
            side_effect=OSError("connection refused"),
        ):
            issues = check_outbound_connectivity()
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.ERROR
        assert "no outbound connectivity" in issues[0].message.lower()

    def test_partial_reachable_warning(self) -> None:
        call_count = 0

        def mock_connect(addr: tuple[str, int], timeout: float = 5.0) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                conn = MagicMock()
                conn.__enter__ = MagicMock(return_value=conn)
                conn.__exit__ = MagicMock(return_value=False)
                return conn
            raise OSError("refused")

        with patch("socket.create_connection", side_effect=mock_connect):
            issues = check_outbound_connectivity()
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.WARNING


# ── Combined preflight tests ───────────────────────────────────────────


class TestRunPreflightChecks:
    """Tests for the combined run_preflight_checks."""

    def test_all_clear(self, tmp_path: Path) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with (
            patch("infomesh.resources.preflight.get_disk_free_mb", return_value=5000.0),
            patch("socket.create_connection", return_value=mock_conn),
        ):
            issues = run_preflight_checks(tmp_path)
        assert len(issues) == 0

    def test_disk_error_blocks(self, tmp_path: Path) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with (
            patch("infomesh.resources.preflight.get_disk_free_mb", return_value=50.0),
            patch("socket.create_connection", return_value=mock_conn),
        ):
            issues = run_preflight_checks(tmp_path)
        errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
        assert len(errors) >= 1

    def test_network_error_blocks(self, tmp_path: Path) -> None:
        with (
            patch("infomesh.resources.preflight.get_disk_free_mb", return_value=5000.0),
            patch("socket.create_connection", side_effect=OSError),
        ):
            issues = run_preflight_checks(tmp_path)
        errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
        assert len(errors) >= 1
