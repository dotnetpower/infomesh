"""LRU query result cache for reducing P2P load.

Caches search results locally so repeated identical queries
do not fan-out to remote peers.  Thread-safe via a simple lock.

Usage::

    cache = QueryCache(max_size=1000, ttl_seconds=300)
    cached = cache.get("python async", limit=10)
    if cached is None:
        results = ...  # actual search
        cache.put("python async", 10, results)
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog

from infomesh.hashing import short_hash as _short_hash

logger = structlog.get_logger()


@dataclass
class CacheEntry:
    """A single cached query result."""

    results: list[object]  # list of RankedResult or similar
    timestamp: float
    hit_count: int = 0


class QueryCache:
    """LRU cache for search query results.

    Attributes:
        max_size: Maximum number of cached queries.
        ttl_seconds: Time-to-live for cache entries.
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_seconds: float = 300.0,
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = CacheStats()

    @property
    def stats(self) -> CacheStats:
        return self._stats

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @staticmethod
    def _make_key(query: str, limit: int) -> str:
        """Create a deterministic cache key from query + limit."""
        raw = f"{query.strip().lower()}:{limit}"
        return _short_hash(raw)

    def get(self, query: str, limit: int) -> list[object] | None:
        """Retrieve cached results for a query.

        Args:
            query: Search query string.
            limit: Result limit (part of the cache key).

        Returns:
            Cached results list, or ``None`` on miss / expiry.
        """
        key = self._make_key(query, limit)
        now = time.time()

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.misses += 1
                return None

            # Expired?
            if now - entry.timestamp > self._ttl:
                del self._cache[key]
                self._stats.misses += 1
                self._stats.evictions += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._stats.hits += 1
            return entry.results

    def put(self, query: str, limit: int, results: list[object]) -> None:
        """Store query results in the cache.

        Args:
            query: Search query string.
            limit: Result limit.
            results: Search results to cache.
        """
        key = self._make_key(query, limit)

        with self._lock:
            if key in self._cache:
                # Update existing
                self._cache[key] = CacheEntry(results=results, timestamp=time.time())
                self._cache.move_to_end(key)
            else:
                # Evict LRU if at capacity
                if len(self._cache) >= self._max_size:
                    self._cache.popitem(last=False)
                    self._stats.evictions += 1
                self._cache[key] = CacheEntry(results=results, timestamp=time.time())

    def invalidate(self, query: str, limit: int) -> bool:
        """Remove a specific entry from the cache.

        Returns:
            ``True`` if an entry was removed.
        """
        key = self._make_key(query, limit)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._cache.clear()
            logger.debug("query_cache_cleared")

    def evict_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries removed.
        """
        now = time.time()
        removed = 0
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items() if now - v.timestamp > self._ttl
            ]
            for k in expired_keys:
                del self._cache[k]
                removed += 1
        if removed:
            self._stats.evictions += removed
            logger.debug("cache_expired_evicted", count=removed)
        return removed


@dataclass
class CacheStats:
    """Cache hit/miss statistics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.hits / self.total
