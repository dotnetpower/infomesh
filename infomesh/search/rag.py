"""RAG (Retrieval-Augmented Generation) and AI features.

Features:
- #82: RAG-optimized output format
- #83: Answer extraction from search results
- #85: Multi-result summarization
- #86: Entity extraction & knowledge graph
- #87: Toxicity/sentiment filtering
- #88: Chain-of-thought re-ranking (CoT)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from infomesh.index.ranking import RankedResult

# ── #82: RAG-optimized output format ──────────────────────────────


@dataclass
class RAGChunk:
    """A chunk of content optimized for RAG pipelines."""

    text: str
    url: str
    title: str
    score: float
    chunk_index: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "url": self.url,
            "title": self.title,
            "score": self.score,
            "chunk_index": self.chunk_index,
            "metadata": self.metadata,
        }


@dataclass
class RAGOutput:
    """RAG-formatted search output."""

    query: str
    chunks: list[RAGChunk]
    total_results: int
    context_window: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "chunks": [c.to_dict() for c in self.chunks],
            "total_results": self.total_results,
            "context_window": self.context_window,
        }


def format_rag_output(
    query: str,
    results: list[RankedResult],
    *,
    chunk_size: int = 500,
    max_chunks: int = 10,
    include_metadata: bool = True,
) -> RAGOutput:
    """Format search results for RAG consumption.

    Splits results into chunks suitable for LLM context windows.

    Args:
        query: Original search query.
        results: Ranked search results.
        chunk_size: Max characters per chunk.
        max_chunks: Maximum chunks to return.
        include_metadata: Include scoring metadata.

    Returns:
        RAGOutput with chunked content.
    """
    chunks: list[RAGChunk] = []

    for r in results:
        text = r.snippet or ""
        if len(text) <= chunk_size:
            metadata: dict[str, object] = {}
            if include_metadata:
                metadata = {
                    "bm25_score": r.bm25_score,
                    "freshness_score": r.freshness_score,
                    "trust_score": r.trust_score,
                    "crawled_at": r.crawled_at,
                }
            chunks.append(
                RAGChunk(
                    text=text,
                    url=r.url,
                    title=r.title,
                    score=r.combined_score,
                    chunk_index=0,
                    metadata=metadata,
                )
            )
        else:
            # Split into multiple chunks
            for i in range(0, len(text), chunk_size):
                chunk_text = text[i : i + chunk_size]
                chunks.append(
                    RAGChunk(
                        text=chunk_text,
                        url=r.url,
                        title=r.title,
                        score=r.combined_score,
                        chunk_index=i // chunk_size,
                        metadata={},
                    )
                )

        if len(chunks) >= max_chunks:
            break

    chunks = chunks[:max_chunks]

    # Build context window
    context_parts = []
    for c in chunks:
        context_parts.append(f"[Source: {c.title} ({c.url})]\n{c.text}")
    context_window = "\n\n---\n\n".join(context_parts)

    return RAGOutput(
        query=query,
        chunks=chunks,
        total_results=len(results),
        context_window=context_window,
    )


# ── #83: Answer extraction ────────────────────────────────────────


@dataclass(frozen=True)
class ExtractedAnswer:
    """An extracted answer from search results."""

    answer: str
    source_url: str
    source_title: str
    confidence: float
    context: str = ""


def extract_answers(
    query: str,
    results: list[RankedResult],
    *,
    max_answers: int = 3,
) -> list[ExtractedAnswer]:
    """Extract direct answers from search results.

    Uses heuristic sentence-level matching to find
    sentences that likely answer the query.

    Args:
        query: User query.
        results: Ranked search results.
        max_answers: Max answers to extract.

    Returns:
        List of extracted answers with confidence scores.
    """
    query_terms = set(query.lower().split())
    answers: list[ExtractedAnswer] = []

    for r in results:
        text = r.snippet or ""
        sentences = re.split(r"[.!?]+", text)

        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 10:
                continue

            # Score by term overlap
            words = set(sent.lower().split())
            overlap = len(query_terms & words)
            if overlap < 1:
                continue

            confidence = min(
                overlap / max(len(query_terms), 1) * 0.8 + r.combined_score * 0.2,
                1.0,
            )

            if confidence > 0.2:
                answers.append(
                    ExtractedAnswer(
                        answer=sent,
                        source_url=r.url,
                        source_title=r.title,
                        confidence=round(confidence, 3),
                        context=text[:200],
                    )
                )

    # Sort by confidence, return top N
    answers.sort(key=lambda a: a.confidence, reverse=True)
    return answers[:max_answers]


# ── #85: Search result summarization ──────────────────────────────


def build_summary_prompt(
    query: str,
    results: list[RankedResult],
    *,
    max_context: int = 3000,
) -> str:
    """Build an LLM prompt for multi-result summarization.

    Args:
        query: User query.
        results: Top search results.
        max_context: Max context chars for prompt.

    Returns:
        Formatted prompt string.
    """
    context_parts: list[str] = []
    char_count = 0

    for i, r in enumerate(results, 1):
        snippet = r.snippet or ""
        entry = f"[{i}] {r.title}\n{snippet}"
        if char_count + len(entry) > max_context:
            break
        context_parts.append(entry)
        char_count += len(entry)

    context = "\n\n".join(context_parts)
    return (
        f"Based on the following search results for "
        f'the query "{query}", provide a concise '
        f"summary that answers the query.\n\n"
        f"Search Results:\n{context}\n\n"
        f"Summary:"
    )


# ── #86: Entity extraction ───────────────────────────────────────


@dataclass
class Entity:
    """An extracted entity from text."""

    text: str
    entity_type: str  # "PERSON", "ORG", "PLACE", "TECH"
    count: int = 1
    source_urls: list[str] = field(default_factory=list)


# Simple pattern-based entity extraction
_TECH_PATTERNS = re.compile(
    r"\b(?:Python|JavaScript|TypeScript|Rust|Go|Java|C\+\+|"
    r"Ruby|Swift|Kotlin|React|Vue|Angular|Django|Flask|"
    r"FastAPI|Node\.js|Docker|Kubernetes|PostgreSQL|MySQL|"
    r"Redis|MongoDB|SQLite|AWS|Azure|GCP|Linux|macOS|"
    r"Windows|GitHub|GitLab|npm|pip|cargo)\b"
)

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


def extract_entities(
    text: str,
    *,
    source_url: str = "",
) -> list[Entity]:
    """Extract entities from text using pattern matching.

    Args:
        text: Input text.
        source_url: Source URL for tracking.

    Returns:
        List of extracted entities.
    """
    entities: dict[str, Entity] = {}

    # Tech entities
    for m in _TECH_PATTERNS.finditer(text):
        name = m.group(0)
        key = f"TECH:{name}"
        if key in entities:
            entities[key].count += 1
        else:
            entities[key] = Entity(
                text=name,
                entity_type="TECH",
                count=1,
                source_urls=[source_url] if source_url else [],
            )

    # Capitalized phrases as potential names/orgs
    for m in re.finditer(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b",
        text,
    ):
        name = m.group(1)
        if len(name) < 30:
            key = f"NAME:{name}"
            if key in entities:
                entities[key].count += 1
            else:
                entities[key] = Entity(
                    text=name,
                    entity_type="NAME",
                    count=1,
                    source_urls=([source_url] if source_url else []),
                )

    return sorted(
        entities.values(),
        key=lambda e: e.count,
        reverse=True,
    )


# ── #87: Toxicity/sentiment filtering ─────────────────────────────

_TOXIC_PATTERNS = re.compile(
    r"\b(hate|kill|violence|racist|sexist|porn|"
    r"gambling|drugs|scam|phishing|malware)\b",
    re.IGNORECASE,
)


def compute_toxicity_score(text: str) -> float:
    """Compute a simple toxicity score for text.

    Returns a score between 0.0 (clean) and 1.0 (toxic).
    Uses keyword matching as a lightweight heuristic.
    """
    if not text:
        return 0.0
    matches = _TOXIC_PATTERNS.findall(text)
    words = len(text.split())
    if words == 0:
        return 0.0
    ratio = len(matches) / words
    return min(ratio * 10, 1.0)


def filter_by_toxicity(
    results: list[RankedResult],
    *,
    threshold: float = 0.3,
) -> list[RankedResult]:
    """Filter search results by toxicity score.

    Args:
        results: Search results.
        threshold: Max toxicity score to include.

    Returns:
        Filtered results.
    """
    return [r for r in results if compute_toxicity_score(r.snippet or "") < threshold]


# ── #88: Chain-of-thought re-ranking prompt ───────────────────────


def build_cot_rerank_prompt(
    query: str,
    results: list[RankedResult],
    *,
    max_candidates: int = 10,
) -> str:
    """Build a chain-of-thought re-ranking prompt.

    Instead of single-shot scoring, asks the LLM to
    reason through relevance step by step.

    Args:
        query: User search query.
        results: Candidate results for re-ranking.
        max_candidates: Max candidates in prompt.

    Returns:
        CoT prompt string.
    """
    candidates: list[str] = []
    for i, r in enumerate(results[:max_candidates], 1):
        snippet = (r.snippet or "")[:200]
        candidates.append(f"{i}. [{r.title}] {snippet}")

    candidate_text = "\n".join(candidates)
    return (
        f'Query: "{query}"\n\n'
        f"Candidates:\n{candidate_text}\n\n"
        f"For each candidate, think step by step:\n"
        f"1. What is this result about?\n"
        f"2. Does it directly answer the query?\n"
        f"3. How relevant is it? (1-10)\n\n"
        f"Then return a JSON array of objects "
        f'with "index" and "score" fields, '
        f"sorted by relevance (highest first)."
    )
