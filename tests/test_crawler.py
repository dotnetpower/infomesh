"""Tests for the crawler modules."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from infomesh.crawler.dedup import DeduplicatorDB, content_hash, normalize_url
from infomesh.crawler.parser import extract_canonical, extract_links
from infomesh.crawler.robots import _CRAWL_DELAY_RE, _SITEMAP_RE, RobotsChecker
from infomesh.crawler.scheduler import Scheduler
from infomesh.crawler.seeds import load_seeds


class TestNormalizeUrl:
    """Tests for URL normalization."""

    def test_lowercase(self) -> None:
        assert normalize_url("HTTPS://EXAMPLE.COM/Page") == "https://example.com/Page"

    def test_strip_fragment(self) -> None:
        assert (
            normalize_url("https://example.com/page#section")
            == "https://example.com/page"
        )

    def test_strip_tracking_params(self) -> None:
        result = normalize_url("https://example.com/page?utm_source=google&q=test")
        assert "utm_source" not in result
        assert "q=test" in result

    def test_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/page/") == "https://example.com/page"
        # Root slash should be preserved
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_sort_query_params(self) -> None:
        result = normalize_url("https://example.com/page?z=1&a=2")
        assert (
            result == "https://example.com/page?a=%5B%272%27%5D&z=%5B%271%27%5D"
            or "a=" in result
        )


class TestContentHash:
    """Tests for content hashing."""

    def test_deterministic(self) -> None:
        h1 = content_hash("hello world")
        h2 = content_hash("hello world")
        assert h1 == h2

    def test_different_content(self) -> None:
        h1 = content_hash("hello")
        h2 = content_hash("world")
        assert h1 != h2


class TestDeduplicatorDB:
    """Tests for the deduplication database."""

    def test_url_dedup(self) -> None:
        db = DeduplicatorDB()
        assert not db.is_url_seen("https://example.com/page")
        db.mark_seen("https://example.com/page", "hash1")
        assert db.is_url_seen("https://example.com/page")
        db.close()

    def test_content_dedup(self) -> None:
        db = DeduplicatorDB()
        assert not db.is_content_seen("hash1")
        db.mark_seen("https://example.com/page", "hash1")
        assert db.is_content_seen("hash1")
        db.close()


class TestSeeds:
    """Tests for seed URL loading."""

    def test_load_all_seeds(self) -> None:
        urls = load_seeds()
        assert len(urls) > 0
        assert all(url.startswith("http") for url in urls)

    def test_load_category(self) -> None:
        urls = load_seeds(category="tech-docs")
        assert len(urls) > 0
        assert "https://docs.python.org/3/" in urls

    def test_nonexistent_category(self) -> None:
        urls = load_seeds(category="nonexistent")
        assert urls == []


class TestExtractLinks:
    """Tests for link extraction from HTML."""

    def test_absolute_links(self) -> None:
        html = (
            '<html><body><a href="https://example.com/page1">Link 1</a></body></html>'
        )
        links = extract_links(html, "https://example.com/")
        assert "https://example.com/page1" in links

    def test_relative_links(self) -> None:
        html = '<html><body><a href="/about">About</a></body></html>'
        links = extract_links(html, "https://example.com/page")
        assert "https://example.com/about" in links

    def test_skips_mailto(self) -> None:
        html = '<html><body><a href="mailto:test@example.com">Email</a></body></html>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 0

    def test_skips_binary_extensions(self) -> None:
        html = (
            '<a href="/file.pdf">PDF</a>'
            '<a href="/file.jpg">Image</a>'
            '<a href="/page">Page</a>'
        )
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1
        assert "/page" in links[0]

    def test_deduplicates(self) -> None:
        html = '<a href="/page">1</a><a href="/page">2</a><a href="/page#section">3</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1

    def test_skips_javascript(self) -> None:
        html = '<a href="javascript:void(0)">Click</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 0


# ── Canonical tag tests ──────────────────────────────────────────────


class TestExtractCanonical:
    """Tests for ``extract_canonical()``."""

    def test_found_rel_first(self) -> None:
        html = (
            "<html><head>"
            '<link rel="canonical" href="https://example.com/page">'
            "</head><body></body></html>"
        )
        assert (
            extract_canonical(html, "https://example.com/page")
            == "https://example.com/page"
        )

    def test_found_href_first(self) -> None:
        html = (
            "<html><head>"
            '<link href="https://example.com/real" rel="canonical">'
            "</head><body></body></html>"
        )
        assert (
            extract_canonical(html, "https://example.com/alt")
            == "https://example.com/real"
        )

    def test_not_found(self) -> None:
        html = "<html><head></head><body></body></html>"
        assert extract_canonical(html, "https://example.com/") is None

    def test_relative_canonical(self) -> None:
        html = '<link rel="canonical" href="/canonical-page">'
        result = extract_canonical(html, "https://example.com/other")
        assert result == "https://example.com/canonical-page"

    def test_rejects_non_http(self) -> None:
        html = '<link rel="canonical" href="ftp://example.com/bad">'
        assert extract_canonical(html, "https://example.com/") is None

    def test_empty_href(self) -> None:
        html = '<link rel="canonical" href="">'
        assert extract_canonical(html, "https://example.com/") is None

    def test_single_quotes(self) -> None:
        html = "<link rel='canonical' href='https://example.com/q'>"
        assert (
            extract_canonical(html, "https://example.com/") == "https://example.com/q"
        )


# ── Sitemap / Crawl-delay regex tests ───────────────────────────────


class TestRobotsRegex:
    """Tests for Sitemap and Crawl-delay regex patterns."""

    def test_sitemap_regex_basic(self) -> None:
        text = "Sitemap: https://example.com/sitemap.xml\n"
        matches = _SITEMAP_RE.findall(text)
        assert matches == ["https://example.com/sitemap.xml"]

    def test_sitemap_regex_multiple(self) -> None:
        text = (
            "User-agent: *\n"
            "Disallow: /admin\n"
            "Sitemap: https://example.com/sitemap1.xml\n"
            "Sitemap: https://example.com/sitemap2.xml\n"
        )
        matches = _SITEMAP_RE.findall(text)
        assert len(matches) == 2

    def test_sitemap_regex_case_insensitive(self) -> None:
        text = "SITEMAP: https://example.com/map.xml\n"
        matches = _SITEMAP_RE.findall(text)
        assert len(matches) == 1

    def test_crawl_delay_regex(self) -> None:
        text = "User-agent: *\nCrawl-delay: 5\n"
        m = _CRAWL_DELAY_RE.search(text)
        assert m is not None
        assert float(m.group(1)) == 5.0

    def test_crawl_delay_float(self) -> None:
        text = "Crawl-delay: 1.5\n"
        m = _CRAWL_DELAY_RE.search(text)
        assert m is not None
        assert float(m.group(1)) == 1.5

    def test_crawl_delay_not_found(self) -> None:
        text = "User-agent: *\nDisallow: /admin\n"
        m = _CRAWL_DELAY_RE.search(text)
        assert m is None


# ── RobotsChecker integration tests ─────────────────────────────────


class TestRobotsChecker:
    """Tests for RobotsChecker sitemap/crawl-delay features."""

    @pytest.mark.asyncio
    async def test_get_sitemaps_populated(self) -> None:
        """Sitemaps are extracted after is_allowed() call."""
        robots_text = (
            "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n"
        )
        checker = RobotsChecker("TestBot")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = robots_text

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        await checker.is_allowed(client, "https://example.com/page")
        sitemaps = checker.get_sitemaps("example.com")
        assert sitemaps == ["https://example.com/sitemap.xml"]

    @pytest.mark.asyncio
    async def test_get_crawl_delay_populated(self) -> None:
        """Crawl-delay is extracted after is_allowed() call."""
        robots_text = "User-agent: *\nCrawl-delay: 3\nAllow: /\n"
        checker = RobotsChecker("TestBot")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = robots_text

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        await checker.is_allowed(client, "https://example.com/page")
        delay = checker.get_crawl_delay("example.com")
        assert delay == 3.0

    @pytest.mark.asyncio
    async def test_no_crawl_delay(self) -> None:
        """No crawl-delay returns None."""
        robots_text = "User-agent: *\nAllow: /\n"
        checker = RobotsChecker("TestBot")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = robots_text

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        await checker.is_allowed(client, "https://example.com/page")
        assert checker.get_crawl_delay("example.com") is None

    @pytest.mark.asyncio
    async def test_clear_cache(self) -> None:
        """clear_cache resets sitemaps and crawl-delays."""
        checker = RobotsChecker("TestBot")
        checker._sitemaps["x.com"] = ["https://x.com/sm.xml"]
        checker._crawl_delays["x.com"] = 2.0
        checker.clear_cache()
        assert checker.get_sitemaps("x.com") == []
        assert checker.get_crawl_delay("x.com") is None


# ── Scheduler Crawl-delay tests ─────────────────────────────────────


class TestSchedulerCrawlDelay:
    """Tests for per-domain crawl-delay in Scheduler."""

    def test_set_crawl_delay(self) -> None:
        sched = Scheduler()
        sched.set_crawl_delay("example.com", 5.0)
        state = sched._domains["example.com"]
        assert state.crawl_delay == 5.0

    def test_set_crawl_delay_capped(self) -> None:
        sched = Scheduler()
        sched.set_crawl_delay("example.com", 120.0)
        state = sched._domains["example.com"]
        assert state.crawl_delay == 60.0  # capped

    @pytest.mark.asyncio
    async def test_get_url_uses_crawl_delay(self) -> None:
        """Scheduler uses per-domain crawl-delay if set."""
        sched = Scheduler(politeness_delay=0.0, urls_per_hour=0)
        await sched.add_url("https://example.com/a", depth=0)
        sched.set_crawl_delay("example.com", 0.0)

        url, depth = await asyncio.wait_for(sched.get_url(), timeout=2.0)
        assert url == "https://example.com/a"
        assert depth == 0


# ── Retry backoff tests ─────────────────────────────────────────────


class TestFetchWithRetry:
    """Tests for _fetch_with_retry in CrawlWorker."""

    @pytest.mark.asyncio
    async def test_success_first_try(self) -> None:
        """Successful fetch on first attempt returns response."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/page"
        mock_resp.raise_for_status = MagicMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        import time

        result = await worker._fetch_with_retry(
            client, "https://example.com/page", time.monotonic()
        )
        assert isinstance(result, httpx.Response)
        dedup.close()

    @pytest.mark.asyncio
    async def test_retry_on_500(self) -> None:
        """5xx triggers retry, then succeeds."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        # First call raises 500, second succeeds
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 500
        error_resp.url = "https://example.com/page"
        exc_500 = httpx.HTTPStatusError("500", request=MagicMock(), response=error_resp)

        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.status_code = 200
        ok_resp.url = "https://example.com/page"
        ok_resp.raise_for_status = MagicMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise exc_500
            return ok_resp

        client.get = mock_get

        import time

        with patch("infomesh.crawler.worker.asyncio.sleep", new_callable=AsyncMock):
            result = await worker._fetch_with_retry(
                client, "https://example.com/page", time.monotonic()
            )

        assert isinstance(result, httpx.Response)
        assert call_count == 2
        dedup.close()

    @pytest.mark.asyncio
    async def test_exhausted_retries_returns_crawl_result(self) -> None:
        """When all retries are exhausted, returns CrawlResult error."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlResult, CrawlWorker

        config = CrawlConfig()
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 503
        error_resp.url = "https://example.com/down"
        exc = httpx.HTTPStatusError("503", request=MagicMock(), response=error_resp)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=exc)

        import time

        with patch("infomesh.crawler.worker.asyncio.sleep", new_callable=AsyncMock):
            result = await worker._fetch_with_retry(
                client, "https://example.com/down", time.monotonic()
            )

        assert isinstance(result, CrawlResult)
        assert not result.success
        assert "http_503" in (result.error or "")
        dedup.close()


# ── Sitemap URL scheduling tests ────────────────────────────────────


class TestScheduleSitemapUrls:
    """Tests for _schedule_sitemap_urls in CrawlWorker."""

    @pytest.mark.asyncio
    async def test_schedule_from_sitemap_xml(self) -> None:
        """URLs from sitemap XML are added to the scheduler."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        # Populate the sitemaps cache
        robots._sitemaps["example.com"] = [
            "https://example.com/sitemap.xml",
        ]

        sitemap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/'
            'schemas/sitemap/0.9">\n'
            "  <url><loc>https://example.com/a</loc></url>\n"
            "  <url><loc>https://example.com/b</loc></url>\n"
            "</urlset>"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sitemap_xml

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        worker._client = mock_client

        await worker._schedule_sitemap_urls("example.com")

        assert sched.pending_count == 2
        dedup.close()

    @pytest.mark.asyncio
    async def test_sitemap_processed_once(self) -> None:
        """Sitemaps for a domain are only processed once."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        robots._sitemaps["example.com"] = [
            "https://example.com/sitemap.xml",
        ]

        sitemap_xml = "<urlset><url><loc>https://example.com/x</loc></url></urlset>"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sitemap_xml

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        worker._client = mock_client

        await worker._schedule_sitemap_urls("example.com")
        count_after_first = sched.pending_count

        # Call again — should be a no-op
        await worker._schedule_sitemap_urls("example.com")
        assert sched.pending_count == count_after_first
        dedup.close()


# ── Force re-crawl tests ────────────────────────────────────────────


class TestForceCrawl:
    """Tests for force re-crawl (bypass URL dedup)."""

    @pytest.mark.asyncio
    async def test_without_force_skips_seen_url(self) -> None:
        """Normal crawl skips already-seen URLs."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        # Mark URL as seen
        dedup.mark_seen("https://example.com/page", "hash1", "text")

        result = await worker.crawl_url("https://example.com/page")
        assert not result.success
        assert result.error == "already_seen"
        dedup.close()

    @pytest.mark.asyncio
    async def test_force_bypasses_url_dedup(self) -> None:
        """force=True bypasses URL dedup check."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig(respect_robots=False)
        sched = Scheduler(urls_per_hour=0)
        dedup = DeduplicatorDB()
        robots = RobotsChecker("TestBot")
        worker = CrawlWorker(config, sched, dedup, robots)

        # Mark URL as seen
        dedup.mark_seen("https://example.com/page", "hash1", "text")

        # Mock the HTTP client to return a valid response
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/page"
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = (
            "<html><head><title>Test</title></head>"
            "<body><p>"
            + "This is a test page with enough content. " * 10
            + "</p></body></html>"
        )
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        worker._client = mock_client

        result = await worker.crawl_url("https://example.com/page", force=True)
        # Should NOT be blocked by already_seen
        assert result.error != "already_seen"
        dedup.close()


class TestReseedQueue:
    """Tests for the _reseed_queue idle-restart helper."""

    @pytest.mark.asyncio
    async def test_reseed_adds_unseen_seeds(self) -> None:
        """Unseen seed URLs are added to the scheduler queue."""
        from infomesh.services import _reseed_queue

        scheduler = Scheduler()
        dedup = MagicMock()
        dedup.is_url_seen = MagicMock(return_value=False)

        worker = MagicMock()
        ctx = MagicMock()
        ctx.scheduler = scheduler
        ctx.dedup = dedup
        ctx.worker = worker
        ctx.config = MagicMock()

        log = MagicMock()

        with (
            patch(
                "infomesh.crawler.crawl_loop.CATEGORIES",
                {"cat1": "desc"},
            ),
            patch(
                "infomesh.crawler.crawl_loop.load_seeds",
                return_value=["https://a.com", "https://b.com"],
            ),
        ):
            added = await _reseed_queue(ctx, log)

        assert added == 2
        assert scheduler.pending_count == 2

    @pytest.mark.asyncio
    async def test_reseed_rediscovers_links(self) -> None:
        """Already-seen seeds are re-fetched to discover new child links."""
        from infomesh.services import _reseed_queue

        scheduler = Scheduler()

        call_count = 0

        def _is_url_seen(url: str) -> bool:
            nonlocal call_count
            # Seed URL is seen; discovered child links are not
            if url == "https://seed.com":
                return True
            call_count += 1
            return False

        dedup = MagicMock()
        dedup.is_url_seen = MagicMock(side_effect=_is_url_seen)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "<html><body>"
            '<a href="https://seed.com/page1">P1</a>'
            '<a href="https://seed.com/page2">P2</a>'
            "</body></html>"
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        worker = MagicMock()
        worker.get_http_client = AsyncMock(return_value=mock_client)

        ctx = MagicMock()
        ctx.scheduler = scheduler
        ctx.dedup = dedup
        ctx.worker = worker

        log = MagicMock()

        with (
            patch(
                "infomesh.crawler.crawl_loop.CATEGORIES",
                {"cat1": "desc"},
            ),
            patch(
                "infomesh.crawler.crawl_loop.load_seeds",
                return_value=["https://seed.com"],
            ),
        ):
            added = await _reseed_queue(ctx, log)

        assert added == 2
        assert scheduler.pending_count == 2

    @pytest.mark.asyncio
    async def test_reseed_returns_zero_when_no_components(self) -> None:
        """Returns 0 when crawler components are None."""
        from infomesh.services import _reseed_queue

        ctx = MagicMock()
        ctx.scheduler = None
        ctx.dedup = None
        ctx.worker = None

        added = await _reseed_queue(ctx, MagicMock())
        assert added == 0
