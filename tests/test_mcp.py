"""Tests for MCP server tools."""

from __future__ import annotations

from infomesh.index.local_store import LocalStore
from infomesh.search.query import search_local

# --- Helper: simulated MCP tool dispatch ---


class _MockMCPDispatcher:
    """Minimal helper that mimics the MCP tool dispatch without actually
    starting a stdio server.  We test the logic that powers each tool
    directly through the library functions.
    """

    def __init__(self) -> None:
        self.store = LocalStore()  # in-memory
        self.store.add_document(
            url="https://example.com/page1",
            title="Example Page",
            text="InfoMesh is a decentralized search engine for LLMs",
            raw_html_hash="aaa",
            text_hash="bbb",
            language="en",
        )

    # -- tool: search / search_local
    def do_search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        result = search_local(self.store, query, limit=limit)
        return [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "score": r.combined_score,
            }
            for r in result.results
        ]

    # -- tool: fetch_page (from cache)
    def do_fetch_page(self, url: str) -> dict[str, object]:
        doc = self.store.get_document_by_url(url)
        if doc:
            return {
                "title": doc.title,
                "url": doc.url,
                "text": doc.text,
                "is_cached": True,
                "crawled_at": doc.crawled_at,
            }
        return {"error": f"not_found: {url}"}

    # -- tool: network_stats
    def do_network_stats(self) -> dict[str, object]:
        stats = self.store.get_stats()
        return {
            "phase": 0,
            "document_count": stats["document_count"],
        }


# --- Tests ---


def test_search_returns_results() -> None:
    """search tool should return matching results."""
    d = _MockMCPDispatcher()
    results = d.do_search("decentralized")
    assert len(results) == 1
    assert results[0]["title"] == "Example Page"
    assert results[0]["score"] > 0


def test_search_no_results() -> None:
    """search tool should return empty list for unmatched query."""
    d = _MockMCPDispatcher()
    results = d.do_search("nonexistentterm12345")
    assert results == []


def test_search_limit() -> None:
    """search tool should respect the limit parameter."""
    d = _MockMCPDispatcher()
    # Add more docs
    for i in range(5):
        d.store.add_document(
            url=f"https://example.com/extra{i}",
            title=f"Extra Page {i}",
            text=f"decentralized search engine page {i}",
            raw_html_hash=f"hash{i}",
            text_hash=f"thash{i}",
        )
    results = d.do_search("decentralized", limit=3)
    assert len(results) <= 3


def test_fetch_page_cached() -> None:
    """fetch_page should return cached content with metadata."""
    d = _MockMCPDispatcher()
    result = d.do_fetch_page("https://example.com/page1")
    assert result["is_cached"] is True
    assert result["title"] == "Example Page"
    assert "decentralized" in result["text"]
    assert result["crawled_at"] > 0


def test_fetch_page_not_found() -> None:
    """fetch_page should return error for unknown URL."""
    d = _MockMCPDispatcher()
    result = d.do_fetch_page("https://example.com/nonexistent")
    assert "error" in result


def test_network_stats() -> None:
    """network_stats should return index info."""
    d = _MockMCPDispatcher()
    stats = d.do_network_stats()
    assert stats["phase"] == 0
    assert stats["document_count"] == 1


def test_search_special_chars() -> None:
    """search tool should handle FTS5 special characters gracefully."""
    d = _MockMCPDispatcher()
    # These should not crash — special chars get sanitized
    results = d.do_search('"hello" OR (world)')
    assert isinstance(results, list)
    results = d.do_search("test*query{}")
    assert isinstance(results, list)
