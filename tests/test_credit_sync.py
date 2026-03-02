"""Tests for infomesh.credits.sync — cross-node credit synchronization."""

from __future__ import annotations

import time

import pytest

from infomesh.credits.ledger import ActionType, CreditLedger
from infomesh.credits.sync import (
    SUMMARY_TTL_HOURS,
    CreditSummary,
    CreditSyncManager,
    CreditSyncStore,
)
from infomesh.hashing import content_hash

# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def ledger():
    """In-memory credit ledger."""
    lg = CreditLedger()
    yield lg
    lg.close()


@pytest.fixture
def sync_store():
    """In-memory credit sync store."""
    store = CreditSyncStore()
    yield store
    store.close()


@pytest.fixture
def manager(ledger, sync_store):
    """CreditSyncManager with test identity."""
    mgr = CreditSyncManager(
        ledger=ledger,
        store=sync_store,
        owner_email="test@example.com",
        key_pair=None,
        local_peer_id="local-peer-001",
    )
    return mgr


@pytest.fixture
def email_hash():
    """Canonical hash of test@example.com."""
    return content_hash("test@example.com")


# ── CreditSummary dataclass ───────────────────────────────


class TestCreditSummary:
    """CreditSummary serialization tests."""

    def test_to_dict_round_trip(self):
        s = CreditSummary(
            peer_id="peer-A",
            owner_email_hash="abc123",
            total_earned=150.5,
            total_spent=30.0,
            contribution_score=120.5,
            entry_count=50,
            tier="Tier 2",
            timestamp=1000.0,
            signature="sig-hex",
        )
        d = s.to_dict()
        s2 = CreditSummary.from_dict(d)
        assert s2.peer_id == s.peer_id
        assert s2.owner_email_hash == s.owner_email_hash
        assert s2.total_earned == s.total_earned
        assert s2.total_spent == s.total_spent
        assert s2.contribution_score == s.contribution_score
        assert s2.entry_count == s.entry_count
        assert s2.tier == s.tier
        assert s2.timestamp == s.timestamp
        assert s2.signature == s.signature

    def test_from_dict_defaults(self):
        s = CreditSummary.from_dict({})
        assert s.peer_id == ""
        assert s.total_earned == 0.0
        assert s.total_spent == 0.0
        assert s.entry_count == 0
        assert s.tier == "Tier 1"
        assert s.signature == ""

    def test_from_dict_type_coercion(self):
        s = CreditSummary.from_dict(
            {
                "peer_id": 123,
                "total_earned": "bad",
                "total_spent": None,
                "entry_count": "bad",
                "contribution_score": "bad",
                "timestamp": "bad",
            }
        )
        assert s.peer_id == "123"
        assert s.total_earned == 0.0
        assert s.total_spent == 0.0
        assert s.entry_count == 0
        assert s.contribution_score == 0.0
        assert s.timestamp == 0.0


# ── CreditSyncStore ───────────────────────────────────────


class TestCreditSyncStore:
    """CreditSyncStore persistence tests."""

    def test_store_and_retrieve(self, sync_store, email_hash):
        s = CreditSummary(
            peer_id="peer-A",
            owner_email_hash=email_hash,
            total_earned=100.0,
            total_spent=10.0,
            contribution_score=90.0,
            entry_count=20,
            tier="Tier 2",
            timestamp=time.time(),
            signature="sig1",
        )
        sync_store.store_summary(s)
        results = sync_store.get_peer_summaries(email_hash)
        assert len(results) == 1
        assert results[0].peer_id == "peer-A"
        assert results[0].total_earned == 100.0

    def test_upsert_overwrites(self, sync_store, email_hash):
        now = time.time()
        s1 = CreditSummary(
            peer_id="peer-A",
            owner_email_hash=email_hash,
            total_earned=100.0,
            total_spent=10.0,
            contribution_score=90.0,
            entry_count=20,
            tier="Tier 2",
            timestamp=now,
            signature="sig1",
        )
        sync_store.store_summary(s1)
        s2 = CreditSummary(
            peer_id="peer-A",
            owner_email_hash=email_hash,
            total_earned=200.0,
            total_spent=20.0,
            contribution_score=180.0,
            entry_count=40,
            tier="Tier 3",
            timestamp=now + 1,
            signature="sig2",
        )
        sync_store.store_summary(s2)
        results = sync_store.get_peer_summaries(email_hash)
        assert len(results) == 1
        assert results[0].total_earned == 200.0
        assert results[0].tier == "Tier 3"

    def test_peer_count(self, sync_store, email_hash):
        assert sync_store.peer_count(email_hash) == 0
        s = CreditSummary(
            peer_id="peer-A",
            owner_email_hash=email_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        sync_store.store_summary(s)
        assert sync_store.peer_count(email_hash) == 1

    def test_purge_stale(self, sync_store, email_hash):
        old_ts = time.time() - (SUMMARY_TTL_HOURS * 3600) - 100
        s = CreditSummary(
            peer_id="old-peer",
            owner_email_hash=email_hash,
            total_earned=10.0,
            total_spent=1.0,
            contribution_score=9.0,
            entry_count=5,
            tier="Tier 1",
            timestamp=old_ts,
            signature="",
        )
        sync_store.store_summary(s)
        assert sync_store.peer_count(email_hash) == 0  # stale
        deleted = sync_store.purge_stale()
        assert deleted == 1

    def test_remove_peer(self, sync_store, email_hash):
        s = CreditSummary(
            peer_id="peer-B",
            owner_email_hash=email_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        sync_store.store_summary(s)
        assert sync_store.peer_count(email_hash) == 1
        sync_store.remove_peer("peer-B")
        assert sync_store.peer_count(email_hash) == 0

    def test_different_owners_isolated(self, sync_store):
        hash_a = content_hash("user_a@example.com")
        hash_b = content_hash("user_b@example.com")
        s_a = CreditSummary(
            peer_id="peer-A",
            owner_email_hash=hash_a,
            total_earned=100.0,
            total_spent=10.0,
            contribution_score=90.0,
            entry_count=20,
            tier="Tier 2",
            timestamp=time.time(),
            signature="",
        )
        s_b = CreditSummary(
            peer_id="peer-B",
            owner_email_hash=hash_b,
            total_earned=200.0,
            total_spent=20.0,
            contribution_score=180.0,
            entry_count=40,
            tier="Tier 3",
            timestamp=time.time(),
            signature="",
        )
        sync_store.store_summary(s_a)
        sync_store.store_summary(s_b)
        assert sync_store.peer_count(hash_a) == 1
        assert sync_store.peer_count(hash_b) == 1
        results_a = sync_store.get_peer_summaries(hash_a)
        assert len(results_a) == 1
        assert results_a[0].peer_id == "peer-A"


# ── CreditSyncManager ─────────────────────────────────────


class TestCreditSyncManager:
    """CreditSyncManager orchestration tests."""

    def test_owner_email_hash(self, manager, email_hash):
        assert manager.owner_email_hash == email_hash
        assert manager.has_identity

    def test_no_identity(self, ledger, sync_store):
        mgr = CreditSyncManager(
            ledger=ledger,
            store=sync_store,
            owner_email="",
            key_pair=None,
            local_peer_id="",
        )
        assert not mgr.has_identity
        assert mgr.owner_email_hash == ""

    def test_build_summary(self, manager, ledger, email_hash):
        ledger.record_action(ActionType.CRAWL, quantity=10)
        summary = manager.build_summary()
        assert summary.peer_id == "local-peer-001"
        assert summary.owner_email_hash == email_hash
        assert summary.total_earned == 10.0
        assert summary.timestamp > 0

    def test_receive_summary_same_owner(self, manager, email_hash):
        s = CreditSummary(
            peer_id="remote-peer-002",
            owner_email_hash=email_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        accepted = manager.receive_summary(s, verify_signature=False)
        assert accepted

    def test_receive_summary_different_owner(self, manager):
        other_hash = content_hash("other@example.com")
        s = CreditSummary(
            peer_id="remote-peer-003",
            owner_email_hash=other_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        accepted = manager.receive_summary(s, verify_signature=False)
        assert not accepted

    def test_reject_own_summary(self, manager, email_hash):
        s = CreditSummary(
            peer_id="local-peer-001",
            owner_email_hash=email_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        accepted = manager.receive_summary(s, verify_signature=False)
        assert not accepted

    def test_reject_future_timestamp(self, manager, email_hash):
        s = CreditSummary(
            peer_id="remote-peer-004",
            owner_email_hash=email_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time() + 600,  # 10 min in future
            signature="",
        )
        accepted = manager.receive_summary(s, verify_signature=False)
        assert not accepted

    def test_aggregated_stats_local_only(self, manager, ledger):
        ledger.record_action(ActionType.CRAWL, quantity=10)
        agg = manager.aggregated_stats()
        assert agg.node_count == 1
        assert agg.total_earned == 10.0
        assert agg.balance == 10.0
        assert len(agg.peer_summaries) == 0

    def test_aggregated_stats_with_peer(self, manager, ledger, email_hash):
        ledger.record_action(ActionType.CRAWL, quantity=10)
        peer_summary = CreditSummary(
            peer_id="remote-peer-005",
            owner_email_hash=email_hash,
            total_earned=20.0,
            total_spent=3.0,
            contribution_score=17.0,
            entry_count=5,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        manager.receive_summary(peer_summary, verify_signature=False)
        agg = manager.aggregated_stats()
        assert agg.node_count == 2
        assert agg.total_earned == 30.0  # 10 local + 20 peer
        assert agg.total_spent == 3.0  # 0 local + 3 peer
        assert agg.balance == 27.0
        assert len(agg.peer_summaries) == 1

    def test_needs_sync(self, manager):
        assert manager.needs_sync("unknown-peer")
        manager.register_same_owner_peer("known-peer")
        assert manager.needs_sync("known-peer")

    def test_register_same_owner_peer(self, manager):
        assert len(manager.get_same_owner_peers()) == 0
        manager.register_same_owner_peer("peer-X")
        assert "peer-X" in manager.get_same_owner_peers()

    def test_register_own_peer_ignored(self, manager):
        manager.register_same_owner_peer("local-peer-001")
        assert len(manager.get_same_owner_peers()) == 0

    def test_purge_stale_delegates(self, manager, sync_store, email_hash):
        old_ts = time.time() - (SUMMARY_TTL_HOURS * 3600) - 100
        s = CreditSummary(
            peer_id="stale-peer",
            owner_email_hash=email_hash,
            total_earned=10.0,
            total_spent=1.0,
            contribution_score=9.0,
            entry_count=5,
            tier="Tier 1",
            timestamp=old_ts,
            signature="",
        )
        sync_store.store_summary(s)
        deleted = manager.purge_stale()
        assert deleted == 1

    def test_receive_no_identity(self, ledger, sync_store, email_hash):
        mgr = CreditSyncManager(
            ledger=ledger,
            store=sync_store,
            owner_email="",
            key_pair=None,
            local_peer_id="",
        )
        s = CreditSummary(
            peer_id="remote",
            owner_email_hash=email_hash,
            total_earned=50.0,
            total_spent=5.0,
            contribution_score=45.0,
            entry_count=10,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        assert not mgr.receive_summary(s, verify_signature=False)


# ── MCP handler injection ─────────────────────────────────


class TestInjectNetworkCredits:
    """Test _inject_network_credits helper."""

    def test_none_manager_no_op(self):
        from infomesh.mcp.handlers import _inject_network_credits

        cr: dict[str, object] = {"balance": 10.0}
        _inject_network_credits(cr, None)
        assert "network" not in cr

    def test_single_node_no_network(self, ledger, sync_store):
        from infomesh.mcp.handlers import _inject_network_credits

        mgr = CreditSyncManager(
            ledger=ledger,
            store=sync_store,
            owner_email="test@example.com",
            key_pair=None,
            local_peer_id="p1",
        )
        cr: dict[str, object] = {"balance": 10.0}
        _inject_network_credits(cr, mgr)
        assert "network" not in cr  # only 1 node

    def test_multi_node_adds_network(self, ledger, sync_store, email_hash):
        from infomesh.mcp.handlers import _inject_network_credits

        mgr = CreditSyncManager(
            ledger=ledger,
            store=sync_store,
            owner_email="test@example.com",
            key_pair=None,
            local_peer_id="p1",
        )
        ledger.record_action(ActionType.CRAWL, quantity=10)
        peer_s = CreditSummary(
            peer_id="p2",
            owner_email_hash=email_hash,
            total_earned=20.0,
            total_spent=2.0,
            contribution_score=18.0,
            entry_count=5,
            tier="Tier 1",
            timestamp=time.time(),
            signature="",
        )
        mgr.receive_summary(peer_s, verify_signature=False)

        cr: dict[str, object] = {"balance": 10.0}
        _inject_network_credits(cr, mgr)
        assert "network" in cr
        net = cr["network"]
        assert isinstance(net, dict)
        assert net["node_count"] == 2
        assert net["total_earned"] == 30.0
