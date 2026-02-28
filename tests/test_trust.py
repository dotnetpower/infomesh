"""Tests for infomesh.trust.scoring — unified trust scoring."""

from __future__ import annotations

import pytest

from infomesh.trust.scoring import (
    AUDIT_FAILURE_ISOLATION_THRESHOLD,
    MAX_CONTRIBUTION_SCORE,
    MAX_UPTIME_HOURS,
    TrustStore,
    TrustTier,
    compute_trust_score,
    trust_tier,
)


@pytest.fixture
def store():
    """In-memory trust store."""
    s = TrustStore()
    yield s
    s.close()


# --- Pure computation tests ------------------------------------------------


class TestComputeTrustScore:
    def test_all_max_is_one(self):
        score = compute_trust_score(
            uptime_hours=MAX_UPTIME_HOURS,
            contribution_raw=MAX_CONTRIBUTION_SCORE,
            audit_total=100,
            audit_passed=100,
            summary_avg=1.0,
        )
        assert score == pytest.approx(1.0)

    def test_all_zero_uses_defaults(self):
        """With no data, audit_rate and summary default to 0.5."""
        score = compute_trust_score(0, 0, 0, 0, 0.0)
        # W_AUDIT * 0.5 + W_SUMMARY * 0.5 = 0.40*0.5 + 0.20*0.5 = 0.30
        assert score == pytest.approx(0.30, abs=0.01)

    def test_partial_uptime(self):
        score = compute_trust_score(
            uptime_hours=MAX_UPTIME_HOURS / 2,
            contribution_raw=0,
            audit_total=10,
            audit_passed=8,
            summary_avg=0.7,
        )
        assert 0.0 < score < 1.0

    def test_audit_dominates(self):
        """Audit weight is 0.40 — the largest signal."""
        good_audit = compute_trust_score(0, 0, 100, 100, 0.0)
        bad_audit = compute_trust_score(0, 0, 100, 0, 0.0)
        assert good_audit > bad_audit


class TestTrustTier:
    def test_trusted(self):
        assert trust_tier(0.9) == TrustTier.TRUSTED

    def test_normal(self):
        assert trust_tier(0.6) == TrustTier.NORMAL

    def test_suspect(self):
        assert trust_tier(0.35) == TrustTier.SUSPECT

    def test_untrusted(self):
        assert trust_tier(0.1) == TrustTier.UNTRUSTED

    def test_boundary_trusted(self):
        assert trust_tier(0.8) == TrustTier.TRUSTED

    def test_boundary_normal(self):
        assert trust_tier(0.5) == TrustTier.NORMAL


# --- TrustStore tests ------------------------------------------------------


class TestTrustStore:
    def test_unknown_peer_returns_none(self, store: TrustStore):
        assert store.get_trust("unknown-peer") is None

    def test_default_trust_score(self, store: TrustStore):
        assert store.get_trust_score("unknown-peer") == 0.5

    def test_update_uptime(self, store: TrustStore):
        store.update_uptime("peer-1", 100.0)
        trust = store.get_trust("peer-1")
        assert trust is not None
        assert trust.uptime_score > 0

    def test_update_contribution(self, store: TrustStore):
        store.update_contribution("peer-1", 500.0)
        trust = store.get_trust("peer-1")
        assert trust is not None
        assert trust.contribution_score > 0

    def test_audit_pass(self, store: TrustStore):
        store.record_audit("peer-1", passed=True)
        trust = store.get_trust("peer-1")
        assert trust is not None
        assert trust.audit_pass_rate == pytest.approx(1.0)
        assert trust.consecutive_audit_failures == 0

    def test_audit_fail(self, store: TrustStore):
        store.record_audit("peer-1", passed=False)
        trust = store.get_trust("peer-1")
        assert trust is not None
        assert trust.audit_pass_rate == pytest.approx(0.0)
        assert trust.consecutive_audit_failures == 1

    def test_consecutive_failures_isolation(self, store: TrustStore):
        """3 consecutive failures → isolation."""
        for _ in range(AUDIT_FAILURE_ISOLATION_THRESHOLD):
            store.record_audit("bad-peer", passed=False)
        trust = store.get_trust("bad-peer")
        assert trust is not None
        assert trust.isolated is True

    def test_pass_resets_consecutive_failures(self, store: TrustStore):
        store.record_audit("peer-1", passed=False)
        store.record_audit("peer-1", passed=False)
        store.record_audit("peer-1", passed=True)  # Reset!
        trust = store.get_trust("peer-1")
        assert trust.consecutive_audit_failures == 0

    def test_summary_rating(self, store: TrustStore):
        store.record_summary_rating("peer-1", 0.8)
        store.record_summary_rating("peer-1", 0.6)
        trust = store.get_trust("peer-1")
        assert trust.summary_quality == pytest.approx(0.7)

    def test_unisolate(self, store: TrustStore):
        for _ in range(AUDIT_FAILURE_ISOLATION_THRESHOLD):
            store.record_audit("peer-1", passed=False)
        assert store.get_trust("peer-1").isolated is True
        store.unisolate("peer-1")
        assert store.get_trust("peer-1").isolated is False

    def test_list_peers(self, store: TrustStore):
        store.update_uptime("p1", 10)
        store.update_uptime("p2", 20)
        peers = store.list_peers()
        assert len(peers) == 2

    def test_list_excludes_isolated(self, store: TrustStore):
        store.update_uptime("good", 10)
        store.update_uptime("bad", 10)
        for _ in range(AUDIT_FAILURE_ISOLATION_THRESHOLD):
            store.record_audit("bad", passed=False)
        peers = store.list_peers(include_isolated=False)
        assert len(peers) == 1
        assert peers[0].peer_id == "good"

    def test_list_isolated(self, store: TrustStore):
        for _ in range(AUDIT_FAILURE_ISOLATION_THRESHOLD):
            store.record_audit("bad", passed=False)
        isolated = store.list_isolated()
        assert len(isolated) == 1
        assert isolated[0].peer_id == "bad"
