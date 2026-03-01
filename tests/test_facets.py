"""Tests for infomesh.search.facets — faceted search and highlighting."""

from __future__ import annotations

from infomesh.index.ranking import RankedResult
from infomesh.search.facets import (
    cluster_results,
    compute_facets,
    dedup_results,
    highlight_snippet,
)


def _make_result(
    url: str = "https://example.com/page",
    title: str = "Test Page",
    snippet: str = "A test snippet for the page.",
    bm25: float = 1.0,
    crawled_at: float = 1_700_000_000.0,
    domain: str = "",
) -> RankedResult:
    if not domain:
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
    return RankedResult(
        doc_id=f"doc_{hash(url) % 10000}",
        url=url,
        title=title,
        snippet=snippet,
        bm25_score=bm25,
        freshness_score=0.5,
        trust_score=0.5,
        authority_score=0.5,
        combined_score=bm25 * 0.5,
        crawled_at=crawled_at,
        peer_id=None,
    )


class TestComputeFacets:
    def test_basic_facets(self) -> None:
        results = [
            _make_result(url="https://a.com/1"),
            _make_result(url="https://a.com/2"),
            _make_result(url="https://b.com/3"),
        ]
        facets = compute_facets(results)
        assert facets.domains["a.com"] == 2
        assert facets.domains["b.com"] == 1

    def test_empty_results(self) -> None:
        facets = compute_facets([])
        assert facets.domains == {}


class TestClusterResults:
    def test_clusters_by_domain(self) -> None:
        results = [
            _make_result(url="https://a.com/1", title="Alpha 1"),
            _make_result(url="https://a.com/2", title="Alpha 2"),
            _make_result(url="https://b.com/3", title="Beta 1"),
        ]
        clusters = cluster_results(results)
        assert len(clusters) >= 1
        assert all(c.label for c in clusters)

    def test_empty(self) -> None:
        assert cluster_results([]) == []


class TestHighlightSnippet:
    def test_highlight(self) -> None:
        result = highlight_snippet(
            "Python is a great programming language",
            "python",
        )
        assert "**Python**" in result

    def test_no_match(self) -> None:
        result = highlight_snippet("Hello world", "python")
        assert result == "Hello world"

    def test_case_insensitive(self) -> None:
        result = highlight_snippet("PYTHON is great", "python")
        assert "**PYTHON**" in result


class TestDedupResults:
    def test_url_dedup(self) -> None:
        results = [
            _make_result(url="https://a.com/1", title="Alpha One"),
            _make_result(url="https://a.com/1", title="Alpha One"),  # duplicate
            _make_result(
                url="https://b.com/2",
                title="Beta Two Different",
                snippet="Completely different content here.",
            ),
        ]
        deduped = dedup_results(results)
        assert len(deduped) == 2

    def test_no_dups(self) -> None:
        results = [
            _make_result(
                url="https://a.com/1",
                title="First Article",
                snippet="First unique content.",
            ),
            _make_result(
                url="https://b.com/2",
                title="Second Article",
                snippet="Second unique content.",
            ),
        ]
        assert len(dedup_results(results)) == 2
