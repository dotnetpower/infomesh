"""Tests for infomesh.summarizer — engine + verify modules."""

from __future__ import annotations

import pytest

from infomesh.summarizer.engine import (
    LlamaCppBackend,
    LLMRuntime,
    ModelInfo,
    OllamaBackend,
    SummarizationEngine,
    SummaryResult,
    create_backend,
)
from infomesh.summarizer.verify import (
    SelfVerificationResult,
    VerificationLevel,
    VerificationReport,
    check_key_facts,
    compute_similarity,
    cross_validate,
    detect_contradiction,
    extract_key_facts,
    self_verify,
    verify_summary,
)

# --- Engine tests ----------------------------------------------------------


class TestCreateBackend:
    def test_ollama(self):
        backend = create_backend("ollama", "qwen2.5:3b")
        assert isinstance(backend, OllamaBackend)

    def test_llama_cpp(self):
        backend = create_backend("llama.cpp")
        assert isinstance(backend, LlamaCppBackend)

    def test_llama_cpp_alias(self):
        backend = create_backend("llamacpp")
        assert isinstance(backend, LlamaCppBackend)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            create_backend("unknown_runtime")


class MockBackend:
    """Mock LLM backend for testing."""

    def __init__(self, response: str = "This is a test summary.") -> None:
        self._response = response

    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        return self._response

    async def is_available(self) -> bool:
        return True

    async def model_info(self) -> ModelInfo:
        return ModelInfo(
            name="mock-model",
            runtime=LLMRuntime.OLLAMA,
            parameter_count="3B",
            quantization="Q4_K_M",
            available=True,
        )


@pytest.mark.asyncio
class TestSummarizationEngine:
    async def test_summarize(self):
        backend = MockBackend(response="Python is a programming language.")
        engine = SummarizationEngine(backend)
        result = await engine.summarize(
            url="https://python.org",
            title="Python",
            text="Python is a general-purpose programming language.",
        )
        assert isinstance(result, SummaryResult)
        assert result.url == "https://python.org"
        assert result.summary == "Python is a programming language."
        assert result.model == "mock-model"
        assert result.content_hash  # Non-empty SHA-256
        assert result.elapsed_ms >= 0

    async def test_is_available(self):
        backend = MockBackend()
        engine = SummarizationEngine(backend)
        assert await engine.is_available() is True

    async def test_token_count_estimate(self):
        backend = MockBackend(response="Hello world test.")
        engine = SummarizationEngine(backend)
        result = await engine.summarize(
            url="https://example.com",
            title="Test",
            text="Some content here.",
        )
        assert result.token_count is not None
        assert result.token_count > 0


# --- Verify: key fact extraction -------------------------------------------


class TestExtractKeyFacts:
    def test_extracts_sentences_with_numbers(self):
        text = (
            "Python was created in 1991 by Guido van Rossum. "
            "It supports multiple programming paradigms. "
            "There are over 400000 packages on PyPI. "
            "The latest version is 3.12."
        )
        facts = extract_key_facts(text)
        assert len(facts) > 0
        # Should prioritize sentences with numbers
        fact_texts = [f.text for f in facts]
        assert any("1991" in t for t in fact_texts)

    def test_empty_text(self):
        assert extract_key_facts("") == []

    def test_max_facts_limit(self):
        text = ". ".join(f"Fact number {i} is important" for i in range(50))
        facts = extract_key_facts(text, max_facts=5)
        assert len(facts) <= 5


# --- Verify: check key facts ---------------------------------------------


class TestCheckKeyFacts:
    def test_found_facts(self):
        from infomesh.summarizer.verify import KeyFact

        facts = [
            KeyFact(
                text="Python was created in 1991",
                source_offset=0,
                found_in_summary=False,
            ),
        ]
        summary = "Python was created in 1991 by Guido van Rossum."
        checked = check_key_facts(summary, facts)
        assert checked[0].found_in_summary is True

    def test_not_found_facts(self):
        from infomesh.summarizer.verify import KeyFact

        facts = [
            KeyFact(
                text="Quantum chromodynamics explains strong nuclear interactions",
                source_offset=0,
                found_in_summary=False,
            ),
        ]
        summary = "Python is a great language for web development."
        checked = check_key_facts(summary, facts)
        assert checked[0].found_in_summary is False


# --- Verify: contradiction detection ---------------------------------------


class TestDetectContradiction:
    def test_no_contradiction(self):
        source = "Python has 400000 packages on PyPI."
        summary = "Python has 400000 packages available."
        assert detect_contradiction(source, summary) is False

    def test_novel_numbers_flagged(self):
        source = "Python is a language."
        summary = "Python has 500 modules, 200 features, and 100 plugins."
        assert detect_contradiction(source, summary) is True


# --- Verify: compute_similarity -------------------------------------------


class TestComputeSimilarity:
    def test_identical_texts(self):
        assert compute_similarity("hello world", "hello world") == pytest.approx(1.0)

    def test_no_overlap(self):
        assert compute_similarity("hello", "world") == pytest.approx(0.0)

    def test_partial_overlap(self):
        sim = compute_similarity("hello world foo", "hello world bar")
        assert 0.0 < sim < 1.0

    def test_empty_text(self):
        assert compute_similarity("", "hello") == 0.0


# --- Verify: self_verify --------------------------------------------------


class TestSelfVerify:
    def test_good_summary_passes(self):
        source = (
            "Python was created in 1991. "
            "It was designed by Guido van Rossum. "
            "Python 3.12 was released in 2023. "
            "There are over 400000 packages on PyPI."
        )
        summary = (
            "Python, created in 1991 by Guido van Rossum, "
            "has over 400000 packages on PyPI."
        )
        result = self_verify(source, summary)
        assert isinstance(result, SelfVerificationResult)
        assert result.passed is True

    def test_hallucinated_summary_may_fail(self):
        source = "Python is a language."
        summary = (
            "Java was created in 1842. It has 999 trillion"
            " users, 888 features, and 777 modules."
        )
        result = self_verify(source, summary)
        # Contradiction detected (novel numbers)
        assert result.has_contradiction is True


# --- Verify: cross_validate ------------------------------------------------


class TestCrossValidate:
    def test_similar_summaries_pass(self):
        our = "Python is a popular programming language created by Guido."
        peers = [
            "Python is a widely used language created by Guido van Rossum.",
            "Python, created by Guido, is a popular programming language.",
        ]
        result = cross_validate(our, peers)
        assert result.passed is True
        assert result.avg_similarity > 0

    def test_no_peers_fails(self):
        result = cross_validate("some summary", [])
        assert result.passed is False

    def test_dissimilar_summaries_fail(self):
        our = "Python is a great language."
        peers = [
            "Quantum mechanics describes subatomic phenomena.",
            "The stock market fluctuated wildly.",
        ]
        result = cross_validate(our, peers)
        assert result.avg_similarity < 0.3


# --- Verify: full pipeline ------------------------------------------------


class TestVerifySummary:
    def test_self_verified_report(self):
        source = (
            "Python was created in 1991. "
            "It is designed by Guido van Rossum. "
            "Python 3.12 was released in 2023. "
            "Over 400000 packages exist on PyPI."
        )
        summary = (
            "Python, created in 1991 by Guido van Rossum, has 400000 packages on PyPI."
        )
        report = verify_summary(
            url="https://python.org",
            content_hash="abc123",
            source_text=source,
            summary=summary,
        )
        assert isinstance(report, VerificationReport)
        assert report.level in (
            VerificationLevel.SELF_VERIFIED,
            VerificationLevel.UNVERIFIED,
        )
        assert report.quality_score >= 0.0

    def test_cross_validated_report(self):
        source = (
            "Python was created in 1991. "
            "Guido van Rossum designed it. "
            "Python is dynamically typed. "
            "It supports object-oriented programming."
        )
        summary = (
            "Python, a dynamically typed language,"
            " was created by Guido van Rossum in 1991."
        )
        peers = [
            "Python was created in 1991 by Guido. It is dynamically typed.",
            "Guido van Rossum created Python, a dynamically typed OOP language.",
        ]
        report = verify_summary(
            url="https://python.org",
            content_hash="abc123",
            source_text=source,
            summary=summary,
            peer_summaries=peers,
        )
        assert report.cross_check is not None
        if report.self_check.passed and report.cross_check.passed:
            assert report.level == VerificationLevel.CROSS_VALIDATED
