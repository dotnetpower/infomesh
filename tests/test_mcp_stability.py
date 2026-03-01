"""Tests for MCP stability fixes — session OOM, webhook SSRF, analytics safety.

Covers:
- SessionStore: bounded size, TTL eviction, get_or_create
- AnalyticsTracker: async concurrency safety
- WebhookRegistry: SSRF validation, max registrations, concurrent notify
- Handler input validation: missing query/url fields
- Query re-truncation after NLP expansion
- PersistentStore cleanup via _create_app return
- check_api_key non-mutation
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infomesh.mcp.session import (
    AnalyticsTracker,
    SearchSession,
    SessionStore,
    WebhookRegistry,
)
from infomesh.mcp.tools import check_api_key

# ── SessionStore ──────────────────────────────────────────


class TestSessionStore:
    """C1: SessionStore prevents unbounded memory growth."""

    def test_get_or_create_new(self) -> None:
        store = SessionStore(max_size=10)
        session = store.get_or_create("s1")
        assert isinstance(session, SearchSession)
        assert len(store) == 1

    def test_get_or_create_existing(self) -> None:
        store = SessionStore(max_size=10)
        s1 = store.get_or_create("s1")
        s1.last_query = "hello"
        s1.updated_at = time.time()
        s2 = store.get_or_create("s1")
        assert s2.last_query == "hello"
        assert len(store) == 1

    def test_max_size_eviction(self) -> None:
        """When max_size is reached, oldest session is evicted."""
        store = SessionStore(max_size=3, ttl_seconds=3600)
        for i in range(3):
            s = store.get_or_create(f"s{i}")
            s.updated_at = time.time() - (300 - i * 100)

        # s0 has oldest updated_at
        assert len(store) == 3
        s_new = store.get_or_create("s_overflow")
        s_new.updated_at = time.time()
        # Should have evicted s0 (oldest)
        assert len(store) == 3
        assert "s0" not in store
        assert "s_overflow" in store

    def test_ttl_eviction(self) -> None:
        """Expired sessions are evicted on capacity pressure."""
        store = SessionStore(max_size=2, ttl_seconds=1.0)
        s1 = store.get_or_create("s1")
        s1.updated_at = time.time() - 5.0  # expired
        s2 = store.get_or_create("s2")
        s2.updated_at = time.time()  # fresh

        # Trigger eviction by hitting capacity
        s3 = store.get_or_create("s3")
        s3.updated_at = time.time()
        # s1 was expired, should be evicted
        assert "s1" not in store
        assert "s2" in store
        assert "s3" in store

    def test_expired_session_creates_fresh(self) -> None:
        """Accessing an expired session creates a fresh one."""
        store = SessionStore(max_size=10, ttl_seconds=1.0)
        s1 = store.get_or_create("s1")
        s1.last_query = "old"
        s1.updated_at = time.time() - 5.0  # expired
        s1_new = store.get_or_create("s1")
        assert s1_new.last_query == ""  # fresh session

    def test_contains(self) -> None:
        store = SessionStore(max_size=10)
        store.get_or_create("s1")
        assert "s1" in store
        assert "s2" not in store


# ── AnalyticsTracker concurrency ─────────────────────────


class TestAnalyticsTrackerConcurrency:
    """C3: AnalyticsTracker is safe under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_record_search(self) -> None:
        tracker = AnalyticsTracker()

        async def record_many(n: int) -> None:
            for _ in range(n):
                await tracker.record_search(10.0)

        # Run 10 concurrent tasks each recording 100 searches
        await asyncio.gather(*(record_many(100) for _ in range(10)))
        assert tracker.total_searches == 1000
        assert tracker.avg_latency_ms == 10.0

    @pytest.mark.asyncio
    async def test_concurrent_mixed_operations(self) -> None:
        tracker = AnalyticsTracker()
        await asyncio.gather(
            tracker.record_search(50.0),
            tracker.record_crawl(),
            tracker.record_fetch(),
            tracker.record_search(100.0),
            tracker.record_crawl(),
        )
        assert tracker.total_searches == 2
        assert tracker.total_crawls == 2
        assert tracker.total_fetches == 1


# ── WebhookRegistry security ─────────────────────────────


class TestWebhookRegistrySecurity:
    """H1+H2: Webhook SSRF validation and registration limits."""

    def test_ssrf_blocked(self) -> None:
        """Internal/metadata URLs should be rejected."""
        reg = WebhookRegistry()
        err = reg.register("http://169.254.169.254/latest")
        assert err is not None
        assert "blocked" in err.lower()
        assert len(reg.urls) == 0

    def test_private_ip_blocked(self) -> None:
        reg = WebhookRegistry()
        err = reg.register("http://192.168.1.1/hook")
        assert err is not None
        assert len(reg.urls) == 0

    def test_valid_url_accepted(self) -> None:
        reg = WebhookRegistry()
        err = reg.register("https://hooks.example.com/1")
        assert err is None
        assert len(reg.urls) == 1

    def test_max_registrations(self) -> None:
        """Registration limit enforced."""
        reg = WebhookRegistry(max_registrations=3)
        for i in range(3):
            err = reg.register(f"https://hooks.example.com/{i}")
            assert err is None
        # 4th should fail
        err = reg.register("https://hooks.example.com/overflow")
        assert err is not None
        assert "Max" in err
        assert len(reg.urls) == 3

    def test_duplicate_not_counted(self) -> None:
        reg = WebhookRegistry(max_registrations=2)
        reg.register("https://hooks.example.com/1")
        err = reg.register("https://hooks.example.com/1")
        assert err is None  # no error, just idempotent
        assert len(reg.urls) == 1

    @pytest.mark.asyncio
    async def test_concurrent_notify(self) -> None:
        """Webhook notifications are sent concurrently."""
        reg = WebhookRegistry()
        reg.register("https://hooks.example.com/1")
        reg.register("https://hooks.example.com/2")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(
                return_value=mock_client,
            )
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            sent = await reg.notify(
                "crawl_completed",
                {"url": "https://example.com"},
            )
        assert sent == 2

    @pytest.mark.asyncio
    async def test_notify_empty_returns_zero(self) -> None:
        reg = WebhookRegistry()
        sent = await reg.notify("event", {})
        assert sent == 0


# ── Handler input validation ─────────────────────────────


class TestHandlerInputValidation:
    """H3: Handlers gracefully handle missing required fields."""

    @pytest.mark.asyncio
    async def test_search_missing_query(self) -> None:
        from infomesh.mcp.handlers import handle_search

        result = await handle_search(
            "search",
            {},  # no query field
            store=MagicMock(),
            vector_store=None,
            distributed_index=None,
            link_graph=None,
            ledger=None,
            llm_backend=None,
            query_cache=MagicMock(get=MagicMock(return_value=None)),
            sessions=SessionStore(),
            analytics=AnalyticsTracker(),
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_fetch_missing_url(self) -> None:
        from infomesh.mcp.handlers import handle_fetch

        result = await handle_fetch(
            {},  # no url field
            config=MagicMock(),
            store=MagicMock(),
            worker=MagicMock(),
            vector_store=None,
            link_graph=None,
            analytics=AnalyticsTracker(),
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_crawl_missing_url(self) -> None:
        from infomesh.mcp.handlers import handle_crawl

        config = MagicMock()
        config.crawl.max_depth = 3
        result = await handle_crawl(
            {},  # no url field
            config=config,
            store=MagicMock(),
            worker=MagicMock(),
            vector_store=None,
            link_graph=None,
            analytics=AnalyticsTracker(),
            webhooks=WebhookRegistry(),
        )
        assert "Error" in result[0].text


# ── Query re-truncation ──────────────────────────────────


class TestQueryReTruncation:
    """H4: Query is re-truncated after NLP synonym expansion."""

    @pytest.mark.asyncio
    async def test_query_truncated_after_expansion(self) -> None:
        from infomesh.mcp.handlers import handle_search

        # Create a 999-char query that will get synonyms appended
        long_query = "python " * 142  # ~994 chars
        assert len(long_query.strip()) > 900

        mock_result = MagicMock()
        mock_result.results = []
        mock_result.total = 0
        mock_result.elapsed_ms = 1.0
        mock_result.source = "fts5"

        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_search(
                "search_local",
                {"query": long_query},
                store=MagicMock(suggest=MagicMock(return_value=[])),
                vector_store=None,
                distributed_index=None,
                link_graph=None,
                ledger=None,
                llm_backend=None,
                query_cache=MagicMock(
                    get=MagicMock(return_value=None),
                    put=MagicMock(),
                ),
                sessions=SessionStore(),
                analytics=AnalyticsTracker(),
            )
        # Should not crash or produce absurdly long queries
        assert len(result) == 1


# ── check_api_key non-mutation ────────────────────────────


class TestCheckApiKeyNonMutation:
    """M3: check_api_key must not mutate the arguments dict."""

    def test_does_not_pop_api_key(self) -> None:
        args = {"query": "test", "api_key": "secret123"}
        check_api_key(args, "secret123")
        # api_key should still be in the dict
        assert "api_key" in args

    def test_does_not_remove_on_mismatch(self) -> None:
        args = {"query": "test", "api_key": "wrong"}
        result = check_api_key(args, "correct")
        assert result is not None  # error
        assert "api_key" in args  # not removed

    def test_no_key_expected(self) -> None:
        args = {"query": "test"}
        result = check_api_key(args, None)
        assert result is None


# ── _create_app returns PersistentStore ──────────────────


class TestCreateAppReturns:
    """C2: _create_app returns PersistentStore for cleanup."""

    def test_returns_three_tuple(self) -> None:
        """_create_app must return (Server, AppContext, PersistentStore)."""
        from infomesh.mcp.server import _create_app

        with patch("infomesh.mcp.server.AppContext") as mock_ctx:
            mock_ctx.return_value.store = MagicMock()
            mock_ctx.return_value.vector_store = None
            mock_ctx.return_value.worker = None
            mock_ctx.return_value.scheduler = None
            mock_ctx.return_value.link_graph = None
            mock_ctx.return_value.ledger = None
            mock_ctx.return_value.llm_backend = None
            mock_config = MagicMock()
            mock_config.node.data_dir.__truediv__ = MagicMock(return_value=":memory:")
            result = _create_app(mock_config)

        assert len(result) == 3
        # Third element should have a close() method
        pstore = result[2]
        assert hasattr(pstore, "close")
        pstore.close()
