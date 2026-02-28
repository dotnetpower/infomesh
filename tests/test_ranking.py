"""Tests for infomesh.index.ranking — BM25 + freshness + trust + authority ranking."""

from __future__ import annotations

import time

import pytest

from infomesh.index.ranking import (
    MIN_FRESHNESS,
    WEIGHT_AUTHORITY,
    WEIGHT_BM25,
    WEIGHT_FRESHNESS,
    WEIGHT_TRUST,
    RankedResult,
    _RawCandidate,
    combined_score,
    freshness_score,
    normalize_bm25,
    rank_results,
)

# --- freshness_score -------------------------------------------------------


class TestFreshnessScore:
    def test_just_crawled_is_near_one(self):
        now = time.time()
        assert freshness_score(now, now=now) == pytest.approx(1.0)

    def test_one_halflife_gives_half(self):
        now = time.time()
        half_life = 7 * 24 * 3600  # 7 days
        score = freshness_score(now - half_life, now=now)
        assert score == pytest.approx(0.5, abs=0.01)

    def test_very_old_returns_min(self):
        now = time.time()
        ancient = now - 365 * 24 * 3600  # 1 year ago
        score = freshness_score(ancient, now=now)
        assert score == pytest.approx(MIN_FRESHNESS, abs=0.01)

    def test_future_timestamp_clamps_to_one(self):
        now = time.time()
        assert freshness_score(now + 1000, now=now) == 1.0


# --- normalize_bm25 --------------------------------------------------------


class TestNormalizeBM25:
    def test_zero_score(self):
        assert normalize_bm25(0.0) == 0.0

    def test_negative_score(self):
        assert normalize_bm25(-1.0) == 0.0

    def test_equal_to_max_gives_half(self):
        assert normalize_bm25(5.0, max_score=5.0) == pytest.approx(0.5)

    def test_large_score_approaches_one(self):
        assert normalize_bm25(1000.0, max_score=1.0) > 0.99

    def test_small_score(self):
        assert 0.0 < normalize_bm25(0.1, max_score=10.0) < 0.1


# --- combined_score ---------------------------------------------------------


class TestCombinedScore:
    def test_all_ones(self):
        assert combined_score(1.0, 1.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_all_zeros(self):
        assert combined_score(0.0, 0.0, 0.0, 0.0) == 0.0

    def test_weights_sum_correctly(self):
        score = combined_score(0.5, 0.5, 0.5, 0.5)
        expected = 0.5 * (
            WEIGHT_BM25 + WEIGHT_FRESHNESS + WEIGHT_TRUST + WEIGHT_AUTHORITY
        )
        assert score == pytest.approx(expected)

    def test_custom_weights(self):
        score = combined_score(
            1.0,
            0.0,
            0.0,
            0.0,
            w_bm25=1.0,
            w_fresh=0.0,
            w_trust=0.0,
            w_authority=0.0,
        )
        assert score == pytest.approx(1.0)

    def test_authority_contribution(self):
        base = combined_score(0.5, 0.5, 0.5, 0.0)
        with_auth = combined_score(0.5, 0.5, 0.5, 1.0)
        assert with_auth > base


# --- rank_results -----------------------------------------------------------


def _make_candidate(
    doc_id: str = "d1",
    url: str = "https://example.com",
    bm25: float = 1.0,
    crawled_at: float | None = None,
    trust: float = 0.5,
) -> _RawCandidate:
    return _RawCandidate(
        doc_id=doc_id,
        url=url,
        title="Test",
        snippet="snippet",
        bm25_raw=bm25,
        crawled_at=crawled_at or time.time(),
        peer_id=None,
        trust=trust,
    )


class TestRankResults:
    def test_empty_list(self):
        assert rank_results([]) == []

    def test_single_candidate(self):
        results = rank_results([_make_candidate()])
        assert len(results) == 1
        assert isinstance(results[0], RankedResult)

    def test_higher_bm25_ranks_first(self):
        now = time.time()
        candidates = [
            _make_candidate(doc_id="low", bm25=1.0, crawled_at=now),
            _make_candidate(doc_id="high", bm25=10.0, crawled_at=now),
        ]
        results = rank_results(candidates, now=now)
        assert results[0].doc_id == "high"

    def test_fresher_doc_ranks_higher(self):
        now = time.time()
        old = now - 30 * 24 * 3600  # 30 days ago
        candidates = [
            _make_candidate(doc_id="old", bm25=5.0, crawled_at=old),
            _make_candidate(doc_id="new", bm25=5.0, crawled_at=now),
        ]
        results = rank_results(candidates, now=now)
        assert results[0].doc_id == "new"

    def test_higher_trust_ranks_higher(self):
        now = time.time()
        candidates = [
            _make_candidate(doc_id="untrusted", bm25=5.0, crawled_at=now, trust=0.1),
            _make_candidate(doc_id="trusted", bm25=5.0, crawled_at=now, trust=0.9),
        ]
        results = rank_results(candidates, now=now)
        assert results[0].doc_id == "trusted"

    def test_limit(self):
        candidates = [_make_candidate(doc_id=f"d{i}") for i in range(20)]
        results = rank_results(candidates, limit=5)
        assert len(results) == 5

    def test_scores_between_zero_and_one(self):
        results = rank_results([_make_candidate()])
        r = results[0]
        assert 0.0 <= r.bm25_score <= 1.0
        assert 0.0 <= r.freshness_score <= 1.0
        assert 0.0 <= r.trust_score <= 1.0
        assert 0.0 <= r.authority_score <= 1.0
        assert 0.0 <= r.combined_score <= 1.0
