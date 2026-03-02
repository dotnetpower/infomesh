"""Tests for infomesh.version_check — PyPI check + peer version tracking."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from infomesh.version_check import (
    PeerVersionTracker,
    UpdateInfo,
    _parse_version,
    check_for_update,
    check_pypi_update,
    format_update_banner,
    is_newer,
)

# ── Version parsing ─────────────────────────────────────────────────


class TestParseVersion:
    def test_simple(self) -> None:
        assert _parse_version("0.1.10") == (0, 1, 10)

    def test_two_segments(self) -> None:
        assert _parse_version("1.0") == (1, 0)

    def test_prerelease(self) -> None:
        # Pre-release suffix is stripped for comparison
        assert _parse_version("0.2.0a1") == (0, 2, 0)

    def test_empty(self) -> None:
        assert _parse_version("") == (0,)


class TestIsNewer:
    def test_newer(self) -> None:
        assert is_newer("0.2.0", "0.1.10") is True

    def test_same(self) -> None:
        assert is_newer("0.1.10", "0.1.10") is False

    def test_older(self) -> None:
        assert is_newer("0.1.9", "0.1.10") is False

    def test_major_bump(self) -> None:
        assert is_newer("1.0.0", "0.99.99") is True

    def test_uses_default_version(self) -> None:
        # When current is None, uses __version__
        # Just verify it doesn't crash
        is_newer("999.0.0")


# ── PyPI check ──────────────────────────────────────────────────────


class TestCheckPypiUpdate:
    def test_newer_available(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="999.0.0",
        ):
            result = check_pypi_update(tmp_path)
            assert result is not None
            assert result.latest == "999.0.0"
            assert result.source == "pypi"

    def test_up_to_date(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="0.0.1",
        ):
            result = check_pypi_update(tmp_path)
            assert result is None

    def test_pypi_unreachable(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value=None,
        ):
            result = check_pypi_update(tmp_path)
            assert result is None

    def test_cache_is_used(self, tmp_path: Path) -> None:
        # Write a fresh cache
        cache_file = tmp_path / "version_cache.json"
        cache_file.write_text(
            json.dumps({"version": "999.0.0", "ts": time.time()}),
        )
        # Should use cache, not call PyPI
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
        ) as mock_fetch:
            result = check_pypi_update(tmp_path)
            assert result is not None
            assert result.latest == "999.0.0"
            mock_fetch.assert_not_called()

    def test_stale_cache_is_ignored(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "version_cache.json"
        cache_file.write_text(
            json.dumps({"version": "999.0.0", "ts": time.time() - 100_000}),
        )
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="888.0.0",
        ):
            result = check_pypi_update(tmp_path)
            assert result is not None
            assert result.latest == "888.0.0"

    def test_cache_written_after_fetch(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="999.0.0",
        ):
            check_pypi_update(tmp_path)
        cache_file = tmp_path / "version_cache.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["version"] == "999.0.0"


# ── Peer version tracking ──────────────────────────────────────────


class TestPeerVersionTracker:
    def test_empty_tracker(self) -> None:
        t = PeerVersionTracker()
        assert t.get_newest_peer_version() is None
        assert t.check_peer_update() is None

    def test_record_and_retrieve(self) -> None:
        t = PeerVersionTracker()
        t.record("peer1", "0.1.10")
        t.record("peer2", "0.2.0")
        assert t.get_newest_peer_version() == "0.2.0"

    def test_update_detected(self) -> None:
        t = PeerVersionTracker()
        t.record("peer1", "999.0.0")
        result = t.check_peer_update()
        assert result is not None
        assert result.latest == "999.0.0"
        assert result.source == "peer"

    def test_no_update_when_same(self) -> None:
        t = PeerVersionTracker()
        from infomesh import __version__

        t.record("peer1", __version__)
        assert t.check_peer_update() is None

    def test_invalid_version_ignored(self) -> None:
        t = PeerVersionTracker()
        t.record("peer1", "")
        assert t.get_newest_peer_version() is None

    def test_peer_versions_property(self) -> None:
        t = PeerVersionTracker()
        t.record("peer1", "0.1.0")
        t.record("peer2", "0.2.0")
        versions = t.peer_versions
        assert len(versions) == 2
        assert versions["peer1"] == "0.1.0"
        # Ensure it's a copy
        versions["peer3"] = "0.3.0"
        assert "peer3" not in t.peer_versions


# ── Combined check ──────────────────────────────────────────────────


class TestCheckForUpdate:
    def test_pypi_only(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="999.0.0",
        ):
            result = check_for_update(data_dir=tmp_path)
            assert result is not None
            assert result.source == "pypi"

    def test_peer_only(self) -> None:
        t = PeerVersionTracker()
        t.record("p1", "999.0.0")
        result = check_for_update(peer_tracker=t)
        assert result is not None
        assert result.source == "peer"

    def test_peer_wins_when_higher(self, tmp_path: Path) -> None:
        t = PeerVersionTracker()
        t.record("p1", "1000.0.0")
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="999.0.0",
        ):
            result = check_for_update(data_dir=tmp_path, peer_tracker=t)
            assert result is not None
            assert result.latest == "1000.0.0"
            assert result.source == "peer"

    def test_pypi_wins_when_higher(self, tmp_path: Path) -> None:
        t = PeerVersionTracker()
        t.record("p1", "999.0.0")
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="1000.0.0",
        ):
            result = check_for_update(data_dir=tmp_path, peer_tracker=t)
            assert result is not None
            assert result.latest == "1000.0.0"
            assert result.source == "pypi"

    def test_no_update(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.version_check._fetch_latest_from_pypi",
            return_value="0.0.1",
        ):
            result = check_for_update(data_dir=tmp_path)
            assert result is None


# ── Banner formatting ───────────────────────────────────────────────


class TestFormatUpdateBanner:
    def test_pypi_banner(self) -> None:
        info = UpdateInfo(current="0.1.10", latest="0.2.0", source="pypi")
        banner = format_update_banner(info)
        assert "v0.1.10" in banner
        assert "v0.2.0" in banner
        assert "PyPI" in banner
        assert "infomesh update" in banner

    def test_peer_banner(self) -> None:
        info = UpdateInfo(current="0.1.10", latest="0.2.0", source="peer")
        banner = format_update_banner(info)
        assert "P2P peer" in banner
