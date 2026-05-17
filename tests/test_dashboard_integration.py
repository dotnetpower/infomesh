"""Integration tests for dashboard data paths.

These tests simulate what the dashboard actually does:
- Read p2p_status.json (fresh, stale, missing)
- Read index.db for stats
- Check is_node_running()
- Verify all panels get correct data

This catches issues like:
- "State: Stopped" when crawler is active
- "Node ID: —" when peer_id is available
- "Crawled: 0" when documents are being indexed
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from infomesh.config import Config, IndexConfig, NodeConfig
from infomesh.dashboard.data_cache import DashboardDataCache
from infomesh.dashboard.utils import (
    is_node_running,
    read_p2p_status,
)


def _make_config(tmp_path: Path) -> Config:
    db_path = tmp_path / "index.db"
    return Config(
        node=NodeConfig(data_dir=tmp_path),
        index=IndexConfig(db_path=db_path),
    )


def _create_index_db(db_path: Path, doc_count: int = 5) -> None:
    """Create a minimal index.db with documents matching LocalStore schema."""
    from infomesh.index.local_store import LocalStore

    store = LocalStore(db_path=db_path)
    for i in range(doc_count):
        store.add_document(
            url=f"https://example.com/page{i}",
            title=f"Page {i}",
            text=f"Content of page {i} with enough text to be indexed properly",
            raw_html_hash=f"hash{i}",
            text_hash=f"texthash{i}",
            language="en",
        )
    store.close()


def _write_p2p_status(
    tmp_path: Path,
    state: str = "running",
    peer_id: str = "12D3KooWTestPeerId",
    peers: int = 3,
    age_seconds: float = 0,
) -> None:
    """Write a p2p_status.json file."""
    data = {
        "state": state,
        "peer_id": peer_id,
        "peers": peers,
        "peer_ids": [f"peer{i}" for i in range(peers)],
        "listen_addrs": ["/ip4/0.0.0.0/tcp/4001"],
        "timestamp": time.time() - age_seconds,
        "error": "",
        "dht": {
            "keys_stored": 10,
            "keys_published": 5,
        },
    }
    (tmp_path / "p2p_status.json").write_text(json.dumps(data))


# ── P2P Status Tests ───────────────────────────────────────────────


class TestP2PStatusFresh:
    """When p2p_status.json is fresh (<30s)."""

    def test_returns_full_data(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _write_p2p_status(tmp_path, state="running", peers=3, age_seconds=5)
        data = read_p2p_status(config)
        assert data["state"] == "running"
        assert data["peers"] == 3
        assert data["peer_id"] == "12D3KooWTestPeerId"


class TestP2PStatusStale:
    """When p2p_status.json is stale (>30s)."""

    def test_preserves_peer_id(self, tmp_path: Path) -> None:
        """REGRESSION: peer_id should still be visible when data is stale."""
        config = _make_config(tmp_path)
        _write_p2p_status(tmp_path, peer_id="12D3KooWMyNode", age_seconds=120)
        data = read_p2p_status(config)
        assert data.get("peer_id") == "12D3KooWMyNode"

    def test_shows_stopped_state(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _write_p2p_status(tmp_path, state="running", age_seconds=120)
        data = read_p2p_status(config)
        assert data.get("state") == "stopped"

    def test_peers_zeroed(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _write_p2p_status(tmp_path, peers=5, age_seconds=120)
        data = read_p2p_status(config)
        assert data.get("peers") == 0


class TestP2PStatusMissing:
    """When p2p_status.json does not exist."""

    def test_returns_empty(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        data = read_p2p_status(config)
        assert data == {}


# ── Node Running Detection ─────────────────────────────────────────


class TestNodeRunning:
    def test_no_pid_no_db(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        assert not is_node_running(config)

    def test_recent_db_activity(self, tmp_path: Path) -> None:
        """REGRESSION: active crawler (DB writes) = node running."""
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=1)
        # Touch the DB to simulate recent write
        config.index.db_path.touch()
        assert is_node_running(config)

    def test_old_db_no_pid(self, tmp_path: Path) -> None:
        """Old DB + no PID = not running."""
        import os

        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=1)
        # Set mtime to 5 minutes ago
        old_time = time.time() - 300
        os.utime(config.index.db_path, (old_time, old_time))
        assert not is_node_running(config)


# ── Dashboard Data Cache ───────────────────────────────────────────


class TestDashboardDataCache:
    def test_reads_document_count(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=10)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        assert stats.document_count == 10
        cache.close()

    def test_pages_last_hour(self, tmp_path: Path) -> None:
        """REGRESSION: recently crawled pages should show in activity."""
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=5)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        # All 5 were created within last hour (staggered by 60s each)
        assert stats.pages_last_hour == 5
        cache.close()

    def test_top_domains(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=5)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        assert len(stats.top_domains) > 0
        assert stats.top_domains[0][0] == "example.com"
        cache.close()

    def test_recent_docs(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=3)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        assert len(stats.recent_docs) == 3
        # All docs should be from example.com
        urls = {d.url for d in stats.recent_docs}
        assert "https://example.com/page0" in urls
        cache.close()

    def test_missing_db(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        assert stats.document_count == 0
        cache.close()

    def test_cache_ttl(self, tmp_path: Path) -> None:
        """Cache should not re-query DB within TTL."""
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=3)
        cache = DashboardDataCache(config, ttl=10)
        stats1 = cache.get_stats()
        assert stats1.document_count == 3

        # Add more docs directly via LocalStore
        from infomesh.index.local_store import LocalStore

        store = LocalStore(db_path=config.index.db_path)
        store.add_document(
            url="https://new.com",
            title="New",
            text="New content for testing cache behavior",
            raw_html_hash="hnew",
            text_hash="thnew",
        )
        store.close()

        # Still cached
        stats2 = cache.get_stats()
        assert stats2.document_count == 3  # TTL not expired
        cache.close()


# ── Overview Panel Data ────────────────────────────────────────────


class TestOverviewData:
    """Test the data flow that feeds the Overview tab."""

    def test_activity_shows_recent_crawls(self, tmp_path: Path) -> None:
        """Crawled count should reflect pages_last_hour."""
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=7)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        # This is what gets fed to ActivityPanel.update_crawl()
        assert stats.pages_last_hour > 0
        cache.close()

    def test_index_count_matches_db(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _create_index_db(config.index.db_path, doc_count=42)
        cache = DashboardDataCache(config, ttl=0)
        stats = cache.get_stats()
        assert stats.document_count == 42
        cache.close()


# ── Network Panel Data ─────────────────────────────────────────────


class TestNetworkPanelData:
    """Test the data flow that feeds the Network tab."""

    def test_fresh_status_shows_online(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        _write_p2p_status(tmp_path, state="running", peers=2, age_seconds=5)
        data = read_p2p_status(config)
        assert data["state"] == "running"
        assert data["peers"] == 2
        assert len(str(data["peer_id"])) > 10

    def test_stale_status_shows_peer_id(self, tmp_path: Path) -> None:
        """REGRESSION: Node ID must be visible even when P2P is stopped."""
        config = _make_config(tmp_path)
        _write_p2p_status(
            tmp_path,
            peer_id="12D3KooWAuHBwnMY",
            age_seconds=300,
        )
        data = read_p2p_status(config)
        assert data.get("peer_id") == "12D3KooWAuHBwnMY"

    def test_no_status_file(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        data = read_p2p_status(config)
        assert data.get("peer_id", "") == ""
