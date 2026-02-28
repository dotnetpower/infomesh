"""Peer network performance profiling.

Tracks per-peer latency, success rate, and bandwidth class for
latency-aware query routing and hedged request decisions.

Usage::

    tracker = PeerProfileTracker()
    tracker.record(peer_id, elapsed_ms=45, success=True)
    profile = tracker.get(peer_id)
    fast_peers = tracker.rank_by_latency(peer_ids)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

EMA_ALPHA = 0.3  # Exponential moving average smoothing factor
MAX_HISTORY = 100  # Rolling window for percentile calculation
STALE_TIMEOUT = 3600  # Seconds before a profile is considered stale
DIVERSITY_RATIO = 0.2  # 20% chance to include a slow peer for diversity


class BandwidthClass(StrEnum):
    """Peer bandwidth classification."""

    FAST = "fast"  # avg_latency < 100ms
    MEDIUM = "medium"  # 100ms <= avg_latency < 500ms
    SLOW = "slow"  # avg_latency >= 500ms
    UNKNOWN = "unknown"  # insufficient data


@dataclass
class PeerProfile:
    """Network performance profile for a single peer."""

    peer_id: str
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    success_rate: float = 1.0
    last_seen: float = 0.0
    bandwidth_class: BandwidthClass = BandwidthClass.UNKNOWN
    total_interactions: int = 0
    _latency_history: list[float] = field(default_factory=list, repr=False)
    _success_history: list[bool] = field(default_factory=list, repr=False)


def _classify_bandwidth(avg_latency_ms: float) -> BandwidthClass:
    """Classify peer bandwidth based on average latency."""
    if avg_latency_ms < 100:
        return BandwidthClass.FAST
    if avg_latency_ms < 500:
        return BandwidthClass.MEDIUM
    return BandwidthClass.SLOW


def _percentile(values: list[float], pct: float) -> float:
    """Compute the *pct*-th percentile of a sorted list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (pct / 100) * (len(sorted_vals) - 1)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return sorted_vals[lower]
    frac = idx - lower
    return sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac


class PeerProfileTracker:
    """Tracks network performance for all known peers.

    Thread-safe access is **not** provided — callers must synchronise
    if used from multiple threads (unlikely given trio usage).
    """

    def __init__(self) -> None:
        self._profiles: dict[str, PeerProfile] = {}

    # ── Recording ───────────────────────────────────────────────────

    def record(
        self,
        peer_id: str,
        elapsed_ms: float,
        *,
        success: bool = True,
    ) -> PeerProfile:
        """Record a peer interaction and update the profile.

        Args:
            peer_id: Peer identifier.
            elapsed_ms: Round-trip time in milliseconds.
            success: Whether the interaction succeeded.

        Returns:
            Updated :class:`PeerProfile`.
        """
        profile = self._profiles.get(peer_id)
        if profile is None:
            profile = PeerProfile(peer_id=peer_id)
            self._profiles[peer_id] = profile

        profile.total_interactions += 1
        profile.last_seen = time.time()

        # Update latency (EMA)
        if success:
            if profile.avg_latency_ms == 0.0:
                profile.avg_latency_ms = elapsed_ms
            else:
                profile.avg_latency_ms = (
                    EMA_ALPHA * elapsed_ms + (1 - EMA_ALPHA) * profile.avg_latency_ms
                )

            # Rolling window for p95
            profile._latency_history.append(elapsed_ms)
            if len(profile._latency_history) > MAX_HISTORY:
                profile._latency_history.pop(0)
            profile.p95_latency_ms = _percentile(profile._latency_history, 95)

        # Success rate (rolling window)
        profile._success_history.append(success)
        if len(profile._success_history) > MAX_HISTORY:
            profile._success_history.pop(0)
        profile.success_rate = sum(profile._success_history) / len(
            profile._success_history
        )

        # Reclassify
        if profile.total_interactions >= 3:
            profile.bandwidth_class = _classify_bandwidth(profile.avg_latency_ms)

        return profile

    # ── Lookup ──────────────────────────────────────────────────────

    def get(self, peer_id: str) -> PeerProfile | None:
        """Return the profile for a peer, or ``None``."""
        return self._profiles.get(peer_id)

    def get_or_default(self, peer_id: str) -> PeerProfile:
        """Return existing profile or a default (UNKNOWN) profile."""
        return self._profiles.get(peer_id) or PeerProfile(peer_id=peer_id)

    @property
    def known_peers(self) -> int:
        return len(self._profiles)

    # ── Ranking / selection ─────────────────────────────────────────

    def rank_by_latency(
        self,
        peer_ids: list[str],
        *,
        diversity: bool = True,
    ) -> list[str]:
        """Rank peers by average latency (fastest first).

        When *diversity* is ``True``, slower peers are included with
        a small probability to avoid starving them (20%).

        Args:
            peer_ids: Candidate peer IDs.
            diversity: Whether to randomly include slow peers.

        Returns:
            Sorted list of peer IDs.
        """
        import random

        profiles = [(pid, self.get_or_default(pid)) for pid in peer_ids]

        # Sort by avg_latency (0.0 = unknown → sort last)
        def _sort_key(item: tuple[str, PeerProfile]) -> float:
            p = item[1]
            if p.bandwidth_class == BandwidthClass.UNKNOWN:
                return 9999.0  # unknown peers last
            return p.avg_latency_ms

        profiles.sort(key=_sort_key)

        if not diversity or len(profiles) <= 2:
            return [pid for pid, _ in profiles]

        # Split into fast and slow
        mid = max(1, len(profiles) // 2)
        fast = [pid for pid, _ in profiles[:mid]]
        slow = [pid for pid, _ in profiles[mid:]]

        # Include some slow peers
        promoted: list[str] = []
        for pid in slow:
            if random.random() < DIVERSITY_RATIO:
                promoted.append(pid)

        result = fast + promoted + [p for p in slow if p not in promoted]
        return result

    def adaptive_timeout(
        self,
        peer_id: str,
        *,
        base_ms: float = 2000.0,
    ) -> float:
        """Compute per-peer adaptive timeout.

        Slower peers get more time; faster peers get tighter deadlines.

        Args:
            peer_id: Peer to compute timeout for.
            base_ms: Baseline timeout in milliseconds.

        Returns:
            Timeout in milliseconds, clamped to [500, 5000].
        """
        profile = self.get(peer_id)
        if profile is None or profile.avg_latency_ms == 0.0:
            return base_ms

        factor = profile.avg_latency_ms / 200.0  # 200ms as reference
        timeout = base_ms * factor
        return max(500.0, min(timeout, 5000.0))

    # ── Pruning ─────────────────────────────────────────────────────

    def prune_stale(self, *, max_age: float = STALE_TIMEOUT) -> int:
        """Remove profiles not seen within *max_age* seconds.

        Returns:
            Number of profiles removed.
        """
        now = time.time()
        stale = [
            pid
            for pid, p in self._profiles.items()
            if now - p.last_seen > max_age and p.last_seen > 0
        ]
        for pid in stale:
            del self._profiles[pid]
        if stale:
            logger.info("peer_profiles_pruned", count=len(stale))
        return len(stale)

    def reset(self) -> None:
        """Clear all profiles."""
        self._profiles.clear()
