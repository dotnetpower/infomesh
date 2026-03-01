"""Tests for infomesh.data_quality — freshness, trust grading, citations."""

from __future__ import annotations

import time

from infomesh.data_quality import (
    FactCheckResult,
    compute_freshness_indicator,
    compute_trust_grade,
    cross_reference_results,
    extract_citations,
)
from infomesh.index.ranking import RankedResult


class TestFreshnessIndicator:
    def test_recent(self) -> None:
        fi = compute_freshness_indicator(time.time() - 3600)  # 1 hour ago
        assert fi.freshness_grade == "A"
        assert fi.age_label  # non-empty label

    def test_old(self) -> None:
        fi = compute_freshness_indicator(time.time() - 365 * 86400)  # 1 year
        assert fi.freshness_grade in ("D", "E", "F")

    def test_very_old(self) -> None:
        fi = compute_freshness_indicator(1_000_000_000.0)  # ~2001
        assert fi.freshness_grade in ("E", "F")


class TestTrustGrade:
    def test_high_trust(self) -> None:
        tg = compute_trust_grade(0.95)
        assert tg.grade == "A+"

    def test_normal_trust(self) -> None:
        tg = compute_trust_grade(0.7)
        assert tg.grade in ("A", "B", "C")

    def test_low_trust(self) -> None:
        tg = compute_trust_grade(0.2)
        assert tg.grade in ("D", "F")


class TestExtractCitations:
    def test_doi(self) -> None:
        text = "See reference 10.1000/xyz123 for more."
        citations = extract_citations(text)
        assert any(c.citation_type == "doi" for c in citations)

    def test_isbn(self) -> None:
        text = "Book ISBN 978-3-16-148410-0 is recommended."
        citations = extract_citations(text)
        assert any(c.citation_type == "isbn" for c in citations)

    def test_arxiv(self) -> None:
        text = "See arXiv:2301.12345 for the paper."
        citations = extract_citations(text)
        assert any(c.citation_type == "arxiv" for c in citations)

    def test_rfc(self) -> None:
        text = "As described in RFC 7231."
        citations = extract_citations(text)
        assert any(c.citation_type == "rfc" for c in citations)

    def test_no_citations(self) -> None:
        assert extract_citations("Plain text, no citations.") == []


def _make_result(
    snippet: str = "Python is a programming language.",
    url: str = "https://example.com",
    score: float = 0.8,
) -> RankedResult:
    return RankedResult(
        doc_id="d1",
        url=url,
        title="Test",
        snippet=snippet,
        bm25_score=score,
        freshness_score=0.5,
        trust_score=0.5,
        authority_score=0.5,
        combined_score=score,
        crawled_at=1_700_000_000.0,
        peer_id=None,
    )


class TestCrossReference:
    def test_supported_claim(self) -> None:
        results = [
            _make_result(snippet="Python was created by Guido van Rossum."),
            _make_result(
                snippet="Guido van Rossum created Python.",
                url="https://b.com",
            ),
        ]
        fc = cross_reference_results(
            "Python was created by Guido van Rossum",
            results,
        )
        assert isinstance(fc, FactCheckResult)
        assert fc.supporting_sources >= 0

    def test_no_results(self) -> None:
        fc = cross_reference_results("Some claim", [])
        assert fc.supporting_sources == 0
