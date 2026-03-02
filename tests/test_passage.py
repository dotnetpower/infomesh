"""Tests for infomesh/search/passage.py — passage extraction, scoring, and helpers."""

from __future__ import annotations

import pytest

from infomesh.search.passage import (
    QueryIntent,
    classify_intent,
    highlight_terms,
    score_passage,
    select_best_passage,
    split_passages,
    title_match_score,
    url_path_score,
)

# ── split_passages ──────────────────────────────────────────────────


class TestSplitPassages:
    def test_empty_text(self) -> None:
        assert split_passages("") == []
        assert split_passages("   ") == []

    def test_single_paragraph(self) -> None:
        text = "Python is a programming language used for web development."
        result = split_passages(text, min_length=10)
        assert len(result) == 1
        assert "Python" in result[0]

    def test_multiple_paragraphs(self) -> None:
        text = (
            "First paragraph about Python.\n\n"
            "Second paragraph about JavaScript.\n\n"
            "Third paragraph about Rust programming."
        )
        result = split_passages(text, min_length=10)
        assert len(result) >= 2

    def test_long_paragraph_splits(self) -> None:
        long_text = "Word " * 200  # ~1000 chars
        result = split_passages(long_text, max_length=100, min_length=10)
        # Should produce multiple passages (not just one giant chunk)
        assert len(result) > 1

    def test_tiny_chunks_merged(self) -> None:
        text = "Hello.\n\nHi.\n\nThis is a longer paragraph with real content."
        result = split_passages(text, min_length=30)
        # tiny "Hello." and "Hi." should be merged
        assert len(result) >= 1


# ── score_passage ───────────────────────────────────────────────────


class TestScorePassage:
    def test_full_match(self) -> None:
        score = score_passage("python sort list example", ["python", "sort", "list"])
        assert score > 0.9

    def test_partial_match(self) -> None:
        score = score_passage("python is a language", ["python", "sort", "list"])
        assert 0.0 < score < 1.0

    def test_no_match(self) -> None:
        score = score_passage("javascript is great", ["python", "sort", "list"])
        assert score == 0.0

    def test_empty_inputs(self) -> None:
        assert score_passage("", ["python"]) == 0.0
        assert score_passage("hello", []) == 0.0
        assert score_passage("", []) == 0.0

    def test_density_bonus(self) -> None:
        # More query term occurrences = higher density
        sparse = score_passage(
            "python is used for many things in the world",
            ["python"],
        )
        dense = score_passage("python python python", ["python"])
        assert dense > sparse


# ── select_best_passage ─────────────────────────────────────────────


class TestSelectBestPassage:
    def test_selects_relevant_passage(self) -> None:
        text = (
            "Introduction to web development.\n\n"
            "Python sort list example: use sorted() for sorting.\n\n"
            "Conclusion about databases."
        )
        result = select_best_passage(text, "python sort list")
        assert "sort" in result.lower()

    def test_fallback_on_no_match(self) -> None:
        text = "This text has nothing relevant at all."
        result = select_best_passage(text, "quantum physics")
        assert len(result) > 0  # falls back to first N chars

    def test_empty_text(self) -> None:
        assert select_best_passage("", "query") == ""

    def test_empty_query(self) -> None:
        result = select_best_passage("some text here", "")
        assert result == "some text here"[:200]

    def test_max_length_respected(self) -> None:
        text = "Word " * 200
        result = select_best_passage(text, "word", max_length=100)
        assert len(result) <= 100


# ── highlight_terms ─────────────────────────────────────────────────


class TestHighlightTerms:
    def test_basic_highlighting(self) -> None:
        result = highlight_terms("Python is great", ["python"])
        assert "<b>Python</b>" in result

    def test_case_preserved(self) -> None:
        result = highlight_terms("Python and PYTHON", ["python"])
        assert "<b>Python</b>" in result
        assert "<b>PYTHON</b>" in result

    def test_empty_tokens(self) -> None:
        assert highlight_terms("hello", []) == "hello"

    def test_empty_text(self) -> None:
        assert highlight_terms("", ["python"]) == ""

    def test_multiple_terms(self) -> None:
        result = highlight_terms("sort a python list", ["sort", "list"])
        assert "<b>sort</b>" in result
        assert "<b>list</b>" in result


# ── title_match_score ───────────────────────────────────────────────


class TestTitleMatchScore:
    def test_full_match(self) -> None:
        score = title_match_score("Python Sort List", ["python", "sort", "list"])
        assert score == pytest.approx(1.0)

    def test_partial_match(self) -> None:
        score = title_match_score("Python Guide", ["python", "sort", "list"])
        assert score == pytest.approx(1.0 / 3.0, abs=0.01)

    def test_no_match(self) -> None:
        score = title_match_score("JavaScript Guide", ["python", "sort"])
        assert score == 0.0

    def test_empty_title(self) -> None:
        assert title_match_score("", ["python"]) == 0.0

    def test_empty_tokens(self) -> None:
        assert title_match_score("Python", []) == 0.0


# ── url_path_score ──────────────────────────────────────────────────


class TestUrlPathScore:
    def test_matching_path(self) -> None:
        score = url_path_score(
            "https://docs.python.org/3/howto/sorting.html",
            ["python", "sorting"],
        )
        assert score > 0.0

    def test_no_match(self) -> None:
        score = url_path_score(
            "https://example.com/about",
            ["python", "sort"],
        )
        assert score == 0.0

    def test_root_path(self) -> None:
        score = url_path_score("https://example.com/", ["python"])
        assert score == 0.0

    def test_empty_url(self) -> None:
        assert url_path_score("", ["python"]) == 0.0

    def test_empty_tokens(self) -> None:
        assert url_path_score("https://example.com/python", []) == 0.0

    def test_partial_match(self) -> None:
        score = url_path_score(
            "https://example.com/docs/hooks/guide",
            ["react", "hooks"],
        )
        assert 0.0 < score < 1.0


# ── classify_intent ─────────────────────────────────────────────────


class TestClassifyIntent:
    def test_informational(self) -> None:
        result = classify_intent("how to sort a list in python")
        assert result == QueryIntent.INFORMATIONAL

    def test_navigational(self) -> None:
        assert classify_intent("python.org") == QueryIntent.NAVIGATIONAL
        assert classify_intent("go to github") == QueryIntent.NAVIGATIONAL
        assert classify_intent("login page") == QueryIntent.NAVIGATIONAL

    def test_transactional(self) -> None:
        assert classify_intent("download python") == QueryIntent.TRANSACTIONAL
        assert classify_intent("install nodejs") == QueryIntent.TRANSACTIONAL

    def test_empty_query(self) -> None:
        assert classify_intent("") == QueryIntent.INFORMATIONAL
