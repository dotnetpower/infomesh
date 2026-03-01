"""Tests for infomesh.search.nlp — NLP query processing."""

from __future__ import annotations

from infomesh.search.nlp import (
    ParsedQuery,
    RelatedSearchTracker,
    did_you_mean,
    expand_query,
    parse_natural_query,
    remove_stop_words,
)


class TestRemoveStopWords:
    def test_removes_english_stops(self) -> None:
        tokens = ["the", "quick", "brown", "fox", "is", "a", "test"]
        result = remove_stop_words(tokens)
        assert "the" not in result
        assert "quick" in result
        assert "brown" in result

    def test_empty_list(self) -> None:
        assert remove_stop_words([]) == []

    def test_all_stop_words(self) -> None:
        result = remove_stop_words(["the", "a", "an", "is"])
        assert isinstance(result, list)

    def test_preserves_meaningful_words(self) -> None:
        result = remove_stop_words(["python", "programming", "tutorial"])
        assert "python" in result
        assert "programming" in result


class TestExpandQuery:
    def test_basic_expansion(self) -> None:
        result = expand_query("error")
        # Should include synonyms like exception, bug, etc.
        assert isinstance(result, list)
        assert len(result) > 0

    def test_empty_query(self) -> None:
        assert expand_query("") == []

    def test_no_expansion_for_unknown(self) -> None:
        result = expand_query("xyznonexistent")
        assert result == []


class TestDidYouMean:
    def test_close_match(self) -> None:
        vocab = ["python", "javascript", "typescript", "golang"]
        result = did_you_mean("pythn", vocab)
        assert isinstance(result, list)
        assert "python" in result

    def test_no_match(self) -> None:
        vocab = ["python", "javascript"]
        result = did_you_mean("xxxxxxxxxxx", vocab)
        assert result == []

    def test_exact_match(self) -> None:
        vocab = ["python", "javascript"]
        result = did_you_mean("python", vocab)
        assert result == []  # No correction needed

    def test_empty_vocab(self) -> None:
        assert did_you_mean("test", []) == []

    def test_empty_query(self) -> None:
        assert did_you_mean("", ["python"]) == []


class TestParseNaturalQuery:
    def test_domain_filter(self) -> None:
        result = parse_natural_query("python site:docs.python.org")
        assert isinstance(result, ParsedQuery)
        assert "docs.python.org" in (result.include_domains or [])
        assert "python" in result.cleaned_query

    def test_language_filter(self) -> None:
        result = parse_natural_query("tutorial in korean")
        assert result.language == "ko"
        assert "tutorial" in result.cleaned_query

    def test_plain_query(self) -> None:
        result = parse_natural_query("simple search query")
        assert result.cleaned_query == "simple search query"

    def test_empty_query(self) -> None:
        result = parse_natural_query("")
        assert result.cleaned_query == ""


class TestRelatedSearchTracker:
    def test_record_and_related(self) -> None:
        tracker = RelatedSearchTracker()
        tracker.record("python tutorial")
        tracker.record("python guide")
        tracker.record("python docs")
        related = tracker.related("python")
        assert isinstance(related, list)

    def test_related_unknown(self) -> None:
        tracker = RelatedSearchTracker()
        assert tracker.related("unknown") == []
