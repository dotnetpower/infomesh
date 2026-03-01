"""Tests for new features added for AI Agent readiness.

Covers:
  - JSON formatters (format_fts_results_json, etc.)
  - Search filters (language, date_from, date_to, domains)
  - Pagination (offset)
  - LocalStore.suggest()
  - Image alt text extraction
  - MCP helper classes (_AnalyticsTracker, _WebhookRegistry, _SearchSession)
  - MCP auth (_check_api_key)
  - MCP filter extraction (_extract_filters)
  - Admin API /readiness, /analytics endpoints
  - API key auth middleware
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from infomesh.api.local_api import AdminState, create_admin_app
from infomesh.config import Config
from infomesh.crawler.parser import ParsedPage, _extract_image_alts
from infomesh.index.local_store import LocalStore
from infomesh.mcp.server import (
    _AnalyticsTracker,
    _check_api_key,
    _extract_filters,
    _SearchSession,
    _WebhookRegistry,
)
from infomesh.search.formatter import (
    format_fts_results_json,
)
from infomesh.search.query import search_local

# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture()
def store() -> LocalStore:
    """In-memory LocalStore with sample docs."""
    s = LocalStore()
    s.add_document(
        url="https://example.com/python",
        title="Python Guide",
        text="Python is a great programming language",
        raw_html_hash="h1",
        text_hash="t1",
        language="en",
    )
    s.add_document(
        url="https://docs.kr/intro",
        title="소개 페이지",
        text="한국어로 작성된 문서입니다",
        raw_html_hash="h2",
        text_hash="t2",
        language="ko",
    )
    s.add_document(
        url="https://other.org/rust",
        title="Rust Guide",
        text="Rust is a systems programming language",
        raw_html_hash="h3",
        text_hash="t3",
        language="en",
    )
    return s


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    from infomesh.config import IndexConfig, NodeConfig

    return Config(
        node=NodeConfig(data_dir=tmp_path),
        index=IndexConfig(db_path=tmp_path / "index.db"),
    )


@pytest.fixture()
def client(config: Config) -> TestClient:
    app = create_admin_app(config=config)
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
# JSON Formatter Tests
# ═══════════════════════════════════════════════════════════


class TestJsonFormatters:
    """Test structured JSON output for search results."""

    def test_fts_results_json_structure(self, store: LocalStore) -> None:
        result = search_local(store, "python", limit=5)
        text = format_fts_results_json(result)
        data = json.loads(text)
        assert "total" in data
        assert "elapsed_ms" in data
        assert "source" in data
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_fts_results_json_result_fields(self, store: LocalStore) -> None:
        result = search_local(store, "programming", limit=5)
        text = format_fts_results_json(result)
        data = json.loads(text)
        assert len(data["results"]) >= 1
        r = data["results"][0]
        assert "url" in r
        assert "title" in r
        assert "domain" in r
        assert "snippet" in r
        assert "score" in r
        assert "scores" in r

    def test_fts_results_json_snippet_length(self, store: LocalStore) -> None:
        result = search_local(store, "python", limit=5)
        text = format_fts_results_json(result, max_snippet=10)
        data = json.loads(text)
        for r in data["results"]:
            assert len(r["snippet"]) <= 10

    def test_fts_results_json_empty(self, store: LocalStore) -> None:
        result = search_local(store, "nonexistent_xyz_123", limit=5)
        text = format_fts_results_json(result)
        data = json.loads(text)
        assert data["results"] == []
        assert data["total"] == 0


# ═══════════════════════════════════════════════════════════
# Search Filter Tests
# ═══════════════════════════════════════════════════════════


class TestSearchFilters:
    """Test language, date, domain filtering in LocalStore.search()."""

    def test_language_filter(self, store: LocalStore) -> None:
        # Only English results
        results = store.search("programming", language="en")
        assert len(results) >= 1
        for r in results:
            assert r.language == "en"

    def test_language_filter_ko(self, store: LocalStore) -> None:
        # FTS5 default tokenizer: Korean tokens split by space
        results = store.search("한국어로", language="ko")
        assert len(results) >= 1
        for r in results:
            assert r.language == "ko"

    def test_language_filter_excludes(self, store: LocalStore) -> None:
        # Korean query on English filter should find nothing
        results = store.search("한국어로", language="en")
        assert len(results) == 0

    def test_include_domains(self, store: LocalStore) -> None:
        results = store.search("programming", include_domains=["example.com"])
        assert len(results) >= 1
        for r in results:
            assert "example.com" in r.url

    def test_exclude_domains(self, store: LocalStore) -> None:
        results = store.search("programming", exclude_domains=["example.com"])
        for r in results:
            assert "example.com" not in r.url

    def test_date_from_filter(self, store: LocalStore) -> None:
        # All docs were just added, so a future date should exclude them
        future_ts = time.time() + 86400
        results = store.search("programming", date_from=future_ts)
        assert len(results) == 0

    def test_date_to_filter(self, store: LocalStore) -> None:
        # A past date should exclude all recent docs
        past_ts = 1000.0
        results = store.search("programming", date_to=past_ts)
        assert len(results) == 0

    def test_date_range_includes_all(self, store: LocalStore) -> None:
        # Wide range should include everything
        results = store.search(
            "programming",
            date_from=0.0,
            date_to=time.time() + 86400,
        )
        assert len(results) >= 1


# ═══════════════════════════════════════════════════════════
# Pagination Tests
# ═══════════════════════════════════════════════════════════


class TestPagination:
    """Test offset-based pagination."""

    def test_offset_skips_results(self) -> None:
        s = LocalStore()
        for i in range(10):
            s.add_document(
                url=f"https://example.com/page{i}",
                title=f"Page {i}",
                text=f"content about search engine technology item {i}",
                raw_html_hash=f"rh{i}",
                text_hash=f"th{i}",
            )
        all_results = s.search("search", limit=10, offset=0)
        offset_results = s.search("search", limit=10, offset=5)
        assert len(all_results) == 10
        assert len(offset_results) == 5

    def test_offset_beyond_results(self) -> None:
        s = LocalStore()
        s.add_document(
            url="https://example.com/one",
            title="One",
            text="single document about testing",
            raw_html_hash="r1",
            text_hash="t1",
        )
        results = s.search("testing", offset=100)
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════
# Suggest Tests
# ═══════════════════════════════════════════════════════════


class TestSuggest:
    """Test LocalStore.suggest() autocomplete."""

    def test_basic_suggest(self, store: LocalStore) -> None:
        results = store.suggest("Pyth")
        assert any("Python" in s for s in results)

    def test_suggest_case_insensitive(self, store: LocalStore) -> None:
        results = store.suggest("python")
        assert len(results) >= 1

    def test_suggest_no_match(self, store: LocalStore) -> None:
        results = store.suggest("xyznonexistent")
        assert results == []

    def test_suggest_limit(self, store: LocalStore) -> None:
        results = store.suggest("", limit=1)
        assert len(results) <= 1

    def test_suggest_sanitizes_wildcards(self, store: LocalStore) -> None:
        # Should not crash with SQL wildcards
        results = store.suggest("test%_string")
        assert isinstance(results, list)


# ═══════════════════════════════════════════════════════════
# Image Alt Text Extraction Tests
# ═══════════════════════════════════════════════════════════


class TestImageAltExtraction:
    """Test _extract_image_alts() and ParsedPage.image_alt_texts."""

    def test_extracts_alt_text(self) -> None:
        html = '<img src="a.png" alt="Beautiful landscape">'
        alts = _extract_image_alts(html)
        assert "Beautiful landscape" in alts

    def test_multiple_images(self) -> None:
        html = '<img alt="First image" src="1.png"><img alt="Second image" src="2.png">'
        alts = _extract_image_alts(html)
        assert len(alts) == 2

    def test_dedup_alt_text(self) -> None:
        html = '<img alt="Same text" src="1.png"><img alt="Same text" src="2.png">'
        alts = _extract_image_alts(html)
        assert len(alts) == 1

    def test_skips_short_alt(self) -> None:
        # Alt text < 3 chars should be skipped
        html = '<img alt="Hi" src="x.png">'
        alts = _extract_image_alts(html)
        assert len(alts) == 0

    def test_empty_html(self) -> None:
        alts = _extract_image_alts("")
        assert alts == []

    def test_no_img_tags(self) -> None:
        alts = _extract_image_alts("<p>No images here</p>")
        assert alts == []

    def test_parsed_page_default_image_alt_texts(self) -> None:
        page = ParsedPage(
            url="https://example.com",
            title="Test",
            text="Content",
            language="en",
            raw_html_hash="abc",
            text_hash="def",
        )
        assert page.image_alt_texts == ()


# ═══════════════════════════════════════════════════════════
# MCP Helper Class Tests
# ═══════════════════════════════════════════════════════════


class TestAnalyticsTracker:
    """Test _AnalyticsTracker in-memory analytics."""

    def test_initial_values(self) -> None:
        tracker = _AnalyticsTracker()
        assert tracker.total_searches == 0
        assert tracker.total_crawls == 0
        assert tracker.total_fetches == 0
        assert tracker.avg_latency_ms == 0.0

    @pytest.mark.asyncio
    async def test_record_search(self) -> None:
        tracker = _AnalyticsTracker()
        await tracker.record_search(100.0)
        await tracker.record_search(200.0)
        assert tracker.total_searches == 2
        assert tracker.avg_latency_ms == 150.0

    @pytest.mark.asyncio
    async def test_record_crawl(self) -> None:
        tracker = _AnalyticsTracker()
        await tracker.record_crawl()
        await tracker.record_crawl()
        assert tracker.total_crawls == 2

    @pytest.mark.asyncio
    async def test_record_fetch(self) -> None:
        tracker = _AnalyticsTracker()
        await tracker.record_fetch()
        assert tracker.total_fetches == 1

    @pytest.mark.asyncio
    async def test_to_dict(self) -> None:
        tracker = _AnalyticsTracker()
        await tracker.record_search(50.0)
        await tracker.record_crawl()
        await tracker.record_fetch()
        d = tracker.to_dict()
        assert d["total_searches"] == 1
        assert d["total_crawls"] == 1
        assert d["total_fetches"] == 1
        assert d["avg_latency_ms"] == 50.0


class TestWebhookRegistry:
    """Test _WebhookRegistry URL management."""

    def test_register(self) -> None:
        reg = _WebhookRegistry()
        reg.register("https://hooks.example.com/1")
        assert "https://hooks.example.com/1" in reg.urls

    def test_register_dedup(self) -> None:
        reg = _WebhookRegistry()
        reg.register("https://hooks.example.com/1")
        reg.register("https://hooks.example.com/1")
        assert len(reg.urls) == 1

    def test_unregister(self) -> None:
        reg = _WebhookRegistry()
        reg.register("https://hooks.example.com/1")
        ok = reg.unregister("https://hooks.example.com/1")
        assert ok is True
        assert len(reg.urls) == 0

    def test_unregister_missing(self) -> None:
        reg = _WebhookRegistry()
        ok = reg.unregister("https://nonexistent.com")
        assert ok is False

    def test_urls_returns_copy(self) -> None:
        reg = _WebhookRegistry()
        reg.register("https://hooks.example.com/1")
        urls = reg.urls
        urls.append("bogus")
        assert len(reg.urls) == 1


class TestSearchSession:
    """Test _SearchSession dataclass."""

    def test_default_values(self) -> None:
        s = _SearchSession()
        assert s.last_query == ""
        assert s.last_results == ""
        assert s.updated_at == 0.0

    def test_fields_mutable(self) -> None:
        s = _SearchSession()
        s.last_query = "test query"
        s.last_results = "some results"
        s.updated_at = time.time()
        assert s.last_query == "test query"


# ═══════════════════════════════════════════════════════════
# MCP Auth & Filter Tests
# ═══════════════════════════════════════════════════════════


class TestCheckApiKey:
    """Test _check_api_key authentication."""

    def test_no_key_required(self) -> None:
        err = _check_api_key({"query": "hi"}, None)
        assert err is None

    def test_valid_key(self) -> None:
        args: dict = {"api_key": "secret123", "query": "hi"}
        err = _check_api_key(args, "secret123")
        assert err is None
        # api_key should NOT be removed (non-mutating .get)
        assert "api_key" in args

    def test_invalid_key(self) -> None:
        args: dict = {"api_key": "wrong", "query": "hi"}
        err = _check_api_key(args, "secret123")
        assert err is not None
        assert "invalid" in err.lower()

    def test_missing_key(self) -> None:
        args: dict = {"query": "hi"}
        err = _check_api_key(args, "secret123")
        assert err is not None


class TestExtractFilters:
    """Test _extract_filters helper."""

    def test_empty_args(self) -> None:
        f = _extract_filters({})
        assert f == {}

    def test_language(self) -> None:
        f = _extract_filters({"language": "en"})
        assert f["language"] == "en"

    def test_date_range(self) -> None:
        f = _extract_filters({"date_from": 1000.0, "date_to": 2000.0})
        assert f["date_from"] == 1000.0
        assert f["date_to"] == 2000.0

    def test_domains(self) -> None:
        f = _extract_filters(
            {
                "include_domains": ["a.com"],
                "exclude_domains": ["b.com"],
            }
        )
        assert f["include_domains"] == ["a.com"]
        assert f["exclude_domains"] == ["b.com"]

    def test_ignores_invalid_types(self) -> None:
        f = _extract_filters(
            {
                "language": 123,
                "include_domains": "not-a-list",
            }
        )
        assert "language" not in f
        assert "include_domains" not in f


# ═══════════════════════════════════════════════════════════
# Admin API Tests
# ═══════════════════════════════════════════════════════════


class TestReadinessEndpoint:
    """Test /readiness probe."""

    def test_readiness_not_ready(self, client: TestClient) -> None:
        resp = client.get("/readiness")
        # DB doesn't exist yet → 503
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"

    def test_readiness_ready(self, config: Config) -> None:
        # Create a real DB
        store = LocalStore(db_path=config.index.db_path)
        store.close()

        app = create_admin_app(config=config)
        c = TestClient(app)
        resp = c.get("/readiness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


class TestAnalyticsEndpoint:
    """Test /analytics endpoint."""

    def test_analytics_initial(self, client: TestClient) -> None:
        resp = client.get("/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_searches"] == 0
        assert data["total_crawls"] == 0
        assert data["total_fetches"] == 0
        assert data["avg_latency_ms"] == 0.0
        assert "uptime_seconds" in data


class TestApiKeyMiddleware:
    """Test API key authentication in admin middleware."""

    def test_no_key_configured(self, config: Config) -> None:
        # Without INFOMESH_API_KEY env var, all requests pass
        app = create_admin_app(config=config)
        c = TestClient(app)
        resp = c.get("/health")
        assert resp.status_code == 200

    def test_valid_key(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INFOMESH_API_KEY", "test-key-123")
        app = create_admin_app(config=config)
        c = TestClient(app)
        resp = c.get("/health", headers={"x-api-key": "test-key-123"})
        assert resp.status_code == 200

    def test_invalid_key(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INFOMESH_API_KEY", "test-key-123")
        app = create_admin_app(config=config)
        c = TestClient(app)
        resp = c.get("/health", headers={"x-api-key": "wrong"})
        assert resp.status_code == 401

    def test_missing_key(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INFOMESH_API_KEY", "test-key-123")
        app = create_admin_app(config=config)
        c = TestClient(app)
        resp = c.get("/health")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# AdminState analytics methods
# ═══════════════════════════════════════════════════════════


class TestAdminState:
    """Test AdminState analytics tracking methods."""

    def test_record_search(self, config: Config) -> None:
        st = AdminState(config=config)
        st.record_search(100.0)
        st.record_search(200.0)
        assert st.total_searches == 2
        assert st.avg_latency_ms == 150.0

    def test_record_crawl(self, config: Config) -> None:
        st = AdminState(config=config)
        st.record_crawl()
        assert st.total_crawls == 1

    def test_record_fetch(self, config: Config) -> None:
        st = AdminState(config=config)
        st.record_fetch()
        assert st.total_fetches == 1
