"""Continuous RSS/Atom feed monitor for real-time content freshness.

Polls RSS/Atom feeds at configurable intervals and triggers priority
crawls for newly discovered URLs.  Integrates with the existing
``rss.py`` parser and ``recrawl.py`` scheduler.

Supports OPML import for user-curated feed lists.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

logger = structlog.get_logger()


# ── Data types ──────────────────────────────────────────────────────────


class FeedPriority(StrEnum):
    """Feed poll priority — determines poll interval."""

    CRITICAL = "critical"  # 1 min  — security advisories
    HIGH = "high"  # 5 min  — breaking news, releases
    NORMAL = "normal"  # 15 min — blogs, docs
    LOW = "low"  # 60 min — infrequent updates


# Default poll intervals per priority (seconds)
POLL_INTERVALS: dict[FeedPriority, int] = {
    FeedPriority.CRITICAL: 60,
    FeedPriority.HIGH: 300,
    FeedPriority.NORMAL: 900,
    FeedPriority.LOW: 3600,
}


@dataclass
class MonitoredFeed:
    """A feed being actively monitored."""

    url: str
    priority: FeedPriority = FeedPriority.NORMAL
    poll_interval: int = 0  # 0 = use priority default
    last_poll_at: float = 0.0
    last_item_url: str = ""
    error_count: int = 0
    items_discovered: int = 0
    label: str = ""  # Optional human-readable label

    @property
    def effective_interval(self) -> int:
        """Return configured or priority-default poll interval."""
        if self.poll_interval > 0:
            return self.poll_interval
        return POLL_INTERVALS[self.priority]


@dataclass
class FeedUpdate:
    """A batch of new items from a single feed poll."""

    feed_url: str
    new_urls: list[str] = field(default_factory=list)
    poll_elapsed_ms: float = 0.0
    error: str | None = None


@dataclass
class FeedMonitorStats:
    """Aggregate stats for the feed monitor."""

    total_feeds: int = 0
    total_polls: int = 0
    total_new_urls: int = 0
    total_errors: int = 0
    feeds_by_priority: dict[str, int] = field(default_factory=dict)


# ── OPML import ─────────────────────────────────────────────────────────

_OPML_OUTLINE_RE = re.compile(
    r"<outline\b([^>]*)/>",
    re.IGNORECASE,
)
_XML_URL_RE = re.compile(r'xmlUrl=["\']([^"\']+)["\']', re.IGNORECASE)
_TEXT_RE = re.compile(r'text=["\']([^"\']*)["\']', re.IGNORECASE)
_TITLE_RE = re.compile(r'title=["\']([^"\']*)["\']', re.IGNORECASE)


def parse_opml(opml_text: str) -> list[MonitoredFeed]:
    """Parse an OPML file and return monitored feeds.

    Args:
        opml_text: Raw OPML XML content.

    Returns:
        List of :class:`MonitoredFeed` with URLs and labels.
    """
    feeds: list[MonitoredFeed] = []
    seen_urls: set[str] = set()

    for m in _OPML_OUTLINE_RE.finditer(opml_text):
        attrs = m.group(1)
        url_m = _XML_URL_RE.search(attrs)
        if not url_m:
            continue
        url = url_m.group(1).strip()
        text_m = _TEXT_RE.search(attrs)
        title_m = _TITLE_RE.search(attrs)
        label = ""
        if text_m:
            label = text_m.group(1).strip()
        elif title_m:
            label = title_m.group(1).strip()

        if url and url not in seen_urls:
            seen_urls.add(url)
            feeds.append(
                MonitoredFeed(
                    url=url,
                    label=label,
                    priority=FeedPriority.NORMAL,
                )
            )

    logger.info("opml_parsed", feed_count=len(feeds))
    return feeds


# ── Core monitor logic ──────────────────────────────────────────────────


class FeedMonitor:
    """Manages a set of RSS/Atom feeds and tracks new items.

    Not async — the poll logic is I/O-free.  The caller is responsible
    for fetching feed XML and calling :meth:`process_feed_response`.
    """

    def __init__(self) -> None:
        self._feeds: dict[str, MonitoredFeed] = {}
        self._seen_urls: set[str] = set()
        self._stats = FeedMonitorStats()

    # ── Feed management ─────────────────────────────────────

    def add_feed(
        self,
        url: str,
        *,
        priority: FeedPriority = FeedPriority.NORMAL,
        poll_interval: int = 0,
        label: str = "",
    ) -> MonitoredFeed:
        """Register a feed for monitoring.

        If the feed is already registered, updates priority and interval.
        """
        if url in self._feeds:
            existing = self._feeds[url]
            existing.priority = priority
            if poll_interval > 0:
                existing.poll_interval = poll_interval
            if label:
                existing.label = label
            return existing

        feed = MonitoredFeed(
            url=url,
            priority=priority,
            poll_interval=poll_interval,
            label=label,
        )
        self._feeds[url] = feed
        self._stats.total_feeds = len(self._feeds)
        logger.info(
            "feed_added",
            url=url,
            priority=priority,
            interval=feed.effective_interval,
        )
        return feed

    def remove_feed(self, url: str) -> bool:
        """Remove a feed from monitoring."""
        removed = self._feeds.pop(url, None) is not None
        if removed:
            self._stats.total_feeds = len(self._feeds)
        return removed

    def add_feeds_from_opml(self, opml_text: str) -> int:
        """Import feeds from OPML and add them all.

        Returns:
            Number of new feeds added.
        """
        parsed = parse_opml(opml_text)
        added = 0
        for feed in parsed:
            if feed.url not in self._feeds:
                self.add_feed(
                    feed.url,
                    priority=feed.priority,
                    label=feed.label,
                )
                added += 1
        return added

    # ── Poll scheduling ─────────────────────────────────────

    def get_due_feeds(self, *, now: float | None = None) -> list[MonitoredFeed]:
        """Return feeds that are due for polling.

        A feed is due when ``now - last_poll_at >= effective_interval``.
        Never-polled feeds are always due.

        Returns:
            List sorted by priority (CRITICAL first), then by overdue time.
        """
        now = now or time.time()
        due: list[tuple[int, float, MonitoredFeed]] = []

        priority_order = {
            FeedPriority.CRITICAL: 0,
            FeedPriority.HIGH: 1,
            FeedPriority.NORMAL: 2,
            FeedPriority.LOW: 3,
        }

        for feed in self._feeds.values():
            if feed.last_poll_at == 0.0:
                # Never polled — always due
                due.append((priority_order[feed.priority], 0.0, feed))
            elif now - feed.last_poll_at >= feed.effective_interval:
                overdue = now - feed.last_poll_at - feed.effective_interval
                due.append((priority_order[feed.priority], -overdue, feed))

        due.sort(key=lambda x: (x[0], x[1]))
        return [f for _, _, f in due]

    # ── Feed processing ─────────────────────────────────────

    def process_feed_response(
        self,
        feed_url: str,
        xml_text: str,
        *,
        now: float | None = None,
    ) -> FeedUpdate:
        """Parse a fetched feed and extract new (unseen) item URLs.

        Args:
            feed_url: URL of the feed that was polled.
            xml_text: Raw XML response body.
            now: Override current time for testing.

        Returns:
            :class:`FeedUpdate` with newly discovered URLs.
        """
        from infomesh.crawler.rss import parse_feed_xml

        now = now or time.time()
        start = time.monotonic()

        feed = self._feeds.get(feed_url)
        if feed is None:
            return FeedUpdate(
                feed_url=feed_url,
                error="feed not registered",
            )

        try:
            result = parse_feed_xml(xml_text, feed_url)
        except Exception as exc:  # noqa: BLE001
            feed.error_count += 1
            feed.last_poll_at = now
            self._stats.total_errors += 1
            logger.warning("feed_parse_error", url=feed_url, error=str(exc))
            return FeedUpdate(
                feed_url=feed_url,
                error=str(exc),
                poll_elapsed_ms=(time.monotonic() - start) * 1000,
            )

        # Track new URLs
        new_urls: list[str] = []
        for item in result.items:
            if item.url and item.url not in self._seen_urls:
                self._seen_urls.add(item.url)
                new_urls.append(item.url)

        feed.last_poll_at = now
        feed.error_count = 0
        feed.items_discovered += len(new_urls)
        if result.items:
            feed.last_item_url = result.items[0].url

        self._stats.total_polls += 1
        self._stats.total_new_urls += len(new_urls)

        elapsed = (time.monotonic() - start) * 1000

        if new_urls:
            logger.info(
                "feed_new_items",
                url=feed_url,
                new_count=len(new_urls),
                total_items=len(result.items),
            )

        return FeedUpdate(
            feed_url=feed_url,
            new_urls=new_urls,
            poll_elapsed_ms=elapsed,
        )

    def mark_url_seen(self, url: str) -> None:
        """Mark a URL as already known (e.g., from index)."""
        self._seen_urls.add(url)

    # ── Stats ───────────────────────────────────────────────

    @property
    def stats(self) -> FeedMonitorStats:
        """Return current monitor statistics."""
        by_priority: dict[str, int] = {}
        for feed in self._feeds.values():
            key = feed.priority.value
            by_priority[key] = by_priority.get(key, 0) + 1
        self._stats.feeds_by_priority = by_priority
        return self._stats

    @property
    def feeds(self) -> list[MonitoredFeed]:
        """Return all monitored feeds."""
        return list(self._feeds.values())
