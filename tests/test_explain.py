"""Tests for infomesh.search.explain — query explanation."""

from __future__ import annotations

from infomesh.index.ranking import RankedResult
from infomesh.search.explain import explain_query, explain_result


def _make_result(score: float = 0.8) -> RankedResult:
    return RankedResult(
        doc_id="doc1",
        url="https://example.com/page",
        title="Test Page",
        snippet="A test snippet.",
        bm25_score=0.9,
        freshness_score=0.7,
        trust_score=0.6,
        authority_score=0.5,
        combined_score=score,
        crawled_at=1_700_000_000.0,
        peer_id=None,
    )


class TestExplainResult:
    def test_basic(self) -> None:
        r = _make_result()
        exp = explain_result(r)
        assert exp.url == "https://example.com/page"
        assert exp.combined_score == r.combined_score
        assert "bm25" in exp.components
        assert "freshness" in exp.components

    def test_weighted_contributions(self) -> None:
        r = _make_result()
        exp = explain_result(r)
        # weighted values should exist for each component
        assert "bm25" in exp.weighted
        assert "trust" in exp.weighted

    def test_to_dict(self) -> None:
        r = _make_result()
        exp = explain_result(r)
        d = exp.to_dict()
        assert "url" in d
        assert "components" in d
        assert "weighted_contributions" in d


class TestExplainQuery:
    def test_basic(self) -> None:
        results = [_make_result(0.8), _make_result(0.6)]
        exp = explain_query("python tutorial", "python tutorial", results, 15.0)
        assert exp.query == "python tutorial"
        assert exp.total_results == 2
        assert exp.elapsed_ms == 15.0
        assert len(exp.results) == 2
        assert len(exp.pipeline) > 0

    def test_empty_results(self) -> None:
        exp = explain_query("test", "test", [], 5.0)
        assert exp.total_results == 0
        assert exp.results == []
