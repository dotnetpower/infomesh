"""Search result formatting — shared by CLI, MCP, and dashboard.

Extracts the duplicated result-rendering logic into a single module
so formatting changes only need to happen in one place.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from infomesh.index.ranking import RankedResult
from infomesh.search.merge import MergedResult
from infomesh.search.query import DistributedResult, HybridResult, QueryResult


def format_fts_results(result: QueryResult, *, max_snippet: int = 200) -> str:
    """Format FTS-only search results as plain text.

    Args:
        result: Query result from ``search_local()``.
        max_snippet: Maximum characters for each snippet.

    Returns:
        Formatted multi-line string.
    """
    if not result.results:
        return "No results found."

    lines = [f"Found {result.total} results ({result.elapsed_ms:.0f}ms):\n"]
    for i, r in enumerate(result.results, 1):
        lines.append(_format_ranked(i, r, max_snippet=max_snippet))
    return "\n".join(lines)


def format_hybrid_results(
    hybrid: HybridResult,
    *,
    max_snippet: int = 200,
) -> str:
    """Format hybrid search results as plain text.

    Args:
        hybrid: Result from ``search_hybrid()``.
        max_snippet: Maximum characters for each snippet.

    Returns:
        Formatted multi-line string.
    """
    if not hybrid.results:
        return "No results found."

    lines = [
        f"Found {hybrid.total} results ({hybrid.elapsed_ms:.0f}ms, {hybrid.source}):\n"
    ]
    for i, r in enumerate(hybrid.results, 1):
        lines.append(_format_merged(i, r, max_snippet=max_snippet))
    return "\n".join(lines)


def format_distributed_results(
    result: DistributedResult,
    *,
    max_snippet: int = 200,
) -> str:
    """Format distributed (local + DHT) search results as plain text.

    Args:
        result: Result from ``search_distributed()``.
        max_snippet: Maximum characters for each snippet.

    Returns:
        Formatted multi-line string.
    """
    if not result.results:
        return "No results found."

    lines = [
        f"Found {result.total} results "
        f"({result.elapsed_ms:.0f}ms, {result.source})\n"
        f"  Local: {result.local_count}, Remote: {result.remote_count}\n"
    ]
    for i, r in enumerate(result.results, 1):
        lines.append(_format_ranked(i, r, max_snippet=max_snippet))
    return "\n".join(lines)


# ── internal helpers ───────────────────────────────────────────────


def _format_ranked(idx: int, r: RankedResult, *, max_snippet: int = 200) -> str:
    """Format a single FTS-ranked result."""
    domain = urlparse(r.url).netloc
    return (
        f"[{idx}] {r.title}\n"
        f"    Source: {r.url}\n"
        f"    Domain: {domain}\n"
        f"    Score: {r.combined_score:.4f} "
        f"(BM25={r.bm25_score:.3f}, "
        f"fresh={r.freshness_score:.3f}, "
        f"trust={r.trust_score:.3f}, "
        f"auth={r.authority_score:.3f})\n"
        f"    {r.snippet[:max_snippet]}\n"
    )


def _format_merged(idx: int, r: MergedResult, *, max_snippet: int = 200) -> str:
    """Format a single hybrid (merged) result."""
    score_parts: list[str] = []
    if r.fts_score is not None:
        score_parts.append(f"BM25={r.fts_score:.3f}")
    if r.vector_score is not None:
        score_parts.append(f"sim={r.vector_score:.3f}")
    score_str = ", ".join(score_parts) if score_parts else "N/A"

    domain = urlparse(r.url).netloc
    snippet = r.snippet[:max_snippet]

    return (
        f"[{idx}] {r.title} [{r.source}]\n"
        f"    Source: {r.url}\n"
        f"    Domain: {domain}\n"
        f"    Score: {score_str} (RRF={r.combined_score:.4f})\n"
        f"    {snippet}\n"
    )


def format_fetch_result(
    *,
    title: str,
    url: str,
    text: str,
    is_cached: bool,
    crawled_at: float = 0.0,
    cache_ttl: float = 604_800,
    is_paywall: bool = False,
) -> str:
    """Format a fetched page for MCP / CLI consumption.

    Produces a metadata header followed by the page text.
    """
    domain = urlparse(url).netloc
    if is_cached:
        age = time.time() - crawled_at
        cache_age_days = age / 86400
        stale = age > cache_ttl
        meta = (
            f"# {title}\n"
            f"Source: {url}\n"
            f"Domain: {domain}\n"
            f"is_cached: true\n"
            f"cache_age: {cache_age_days:.1f} days\n"
            f"crawl_timestamp: {crawled_at:.0f}\n"
        )
        if stale:
            meta += "stale_warning: true (cached content older than TTL)\n"
    else:
        meta = (
            f"# {title}\n"
            f"Source: {url}\n"
            f"Domain: {domain}\n"
            f"is_cached: false\n"
            f"cache_age: 0 days (freshly crawled)\n"
        )
        if is_paywall:
            meta += (
                "paywall_warning: Content may be behind "
                "a paywall (partial content returned)\n"
            )
    return f"{meta}\n{text}"
