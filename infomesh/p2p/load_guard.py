"""Node load guard — per-node rate limiting and backpressure.

Prevents overload on small networks by:
- Tracking incoming query rate per minute.
- Limiting concurrent query processing.
- Broadcasting OVERLOADED status via backpressure signals.
- Gradual degradation levels matching :class:`ResourceGovernor`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# ── Defaults ────────────────────────────────────────────────────────────

MAX_QUERIES_PER_MINUTE = 30
MAX_CONCURRENT_QUERIES = 5
OVERLOAD_RETRY_MS = 5000  # suggested retry-after for rejected queries


@dataclass
class LoadGuardStats:
    """Observable load guard statistics."""

    accepted: int = 0
    rejected: int = 0
    concurrent: int = 0
    queries_this_minute: int = 0
    is_overloaded: bool = False


class NodeLoadGuard:
    """Per-node request rate limiter and overload detector.

    Usage::

        guard = NodeLoadGuard()
        if guard.try_acquire(peer_id):
            try:
                # process query
                ...
            finally:
                guard.release(peer_id)
        else:
            # return OVERLOADED response
            ...
    """

    def __init__(
        self,
        max_queries_per_minute: int = MAX_QUERIES_PER_MINUTE,
        max_concurrent: int = MAX_CONCURRENT_QUERIES,
    ) -> None:
        self._max_qpm = max_queries_per_minute
        self._max_concurrent = max_concurrent
        self._concurrent = 0
        self._timestamps: deque[float] = deque()
        self._stats = LoadGuardStats()
        self._lock = threading.RLock()
        # Per-peer tracking for fairness (bounded to prevent memory leak)
        self._peer_counts: dict[str, int] = {}
        _MAX_TRACKED_PEERS = 10_000
        self._max_tracked_peers = _MAX_TRACKED_PEERS

    @property
    def stats(self) -> LoadGuardStats:
        with self._lock:
            self._update_stats()
            return self._stats

    @property
    def is_overloaded(self) -> bool:
        """Return ``True`` if the node is currently overloaded."""
        with self._lock:
            self._prune_old_timestamps()
            return (
                self._concurrent >= self._max_concurrent
                or len(self._timestamps) >= self._max_qpm
            )

    def try_acquire(self, peer_id: str = "") -> bool:
        """Try to accept a new query from *peer_id*.

        Args:
            peer_id: Identifier of the requesting peer.

        Returns:
            ``True`` if accepted; ``False`` if overloaded.
        """
        with self._lock:
            self._prune_old_timestamps()

            # Check rate limit
            if len(self._timestamps) >= self._max_qpm:
                self._stats.rejected += 1
                logger.info("loadguard_rate_limited", peer=peer_id[:12])
                return False

            # Check concurrency limit
            if self._concurrent >= self._max_concurrent:
                self._stats.rejected += 1
                logger.info(
                    "loadguard_concurrent_limited",
                    peer=peer_id[:12],
                )
                return False

            # Accept
            now = time.monotonic()
            self._timestamps.append(now)
            self._concurrent += 1
            self._stats.accepted += 1
            if len(self._peer_counts) < self._max_tracked_peers:
                self._peer_counts[peer_id] = self._peer_counts.get(peer_id, 0) + 1
            return True

    def release(self, peer_id: str = "") -> None:
        """Release a previously acquired query slot.

        Must be called after :meth:`try_acquire` returns ``True``.
        """
        with self._lock:
            if self._concurrent > 0:
                self._concurrent -= 1

    def _prune_old_timestamps(self) -> None:
        """Remove timestamps older than 60 seconds."""
        cutoff = time.monotonic() - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def _update_stats(self) -> None:
        """Refresh observable stats."""
        self._prune_old_timestamps()
        self._stats.concurrent = self._concurrent
        # Prune peer entries with zero counts to prevent unbounded growth
        stale = [pid for pid, cnt in self._peer_counts.items() if cnt <= 0]
        for pid in stale:
            del self._peer_counts[pid]
        self._stats.queries_this_minute = len(self._timestamps)
        self._stats.is_overloaded = self.is_overloaded

    def get_reject_info(self) -> dict[str, object]:
        """Build a rejection response payload for overloaded state.

        Returns:
            Dict suitable for serialization as an OVERLOADED response.
        """
        with self._lock:
            return {
                "status": "OVERLOADED",
                "retry_after_ms": OVERLOAD_RETRY_MS,
                "concurrent": self._concurrent,
                "qpm": len(self._timestamps),
            }

    def peer_query_count(self, peer_id: str) -> int:
        """Return total queries from a specific peer."""
        with self._lock:
            return self._peer_counts.get(peer_id, 0)

    def reset(self) -> None:
        """Reset all counters (for testing)."""
        with self._lock:
            self._concurrent = 0
            self._timestamps.clear()
            self._peer_counts.clear()
            self._stats = LoadGuardStats()
