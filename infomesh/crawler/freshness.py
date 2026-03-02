"""Freshness tier system for priority-based recrawling.

Classifies documents into freshness tiers (hot, warm, cold) and
maintains a priority recrawl queue that gives precedence to
RSS-discovered and user-requested URLs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from heapq import heappop, heappush

import structlog

logger = structlog.get_logger()


# ── Freshness tiers ─────────────────────────────────────────────────────


class FreshnessTier(StrEnum):
    """Document freshness classification."""

    HOT = "hot"  # < 1 hour since crawl
    WARM = "warm"  # < 24 hours
    COLD = "cold"  # > 7 days
    STALE = "stale"  # never recrawled or very old


# Thresholds in seconds
TIER_HOT_MAX = 3600  # 1 hour
TIER_WARM_MAX = 86400  # 24 hours
TIER_COLD_MAX = 604800  # 7 days


def classify_freshness(
    crawled_at: float,
    *,
    now: float | None = None,
) -> FreshnessTier:
    """Classify a document's freshness based on when it was last crawled.

    Args:
        crawled_at: Unix timestamp of last crawl/recrawl.
        now: Override current time for testing.

    Returns:
        :class:`FreshnessTier` classification.
    """
    now = now or time.time()
    age = now - crawled_at

    if age <= TIER_HOT_MAX:
        return FreshnessTier.HOT
    if age <= TIER_WARM_MAX:
        return FreshnessTier.WARM
    if age <= TIER_COLD_MAX:
        return FreshnessTier.COLD
    return FreshnessTier.STALE


# ── Recrawl triggers ────────────────────────────────────────────────────


class RecrawlTrigger(StrEnum):
    """Why a URL was added to the priority queue."""

    RSS_UPDATE = "rss_update"  # New item from RSS feed
    USER_REQUEST = "user_request"  # force=True from crawl_url()
    CONTENT_CHANGE = "content_change"  # Detected via diff
    SCHEDULED = "scheduled"  # Regular adaptive recrawl
    PEER_ANNOUNCE = "peer_announce"  # P2P freshness announcement


# Priority weights (lower = higher priority in min-heap)
TRIGGER_PRIORITY: dict[RecrawlTrigger, int] = {
    RecrawlTrigger.USER_REQUEST: 0,
    RecrawlTrigger.RSS_UPDATE: 1,
    RecrawlTrigger.CONTENT_CHANGE: 2,
    RecrawlTrigger.PEER_ANNOUNCE: 3,
    RecrawlTrigger.SCHEDULED: 4,
}


# ── Priority recrawl queue ──────────────────────────────────────────────


@dataclass(frozen=True, order=True)
class PriorityRecrawlItem:
    """An item in the priority recrawl queue.

    Ordered by (priority, enqueued_at) for min-heap.
    """

    priority: int
    enqueued_at: float
    url: str = field(compare=False)
    trigger: RecrawlTrigger = field(compare=False)
    source_feed: str = field(default="", compare=False)


class PriorityRecrawlQueue:
    """Min-heap priority queue for recrawl URLs.

    Higher-priority triggers (USER_REQUEST, RSS_UPDATE) are processed
    before lower-priority ones (SCHEDULED).  Deduplicates URLs.
    """

    def __init__(self, *, max_size: int = 10000) -> None:
        self._heap: list[PriorityRecrawlItem] = []
        self._urls: set[str] = set()
        self._max_size = max_size
        self._total_enqueued = 0
        self._total_dequeued = 0

    def enqueue(
        self,
        url: str,
        trigger: RecrawlTrigger,
        *,
        source_feed: str = "",
        now: float | None = None,
    ) -> bool:
        """Add a URL to the priority queue.

        Args:
            url: URL to recrawl.
            trigger: Why this recrawl was requested.
            source_feed: Feed URL that discovered this (for RSS triggers).
            now: Override current time.

        Returns:
            ``True`` if enqueued, ``False`` if duplicate or queue full.
        """
        if url in self._urls:
            return False
        if len(self._heap) >= self._max_size:
            logger.warning(
                "recrawl_queue_full",
                max_size=self._max_size,
                url=url,
            )
            return False

        now = now or time.time()
        priority = TRIGGER_PRIORITY.get(trigger, 4)
        item = PriorityRecrawlItem(
            priority=priority,
            enqueued_at=now,
            url=url,
            trigger=trigger,
            source_feed=source_feed,
        )
        heappush(self._heap, item)
        self._urls.add(url)
        self._total_enqueued += 1
        return True

    def dequeue(self) -> PriorityRecrawlItem | None:
        """Remove and return the highest-priority item.

        Returns:
            :class:`PriorityRecrawlItem` or ``None`` if empty.
        """
        while self._heap:
            item = heappop(self._heap)
            # URL might have been removed via discard
            if item.url in self._urls:
                self._urls.discard(item.url)
                self._total_dequeued += 1
                return item
        return None

    def discard(self, url: str) -> None:
        """Remove a URL from the queue (lazy deletion)."""
        self._urls.discard(url)

    def peek(self) -> PriorityRecrawlItem | None:
        """Return the highest-priority item without removing it."""
        while self._heap:
            if self._heap[0].url in self._urls:
                return self._heap[0]
            heappop(self._heap)  # stale entry
        return None

    @property
    def size(self) -> int:
        """Number of unique URLs in the queue."""
        return len(self._urls)

    @property
    def total_enqueued(self) -> int:
        """Total items ever enqueued."""
        return self._total_enqueued

    @property
    def total_dequeued(self) -> int:
        """Total items ever dequeued."""
        return self._total_dequeued

    def clear(self) -> None:
        """Remove all items."""
        self._heap.clear()
        self._urls.clear()


# ── Conditional request helpers ─────────────────────────────────────────


@dataclass(frozen=True)
class ConditionalHeaders:
    """HTTP conditional request headers for bandwidth-efficient recrawl."""

    etag: str | None = None
    last_modified: str | None = None

    def to_request_headers(self) -> dict[str, str]:
        """Build HTTP headers for conditional GET."""
        headers: dict[str, str] = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        return headers

    @staticmethod
    def from_response_headers(
        headers: dict[str, str],
    ) -> ConditionalHeaders:
        """Extract ETag and Last-Modified from response headers."""
        return ConditionalHeaders(
            etag=headers.get("etag") or headers.get("ETag"),
            last_modified=(
                headers.get("last-modified") or headers.get("Last-Modified")
            ),
        )
