"""Tests for NodeLoadGuard — per-node rate limiting and backpressure."""

from __future__ import annotations

import time

from infomesh.p2p.load_guard import (
    OVERLOAD_RETRY_MS,
    NodeLoadGuard,
)

# ── Basic acquire / release ─────────────────────────────────────────────


class TestLoadGuardBasic:
    def test_acquire_succeeds(self) -> None:
        guard = NodeLoadGuard()
        assert guard.try_acquire("peer_a") is True

    def test_release_decrements(self) -> None:
        guard = NodeLoadGuard()
        guard.try_acquire("peer_a")
        guard.release("peer_a")
        assert guard.stats.concurrent == 0

    def test_release_without_acquire_safe(self) -> None:
        guard = NodeLoadGuard()
        guard.release("peer_a")  # no-op
        assert guard.stats.concurrent == 0


# ── Concurrency limiting ────────────────────────────────────────────────


class TestLoadGuardConcurrency:
    def test_concurrent_limit_enforced(self) -> None:
        guard = NodeLoadGuard(max_concurrent=2)
        assert guard.try_acquire("a") is True
        assert guard.try_acquire("b") is True
        assert guard.try_acquire("c") is False  # limited

    def test_release_allows_new(self) -> None:
        guard = NodeLoadGuard(max_concurrent=1)
        guard.try_acquire("a")
        guard.release("a")
        assert guard.try_acquire("b") is True

    def test_stats_reflect_concurrent(self) -> None:
        guard = NodeLoadGuard(max_concurrent=5)
        guard.try_acquire("a")
        guard.try_acquire("b")
        assert guard.stats.concurrent == 2


# ── Rate limiting ───────────────────────────────────────────────────────


class TestLoadGuardRateLimit:
    def test_rate_limit_enforced(self) -> None:
        guard = NodeLoadGuard(max_queries_per_minute=3, max_concurrent=100)
        for i in range(3):
            assert guard.try_acquire(f"peer_{i}") is True
            guard.release(f"peer_{i}")
        # 4th should be rate limited (3 timestamps in the last minute)
        assert guard.try_acquire("peer_4") is False

    def test_rate_limit_clears_after_window(self) -> None:
        guard = NodeLoadGuard(max_queries_per_minute=2, max_concurrent=100)
        # Inject old timestamps that are beyond the 60s window
        old_time = time.monotonic() - 61
        guard._timestamps.append(old_time)
        guard._timestamps.append(old_time)
        # Should be pruned and allow new queries
        assert guard.try_acquire("peer_a") is True


# ── Overload detection ──────────────────────────────────────────────────


class TestLoadGuardOverload:
    def test_not_overloaded_initially(self) -> None:
        guard = NodeLoadGuard()
        assert guard.is_overloaded is False

    def test_overloaded_by_concurrency(self) -> None:
        guard = NodeLoadGuard(max_concurrent=2)
        guard.try_acquire("a")
        guard.try_acquire("b")
        assert guard.is_overloaded is True

    def test_overloaded_by_rate(self) -> None:
        guard = NodeLoadGuard(max_queries_per_minute=2, max_concurrent=100)
        guard.try_acquire("a")
        guard.release("a")
        guard.try_acquire("b")
        guard.release("b")
        assert guard.is_overloaded is True


# ── Stats tracking ──────────────────────────────────────────────────────


class TestLoadGuardStats:
    def test_accepted_tracked(self) -> None:
        guard = NodeLoadGuard()
        guard.try_acquire("a")
        guard.try_acquire("b")
        assert guard.stats.accepted == 2

    def test_rejected_tracked(self) -> None:
        guard = NodeLoadGuard(max_concurrent=1)
        guard.try_acquire("a")
        guard.try_acquire("b")  # rejected
        assert guard.stats.rejected == 1

    def test_qpm_tracked(self) -> None:
        guard = NodeLoadGuard()
        guard.try_acquire("a")
        guard.release("a")
        assert guard.stats.queries_this_minute == 1


# ── Reject info ─────────────────────────────────────────────────────────


class TestLoadGuardRejectInfo:
    def test_reject_info_structure(self) -> None:
        guard = NodeLoadGuard()
        info = guard.get_reject_info()
        assert info["status"] == "OVERLOADED"
        assert info["retry_after_ms"] == OVERLOAD_RETRY_MS
        assert "concurrent" in info
        assert "qpm" in info


# ── Peer tracking ──────────────────────────────────────────────────────


class TestLoadGuardPeerTracking:
    def test_peer_query_count(self) -> None:
        guard = NodeLoadGuard()
        guard.try_acquire("peer_a")
        guard.try_acquire("peer_a")
        guard.try_acquire("peer_b")
        assert guard.peer_query_count("peer_a") == 2
        assert guard.peer_query_count("peer_b") == 1

    def test_unknown_peer_returns_zero(self) -> None:
        guard = NodeLoadGuard()
        assert guard.peer_query_count("unknown") == 0


# ── Reset ───────────────────────────────────────────────────────────────


class TestLoadGuardReset:
    def test_reset_clears_all(self) -> None:
        guard = NodeLoadGuard()
        guard.try_acquire("a")
        guard.try_acquire("b")
        guard.reset()
        assert guard.stats.concurrent == 0
        assert guard.stats.accepted == 0
        assert guard.peer_query_count("a") == 0
        assert guard.is_overloaded is False
