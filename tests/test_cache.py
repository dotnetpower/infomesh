"""Tests for LRU query result cache."""

from __future__ import annotations

import time

import pytest

from infomesh.search.cache import CacheStats, QueryCache

# ── CacheStats ──────────────────────────────────────────────────────────


class TestCacheStats:
    def test_initial_hit_rate(self) -> None:
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_100(self) -> None:
        stats = CacheStats(hits=10, misses=0)
        assert stats.hit_rate == 1.0

    def test_hit_rate_50(self) -> None:
        stats = CacheStats(hits=5, misses=5)
        assert stats.hit_rate == 0.5

    def test_total(self) -> None:
        stats = CacheStats(hits=3, misses=7)
        assert stats.total == 10


# ── QueryCache core ops ────────────────────────────────────────────────


class TestQueryCacheBasic:
    def test_miss_returns_none(self) -> None:
        cache = QueryCache()
        assert cache.get("missing", 10) is None

    def test_put_then_get(self) -> None:
        cache = QueryCache()
        results = [{"url": "https://example.com"}]
        cache.put("python async", 10, results)
        cached = cache.get("python async", 10)
        assert cached == results

    def test_different_limit_is_different_key(self) -> None:
        cache = QueryCache()
        cache.put("test", 5, ["a"])
        cache.put("test", 10, ["a", "b"])
        assert cache.get("test", 5) == ["a"]
        assert cache.get("test", 10) == ["a", "b"]

    def test_case_insensitive(self) -> None:
        cache = QueryCache()
        cache.put("Python", 5, ["result"])
        assert cache.get("python", 5) == ["result"]

    def test_whitespace_normalized(self) -> None:
        cache = QueryCache()
        cache.put("  python  ", 5, ["result"])
        assert cache.get("python", 5) == ["result"]

    def test_size(self) -> None:
        cache = QueryCache()
        assert cache.size == 0
        cache.put("a", 5, [])
        assert cache.size == 1
        cache.put("b", 5, [])
        assert cache.size == 2

    def test_update_existing(self) -> None:
        cache = QueryCache()
        cache.put("test", 5, ["old"])
        cache.put("test", 5, ["new"])
        assert cache.get("test", 5) == ["new"]
        assert cache.size == 1


# ── Cache invalidation ─────────────────────────────────────────────────


class TestQueryCacheInvalidation:
    def test_invalidate_existing(self) -> None:
        cache = QueryCache()
        cache.put("test", 5, ["data"])
        assert cache.invalidate("test", 5) is True
        assert cache.get("test", 5) is None

    def test_invalidate_nonexistent(self) -> None:
        cache = QueryCache()
        assert cache.invalidate("nope", 5) is False

    def test_clear(self) -> None:
        cache = QueryCache()
        for i in range(5):
            cache.put(f"q{i}", 5, [i])
        cache.clear()
        assert cache.size == 0


# ── TTL expiration ──────────────────────────────────────────────────────


class TestQueryCacheTTL:
    def test_expired_entry_returns_none(self) -> None:
        cache = QueryCache(ttl_seconds=0.1)
        cache.put("test", 5, ["data"])
        time.sleep(0.15)
        assert cache.get("test", 5) is None

    def test_not_yet_expired(self) -> None:
        cache = QueryCache(ttl_seconds=10.0)
        cache.put("test", 5, ["data"])
        assert cache.get("test", 5) == ["data"]

    def test_evict_expired(self) -> None:
        cache = QueryCache(ttl_seconds=0.05)
        cache.put("old1", 5, ["a"])
        cache.put("old2", 5, ["b"])
        time.sleep(0.1)
        cache.put("new", 5, ["c"])  # not expired
        removed = cache.evict_expired()
        assert removed == 2
        assert cache.size == 1
        assert cache.get("new", 5) == ["c"]


# ── LRU eviction ───────────────────────────────────────────────────────


class TestQueryCacheLRU:
    def test_evict_lru_when_full(self) -> None:
        cache = QueryCache(max_size=3)
        cache.put("first", 5, [1])
        cache.put("second", 5, [2])
        cache.put("third", 5, [3])
        # At capacity — adding one more should evict "first"
        cache.put("fourth", 5, [4])
        assert cache.get("first", 5) is None
        assert cache.get("second", 5) == [2]
        assert cache.size == 3

    def test_access_refreshes_position(self) -> None:
        cache = QueryCache(max_size=3)
        cache.put("a", 5, [1])
        cache.put("b", 5, [2])
        cache.put("c", 5, [3])
        # Access "a" to make it most recently used
        cache.get("a", 5)
        # Add "d" — should evict "b" (now LRU)
        cache.put("d", 5, [4])
        assert cache.get("a", 5) == [1]  # refreshed, survived
        assert cache.get("b", 5) is None  # evicted


# ── Stats tracking ─────────────────────────────────────────────────────


class TestQueryCacheStats:
    def test_hit_miss_tracking(self) -> None:
        cache = QueryCache()
        cache.put("test", 5, ["data"])
        cache.get("test", 5)  # hit
        cache.get("test", 5)  # hit
        cache.get("missing", 5)  # miss
        assert cache.stats.hits == 2
        assert cache.stats.misses == 1
        assert cache.stats.hit_rate == pytest.approx(2 / 3)

    def test_eviction_counted(self) -> None:
        cache = QueryCache(max_size=1)
        cache.put("a", 5, [1])
        cache.put("b", 5, [2])  # evicts "a"
        assert cache.stats.evictions == 1

    def test_ttl_expiry_counted_as_miss_and_eviction(self) -> None:
        cache = QueryCache(ttl_seconds=0.01)
        cache.put("test", 5, ["data"])
        time.sleep(0.02)
        cache.get("test", 5)  # expired → miss + eviction
        assert cache.stats.misses == 1
        assert cache.stats.evictions == 1


# ── Key determinism ─────────────────────────────────────────────────────


class TestCacheKeyDeterminism:
    def test_same_query_same_key(self) -> None:
        k1 = QueryCache._make_key("hello world", 10)
        k2 = QueryCache._make_key("hello world", 10)
        assert k1 == k2

    def test_different_query_different_key(self) -> None:
        k1 = QueryCache._make_key("hello", 10)
        k2 = QueryCache._make_key("world", 10)
        assert k1 != k2

    def test_different_limit_different_key(self) -> None:
        k1 = QueryCache._make_key("hello", 5)
        k2 = QueryCache._make_key("hello", 10)
        assert k1 != k2
