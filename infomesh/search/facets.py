"""Faceted search, result clustering, and highlighting.

Features:
- #6: Faceted search (domain, language, date facet counts)
- #7: Search result clustering by topic
- #9: Query term highlighting in snippets
- #93: Search result dedup for returned results
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

from infomesh.index.ranking import RankedResult

# ── #6: Faceted search ─────────────────────────────────────────────


@dataclass
class FacetCounts:
    """Facet count aggregation from search results."""

    domains: dict[str, int] = field(default_factory=dict)
    languages: dict[str, int] = field(default_factory=dict)
    date_ranges: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {
            "domains": dict(
                sorted(
                    self.domains.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:20]
            ),
            "languages": dict(self.languages),
            "date_ranges": dict(self.date_ranges),
        }


def compute_facets(
    results: list[RankedResult],
    *,
    max_domains: int = 20,
) -> FacetCounts:
    """Compute facet counts from search results.

    Args:
        results: Ranked search results.
        max_domains: Max domain facets to return.

    Returns:
        FacetCounts with domain, language, and date aggregations.
    """
    import time as _time

    facets = FacetCounts()
    now = _time.time()

    domain_counter: Counter[str] = Counter()
    lang_counter: Counter[str] = Counter()
    date_counter: Counter[str] = Counter()

    for r in results:
        # Domain facet
        try:
            domain = urlparse(r.url).netloc
            if domain:
                domain_counter[domain] += 1
        except Exception:  # noqa: BLE001
            pass

        # Date facet
        age_days = (now - r.crawled_at) / 86400 if r.crawled_at else -1
        if age_days < 0:
            date_counter["unknown"] += 1
        elif age_days <= 1:
            date_counter["today"] += 1
        elif age_days <= 7:
            date_counter["this_week"] += 1
        elif age_days <= 30:
            date_counter["this_month"] += 1
        elif age_days <= 365:
            date_counter["this_year"] += 1
        else:
            date_counter["older"] += 1

    facets.domains = dict(domain_counter.most_common(max_domains))
    facets.languages = dict(lang_counter)
    facets.date_ranges = dict(date_counter)
    return facets


# ── #7: Search result clustering ──────────────────────────────────


@dataclass
class ResultCluster:
    """A cluster of related search results."""

    label: str
    results: list[RankedResult]
    score: float = 0.0


def cluster_results(
    results: list[RankedResult],
    *,
    max_clusters: int = 5,
    min_cluster_size: int = 2,
) -> list[ResultCluster]:
    """Cluster results by keyword overlap in title + snippet.

    Uses simple token overlap clustering (no ML required).

    Args:
        results: Ranked results to cluster.
        max_clusters: Maximum number of clusters.
        min_cluster_size: Minimum results per cluster.

    Returns:
        List of ResultCluster objects.
    """
    if len(results) < min_cluster_size:
        return []

    # Tokenize each result
    def _tokens(r: RankedResult) -> set[str]:
        text = f"{r.title} {r.snippet}".lower()
        return {w for w in re.findall(r"\w+", text) if len(w) > 3}

    result_tokens = [(r, _tokens(r)) for r in results]

    # Find common keywords
    all_tokens: Counter[str] = Counter()
    for _, toks in result_tokens:
        all_tokens.update(toks)

    # Use top keywords as cluster seeds
    top_keywords = [
        kw
        for kw, cnt in all_tokens.most_common(max_clusters * 3)
        if cnt >= min_cluster_size
    ]

    clusters: list[ResultCluster] = []
    used: set[int] = set()

    for kw in top_keywords:
        if len(clusters) >= max_clusters:
            break
        members: list[RankedResult] = []
        for i, (r, toks) in enumerate(result_tokens):
            if i not in used and kw in toks:
                members.append(r)
        if len(members) >= min_cluster_size:
            for i, (r, _) in enumerate(result_tokens):
                if r in members:
                    used.add(i)
            avg_score = sum(m.combined_score for m in members) / len(members)
            clusters.append(
                ResultCluster(
                    label=kw,
                    results=members,
                    score=avg_score,
                )
            )

    return sorted(clusters, key=lambda c: c.score, reverse=True)


# ── #9: Query term highlighting ───────────────────────────────────


def highlight_snippet(
    snippet: str,
    query: str,
    *,
    marker: str = "**",
) -> str:
    """Highlight query terms in snippet with markers.

    Args:
        snippet: Text snippet to highlight.
        query: Search query with terms to highlight.
        marker: Markup string to wrap terms with.

    Returns:
        Snippet with highlighted terms.
    """
    terms = set(query.lower().split())
    if not terms:
        return snippet

    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.lower() in terms:
            return f"{marker}{word}{marker}"
        return word

    pattern = r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b"
    return re.sub(pattern, _replace, snippet, flags=re.IGNORECASE)


# ── #93: Result dedup (post-search) ───────────────────────────────


def dedup_results(
    results: list[RankedResult],
    *,
    similarity_threshold: float = 0.7,
) -> list[RankedResult]:
    """Remove near-duplicate results from search output.

    Uses URL and title/snippet similarity check.

    Args:
        results: Ranked results.
        similarity_threshold: Jaccard threshold for dedup.

    Returns:
        Deduplicated result list.
    """
    if len(results) <= 1:
        return results

    seen_urls: set[str] = set()
    deduped: list[RankedResult] = []

    for r in results:
        # URL dedup (normalize trailing slash)
        norm_url = r.url.rstrip("/")
        if norm_url in seen_urls:
            continue

        # Title+snippet similarity check against existing results
        r_tokens = set(f"{r.title} {r.snippet}".lower().split())
        is_dup = False
        for existing in deduped:
            e_tokens = set(f"{existing.title} {existing.snippet}".lower().split())
            if r_tokens and e_tokens:
                jaccard = len(r_tokens & e_tokens) / len(r_tokens | e_tokens)
                if jaccard >= similarity_threshold:
                    is_dup = True
                    break

        if not is_dup:
            seen_urls.add(norm_url)
            deduped.append(r)

    return deduped
