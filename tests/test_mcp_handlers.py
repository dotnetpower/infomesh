"""Tests for infomesh.mcp.handlers — MCP tool handler implementations."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infomesh.mcp.handlers import (
    MCP_API_VERSION,
    deduct_search_cost,
    handle_batch,
    handle_crawl,
    handle_explain,
    handle_extract_answer,
    handle_fact_check,
    handle_search_rag,
    handle_stats,
    handle_status,
    handle_suggest,
    handle_web_search,
)
from infomesh.mcp.session import AnalyticsTracker, WebhookRegistry

# ─── Helpers ──────────────────────────────────────────────


def _mock_store() -> MagicMock:
    """Create a mock LocalStore with common methods."""
    store = MagicMock()
    store.get_stats.return_value = {"document_count": 100}
    store.suggest.return_value = ["python", "pytorch"]
    return store


def _mock_search_result() -> MagicMock:
    """Build a mock QueryResult from search_local."""
    r = MagicMock()
    r.url = "https://example.com"
    r.title = "Example"
    r.snippet = "Some text"
    r.combined_score = 0.95
    r.text = "Full text of example"
    r.crawled_at = 1_700_000_000.0
    r.language = "en"
    r.bm25_score = 0.5
    r.freshness_score = 0.3
    r.trust_score = 0.1
    r.authority_score = 0.05
    r.peer_id = None
    # Ensure dict-like weighted access for explain
    r.weighted = {"bm25": 0.5, "freshness": 0.3, "trust": 0.1, "authority": 0.05}
    result = MagicMock()
    result.results = [r]
    result.total = 1
    result.elapsed_ms = 5.0
    result.source = "fts5"
    return result


# ─── deduct_search_cost ──────────────────────────────────


class TestDeductSearchCost:
    def test_none_ledger(self) -> None:
        # Should not raise
        deduct_search_cost(None)

    def test_normal_deduction(self) -> None:
        ledger = MagicMock()
        allowance = MagicMock()
        allowance.search_cost = 0.1
        ledger.search_allowance.return_value = allowance
        deduct_search_cost(ledger)
        ledger.spend.assert_called_once_with(0.1, reason="search")

    def test_exception_suppressed(self) -> None:
        ledger = MagicMock()
        ledger.search_allowance.side_effect = RuntimeError("db locked")
        # Should not raise
        deduct_search_cost(ledger)


# ─── handle_suggest ──────────────────────────────────────


class TestHandleSuggest:
    def test_valid_prefix(self) -> None:
        store = _mock_store()
        result = handle_suggest(
            {"prefix": "py", "limit": 5},
            store=store,
        )
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["prefix"] == "py"
        assert "python" in data["suggestions"]
        store.suggest.assert_called_once_with("py", limit=5)

    def test_empty_prefix(self) -> None:
        store = _mock_store()
        result = handle_suggest({"prefix": ""}, store=store)
        assert "Error" in result[0].text

    def test_missing_prefix(self) -> None:
        store = _mock_store()
        result = handle_suggest({}, store=store)
        assert "Error" in result[0].text

    def test_limit_capped(self) -> None:
        store = _mock_store()
        handle_suggest({"prefix": "ab", "limit": 999}, store=store)
        store.suggest.assert_called_once_with("ab", limit=50)


# ─── handle_stats ────────────────────────────────────────


class TestHandleStats:
    def test_text_format(self) -> None:
        store = _mock_store()
        result = handle_stats(
            {"format": "text"},
            store=store,
            vector_store=None,
            link_graph=None,
            ledger=None,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        text = result[0].text
        assert "InfoMesh Node Status" in text
        assert "100" in text  # document count

    def test_json_format(self) -> None:
        store = _mock_store()
        result = handle_stats(
            {"format": "json"},
            store=store,
            vector_store=None,
            link_graph=None,
            ledger=None,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        data = json.loads(result[0].text)
        assert data["api_version"] == MCP_API_VERSION
        assert data["documents_indexed"] == 100

    def test_with_ledger(self) -> None:
        store = _mock_store()
        ledger = MagicMock()
        ledger.balance.return_value = 42.5
        allowance = MagicMock()
        allowance.search_cost = 0.05
        allowance.state = MagicMock()
        allowance.state.value = "normal"
        ledger.search_allowance.return_value = allowance

        result = handle_stats(
            {"format": "json"},
            store=store,
            vector_store=None,
            link_graph=None,
            ledger=ledger,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        data = json.loads(result[0].text)
        assert data["credits"]["balance"] == 42.5
        assert data["credits"]["state"] == "normal"

    def test_with_vector_store(self) -> None:
        store = _mock_store()
        vs = MagicMock()
        vs.get_stats.return_value = {
            "document_count": 50,
            "model": "all-MiniLM-L6-v2",
        }
        result = handle_stats(
            {"format": "json"},
            store=store,
            vector_store=vs,
            link_graph=None,
            ledger=None,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        data = json.loads(result[0].text)
        assert data["vector"]["documents"] == 50


# ─── handle_crawl ────────────────────────────────────────


class TestHandleCrawl:
    @pytest.mark.asyncio
    async def test_no_worker(self) -> None:
        config = MagicMock()
        config.crawl.max_depth = 3
        result = await handle_crawl(
            {"url": "https://example.com"},
            config=config,
            store=MagicMock(),
            worker=None,
            vector_store=None,
            link_graph=None,
            analytics=AnalyticsTracker(),
            webhooks=WebhookRegistry(),
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self) -> None:
        config = MagicMock()
        config.crawl.max_depth = 3
        result = await handle_crawl(
            {"url": "http://169.254.169.254/latest"},
            config=config,
            store=MagicMock(),
            worker=MagicMock(),
            vector_store=None,
            link_graph=None,
            analytics=AnalyticsTracker(),
            webhooks=WebhookRegistry(),
        )
        assert "blocked" in result[0].text

    @pytest.mark.asyncio
    async def test_successful_crawl(self) -> None:
        config = MagicMock()
        config.crawl.max_depth = 3

        worker = AsyncMock()
        store = MagicMock()
        store.add_document.return_value = 1

        crawl_result = MagicMock()
        crawl_result.success = True
        crawl_result.page = MagicMock()
        crawl_result.page.title = "Test"
        crawl_result.page.text = "content"
        crawl_result.page.url = "https://example.com"
        crawl_result.page.raw_html_hash = "h1"
        crawl_result.page.text_hash = "h2"
        crawl_result.page.language = "en"
        crawl_result.discovered_links = ["https://a.com"]
        crawl_result.elapsed_ms = 100.0
        worker.crawl_url.return_value = crawl_result

        result = await handle_crawl(
            {"url": "https://example.com", "depth": 1},
            config=config,
            store=store,
            worker=worker,
            vector_store=None,
            link_graph=None,
            analytics=AnalyticsTracker(),
            webhooks=WebhookRegistry(),
        )
        assert "Crawled successfully" in result[0].text


# ─── handle_batch ────────────────────────────────────────


class TestHandleBatch:
    @pytest.mark.asyncio
    async def test_empty_queries(self) -> None:
        result = await handle_batch(
            {"queries": []},
            store=_mock_store(),
            link_graph=None,
            ledger=None,
            analytics=AnalyticsTracker(),
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_invalid_queries_type(self) -> None:
        result = await handle_batch(
            {"queries": "not a list"},
            store=_mock_store(),
            link_graph=None,
            ledger=None,
            analytics=AnalyticsTracker(),
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_text_format(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_batch(
                {"queries": ["python", "rust"], "format": "text"},
                store=store,
                link_graph=None,
                ledger=None,
                analytics=AnalyticsTracker(),
            )
        text = result[0].text
        assert "Query 1" in text
        assert "Query 2" in text

    @pytest.mark.asyncio
    async def test_json_format(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_batch(
                {"queries": ["python"], "format": "json"},
                store=store,
                link_graph=None,
                ledger=None,
                analytics=AnalyticsTracker(),
            )
        data = json.loads(result[0].text)
        assert "batch_results" in data

    @pytest.mark.asyncio
    async def test_max_10_queries(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        queries = [f"q{i}" for i in range(15)]
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ) as mock_search:
            await handle_batch(
                {"queries": queries, "format": "text"},
                store=store,
                link_graph=None,
                ledger=None,
                analytics=AnalyticsTracker(),
            )
            # Should be capped at 10
            assert mock_search.call_count == 10


# ─── handle_explain ──────────────────────────────────────


class TestHandleExplain:
    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        result = await handle_explain(
            {"query": ""},
            store=_mock_store(),
            link_graph=None,
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_valid_query(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_explain(
                {"query": "python", "limit": 3},
                store=store,
                link_graph=None,
            )
        data = json.loads(result[0].text)
        assert data["query"] == "python"
        assert "results" in data


# ─── handle_search_rag ───────────────────────────────────


class TestHandleSearchRag:
    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        result = await handle_search_rag(
            {"query": ""},
            store=_mock_store(),
            link_graph=None,
            analytics=AnalyticsTracker(),
            ledger=None,
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_valid_query(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_search_rag(
                {"query": "python tutorial", "limit": 3},
                store=store,
                link_graph=None,
                analytics=AnalyticsTracker(),
                ledger=None,
            )
        data = json.loads(result[0].text)
        assert data["query"] == "python tutorial"
        assert "context_chunks" in data


# ─── handle_extract_answer ───────────────────────────────


class TestHandleExtractAnswer:
    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        result = await handle_extract_answer(
            {"query": ""},
            store=_mock_store(),
            link_graph=None,
            ledger=None,
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_valid_query(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_extract_answer(
                {"query": "what is Python"},
                store=store,
                link_graph=None,
                ledger=None,
            )
        data = json.loads(result[0].text)
        assert data["query"] == "what is Python"
        assert "answers" in data


# ─── handle_fact_check ───────────────────────────────────


class TestHandleFactCheck:
    @pytest.mark.asyncio
    async def test_empty_claim(self) -> None:
        result = await handle_fact_check(
            {"claim": ""},
            store=_mock_store(),
            link_graph=None,
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_valid_claim(self) -> None:
        store = _mock_store()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_fact_check(
                {"claim": "Python is a programming language"},
                store=store,
                link_graph=None,
            )
        data = json.loads(result[0].text)
        assert data["claim"] == "Python is a programming language"
        assert "verdict" in data
        assert "confidence" in data


# ─── handle_web_search (unified) ─────────────────────────


def _web_search_deps() -> dict[str, Any]:
    """Common kwargs for handle_web_search."""
    return {
        "config": MagicMock(
            mcp=MagicMock(
                show_attribution=True,
                max_response_chars=0,
            ),
        ),
        "store": _mock_store(),
        "vector_store": None,
        "distributed_index": None,
        "link_graph": None,
        "ledger": None,
        "llm_backend": None,
        "query_cache": MagicMock(
            get=MagicMock(return_value=None),
            put=MagicMock(),
        ),
        "sessions": MagicMock(),
        "analytics": AnalyticsTracker(),
    }


class TestHandleWebSearch:
    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        result = await handle_web_search(
            {"query": ""},
            **_web_search_deps(),
        )
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_snippets_mode(self) -> None:
        deps = _web_search_deps()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_web_search(
                {"query": "python", "top_k": 3},
                **deps,
            )
        assert len(result) == 1
        assert result[0].text  # non-empty

    @pytest.mark.asyncio
    async def test_explain_mode(self) -> None:
        deps = _web_search_deps()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_web_search(
                {
                    "query": "python",
                    "explain": True,
                },
                **deps,
            )
        data = json.loads(result[0].text)
        assert "results" in data
        assert data["query"] == "python"

    @pytest.mark.asyncio
    async def test_rag_chunk_mode(self) -> None:
        deps = _web_search_deps()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_web_search(
                {
                    "query": "python",
                    "chunk_size": 500,
                },
                **deps,
            )
        data = json.loads(result[0].text)
        assert "context_chunks" in data

    @pytest.mark.asyncio
    async def test_summary_mode(self) -> None:
        deps = _web_search_deps()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_web_search(
                {
                    "query": "python",
                    "answer_mode": "summary",
                },
                **deps,
            )
        data = json.loads(result[0].text)
        assert "answers" in data

    @pytest.mark.asyncio
    async def test_local_only(self) -> None:
        deps = _web_search_deps()
        mock_result = _mock_search_result()
        with patch(
            "infomesh.mcp.handlers.search_local",
            return_value=mock_result,
        ):
            result = await handle_web_search(
                {
                    "query": "python",
                    "local_only": True,
                },
                **deps,
            )
        assert len(result) == 1


# ─── handle_status (unified) ─────────────────────────────


class TestHandleStatus:
    def test_returns_json(self) -> None:
        store = _mock_store()
        result = handle_status(
            {},
            store=store,
            vector_store=None,
            link_graph=None,
            ledger=None,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        data = json.loads(result[0].text)
        assert data["status"] == "ok"
        assert data["api_version"] == MCP_API_VERSION
        assert data["documents_indexed"] == 100

    def test_includes_credits(self) -> None:
        store = _mock_store()
        ledger = MagicMock()
        ledger.balance.return_value = 42.5
        allowance = MagicMock()
        allowance.search_cost = 0.05
        allowance.state = MagicMock()
        allowance.state.value = "normal"
        ledger.search_allowance.return_value = allowance

        result = handle_status(
            {},
            store=store,
            vector_store=None,
            link_graph=None,
            ledger=ledger,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        data = json.loads(result[0].text)
        assert data["credits"]["balance"] == 42.5
        assert data["credits"]["tier"] == 1

    def test_includes_ping_fields(self) -> None:
        store = _mock_store()
        result = handle_status(
            {},
            store=store,
            vector_store=None,
            link_graph=None,
            ledger=None,
            scheduler=None,
            p2p_node=None,
            distributed_index=None,
            analytics=AnalyticsTracker(),
        )
        data = json.loads(result[0].text)
        assert data["server"] == "infomesh"
        assert data["version"]
