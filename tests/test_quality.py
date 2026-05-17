"""Tests for search quality features (#1,3,6,7,8)."""

from __future__ import annotations

from infomesh.search.quality import (
    ABTest,
    QueryIntentClassifier,
    cluster_results,
    detect_domain_category,
    diversify_results,
    extract_temporal_hint,
    get_profile,
    ndcg_at_k,
)


class TestABTesting:
    def test_ndcg_perfect(self) -> None:
        assert ndcg_at_k([3, 2, 1], k=3) == 1.0

    def test_ndcg_empty(self) -> None:
        assert ndcg_at_k([], k=5) == 0.0

    def test_ab_comparison(self) -> None:
        ab = ABTest("test")
        result = ab.compare("query", [3, 2, 1], [1, 2, 3])
        assert result.winner in ("A", "B", "tie")
        assert ab.summary()["total"] == 1

    def test_ab_summary(self) -> None:
        ab = ABTest("test")
        ab.compare("q1", [3, 2], [1, 0])
        ab.compare("q2", [1, 0], [3, 2])
        s = ab.summary()
        assert s["total"] == 2


class TestRankingProfiles:
    def test_default_profile(self) -> None:
        p = get_profile("default")
        assert p.bm25_weight == 0.40

    def test_news_profile(self) -> None:
        p = get_profile("news")
        assert p.freshness_weight > p.bm25_weight

    def test_unknown_falls_back(self) -> None:
        p = get_profile("nonexistent")
        assert p.name == "default"

    def test_domain_detection(self) -> None:
        assert detect_domain_category("https://docs.python.org") == "tech-docs"
        assert detect_domain_category("https://arxiv.org/abs/123") == "academic"
        assert detect_domain_category("https://example.com") == "default"


class TestResultClustering:
    def test_cluster_by_domain(self) -> None:
        results = [
            {"url": "https://a.com/1", "title": "A1"},
            {"url": "https://a.com/2", "title": "A2"},
            {"url": "https://b.com/1", "title": "B1"},
        ]
        clusters = cluster_results(results)
        assert len(clusters) == 2

    def test_diversify(self) -> None:
        results = [
            {"url": "https://a.com/1"},
            {"url": "https://a.com/2"},
            {"url": "https://a.com/3"},
            {"url": "https://b.com/1"},
        ]
        div = diversify_results(results, max_per_domain=2)
        assert len(div) >= 3


class TestTemporalSearch:
    def test_today(self) -> None:
        assert extract_temporal_hint("news today") == 1

    def test_last_week(self) -> None:
        assert extract_temporal_hint("updates last week") == 14

    def test_last_n_days(self) -> None:
        assert extract_temporal_hint("changes last 30 days") == 30

    def test_no_hint(self) -> None:
        assert extract_temporal_hint("python tutorial") is None

    def test_recent(self) -> None:
        assert extract_temporal_hint("latest python releases") == 7


class TestIntentClassifier:
    def test_how_to(self) -> None:
        c = QueryIntentClassifier()
        assert c.classify("how to install python") == "how_to"

    def test_definition(self) -> None:
        c = QueryIntentClassifier()
        assert c.classify("what is kubernetes") == "definition"

    def test_comparison(self) -> None:
        c = QueryIntentClassifier()
        assert c.classify("python vs javascript") == "comparison"

    def test_error(self) -> None:
        c = QueryIntentClassifier()
        assert c.classify("ModuleNotFoundError traceback") == "error_debug"

    def test_informational_default(self) -> None:
        c = QueryIntentClassifier()
        assert c.classify("blue sky") == "informational"

    def test_with_confidence(self) -> None:
        c = QueryIntentClassifier()
        intent, conf = c.classify_with_confidence("how to deploy docker")
        assert intent == "how_to"
        assert 0 < conf <= 1.0
