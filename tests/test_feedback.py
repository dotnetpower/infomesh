"""Tests for search feedback loop (Issue #7)."""

from __future__ import annotations

from infomesh.search.feedback import FeedbackStore, URLBoost


class TestFeedbackStore:
    """Test FeedbackStore operations."""

    def test_record_fetch(self) -> None:
        store = FeedbackStore()
        try:
            store.record_fetch("python sort list", "https://example.com/sort", 1)
            boost = store.get_boost("https://example.com/sort")
            assert boost > 0
            assert store.signal_count() == 1
        finally:
            store.close()

    def test_record_skip(self) -> None:
        store = FeedbackStore()
        try:
            store.record_skip("query", ["https://a.com", "https://b.com"])
            assert store.get_boost("https://a.com") < 0
            assert store.signal_count() == 2
        finally:
            store.close()

    def test_record_citation(self) -> None:
        store = FeedbackStore()
        try:
            store.record_citation("claim", "https://source.com/article")
            stats = store.get_url_stats("https://source.com/article")
            assert stats is not None
            assert stats.cite_count == 1
            assert stats.boost_score > 0
        finally:
            store.close()

    def test_reformulation_detection(self) -> None:
        store = FeedbackStore()
        try:
            store.record_fetch("python sort", "https://example.com", 1)
            assert store.is_reformulation("python sort")
            assert not store.is_reformulation("completely different query")
        finally:
            store.close()

    def test_top_boosted_urls(self) -> None:
        store = FeedbackStore()
        try:
            for i in range(5):
                store.record_fetch(f"query{i}", f"https://example.com/{i}", 1)
            top = store.top_boosted_urls(limit=3)
            assert len(top) <= 3
            assert all(isinstance(u, URLBoost) for u in top)
        finally:
            store.close()

    def test_hash_query_privacy(self) -> None:
        h = FeedbackStore.hash_query("secret query")
        assert len(h) == 64  # SHA-256 hex
        assert "secret" not in h

    def test_unknown_url_boost(self) -> None:
        store = FeedbackStore()
        try:
            assert store.get_boost("https://unknown.com") == 0.0
            assert store.get_url_stats("https://unknown.com") is None
        finally:
            store.close()

    def test_boost_accumulates(self) -> None:
        store = FeedbackStore()
        try:
            store.record_fetch("q1", "https://a.com", 1)
            b1 = store.get_boost("https://a.com")
            store.record_fetch("q2", "https://a.com", 2)
            b2 = store.get_boost("https://a.com")
            assert b2 > b1
        finally:
            store.close()
