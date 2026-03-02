"""Tests for RSS feed monitoring and real-time content freshness (Issue #4).

Covers: feed_monitor, freshness, OPML import, priority recrawl queue,
conditional headers, crawl_loop integration.
"""

from __future__ import annotations

import time

from infomesh.crawler.feed_monitor import (
    FeedMonitor,
    FeedPriority,
    FeedUpdate,
    MonitoredFeed,
    parse_opml,
)
from infomesh.crawler.freshness import (
    ConditionalHeaders,
    FreshnessTier,
    PriorityRecrawlItem,
    PriorityRecrawlQueue,
    RecrawlTrigger,
    classify_freshness,
)

# ── FeedMonitor tests ───────────────────────────────────────────────────


class TestFeedMonitor:
    def test_add_feed_basic(self) -> None:
        monitor = FeedMonitor()
        feed = monitor.add_feed("https://example.com/feed.xml")
        assert isinstance(feed, MonitoredFeed)
        assert feed.url == "https://example.com/feed.xml"
        assert feed.priority == FeedPriority.NORMAL

    def test_add_feed_with_priority(self) -> None:
        monitor = FeedMonitor()
        feed = monitor.add_feed(
            "https://sec.example.com/rss",
            priority=FeedPriority.CRITICAL,
        )
        assert feed.priority == FeedPriority.CRITICAL
        assert feed.effective_interval == 60

    def test_add_feed_custom_interval(self) -> None:
        monitor = FeedMonitor()
        feed = monitor.add_feed(
            "https://example.com/feed.xml",
            poll_interval=120,
        )
        assert feed.effective_interval == 120

    def test_add_duplicate_updates(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        feed = monitor.add_feed(
            "https://example.com/feed.xml",
            priority=FeedPriority.HIGH,
        )
        assert feed.priority == FeedPriority.HIGH
        assert len(monitor.feeds) == 1

    def test_remove_feed(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        assert monitor.remove_feed("https://example.com/feed.xml")
        assert len(monitor.feeds) == 0

    def test_remove_nonexistent(self) -> None:
        monitor = FeedMonitor()
        assert not monitor.remove_feed("https://example.com/nonexistent")

    def test_stats(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://a.com/rss", priority=FeedPriority.HIGH)
        monitor.add_feed("https://b.com/rss", priority=FeedPriority.NORMAL)
        stats = monitor.stats
        assert stats.total_feeds == 2
        assert stats.feeds_by_priority["high"] == 1
        assert stats.feeds_by_priority["normal"] == 1


class TestFeedMonitorDueFeeds:
    def test_never_polled_always_due(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        due = monitor.get_due_feeds(now=1000.0)
        assert len(due) == 1

    def test_recently_polled_not_due(self) -> None:
        monitor = FeedMonitor()
        feed = monitor.add_feed("https://example.com/feed.xml")
        feed.last_poll_at = 999.0  # polled 1 second ago
        due = monitor.get_due_feeds(now=1000.0)
        assert len(due) == 0

    def test_overdue_feed(self) -> None:
        monitor = FeedMonitor()
        feed = monitor.add_feed(
            "https://example.com/feed.xml",
            priority=FeedPriority.NORMAL,
        )
        feed.last_poll_at = 0.0  # polled long ago
        due = monitor.get_due_feeds(now=10000.0)
        assert len(due) == 1

    def test_priority_ordering(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://low.com/rss", priority=FeedPriority.LOW)
        monitor.add_feed("https://crit.com/rss", priority=FeedPriority.CRITICAL)
        monitor.add_feed("https://high.com/rss", priority=FeedPriority.HIGH)
        due = monitor.get_due_feeds(now=1000.0)
        assert len(due) == 3
        assert due[0].priority == FeedPriority.CRITICAL
        assert due[1].priority == FeedPriority.HIGH
        assert due[2].priority == FeedPriority.LOW


class TestFeedProcessing:
    RSS_TEMPLATE = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <item>
          <title>Article 1</title>
          <link>https://example.com/1</link>
        </item>
        <item>
          <title>Article 2</title>
          <link>https://example.com/2</link>
        </item>
      </channel>
    </rss>"""

    def test_process_feed_response(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        update = monitor.process_feed_response(
            "https://example.com/feed.xml",
            self.RSS_TEMPLATE,
            now=1000.0,
        )
        assert isinstance(update, FeedUpdate)
        assert len(update.new_urls) == 2
        assert "https://example.com/1" in update.new_urls

    def test_process_deduplicates_urls(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        # First poll
        monitor.process_feed_response(
            "https://example.com/feed.xml",
            self.RSS_TEMPLATE,
            now=1000.0,
        )
        # Second poll — same items
        update2 = monitor.process_feed_response(
            "https://example.com/feed.xml",
            self.RSS_TEMPLATE,
            now=2000.0,
        )
        assert len(update2.new_urls) == 0

    def test_process_unregistered_feed(self) -> None:
        monitor = FeedMonitor()
        update = monitor.process_feed_response(
            "https://unknown.com/feed.xml",
            self.RSS_TEMPLATE,
        )
        assert update.error == "feed not registered"

    def test_process_invalid_xml(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        update = monitor.process_feed_response(
            "https://example.com/feed.xml",
            "<not valid",
            now=1000.0,
        )
        # Should not error — parser handles gracefully
        assert update.error is None

    def test_mark_url_seen(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        monitor.mark_url_seen("https://example.com/1")
        update = monitor.process_feed_response(
            "https://example.com/feed.xml",
            self.RSS_TEMPLATE,
            now=1000.0,
        )
        assert "https://example.com/1" not in update.new_urls
        assert "https://example.com/2" in update.new_urls

    def test_stats_update_after_poll(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://example.com/feed.xml")
        monitor.process_feed_response(
            "https://example.com/feed.xml",
            self.RSS_TEMPLATE,
            now=1000.0,
        )
        stats = monitor.stats
        assert stats.total_polls == 1
        assert stats.total_new_urls == 2


# ── OPML import tests ──────────────────────────────────────────────────


class TestOPMLImport:
    OPML_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
    <opml version="1.0">
      <body>
        <outline text="Tech News" xmlUrl="https://tech.com/feed.xml"/>
        <outline title="Python Blog" xmlUrl="https://python.org/rss"/>
        <outline text="Security"
                 xmlUrl="https://security.com/advisory.xml"
                 title="Security Advisories"/>
      </body>
    </opml>"""

    def test_parse_opml_basic(self) -> None:
        feeds = parse_opml(self.OPML_TEXT)
        assert len(feeds) == 3
        urls = [f.url for f in feeds]
        assert "https://tech.com/feed.xml" in urls
        assert "https://python.org/rss" in urls

    def test_parse_opml_labels(self) -> None:
        feeds = parse_opml(self.OPML_TEXT)
        by_url = {f.url: f for f in feeds}
        assert by_url["https://tech.com/feed.xml"].label == "Tech News"
        assert by_url["https://python.org/rss"].label == "Python Blog"

    def test_parse_opml_dedup(self) -> None:
        opml = """<opml><body>
        <outline xmlUrl="https://a.com/rss"/>
        <outline xmlUrl="https://a.com/rss"/>
        </body></opml>"""
        feeds = parse_opml(opml)
        assert len(feeds) == 1

    def test_parse_opml_empty(self) -> None:
        feeds = parse_opml("")
        assert feeds == []

    def test_monitor_add_from_opml(self) -> None:
        monitor = FeedMonitor()
        added = monitor.add_feeds_from_opml(self.OPML_TEXT)
        assert added == 3
        assert len(monitor.feeds) == 3

    def test_monitor_opml_no_duplicates(self) -> None:
        monitor = FeedMonitor()
        monitor.add_feed("https://tech.com/feed.xml")
        added = monitor.add_feeds_from_opml(self.OPML_TEXT)
        assert added == 2  # tech.com already existed


# ── Freshness tier tests ───────────────────────────────────────────────


class TestFreshnessTier:
    def test_hot(self) -> None:
        now = time.time()
        assert classify_freshness(now - 30, now=now) == FreshnessTier.HOT

    def test_warm(self) -> None:
        now = time.time()
        assert classify_freshness(now - 7200, now=now) == FreshnessTier.WARM

    def test_cold(self) -> None:
        now = time.time()
        assert classify_freshness(now - 172800, now=now) == FreshnessTier.COLD

    def test_stale(self) -> None:
        now = time.time()
        assert classify_freshness(now - 864000, now=now) == FreshnessTier.STALE

    def test_boundary_hot_warm(self) -> None:
        now = 100000.0
        assert classify_freshness(now - 3600, now=now) == FreshnessTier.HOT
        assert classify_freshness(now - 3601, now=now) == FreshnessTier.WARM


# ── Priority recrawl queue tests ───────────────────────────────────────


class TestPriorityRecrawlQueue:
    def test_enqueue_dequeue(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue("https://a.com", RecrawlTrigger.SCHEDULED, now=1.0)
        item = q.dequeue()
        assert item is not None
        assert item.url == "https://a.com"
        assert item.trigger == RecrawlTrigger.SCHEDULED

    def test_priority_ordering(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue("https://sched.com", RecrawlTrigger.SCHEDULED, now=1.0)
        q.enqueue("https://rss.com", RecrawlTrigger.RSS_UPDATE, now=2.0)
        q.enqueue("https://user.com", RecrawlTrigger.USER_REQUEST, now=3.0)

        item1 = q.dequeue()
        item2 = q.dequeue()
        item3 = q.dequeue()

        assert item1 is not None
        assert item1.trigger == RecrawlTrigger.USER_REQUEST
        assert item2 is not None
        assert item2.trigger == RecrawlTrigger.RSS_UPDATE
        assert item3 is not None
        assert item3.trigger == RecrawlTrigger.SCHEDULED

    def test_dedup(self) -> None:
        q = PriorityRecrawlQueue()
        assert q.enqueue("https://a.com", RecrawlTrigger.SCHEDULED)
        assert not q.enqueue("https://a.com", RecrawlTrigger.RSS_UPDATE)
        assert q.size == 1

    def test_max_size(self) -> None:
        q = PriorityRecrawlQueue(max_size=2)
        q.enqueue("https://a.com", RecrawlTrigger.SCHEDULED)
        q.enqueue("https://b.com", RecrawlTrigger.SCHEDULED)
        assert not q.enqueue("https://c.com", RecrawlTrigger.SCHEDULED)
        assert q.size == 2

    def test_discard(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue("https://a.com", RecrawlTrigger.SCHEDULED)
        q.discard("https://a.com")
        assert q.dequeue() is None

    def test_peek(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue("https://a.com", RecrawlTrigger.USER_REQUEST, now=1.0)
        item = q.peek()
        assert item is not None
        assert item.url == "https://a.com"
        assert q.size == 1  # still in queue

    def test_peek_empty(self) -> None:
        q = PriorityRecrawlQueue()
        assert q.peek() is None

    def test_clear(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue("https://a.com", RecrawlTrigger.SCHEDULED)
        q.enqueue("https://b.com", RecrawlTrigger.SCHEDULED)
        q.clear()
        assert q.size == 0
        assert q.dequeue() is None

    def test_empty_dequeue(self) -> None:
        q = PriorityRecrawlQueue()
        assert q.dequeue() is None

    def test_source_feed_tracking(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue(
            "https://article.com",
            RecrawlTrigger.RSS_UPDATE,
            source_feed="https://blog.com/feed.xml",
        )
        item = q.dequeue()
        assert item is not None
        assert item.source_feed == "https://blog.com/feed.xml"

    def test_total_counters(self) -> None:
        q = PriorityRecrawlQueue()
        q.enqueue("https://a.com", RecrawlTrigger.SCHEDULED)
        q.enqueue("https://b.com", RecrawlTrigger.SCHEDULED)
        assert q.total_enqueued == 2
        q.dequeue()
        assert q.total_dequeued == 1


class TestPriorityRecrawlItem:
    def test_ordering(self) -> None:
        a = PriorityRecrawlItem(0, 1.0, "a", RecrawlTrigger.USER_REQUEST)
        b = PriorityRecrawlItem(1, 1.0, "b", RecrawlTrigger.RSS_UPDATE)
        assert a < b

    def test_same_priority_time_order(self) -> None:
        a = PriorityRecrawlItem(1, 1.0, "a", RecrawlTrigger.RSS_UPDATE)
        b = PriorityRecrawlItem(1, 2.0, "b", RecrawlTrigger.RSS_UPDATE)
        assert a < b


# ── Conditional headers tests ──────────────────────────────────────────


class TestConditionalHeaders:
    def test_to_request_headers_both(self) -> None:
        ch = ConditionalHeaders(etag='"abc"', last_modified="Mon, 01 Jan 2024")
        headers = ch.to_request_headers()
        assert headers["If-None-Match"] == '"abc"'
        assert headers["If-Modified-Since"] == "Mon, 01 Jan 2024"

    def test_to_request_headers_etag_only(self) -> None:
        ch = ConditionalHeaders(etag='"abc"')
        headers = ch.to_request_headers()
        assert "If-None-Match" in headers
        assert "If-Modified-Since" not in headers

    def test_to_request_headers_empty(self) -> None:
        ch = ConditionalHeaders()
        assert ch.to_request_headers() == {}

    def test_from_response_headers(self) -> None:
        ch = ConditionalHeaders.from_response_headers(
            {"etag": '"xyz"', "last-modified": "Tue, 02 Jan 2024"}
        )
        assert ch.etag == '"xyz"'
        assert ch.last_modified == "Tue, 02 Jan 2024"


# ── FeedPriority enum tests ────────────────────────────────────────────


class TestFeedPriority:
    def test_values(self) -> None:
        assert FeedPriority.CRITICAL == "critical"
        assert FeedPriority.HIGH == "high"
        assert FeedPriority.NORMAL == "normal"
        assert FeedPriority.LOW == "low"


class TestMonitoredFeed:
    def test_effective_interval_default(self) -> None:
        feed = MonitoredFeed(url="https://a.com/rss")
        assert feed.effective_interval == 900  # NORMAL default

    def test_effective_interval_custom(self) -> None:
        feed = MonitoredFeed(url="https://a.com/rss", poll_interval=120)
        assert feed.effective_interval == 120

    def test_effective_interval_critical(self) -> None:
        feed = MonitoredFeed(
            url="https://a.com/rss",
            priority=FeedPriority.CRITICAL,
        )
        assert feed.effective_interval == 60


class TestRecrawlTrigger:
    def test_values(self) -> None:
        assert RecrawlTrigger.RSS_UPDATE == "rss_update"
        assert RecrawlTrigger.USER_REQUEST == "user_request"
        assert RecrawlTrigger.CONTENT_CHANGE == "content_change"
        assert RecrawlTrigger.SCHEDULED == "scheduled"
        assert RecrawlTrigger.PEER_ANNOUNCE == "peer_announce"


# ── CrawlConfig RSS fields ─────────────────────────────────────────────


class TestCrawlConfigRSSFields:
    def test_defaults(self) -> None:
        from infomesh.config import CrawlConfig

        cfg = CrawlConfig()
        assert cfg.rss_enabled is False
        assert cfg.rss_default_interval == 900
        assert cfg.rss_max_feeds == 100
        assert cfg.rss_discovery is True

    def test_custom_values(self) -> None:
        from infomesh.config import CrawlConfig

        cfg = CrawlConfig(
            rss_enabled=True,
            rss_default_interval=60,
            rss_max_feeds=50,
            rss_discovery=False,
        )
        assert cfg.rss_enabled is True
        assert cfg.rss_default_interval == 60
        assert cfg.rss_max_feeds == 50
        assert cfg.rss_discovery is False


# ── CrawlResult discovered_feeds field ──────────────────────────────────


class TestCrawlResultFeeds:
    def test_default_empty(self) -> None:
        from infomesh.crawler.worker import CrawlResult

        result = CrawlResult(url="https://example.com", success=True)
        assert result.discovered_feeds == []

    def test_with_feeds(self) -> None:
        from infomesh.crawler.worker import CrawlResult

        result = CrawlResult(
            url="https://example.com",
            success=True,
            discovered_feeds=["https://example.com/rss"],
        )
        assert len(result.discovered_feeds) == 1
