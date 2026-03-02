"""Tests for PeerStore — persistent peer cache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from infomesh.p2p.peer_store import CachedPeer, PeerStore


@pytest.fixture()
def store(tmp_path: Path) -> PeerStore:
    """Create a PeerStore backed by a temp directory."""
    s = PeerStore(tmp_path)
    yield s  # type: ignore[misc]
    s.close()


PEER_A = "12D3KooWAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PEER_B = "12D3KooWBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
PEER_C = "12D3KooWCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
ADDR_A = f"/ip4/1.2.3.4/tcp/4001/p2p/{PEER_A}"
ADDR_B = f"/ip4/5.6.7.8/tcp/4001/p2p/{PEER_B}"
ADDR_C = f"/ip4/9.10.11.12/tcp/4001/p2p/{PEER_C}"


class TestUpsertAndLoad:
    """Tests for upsert and load_recent."""

    def test_upsert_new_peer(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        assert store.count() == 1

        peers = store.load_recent()
        assert len(peers) == 1
        assert peers[0].peer_id == PEER_A
        assert peers[0].multiaddr == ADDR_A
        assert peers[0].success_count == 1
        assert peers[0].fail_count == 0

    def test_upsert_increments_success_count(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        store.upsert(PEER_A, ADDR_A)
        store.upsert(PEER_A, ADDR_A)

        peers = store.load_recent()
        assert len(peers) == 1
        assert peers[0].success_count == 3

    def test_upsert_updates_multiaddr(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        new_addr = "/ip4/10.0.0.1/tcp/4001/p2p/" + PEER_A
        store.upsert(PEER_A, new_addr)

        peers = store.load_recent()
        assert peers[0].multiaddr == new_addr

    def test_load_recent_ordered_by_freshness(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        store.upsert(PEER_B, ADDR_B)
        store.upsert(PEER_C, ADDR_C)

        peers = store.load_recent()
        assert len(peers) == 3
        # Most recent first
        assert peers[0].peer_id == PEER_C
        assert peers[2].peer_id == PEER_A

    def test_load_recent_respects_limit(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        store.upsert(PEER_B, ADDR_B)
        store.upsert(PEER_C, ADDR_C)

        peers = store.load_recent(limit=2)
        assert len(peers) == 2


class TestFailureTracking:
    """Tests for recording failures and filtering unreliable peers."""

    def test_record_failure(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        store.record_failure(PEER_A)
        store.record_failure(PEER_A)

        peers = store.load_recent()
        assert peers[0].fail_count == 2
        assert peers[0].success_count == 1

    def test_high_failure_peers_excluded(self, store: PeerStore) -> None:
        """Peers with >80% failure rate and >=5 attempts are excluded."""
        store.upsert(PEER_A, ADDR_A)
        # 1 success + 5 failures = 6 total, success_rate = 1/6 ≈ 0.167
        for _ in range(5):
            store.record_failure(PEER_A)

        # Should be excluded (>80% failure with >=5 attempts)
        peers = store.load_recent()
        assert len(peers) == 0

    def test_low_failure_peers_included(self, store: PeerStore) -> None:
        """Peers with acceptable failure rate are still returned."""
        store.upsert(PEER_A, ADDR_A)
        store.upsert(PEER_A, ADDR_A)  # 2 successes
        store.record_failure(PEER_A)  # 1 failure, rate = 2/3 ≈ 0.67

        peers = store.load_recent()
        assert len(peers) == 1


class TestRemove:
    """Tests for peer removal."""

    def test_remove_peer(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        store.upsert(PEER_B, ADDR_B)
        store.remove(PEER_A)

        assert store.count() == 1
        peers = store.load_recent()
        assert peers[0].peer_id == PEER_B

    def test_remove_nonexistent_no_error(self, store: PeerStore) -> None:
        store.remove("nonexistent_peer")
        assert store.count() == 0


class TestPrune:
    """Tests for pruning stale entries."""

    def test_prune_old_peers(self, store: PeerStore) -> None:
        store.upsert(PEER_A, ADDR_A)
        # Manually backdate last_seen to older than max_age
        store._conn.execute(
            "UPDATE peers SET last_seen = ? WHERE peer_id = ?",
            (time.time() - 3600 * 200, PEER_A),  # 200 hours ago
        )
        store._conn.commit()

        store.upsert(PEER_B, ADDR_B)  # fresh peer

        removed = store.prune(max_age_hours=168)
        assert removed == 1
        assert store.count() == 1

        peers = store.load_recent()
        assert peers[0].peer_id == PEER_B

    def test_prune_excess_peers(self, store: PeerStore) -> None:
        for i in range(10):
            pid = f"12D3KooW{'X' * 40}{i:02d}"
            store.upsert(pid, f"/ip4/1.2.3.{i}/tcp/4001/p2p/{pid}")

        removed = store.prune(max_age_hours=9999, max_peers=5)
        assert removed == 5
        assert store.count() == 5


class TestSaveConnected:
    """Tests for batch-saving connected peers."""

    def test_save_connected_list(self, store: PeerStore) -> None:
        peers = [
            (PEER_A, ADDR_A),
            (PEER_B, ADDR_B),
        ]
        store.save_connected(peers)

        assert store.count() == 2
        loaded = store.load_recent()
        ids = {p.peer_id for p in loaded}
        assert PEER_A in ids
        assert PEER_B in ids

    def test_save_connected_empty(self, store: PeerStore) -> None:
        store.save_connected([])
        assert store.count() == 0


class TestCachedPeer:
    """Tests for CachedPeer dataclass."""

    def test_success_rate_zero_total(self) -> None:
        p = CachedPeer("id", "/addr", 0.0, 0, 0)
        assert p.success_rate == 0.0

    def test_success_rate_all_success(self) -> None:
        p = CachedPeer("id", "/addr", 0.0, 10, 0)
        assert p.success_rate == 1.0

    def test_success_rate_mixed(self) -> None:
        p = CachedPeer("id", "/addr", 0.0, 3, 7)
        assert p.success_rate == pytest.approx(0.3)


class TestPersistence:
    """Tests that data survives store reopening."""

    def test_data_persists_across_reopen(self, tmp_path: Path) -> None:
        # Write
        store1 = PeerStore(tmp_path)
        store1.upsert(PEER_A, ADDR_A)
        store1.close()

        # Re-open
        store2 = PeerStore(tmp_path)
        peers = store2.load_recent()
        assert len(peers) == 1
        assert peers[0].peer_id == PEER_A
        store2.close()
