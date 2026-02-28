"""Tests for LLM reputation-based trust tracking."""

from __future__ import annotations

import pytest

from infomesh.trust.reputation import (
    EMA_ALPHA,
    MIN_SAMPLES,
    LLMReputationTracker,
    ReputationGrade,
    _grade_from_score,
)


class TestGradeFromScore:
    """Tests for grade classification."""

    def test_excellent(self) -> None:
        assert _grade_from_score(0.90, 10) == ReputationGrade.EXCELLENT

    def test_good(self) -> None:
        assert _grade_from_score(0.75, 10) == ReputationGrade.GOOD

    def test_acceptable(self) -> None:
        assert _grade_from_score(0.55, 10) == ReputationGrade.ACCEPTABLE

    def test_poor(self) -> None:
        assert _grade_from_score(0.35, 10) == ReputationGrade.POOR

    def test_unreliable(self) -> None:
        assert _grade_from_score(0.10, 10) == ReputationGrade.UNRELIABLE

    def test_unknown_below_min_samples(self) -> None:
        assert _grade_from_score(0.99, 2) == ReputationGrade.UNKNOWN

    def test_exact_threshold_excellent(self) -> None:
        assert _grade_from_score(0.85, 10) == ReputationGrade.EXCELLENT

    def test_exact_threshold_good(self) -> None:
        assert _grade_from_score(0.70, 10) == ReputationGrade.GOOD


class TestLLMReputationTracker:
    """Tests for LLMReputationTracker."""

    @pytest.fixture
    def tracker(self) -> LLMReputationTracker:
        return LLMReputationTracker()  # in-memory

    def test_no_ratings_returns_none(self, tracker: LLMReputationTracker) -> None:
        assert tracker.get_reputation("unknown") is None

    def test_single_rating(self, tracker: LLMReputationTracker) -> None:
        tracker.record_quality("peer1", 0.8, url="https://example.com")
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        assert rep.total_ratings == 1
        assert rep.avg_quality == 0.8

    def test_ema_updates(self, tracker: LLMReputationTracker) -> None:
        # Default EMA starts at 0.5
        tracker.record_quality("peer1", 1.0)
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        # EMA = 0.3 * 1.0 + 0.7 * 0.5 = 0.65
        expected = EMA_ALPHA * 1.0 + (1 - EMA_ALPHA) * 0.5
        assert abs(rep.ema_quality - expected) < 0.001

    def test_multiple_ratings_avg(self, tracker: LLMReputationTracker) -> None:
        tracker.record_quality("peer1", 0.6)
        tracker.record_quality("peer1", 0.8)
        tracker.record_quality("peer1", 1.0)
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        assert rep.total_ratings == 3
        assert abs(rep.avg_quality - 0.8) < 0.001

    def test_quality_clamped(self, tracker: LLMReputationTracker) -> None:
        tracker.record_quality("peer1", 1.5)  # exceeds 1.0
        tracker.record_quality("peer1", -0.5)  # below 0.0
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        assert rep.avg_quality == 0.5  # (1.0 + 0.0) / 2

    def test_grade_unknown_below_min(self, tracker: LLMReputationTracker) -> None:
        for _i in range(MIN_SAMPLES - 1):
            tracker.record_quality("peer1", 0.9)
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        assert rep.grade == ReputationGrade.UNKNOWN

    def test_grade_assigned_at_min(self, tracker: LLMReputationTracker) -> None:
        for _ in range(MIN_SAMPLES):
            tracker.record_quality("peer1", 0.9)
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        assert rep.grade != ReputationGrade.UNKNOWN

    def test_get_quality_score_default(self, tracker: LLMReputationTracker) -> None:
        assert tracker.get_quality_score("unknown") == 0.5

    def test_get_quality_score(self, tracker: LLMReputationTracker) -> None:
        tracker.record_quality("peer1", 0.9)
        score = tracker.get_quality_score("peer1")
        assert score > 0.5

    def test_list_peers_empty(self, tracker: LLMReputationTracker) -> None:
        assert tracker.list_peers() == []

    def test_list_peers_sorted(self, tracker: LLMReputationTracker) -> None:
        for _ in range(5):
            tracker.record_quality("low", 0.3)
            tracker.record_quality("high", 0.9)
        peers = tracker.list_peers()
        assert len(peers) == 2
        assert peers[0].peer_id == "high"

    def test_list_peers_min_ratings_filter(self, tracker: LLMReputationTracker) -> None:
        tracker.record_quality("few", 0.9)
        for _ in range(10):
            tracker.record_quality("many", 0.8)
        assert len(tracker.list_peers(min_ratings=5)) == 1

    def test_list_peers_grade_filter(self, tracker: LLMReputationTracker) -> None:
        for _ in range(10):
            tracker.record_quality("good", 0.75)
            tracker.record_quality("bad", 0.2)
        peers = tracker.list_peers(grade=ReputationGrade.UNRELIABLE)
        assert all(p.grade == ReputationGrade.UNRELIABLE for p in peers)

    def test_top_peers(self, tracker: LLMReputationTracker) -> None:
        for _ in range(MIN_SAMPLES + 1):
            tracker.record_quality("a", 0.9)
            tracker.record_quality("b", 0.5)
        top = tracker.top_peers(n=1)
        assert len(top) == 1
        assert top[0].peer_id == "a"

    def test_content_hash_stored(self, tracker: LLMReputationTracker) -> None:
        tracker.record_quality("peer1", 0.8, content_hash="abc123")
        rep = tracker.get_reputation("peer1")
        assert rep is not None
        assert rep.total_ratings == 1

    def test_persistent_db(self, tmp_path) -> None:
        db = tmp_path / "rep.db"
        t1 = LLMReputationTracker(db)
        t1.record_quality("peer1", 0.9)
        t1.close()

        t2 = LLMReputationTracker(db)
        rep = t2.get_reputation("peer1")
        assert rep is not None
        assert rep.total_ratings == 1
        t2.close()
