"""LLM-based search result re-ranking.

Takes top-N search results and uses a local LLM to re-rank them
by semantic relevance to the original query.  This is a post-processing
step applied after the initial BM25/freshness/authority ranking.
"""

from __future__ import annotations

import json
import re

import structlog

from infomesh.index.ranking import RankedResult

logger = structlog.get_logger()

# Maximum number of candidates to send to the LLM for re-ranking
MAX_RERANK_CANDIDATES = 20

# Prompt template for the LLM re-ranker
_RERANK_PROMPT = (
    "You are a search result relevance judge. "
    "Given a search query and a list of search results, "
    "re-rank them by relevance to the query.\n\n"
    "Query: {query}\n\n"
    "Results:\n{results_block}\n\n"
    "Return ONLY a JSON array of result numbers "
    "in order of relevance, most relevant first.\n"
    "Example: [3, 1, 5, 2, 4]\n\n"
    "Your ranking (JSON array only):"
)


def _build_results_block(results: list[RankedResult], *, max_snippet: int = 150) -> str:
    """Format results into a numbered block for the LLM prompt."""
    lines = []
    for i, r in enumerate(results, 1):
        snippet = r.snippet[:max_snippet].replace("\n", " ")
        lines.append(f"{i}. [{r.title}] {snippet}")
    return "\n".join(lines)


def _parse_ranking_response(response: str, count: int) -> list[int] | None:
    """Parse the LLM's JSON array response into 0-based indices.

    Returns None if the response cannot be parsed or is invalid.
    """
    # Try to extract a JSON array from the response
    match = re.search(r"\[[\d\s,]*\]", response)
    if not match:
        return None

    try:
        indices = json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        return None

    # Validate: must be list of ints in valid range
    if not isinstance(indices, list):
        return None

    if not indices:
        # Empty array → return original order
        return list(range(count))

    # Convert 1-based to 0-based
    zero_based = []
    seen = set()
    for idx in indices:
        if not isinstance(idx, int) or idx < 1 or idx > count:
            continue
        if idx - 1 not in seen:
            zero_based.append(idx - 1)
            seen.add(idx - 1)

    # Add any missing indices at the end (in original order)
    for i in range(count):
        if i not in seen:
            zero_based.append(i)

    return zero_based


async def rerank_with_llm(
    query: str,
    results: list[RankedResult],
    llm_backend: object,  # LLMBackend
    *,
    top_n: int | None = None,
    max_candidates: int = MAX_RERANK_CANDIDATES,
) -> list[RankedResult]:
    """Re-rank search results using a local LLM.

    Takes the top ``max_candidates`` results, sends them to the LLM
    with the query, and re-orders based on the LLM's relevance
    judgement.  Results beyond ``max_candidates`` are appended in
    their original order.

    Args:
        query: Original search query.
        results: Pre-ranked search results.
        llm_backend: An LLM backend instance (OllamaBackend or LlamaCppBackend).
        top_n: If set, return only the top N results after re-ranking.
        max_candidates: Maximum results to include in the LLM prompt.

    Returns:
        Re-ranked list of RankedResult. On LLM failure, returns
        the original list unchanged.
    """
    from infomesh.summarizer.engine import LLMBackend

    if not isinstance(llm_backend, LLMBackend):
        logger.warning(
            "reranker_invalid_backend",
            backend_type=type(llm_backend).__name__,
        )
        return results

    if not results:
        return results

    # Split into candidates (for LLM) and remainder
    candidates = results[:max_candidates]
    remainder = results[max_candidates:]

    # Build and send prompt
    prompt = _RERANK_PROMPT.format(
        query=query,
        results_block=_build_results_block(candidates),
    )

    try:
        available = await llm_backend.is_available()
        if not available:
            logger.debug("reranker_llm_unavailable")
            return results

        response = await llm_backend.generate(prompt, max_tokens=256)

        indices = _parse_ranking_response(response, len(candidates))
        if indices is None:
            logger.warning("reranker_parse_failed", response=response[:200])
            return results

        # Re-order candidates according to LLM ranking
        reranked = [candidates[i] for i in indices]
        reranked.extend(remainder)

        logger.info(
            "reranked_with_llm",
            query=query[:60],
            candidates=len(candidates),
            reranked=len(reranked),
        )

        final = reranked[:top_n] if top_n else reranked
        return final

    except Exception as exc:
        logger.error("reranker_error", error=str(exc))
        return results
