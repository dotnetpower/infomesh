"""Tests for query result cross-validation."""

from __future__ import annotations

from infomesh.search.cross_validate import (
    VERDICT_FABRICATED,
    VERDICT_SUSPICIOUS,
    VERDICT_TRUSTED,
    VERDICT_UNVERIFIED,
    PeerResult,
    cross_validate_results,
    snippet_similarity,
)


def _make_result(peer_id: str, url: str, score: float = 1.0) -> PeerResult:
    return PeerResult(
        peer_id=peer_id,
        url=url,
        title=f"Title for {url}",
        snippet=f"Snippet for {url}",
        score=score,
    )


class TestSnippetSimilarity:
    def test_identical(self) -> None:
        assert snippet_similarity("hello world", "hello world") == 1.0

    def test_no_overlap(self) -> None:
        assert snippet_similarity("hello world", "foo bar") == 0.0

    def test_partial_overlap(self) -> None:
        sim = snippet_similarity("hello world foo", "hello world bar")
        assert 0.3 < sim < 0.7  # 2 shared out of 4 unique

    def test_empty_string(self) -> None:
        assert snippet_similarity("", "hello") == 0.0


class TestCrossValidation:
    def test_insufficient_peers(self) -> None:
        """With only 1 peer, all results are unverified."""
        peer_results = {
            "peer-1": [_make_result("peer-1", "http://a.com")],
        }
        report = cross_validate_results("test query", peer_results)
        assert report.total_peers == 1
        assert all(r.verdict == VERDICT_UNVERIFIED for r in report.results)

    def test_all_peers_agree(self) -> None:
        """URL returned by all peers → trusted."""
        peer_results = {
            "peer-1": [_make_result("peer-1", "http://a.com", 5.0)],
            "peer-2": [_make_result("peer-2", "http://a.com", 4.8)],
            "peer-3": [_make_result("peer-3", "http://a.com", 5.1)],
        }
        report = cross_validate_results("test", peer_results)
        assert report.total_peers == 3
        assert len(report.results) == 1
        assert report.results[0].verdict == VERDICT_TRUSTED
        assert report.results[0].agreement_ratio == 1.0

    def test_single_peer_url_fabricated(self) -> None:
        """URL returned by only 1 of 3 peers → fabricated."""
        peer_results = {
            "peer-1": [
                _make_result("peer-1", "http://a.com"),
                _make_result("peer-1", "http://fake.com"),
            ],
            "peer-2": [_make_result("peer-2", "http://a.com")],
            "peer-3": [_make_result("peer-3", "http://a.com")],
        }
        report = cross_validate_results("test", peer_results)
        fake_results = [r for r in report.results if r.url == "http://fake.com"]
        assert len(fake_results) == 1
        assert fake_results[0].verdict == VERDICT_FABRICATED
        assert report.fabricated_count == 1

    def test_partial_agreement_suspicious(self) -> None:
        """URL returned by 1/3 peers when threshold is 0.5.

        Should be suspicious or fabricated.
        """
        peer_results = {
            "peer-1": [_make_result("peer-1", "http://a.com")],
            "peer-2": [_make_result("peer-2", "http://a.com")],
            "peer-3": [_make_result("peer-3", "http://b.com")],
        }
        report = cross_validate_results("test", peer_results)
        b_results = [r for r in report.results if r.url == "http://b.com"]
        assert len(b_results) == 1
        assert b_results[0].verdict in (VERDICT_SUSPICIOUS, VERDICT_FABRICATED)

    def test_score_deviation_flagged(self) -> None:
        """Wildly different scores for same URL → suspicious."""
        peer_results = {
            "peer-1": [_make_result("peer-1", "http://a.com", score=1.0)],
            "peer-2": [_make_result("peer-2", "http://a.com", score=1.0)],
            "peer-3": [_make_result("peer-3", "http://a.com", score=100.0)],
        }
        report = cross_validate_results("test", peer_results)
        assert report.results[0].agreement_ratio == 1.0
        # Score deviation is (100 - 1) / 1 = 99 >> 3.0 threshold
        assert report.results[0].verdict == VERDICT_SUSPICIOUS

    def test_deduplication_with_single_peer(self) -> None:
        """Single peer with duplicate URLs should deduplicate."""
        peer_results = {
            "peer-1": [
                _make_result("peer-1", "http://a.com"),
                _make_result("peer-1", "http://b.com"),
            ],
        }
        report = cross_validate_results("test", peer_results)
        urls = {r.url for r in report.results}
        assert "http://a.com" in urls
        assert "http://b.com" in urls

    def test_empty_results(self) -> None:
        report = cross_validate_results("empty", {})
        assert report.total_peers == 0
        assert len(report.results) == 0

    def test_two_peers_minimum(self) -> None:
        """Two peers should be enough for cross-validation."""
        peer_results = {
            "peer-1": [_make_result("peer-1", "http://a.com")],
            "peer-2": [_make_result("peer-2", "http://a.com")],
        }
        report = cross_validate_results("test", peer_results)
        assert report.total_peers == 2
        assert report.results[0].verdict == VERDICT_TRUSTED
