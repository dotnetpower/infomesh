"""Tests for search query parsing and local search."""

from __future__ import annotations

from infomesh.index.local_store import LocalStore
from infomesh.search.extended import SummaryCache, translate_query_keywords
from infomesh.search.query import QueryResult, _sanitize_fts_query, search_local


class TestSanitizeQuery:
    """Tests for FTS5 query sanitization."""

    def test_normal_query(self) -> None:
        assert _sanitize_fts_query("python async") == "python async"

    def test_special_chars(self) -> None:
        result = _sanitize_fts_query('python "async" (patterns)')
        assert '"' not in result
        assert "(" not in result
        assert ")" not in result

    def test_empty_after_sanitize(self) -> None:
        result = _sanitize_fts_query("***")
        assert result  # Should not be empty


class TestSearchLocal:
    """Tests for local search orchestration."""

    def test_search_returns_query_result(self) -> None:
        store = LocalStore()
        store.add_document(
            url="https://example.com/test",
            title="Test Page",
            text="This is a test page about Python programming language features.",
            raw_html_hash="h1",
            text_hash="t1",
        )

        result = search_local(store, "python")
        assert isinstance(result, QueryResult)
        assert result.source == "local"
        assert result.elapsed_ms >= 0
        assert len(result.results) == 1

        store.close()

    def test_search_empty_index(self) -> None:
        store = LocalStore()
        result = search_local(store, "anything")
        assert result.total == 0
        assert result.results == []
        store.close()


class TestSearchExtensions:
    def test_summary_cache_eviction(self) -> None:
        cache = SummaryCache(max_entries=2)
        cache.put("q1", "s1", [])
        cache.put("q2", "s2", [])
        cache.put("q3", "s3", [])
        assert cache.size == 2
        assert cache.get("q1") is None

    def test_translate_korean_keywords(self) -> None:
        terms = translate_query_keywords("파이썬 설치 오류", "ko")
        assert "install" in terms
        assert "error" in terms
        assert translate_query_keywords("hello", "xx") == []
