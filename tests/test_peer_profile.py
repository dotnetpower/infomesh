"""Tests for infomesh.p2p.peer_profile — latency-aware peer profiling."""

from __future__ import annotations

import time

import pytest

from infomesh.p2p.peer_profile import (
    EMA_ALPHA,
    MAX_HISTORY,
    STALE_TIMEOUT,
    BandwidthClass,
    PeerProfile,
    PeerProfileTracker,
    _classify_bandwidth,
    _percentile,
)


@pytest.fixture
def tracker():
    return PeerProfileTracker()


# --- BandwidthClass --------------------------------------------------------


class TestBandwidthClass:
    def test_fast(self):
        assert _classify_bandwidth(50.0) == BandwidthClass.FAST

    def test_medium(self):
        assert _classify_bandwidth(200.0) == BandwidthClass.MEDIUM

    def test_slow(self):
        assert _classify_bandwidth(800.0) == BandwidthClass.SLOW

    def test_boundary_100(self):
        assert _classify_bandwidth(100.0) == BandwidthClass.MEDIUM

    def test_boundary_500(self):
        assert _classify_bandwidth(500.0) == BandwidthClass.SLOW

    def test_zero_is_fast(self):
        assert _classify_bandwidth(0.0) == BandwidthClass.FAST


# --- _percentile -----------------------------------------------------------


class TestPercentile:
    def test_empty_list(self):
        assert _percentile([], 95) == 0.0

    def test_single_element(self):
        assert _percentile([42.0], 95) == 42.0

    def test_median(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_p95_sorted(self):
        vals = list(range(1, 101))  # 1..100
        p95 = _percentile(vals, 95)
        assert 95.0 <= p95 <= 96.0

    def test_p0(self):
        assert _percentile([10, 20, 30], 0) == 10.0

    def test_p100(self):
        assert _percentile([10, 20, 30], 100) == 30.0


# --- PeerProfile -----------------------------------------------------------


class TestPeerProfile:
    def test_default_values(self):
        p = PeerProfile(peer_id="test-1")
        assert p.avg_latency_ms == 0.0
        assert p.success_rate == 1.0
        assert p.bandwidth_class == BandwidthClass.UNKNOWN
        assert p.total_interactions == 0

    def test_frozen_fields(self):
        p = PeerProfile(peer_id="test-2")
        assert p.peer_id == "test-2"


# --- PeerProfileTracker.record ---------------------------------------------


class TestRecord:
    def test_first_interaction(self, tracker):
        profile = tracker.record("peer-1", 100.0, success=True)
        assert profile.avg_latency_ms == 100.0
        assert profile.total_interactions == 1

    def test_ema_update(self, tracker):
        tracker.record("peer-1", 100.0)
        profile = tracker.record("peer-1", 200.0)
        expected = EMA_ALPHA * 200.0 + (1 - EMA_ALPHA) * 100.0
        assert abs(profile.avg_latency_ms - expected) < 0.01

    def test_multiple_ema_converges(self, tracker):
        # Feed constant 50ms — should converge to 50
        for _ in range(50):
            profile = tracker.record("peer-1", 50.0)
        assert abs(profile.avg_latency_ms - 50.0) < 1.0

    def test_failed_interaction_latency_unchanged(self, tracker):
        tracker.record("peer-1", 100.0, success=True)
        profile = tracker.record("peer-1", 5000.0, success=False)
        # Latency should NOT update on failure
        assert profile.avg_latency_ms == 100.0

    def test_success_rate_tracking(self, tracker):
        tracker.record("peer-1", 50.0, success=True)
        tracker.record("peer-1", 50.0, success=True)
        profile = tracker.record("peer-1", 5000.0, success=False)
        assert abs(profile.success_rate - 2 / 3) < 0.01

    def test_p95_computed(self, tracker):
        for i in range(20):
            tracker.record("peer-1", float(i * 10))
        profile = tracker.get("peer-1")
        assert profile is not None
        assert profile.p95_latency_ms > 0

    def test_bandwidth_class_after_3_interactions(self, tracker):
        for _ in range(3):
            profile = tracker.record("peer-1", 30.0)
        assert profile.bandwidth_class == BandwidthClass.FAST

    def test_bandwidth_unknown_before_3(self, tracker):
        tracker.record("peer-1", 30.0)
        tracker.record("peer-1", 30.0)
        profile = tracker.get("peer-1")
        assert profile.bandwidth_class == BandwidthClass.UNKNOWN

    def test_history_rolling_window(self, tracker):
        for i in range(MAX_HISTORY + 20):
            tracker.record("peer-1", float(i))
        profile = tracker.get("peer-1")
        assert len(profile._latency_history) == MAX_HISTORY
        assert len(profile._success_history) == MAX_HISTORY

    def test_last_seen_updated(self, tracker):
        before = time.time()
        tracker.record("peer-1", 50.0)
        profile = tracker.get("peer-1")
        assert profile.last_seen >= before


# --- PeerProfileTracker.get ------------------------------------------------


class TestGet:
    def test_unknown_peer_returns_none(self, tracker):
        assert tracker.get("no-such-peer") is None

    def test_get_or_default_unknown(self, tracker):
        p = tracker.get_or_default("no-such-peer")
        assert p.peer_id == "no-such-peer"
        assert p.bandwidth_class == BandwidthClass.UNKNOWN

    def test_known_peers_count(self, tracker):
        tracker.record("peer-1", 50.0)
        tracker.record("peer-2", 100.0)
        assert tracker.known_peers == 2


# --- PeerProfileTracker.rank_by_latency ------------------------------------


class TestRankByLatency:
    def test_empty_list(self, tracker):
        assert tracker.rank_by_latency([]) == []

    def test_single_peer(self, tracker):
        tracker.record("peer-1", 50.0)
        assert tracker.rank_by_latency(["peer-1"]) == ["peer-1"]

    def test_fast_first(self, tracker):
        for _ in range(5):
            tracker.record("fast", 30.0)
            tracker.record("slow", 300.0)
        result = tracker.rank_by_latency(["slow", "fast"], diversity=False)
        assert result[0] == "fast"

    def test_unknown_last(self, tracker):
        for _ in range(5):
            tracker.record("known", 50.0)
        result = tracker.rank_by_latency(["unknown", "known"], diversity=False)
        assert result[-1] == "unknown"

    def test_diversity_includes_all(self, tracker):
        """With diversity=False, result is deterministic."""
        for _ in range(5):
            tracker.record("p1", 10.0)
            tracker.record("p2", 100.0)
            tracker.record("p3", 500.0)
        result = tracker.rank_by_latency(["p3", "p2", "p1"], diversity=False)
        assert result == ["p1", "p2", "p3"]

    def test_diversity_mode_returns_all_peers(self, tracker):
        """Even with diversity, all peers should appear in results."""
        for _ in range(5):
            tracker.record("p1", 10.0)
            tracker.record("p2", 100.0)
            tracker.record("p3", 500.0)
        result = tracker.rank_by_latency(["p3", "p2", "p1"], diversity=True)
        assert set(result) == {"p1", "p2", "p3"}

    def test_two_peers_no_diversity_split(self, tracker):
        """With 2 or fewer peers, diversity splitting is skipped."""
        for _ in range(5):
            tracker.record("a", 10.0)
            tracker.record("b", 200.0)
        result = tracker.rank_by_latency(["b", "a"], diversity=True)
        assert result[0] == "a"


# --- PeerProfileTracker.adaptive_timeout -----------------------------------


class TestAdaptiveTimeout:
    def test_unknown_peer_returns_base(self, tracker):
        assert tracker.adaptive_timeout("unknown") == 2000.0

    def test_fast_peer_lower_timeout(self, tracker):
        for _ in range(5):
            tracker.record("fast", 50.0)
        timeout = tracker.adaptive_timeout("fast")
        assert timeout < 2000.0

    def test_slow_peer_higher_timeout(self, tracker):
        for _ in range(5):
            tracker.record("slow", 800.0)
        timeout = tracker.adaptive_timeout("slow")
        assert timeout > 2000.0

    def test_min_clamp_500(self, tracker):
        for _ in range(5):
            tracker.record("very-fast", 5.0)
        timeout = tracker.adaptive_timeout("very-fast")
        assert timeout == 500.0

    def test_max_clamp_5000(self, tracker):
        for _ in range(5):
            tracker.record("very-slow", 2000.0)
        timeout = tracker.adaptive_timeout("very-slow")
        assert timeout == 5000.0

    def test_custom_base(self, tracker):
        timeout = tracker.adaptive_timeout("unknown", base_ms=3000.0)
        assert timeout == 3000.0


# --- PeerProfileTracker.prune_stale ----------------------------------------


class TestPruneStale:
    def test_no_stale(self, tracker):
        tracker.record("peer-1", 50.0)
        pruned = tracker.prune_stale()
        assert pruned == 0
        assert tracker.known_peers == 1

    def test_prune_old_profiles(self, tracker):
        tracker.record("peer-1", 50.0)
        # Manually age the profile
        tracker._profiles["peer-1"].last_seen = time.time() - STALE_TIMEOUT - 1
        pruned = tracker.prune_stale()
        assert pruned == 1
        assert tracker.known_peers == 0

    def test_keep_recent(self, tracker):
        tracker.record("peer-1", 50.0)
        tracker.record("peer-2", 100.0)
        tracker._profiles["peer-1"].last_seen = time.time() - STALE_TIMEOUT - 1
        pruned = tracker.prune_stale()
        assert pruned == 1
        assert tracker.known_peers == 1
        assert tracker.get("peer-2") is not None

    def test_custom_max_age(self, tracker):
        tracker.record("peer-1", 50.0)
        tracker._profiles["peer-1"].last_seen = time.time() - 61
        pruned = tracker.prune_stale(max_age=60)
        assert pruned == 1


# --- PeerProfileTracker.reset ----------------------------------------------


class TestReset:
    def test_reset_clears_all(self, tracker):
        tracker.record("peer-1", 50.0)
        tracker.record("peer-2", 100.0)
        tracker.reset()
        assert tracker.known_peers == 0
