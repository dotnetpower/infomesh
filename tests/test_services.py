"""Tests for infomesh.services — business-logic orchestration layer."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infomesh.config import Config, NodeConfig
from infomesh.crawler.parser import ParsedPage
from infomesh.services import (
    AppContext,
    CrawlAndIndexResult,
    FetchPageResult,
    _truncate_to_bytes,
    crawl_and_index,
    fetch_page,
    fetch_page_async,
    index_document,
    is_paywall_content,
    publish_document_to_network,
    republish_local_index,
)

# ─── Helpers ──────────────────────────────────────────────


def _make_page(
    *,
    url: str = "https://example.com",
    title: str = "Example",
    text: str = "Hello world",
    raw_html_hash: str = "abc123",
    text_hash: str = "def456",
    language: str = "en",
) -> ParsedPage:
    return ParsedPage(
        url=url,
        title=title,
        text=text,
        raw_html_hash=raw_html_hash,
        text_hash=text_hash,
        language=language,
    )


def _mock_store(*, doc: object | None = None) -> MagicMock:
    store = MagicMock()
    store.add_document.return_value = 42
    store.get_document_by_url.return_value = doc
    return store


def _mock_worker() -> AsyncMock:
    return AsyncMock()


class TestAppContextPriority:
    def test_applies_os_priority_before_store_initialization(
        self, tmp_path: Path
    ) -> None:
        events: list[str] = []

        class FakeGovernor:
            def apply_os_priority(self) -> None:
                events.append("priority")

            def check_and_adjust(self) -> None:
                events.append("check")

        class FakeStore:
            def __init__(self, **kwargs: object) -> None:
                events.append("store")

            def close(self) -> None:
                events.append("close")

        config = Config(node=NodeConfig(data_dir=tmp_path, role="control"))

        with (
            patch("infomesh.services.ResourceGovernor", return_value=FakeGovernor()),
            patch("infomesh.services.LocalStore", FakeStore),
            patch("infomesh.services.ensure_keys", return_value=None),
            patch("infomesh.services.resolve_github_email", return_value=""),
        ):
            ctx = AppContext(config, apply_os_priority=True)
            ctx.close()

        assert events[:2] == ["priority", "store"]


# ─── is_paywall_content ──────────────────────────────────


class TestIsPaywallContent:
    def test_detects_subscribe_signal(self) -> None:
        assert is_paywall_content("Please subscribe to continue reading.")

    def test_detects_sign_in_signal(self) -> None:
        assert is_paywall_content("Sign in to read the full article.")

    def test_detects_account_signal(self) -> None:
        assert is_paywall_content("Create a free account to view.")

    def test_detects_subscriber_signal(self) -> None:
        assert is_paywall_content("This content is for subscribers only.")

    def test_case_insensitive(self) -> None:
        assert is_paywall_content("SUBSCRIBE TO CONTINUE reading now!")

    def test_normal_content(self) -> None:
        assert not is_paywall_content("Python is a great programming language.")

    def test_empty_string(self) -> None:
        assert not is_paywall_content("")


# ─── _truncate_to_bytes ──────────────────────────────────


class TestTruncateToBytes:
    def test_short_string_untouched(self) -> None:
        assert _truncate_to_bytes("hello", 100) == "hello"

    def test_truncates_at_limit(self) -> None:
        result = _truncate_to_bytes("a" * 200, 100)
        assert len(result.encode("utf-8")) <= 100

    def test_handles_multibyte_chars(self) -> None:
        # Korean characters: 3 bytes each in UTF-8
        text = "가나다라마바사아자차"  # 10 chars × 3 bytes = 30 bytes
        result = _truncate_to_bytes(text, 15)
        assert len(result.encode("utf-8")) <= 15
        # Should not produce garbled output
        result.encode("utf-8")

    def test_exact_boundary(self) -> None:
        text = "hello"
        result = _truncate_to_bytes(text, 5)
        assert result == "hello"

    def test_zero_bytes(self) -> None:
        result = _truncate_to_bytes("hello", 0)
        assert result == ""


# ─── index_document ──────────────────────────────────────


class TestIndexDocument:
    def test_indexes_to_fts_only(self) -> None:
        page = _make_page()
        store = _mock_store()
        doc_id = index_document(page, store)
        assert doc_id == 42
        store.add_document.assert_called_once_with(
            url="https://example.com",
            title="Example",
            text="Hello world",
            raw_html_hash="abc123",
            text_hash="def456",
            language="en",
            js_required=False,
        )

    def test_indexes_to_fts_and_vector(self) -> None:
        page = _make_page()
        store = _mock_store()
        vs = MagicMock()
        doc_id = index_document(page, store, vs)
        assert doc_id == 42
        vs.add_document.assert_called_once_with(
            doc_id=42,
            url="https://example.com",
            title="Example",
            text="Hello world",
            language="en",
        )

    def test_skips_vector_on_duplicate(self) -> None:
        page = _make_page()
        store = _mock_store()
        store.add_document.return_value = None  # duplicate
        vs = MagicMock()
        doc_id = index_document(page, store, vs)
        assert doc_id is None
        vs.add_document.assert_not_called()

    def test_skips_vector_when_none(self) -> None:
        page = _make_page()
        store = _mock_store()
        doc_id = index_document(page, store, None)
        assert doc_id == 42


class TestDistributedPublishing:
    @pytest.mark.asyncio
    async def test_publish_document_uses_p2p_node(self) -> None:
        page = _make_page(text="Python distributed search")
        p2p_node = AsyncMock()
        p2p_node.publish_document_to_network.return_value = 7

        published = await publish_document_to_network(
            page,
            42,
            p2p_node=p2p_node,
        )

        assert published == 7
        p2p_node.publish_document_to_network.assert_awaited_once_with(
            42,
            page.url,
            page.title,
            page.text,
        )

    @pytest.mark.asyncio
    async def test_publish_document_skips_duplicates(self) -> None:
        page = _make_page()
        p2p_node = AsyncMock()

        published = await publish_document_to_network(
            page,
            None,
            p2p_node=p2p_node,
        )

        assert published == 0
        p2p_node.publish_document_to_network.assert_not_called()

    @pytest.mark.asyncio
    async def test_republish_local_index_batches_documents(self) -> None:
        store = MagicMock()
        store.get_documents_for_publish.side_effect = [
            [
                {
                    "doc_id": 1,
                    "url": "https://a.example",
                    "title": "A",
                    "text": "Python search",
                }
            ],
            [
                {
                    "doc_id": 2,
                    "url": "https://b.example",
                    "title": "B",
                    "text": "Distributed search",
                }
            ],
            [],
        ]
        distributed_index = AsyncMock()
        distributed_index.publish_batch.return_value = 3

        published = await republish_local_index(
            store,
            distributed_index=distributed_index,
            batch_size=1,
        )

        assert published == 6
        assert distributed_index.publish_batch.await_count == 2


# ─── FetchPageResult ─────────────────────────────────────


class TestFetchPageResult:
    def test_defaults(self) -> None:
        r = FetchPageResult(success=True)
        assert r.title == ""
        assert r.url == ""
        assert r.text == ""
        assert r.is_cached is False
        assert r.is_stale is False
        assert r.is_paywall is False
        assert r.error is None

    def test_frozen(self) -> None:
        r = FetchPageResult(success=True, title="T")
        with pytest.raises(AttributeError):
            r.title = "X"  # type: ignore[misc]


# ─── fetch_page (sync cache lookup) ──────────────────────


class TestFetchPage:
    def test_returns_cached_doc(self) -> None:
        doc = MagicMock()
        doc.title = "Cached"
        doc.url = "https://example.com"
        doc.text = "cached text"
        doc.crawled_at = time.time()  # fresh
        store = _mock_store(doc=doc)
        worker = _mock_worker()

        result = fetch_page(
            "https://example.com",
            store=store,
            worker=worker,
        )
        assert result.success is True
        assert result.is_cached is True
        assert result.title == "Cached"

    def test_marks_stale_doc(self) -> None:
        doc = MagicMock()
        doc.title = "Old"
        doc.url = "https://example.com"
        doc.text = "old text"
        doc.crawled_at = time.time() - 1_000_000  # very old
        store = _mock_store(doc=doc)
        worker = _mock_worker()

        result = fetch_page(
            "https://example.com",
            store=store,
            worker=worker,
            cache_ttl_seconds=3600,
        )
        assert result.is_stale is True

    def test_not_cached_returns_error(self) -> None:
        store = _mock_store(doc=None)
        worker = _mock_worker()
        result = fetch_page(
            "https://example.com",
            store=store,
            worker=worker,
        )
        assert result.success is False
        assert result.error == "not_cached"

    def test_ssrf_blocked(self) -> None:
        store = _mock_store()
        worker = _mock_worker()
        result = fetch_page(
            "http://169.254.169.254/latest/meta-data",
            store=store,
            worker=worker,
        )
        assert result.success is False
        assert "blocked" in (result.error or "")


# ─── fetch_page_async ────────────────────────────────────


class TestFetchPageAsync:
    @pytest.mark.asyncio
    async def test_returns_cached_doc(self) -> None:
        doc = MagicMock()
        doc.title = "Cached"
        doc.url = "https://example.com"
        doc.text = "cached text"
        doc.crawled_at = time.time()
        store = _mock_store(doc=doc)
        worker = _mock_worker()

        result = await fetch_page_async(
            "https://example.com",
            store=store,
            worker=worker,
        )
        assert result.success is True
        assert result.is_cached is True

    @pytest.mark.asyncio
    async def test_live_crawl_on_miss(self) -> None:
        store = _mock_store(doc=None)
        worker = _mock_worker()
        page = _make_page(text="live content")
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.error = None
        worker.crawl_url.return_value = crawl_result

        result = await fetch_page_async(
            "https://example.com",
            store=store,
            worker=worker,
        )
        assert result.success is True
        assert result.is_cached is False
        assert "live content" in result.text
        store.add_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_paywall_http_402(self) -> None:
        store = _mock_store(doc=None)
        worker = _mock_worker()
        crawl_result = MagicMock()
        crawl_result.success = False
        crawl_result.error = "http_402"
        worker.crawl_url.return_value = crawl_result

        result = await fetch_page_async(
            "https://paywall.example.com",
            store=store,
            worker=worker,
        )
        assert result.success is False
        assert result.is_paywall is True
        assert "paywall" in (result.error or "")

    @pytest.mark.asyncio
    async def test_paywall_content_detection(self) -> None:
        store = _mock_store(doc=None)
        worker = _mock_worker()
        page = _make_page(text="Please subscribe to continue reading.")
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.error = None
        worker.crawl_url.return_value = crawl_result

        result = await fetch_page_async(
            "https://example.com",
            store=store,
            worker=worker,
        )
        assert result.success is True
        assert result.is_paywall is True

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self) -> None:
        store = _mock_store()
        worker = _mock_worker()
        result = await fetch_page_async(
            "http://10.0.0.1/secret",
            store=store,
            worker=worker,
        )
        assert result.success is False
        assert "blocked" in (result.error or "")


# ─── CrawlAndIndexResult ─────────────────────────────────


class TestCrawlAndIndexResult:
    def test_defaults(self) -> None:
        r = CrawlAndIndexResult(success=True)
        assert r.title == ""
        assert r.links_discovered == 0
        assert r.error is None


# ─── crawl_and_index ─────────────────────────────────────


class TestCrawlAndIndex:
    @pytest.mark.asyncio
    async def test_successful_crawl(self) -> None:
        worker = _mock_worker()
        store = _mock_store()
        page = _make_page(text="indexed content")
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.discovered_links = ["https://a.com", "https://b.com"]
        crawl_result.elapsed_ms = 123.4
        worker.crawl_url.return_value = crawl_result

        result = await crawl_and_index(
            "https://example.com",
            worker=worker,
            store=store,
        )
        assert result.success is True
        assert result.title == "Example"
        assert result.links_discovered == 2
        store.add_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_crawl_publishes_to_network(self) -> None:
        worker = _mock_worker()
        store = _mock_store()
        p2p_node = AsyncMock()
        p2p_node.publish_document_to_network.return_value = 4
        page = _make_page(text="distributed content")
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.discovered_links = []
        crawl_result.elapsed_ms = 12.0
        worker.crawl_url.return_value = crawl_result

        result = await crawl_and_index(
            "https://example.com",
            worker=worker,
            store=store,
            p2p_node=p2p_node,
        )

        assert result.success is True
        p2p_node.publish_document_to_network.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_link_graph(self) -> None:
        worker = _mock_worker()
        store = _mock_store()
        link_graph = MagicMock()
        page = _make_page()
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.discovered_links = ["https://a.com"]
        crawl_result.elapsed_ms = 50.0
        worker.crawl_url.return_value = crawl_result

        await crawl_and_index(
            "https://example.com",
            worker=worker,
            store=store,
            link_graph=link_graph,
        )
        link_graph.add_links.assert_called_once_with(
            "https://example.com",
            ["https://a.com"],
        )

    @pytest.mark.asyncio
    async def test_failed_crawl(self) -> None:
        worker = _mock_worker()
        store = _mock_store()
        crawl_result = MagicMock()
        crawl_result.success = False
        crawl_result.page = None
        crawl_result.error = "http_404"
        worker.crawl_url.return_value = crawl_result

        result = await crawl_and_index(
            "https://example.com",
            worker=worker,
            store=store,
        )
        assert result.success is False
        assert result.error == "http_404"
        store.add_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_recrawl(self) -> None:
        worker = _mock_worker()
        store = _mock_store()
        page = _make_page()
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.discovered_links = []
        crawl_result.elapsed_ms = 10.0
        worker.crawl_url.return_value = crawl_result

        await crawl_and_index(
            "https://example.com",
            worker=worker,
            store=store,
            force=True,
        )
        worker.crawl_url.assert_called_once_with(
            "https://example.com",
            depth=0,
            force=True,
        )

    @pytest.mark.asyncio
    async def test_vector_store_indexed(self) -> None:
        worker = _mock_worker()
        store = _mock_store()
        vs = MagicMock()
        page = _make_page()
        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = page
        crawl_result.discovered_links = []
        crawl_result.elapsed_ms = 5.0
        worker.crawl_url.return_value = crawl_result

        await crawl_and_index(
            "https://example.com",
            worker=worker,
            store=store,
            vector_store=vs,
        )
        vs.add_document.assert_called_once()
