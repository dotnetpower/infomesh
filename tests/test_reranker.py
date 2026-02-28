"""Tests for infomesh.search.reranker — LLM-based re-ranking."""

from __future__ import annotations

import time

import pytest

from infomesh.index.ranking import RankedResult
from infomesh.search.reranker import (
    _build_results_block,
    _parse_ranking_response,
    rerank_with_llm,
)


def _make_ranked(doc_id: str, title: str = "Test", score: float = 0.5) -> RankedResult:
    return RankedResult(
        doc_id=doc_id,
        url=f"https://example.com/{doc_id}",
        title=title,
        snippet=f"Snippet for {doc_id}",
        bm25_score=score,
        freshness_score=0.8,
        trust_score=0.5,
        authority_score=0.3,
        combined_score=score,
        crawled_at=time.time(),
    )


class TestBuildResultsBlock:
    def test_formats_numbered_list(self):
        results = [_make_ranked("a", "Alpha"), _make_ranked("b", "Beta")]
        block = _build_results_block(results)
        assert "1. [Alpha]" in block
        assert "2. [Beta]" in block

    def test_truncates_snippet(self):
        r = _make_ranked("a")
        block = _build_results_block([r], max_snippet=5)
        # Snippet should be short
        lines = block.strip().split("\n")
        assert len(lines) == 1


class TestParseRankingResponse:
    def test_valid_json_array(self):
        result = _parse_ranking_response("[3, 1, 2]", 3)
        assert result == [2, 0, 1]  # 0-based

    def test_with_surrounding_text(self):
        result = _parse_ranking_response("Here is my ranking: [2, 1, 3]\nDone.", 3)
        assert result == [1, 0, 2]

    def test_missing_indices_appended(self):
        result = _parse_ranking_response("[2]", 3)
        assert result is not None
        assert len(result) == 3
        assert result[0] == 1  # index 2 → 0-based 1
        # Missing indices 0 and 2 appended
        assert 0 in result
        assert 2 in result

    def test_out_of_range_skipped(self):
        result = _parse_ranking_response("[1, 99, 2]", 2)
        assert result is not None
        assert 98 not in result  # 99-1=98 out of range

    def test_invalid_response(self):
        assert _parse_ranking_response("no array here", 3) is None

    def test_empty_array(self):
        result = _parse_ranking_response("[]", 3)
        assert result is not None
        assert len(result) == 3  # all missing indices appended

    def test_duplicates_ignored(self):
        result = _parse_ranking_response("[1, 1, 2]", 3)
        assert result is not None
        # 1 should appear only once (0-indexed: 0)
        assert result.count(0) == 1


class MockBackend:
    """Mock LLM backend for testing."""

    def __init__(self, response: str = "[1, 2, 3]", available: bool = True):
        self._response = response
        self._available = available

    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        return self._response

    async def is_available(self) -> bool:
        return self._available

    async def model_info(self):
        return None


class TestRerankWithLLM:
    @pytest.mark.asyncio
    async def test_rerank_reverses_order(self):
        """LLM returns reversed order → results should be reversed."""
        # Need to use the real base class for isinstance check
        from infomesh.summarizer.engine import LLMBackend

        class TestBackend(LLMBackend):
            async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
                return "[3, 2, 1]"

            async def is_available(self) -> bool:
                return True

            async def model_info(self):
                return None

        results = [
            _make_ranked("first", score=0.9),
            _make_ranked("second", score=0.5),
            _make_ranked("third", score=0.1),
        ]
        backend = TestBackend()

        reranked = await rerank_with_llm("test query", results, backend)
        assert len(reranked) == 3
        assert reranked[0].doc_id == "third"
        assert reranked[1].doc_id == "second"
        assert reranked[2].doc_id == "first"

    @pytest.mark.asyncio
    async def test_rerank_unavailable_backend(self):
        """When LLM is unavailable, return original order."""
        from infomesh.summarizer.engine import LLMBackend

        class UnavailableBackend(LLMBackend):
            async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
                return ""

            async def is_available(self) -> bool:
                return False

            async def model_info(self):
                return None

        results = [_make_ranked("a"), _make_ranked("b")]
        backend = UnavailableBackend()

        reranked = await rerank_with_llm("q", results, backend)
        assert reranked[0].doc_id == "a"

    @pytest.mark.asyncio
    async def test_rerank_empty_results(self):
        """Empty results should return empty."""
        from infomesh.summarizer.engine import LLMBackend

        class DummyBackend(LLMBackend):
            async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
                return "[]"

            async def is_available(self) -> bool:
                return True

            async def model_info(self):
                return None

        reranked = await rerank_with_llm("q", [], DummyBackend())
        assert reranked == []

    @pytest.mark.asyncio
    async def test_rerank_invalid_backend_type(self):
        """Non-LLMBackend should return original results."""
        results = [_make_ranked("a")]
        reranked = await rerank_with_llm("q", results, "not a backend")
        assert reranked == results

    @pytest.mark.asyncio
    async def test_rerank_parse_failure_returns_original(self):
        """If LLM returns garbage, original order preserved."""
        from infomesh.summarizer.engine import LLMBackend

        class GarbageBackend(LLMBackend):
            async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
                return "I don't know how to rank these."

            async def is_available(self) -> bool:
                return True

            async def model_info(self):
                return None

        results = [_make_ranked("a"), _make_ranked("b")]
        reranked = await rerank_with_llm("q", results, GarbageBackend())
        assert reranked[0].doc_id == "a"

    @pytest.mark.asyncio
    async def test_rerank_with_top_n(self):
        """top_n should limit output."""
        from infomesh.summarizer.engine import LLMBackend

        class IdentityBackend(LLMBackend):
            async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
                return "[1, 2, 3]"

            async def is_available(self) -> bool:
                return True

            async def model_info(self):
                return None

        results = [_make_ranked(f"d{i}") for i in range(5)]
        reranked = await rerank_with_llm("q", results, IdentityBackend(), top_n=2)
        assert len(reranked) == 2

    @pytest.mark.asyncio
    async def test_rerank_exception_returns_original(self):
        """Backend exceptions should not crash; return original."""
        from infomesh.summarizer.engine import LLMBackend

        class CrashBackend(LLMBackend):
            async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
                raise RuntimeError("LLM crashed")

            async def is_available(self) -> bool:
                return True

            async def model_info(self):
                return None

        results = [_make_ranked("a")]
        reranked = await rerank_with_llm("q", results, CrashBackend())
        assert reranked[0].doc_id == "a"
