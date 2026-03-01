"""Tests for infomesh.search.rag — RAG output formatting."""

from __future__ import annotations

from infomesh.index.ranking import RankedResult
from infomesh.search.rag import (
    build_cot_rerank_prompt,
    build_summary_prompt,
    compute_toxicity_score,
    extract_answers,
    extract_entities,
    filter_by_toxicity,
    format_rag_output,
)


def _make_result(
    url: str = "https://example.com/page",
    title: str = "Test Page",
    snippet: str = "Python is a programming language created by Guido.",
    score: float = 0.8,
) -> RankedResult:
    return RankedResult(
        doc_id=f"doc_{hash(url) % 10000}",
        url=url,
        title=title,
        snippet=snippet,
        bm25_score=score,
        freshness_score=0.5,
        trust_score=0.5,
        authority_score=0.5,
        combined_score=score,
        crawled_at=1_700_000_000.0,
        peer_id=None,
    )


class TestFormatRagOutput:
    def test_basic_rag(self) -> None:
        results = [_make_result(), _make_result(url="https://b.com/2")]
        rag = format_rag_output("python language", results)
        assert rag.total_results == 2
        assert len(rag.chunks) == 2

    def test_empty_results(self) -> None:
        rag = format_rag_output("test", [])
        assert rag.total_results == 0
        assert rag.chunks == []


class TestExtractAnswers:
    def test_extracts_from_snippet(self) -> None:
        results = [
            _make_result(
                snippet="Python is a high-level programming language. "
                "It was created in 1991.",
            )
        ]
        answers = extract_answers("What is Python?", results)
        assert isinstance(answers, list)

    def test_empty_results(self) -> None:
        assert extract_answers("test", []) == []


class TestBuildSummaryPrompt:
    def test_prompt_structure(self) -> None:
        results = [_make_result()]
        prompt = build_summary_prompt("python language", results)
        assert "python language" in prompt
        assert "Search Results" in prompt or "Result" in prompt


class TestExtractEntities:
    def test_extract_tech_terms(self) -> None:
        text = "Python 3.12 and JavaScript ES2024 are popular."
        entities = extract_entities(text)
        assert isinstance(entities, list)

    def test_empty_text(self) -> None:
        assert extract_entities("") == []


class TestToxicityFilter:
    def test_clean_text(self) -> None:
        score = compute_toxicity_score("Python is a great language.")
        assert score < 0.5

    def test_filtering(self) -> None:
        results = [
            _make_result(snippet="A clean and informative text."),
            _make_result(snippet="This is a normal document."),
        ]
        filtered = filter_by_toxicity(results, threshold=0.9)
        assert len(filtered) >= 1


class TestBuildCotRerankPrompt:
    def test_prompt_structure(self) -> None:
        results = [_make_result(), _make_result(url="https://b.com")]
        prompt = build_cot_rerank_prompt("python", results)
        assert "python" in prompt
        assert "Step" in prompt or "step" in prompt
