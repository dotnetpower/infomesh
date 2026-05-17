"""Search quality benchmarking and A/B testing framework.

Features:
- #1: A/B testing for ranking algorithms (NDCG/MRR comparison)
- #3: Domain-specific ranking profiles
- #4: Cache pre-warming for popular queries
- #6: Result clustering for diversity
- #7: Temporal search optimization
- #8: Zero-shot query intent classification
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# ── #1: A/B Testing Framework ──────────────────────────────────────


@dataclass
class ABTestResult:
    """Result of an A/B ranking comparison."""

    test_name: str
    query: str
    variant_a_ndcg: float
    variant_b_ndcg: float
    winner: str  # "A", "B", or "tie"
    improvement_pct: float


def ndcg_at_k(relevance_scores: list[float], k: int = 10) -> float:
    """Compute NDCG@k (Normalized Discounted Cumulative Gain)."""
    if not relevance_scores:
        return 0.0

    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevance_scores[:k]))
    ideal = sorted(relevance_scores, reverse=True)[:k]
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))

    return dcg / idcg if idcg > 0 else 0.0


def mrr(ranks: list[int]) -> float:
    """Mean Reciprocal Rank."""
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r > 0) / len(ranks)


class ABTest:
    """Run A/B comparison between two ranking functions."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.results: list[ABTestResult] = []

    def compare(
        self,
        query: str,
        scores_a: list[float],
        scores_b: list[float],
        k: int = 10,
    ) -> ABTestResult:
        ndcg_a = ndcg_at_k(scores_a, k)
        ndcg_b = ndcg_at_k(scores_b, k)
        diff = ndcg_b - ndcg_a
        pct = (diff / ndcg_a * 100) if ndcg_a > 0 else 0.0
        winner = "B" if diff > 0.01 else ("A" if diff < -0.01 else "tie")
        result = ABTestResult(
            test_name=self.name,
            query=query,
            variant_a_ndcg=round(ndcg_a, 4),
            variant_b_ndcg=round(ndcg_b, 4),
            winner=winner,
            improvement_pct=round(pct, 2),
        )
        self.results.append(result)
        return result

    def summary(self) -> dict[str, object]:
        wins = Counter(r.winner for r in self.results)
        return {
            "test": self.name,
            "total": len(self.results),
            "A_wins": wins.get("A", 0),
            "B_wins": wins.get("B", 0),
            "ties": wins.get("tie", 0),
            "avg_improvement": (
                round(
                    sum(r.improvement_pct for r in self.results) / len(self.results),
                    2,
                )
                if self.results
                else 0.0
            ),
        }


# ── #3: Domain-Specific Ranking Profiles ───────────────────────────


@dataclass(frozen=True)
class RankingProfile:
    """Weighted ranking profile for a domain category."""

    name: str
    bm25_weight: float = 0.40
    freshness_weight: float = 0.15
    trust_weight: float = 0.10
    authority_weight: float = 0.15
    title_weight: float = 0.15
    url_weight: float = 0.05


RANKING_PROFILES: dict[str, RankingProfile] = {
    "default": RankingProfile(name="default"),
    "tech-docs": RankingProfile(
        name="tech-docs",
        bm25_weight=0.50,
        freshness_weight=0.05,
        title_weight=0.20,
        url_weight=0.10,
    ),
    "news": RankingProfile(
        name="news",
        bm25_weight=0.25,
        freshness_weight=0.45,
        trust_weight=0.15,
        authority_weight=0.10,
        title_weight=0.05,
    ),
    "academic": RankingProfile(
        name="academic",
        bm25_weight=0.35,
        freshness_weight=0.05,
        trust_weight=0.20,
        authority_weight=0.25,
        title_weight=0.10,
        url_weight=0.05,
    ),
}


def get_profile(name: str) -> RankingProfile:
    """Get a ranking profile by name."""
    return RANKING_PROFILES.get(name, RANKING_PROFILES["default"])


def detect_domain_category(url: str) -> str:
    """Heuristic domain category detection."""
    domain = url.lower()
    if any(
        d in domain
        for d in [
            "docs.",
            "documentation",
            "readthedocs",
            "devdocs",
            "developer.",
            "api.",
        ]
    ):
        return "tech-docs"
    if any(d in domain for d in ["news", "bbc", "reuters", "cnn", "nytimes"]):
        return "news"
    if any(
        d in domain
        for d in [
            "arxiv",
            "scholar",
            "academic",
            "ieee",
            "springer",
            "pubmed",
        ]
    ):
        return "academic"
    return "default"


# ── #4: Cache Pre-warming ──────────────────────────────────────────


@dataclass
class PrewarmConfig:
    """Configuration for cache pre-warming."""

    popular_queries: list[str] = field(default_factory=list)
    max_queries: int = 100
    interval_seconds: int = 3600


DEFAULT_PREWARM_QUERIES = [
    "python tutorial",
    "javascript async await",
    "react hooks",
    "docker compose",
    "kubernetes deployment",
    "git rebase",
    "sql join",
    "css flexbox",
    "rust ownership",
    "golang goroutine",
]


# ── #6: Result Clustering ─────────────────────────────────────────


@dataclass
class ResultCluster:
    """A cluster of similar search results."""

    domain: str
    results: list[dict[str, object]] = field(default_factory=list)
    representative_title: str = ""


def cluster_results(
    results: list[dict[str, object]],
    max_per_domain: int = 3,
) -> list[ResultCluster]:
    """Cluster search results by domain for diversity."""
    from urllib.parse import urlparse

    clusters: dict[str, ResultCluster] = {}

    for r in results:
        url = str(r.get("url", ""))
        try:
            domain = urlparse(url).netloc
        except Exception:
            domain = "unknown"

        if domain not in clusters:
            clusters[domain] = ResultCluster(
                domain=domain,
                representative_title=str(r.get("title", "")),
            )
        if len(clusters[domain].results) < max_per_domain:
            clusters[domain].results.append(r)

    return list(clusters.values())


def diversify_results(
    results: list[dict[str, object]],
    max_per_domain: int = 3,
) -> list[dict[str, object]]:
    """Reorder results for domain diversity (round-robin)."""
    from urllib.parse import urlparse

    by_domain: dict[str, list[dict[str, object]]] = {}
    for r in results:
        url = str(r.get("url", ""))
        try:
            domain = urlparse(url).netloc
        except Exception:
            domain = "unknown"
        by_domain.setdefault(domain, []).append(r)

    diversified: list[dict[str, object]] = []
    domains = list(by_domain.keys())
    idx = 0
    while domains:
        domain = domains[idx % len(domains)]
        items = by_domain[domain]
        if items:
            diversified.append(items.pop(0))
        if (
            not items
            or len([r for r in diversified if _get_domain(r) == domain])
            >= max_per_domain
        ):
            domains.remove(domain)
        if domains:
            idx = (idx + 1) % len(domains) if domains else 0

    return diversified


def _get_domain(r: dict[str, object]) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(str(r.get("url", ""))).netloc
    except Exception:
        return "unknown"


# ── #7: Temporal Search ────────────────────────────────────────────


_TEMPORAL_PATTERNS = [
    (r"\b(?:today|tonight)\b", 1),
    (r"\byesterday\b", 2),
    (r"\bthis\s+week\b", 7),
    (r"\blast\s+week\b", 14),
    (r"\bthis\s+month\b", 30),
    (r"\blast\s+month\b", 60),
    (r"\bthis\s+year\b", 365),
    (r"\blast\s+year\b", 730),
    (r"\blast\s+(\d+)\s+days?\b", -1),  # dynamic
    (r"\b(?:latest|newest|recent)\b", 7),
    (r"\b20[2-3]\d\b", -2),  # year mention
]


def extract_temporal_hint(query: str) -> int | None:
    """Extract recency_days from temporal query patterns.

    Returns number of days for recency filter, or None.
    """
    for pattern, days in _TEMPORAL_PATTERNS:
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            if days == -1:
                return int(m.group(1))
            if days == -2:
                year = int(m.group(0))
                now_year = time.localtime().tm_year
                return max(1, (now_year - year + 1) * 365)
            return days
    return None


# ── #8: Lightweight Intent Classification ──────────────────────────


class QueryIntentClassifier:
    """Rule-based query intent classifier (no ML dependency)."""

    INTENTS = {
        "how_to": [
            r"\bhow\s+(?:to|do|can|does)\b",
            r"\btutorial\b",
            r"\bguide\b",
            r"\bstep.by.step\b",
        ],
        "definition": [
            r"\bwhat\s+is\b",
            r"\bdefin(?:e|ition)\b",
            r"\bmeaning\s+of\b",
        ],
        "comparison": [
            r"\bvs\.?\b",
            r"\bversus\b",
            r"\bcompare\b",
            r"\bdifference\s+between\b",
            r"\bor\b.*\bwhich\b",
        ],
        "error_debug": [
            r"\berror\b",
            r"\bexception\b",
            r"\btraceback\b",
            r"\bfailed?\b",
            r"\bnot\s+work",
            r"\bbug\b",
        ],
        "api_reference": [
            r"\bapi\b",
            r"\bfunction\b.*\bsignature\b",
            r"\bmethod\b.*\bparameter",
            r"\breturn\s+type\b",
        ],
        "navigational": [
            r"\blogin\b",
            r"\bofficial\b",
            r"\bhomepage\b",
            r"\bdownload\b",
            r"\.(?:com|org|io|dev)$",
        ],
    }

    def classify(self, query: str) -> str:
        """Classify query intent. Returns intent name."""
        for intent, patterns in self.INTENTS.items():
            for p in patterns:
                if re.search(p, query, re.IGNORECASE):
                    return intent
        return "informational"

    def classify_with_confidence(
        self,
        query: str,
    ) -> tuple[str, float]:
        """Classify with confidence score."""
        scores: dict[str, int] = {}
        for intent, patterns in self.INTENTS.items():
            score = sum(1 for p in patterns if re.search(p, query, re.IGNORECASE))
            if score > 0:
                scores[intent] = score

        if not scores:
            return "informational", 0.3

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        conf = min(1.0, scores[best] / 3.0)
        return best, round(conf, 2)
