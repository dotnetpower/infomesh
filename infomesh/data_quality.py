"""Data quality utilities — freshness indicators, citation extraction.

Features:
- #91: Freshness indicator (last crawled, change frequency)
- #92: Source trust grade (human-readable)
- #94: Citation/reference extraction from text
- #95: Fact-checking cross-reference support
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from infomesh.index.ranking import RankedResult

# ── #91: Freshness indicator ─────────────────────────────────────


@dataclass(frozen=True)
class FreshnessIndicator:
    """Human-readable freshness metadata for a result."""

    crawled_at: float
    age_seconds: float
    age_label: str  # "just now", "1 hour ago", "3 days ago", etc.
    freshness_grade: str  # "A", "B", "C", "D", "F"


def compute_freshness_indicator(
    crawled_at: float,
    *,
    now: float | None = None,
) -> FreshnessIndicator:
    """Compute a human-readable freshness indicator.

    Args:
        crawled_at: Unix timestamp of crawl.
        now: Current time (defaults to time.time()).

    Returns:
        FreshnessIndicator with label and grade.
    """
    now = now or time.time()
    age = max(0.0, now - crawled_at)

    # Age label
    if age < 60:
        label = "just now"
    elif age < 3600:
        mins = int(age / 60)
        label = f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif age < 86400:
        hours = int(age / 3600)
        label = f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif age < 604800:
        days = int(age / 86400)
        label = f"{days} day{'s' if days != 1 else ''} ago"
    elif age < 2592000:
        weeks = int(age / 604800)
        label = f"{weeks} week{'s' if weeks != 1 else ''} ago"
    else:
        months = int(age / 2592000)
        label = f"{months} month{'s' if months != 1 else ''} ago"

    # Grade
    if age < 86400:  # < 1 day
        grade = "A"
    elif age < 604800:  # < 1 week
        grade = "B"
    elif age < 2592000:  # < 30 days
        grade = "C"
    elif age < 7776000:  # < 90 days
        grade = "D"
    else:
        grade = "F"

    return FreshnessIndicator(
        crawled_at=crawled_at,
        age_seconds=age,
        age_label=label,
        freshness_grade=grade,
    )


# ── #92: Source trust grade ──────────────────────────────────────


@dataclass(frozen=True)
class TrustGrade:
    """Human-readable trust classification."""

    score: float
    grade: str  # "A+", "A", "B", "C", "D", "F"
    label: str  # "Highly Trusted", "Trusted", etc.
    color: str  # For UI rendering


def compute_trust_grade(trust_score: float) -> TrustGrade:
    """Convert a numeric trust score to a human-readable grade.

    Args:
        trust_score: Trust score between 0.0 and 1.0.

    Returns:
        TrustGrade with grade, label, and color.
    """
    if trust_score >= 0.9:
        return TrustGrade(trust_score, "A+", "Highly Trusted", "green")
    if trust_score >= 0.8:
        return TrustGrade(trust_score, "A", "Trusted", "green")
    if trust_score >= 0.65:
        return TrustGrade(trust_score, "B", "Reliable", "blue")
    if trust_score >= 0.5:
        return TrustGrade(trust_score, "C", "Moderate", "yellow")
    if trust_score >= 0.3:
        return TrustGrade(trust_score, "D", "Low Trust", "orange")
    return TrustGrade(trust_score, "F", "Untrusted", "red")


# ── #94: Citation extraction ─────────────────────────────────────


@dataclass
class Citation:
    """An extracted citation/reference from text."""

    text: str
    citation_type: str  # "url", "doi", "isbn", "arxiv", "rfc"
    identifier: str  # The actual URL/DOI/ISBN
    context: str = ""  # Surrounding text


# Patterns for common citation types
_DOI_PATTERN = re.compile(r"\b(10\.\d{4,}/[^\s]+)\b")
_ISBN_PATTERN = re.compile(
    r"\b((?:978|979)[-\s]?\d[-\s]?\d{2,7}[-\s]?\d{1,7}[-\s]?\d)\b"
)
_ARXIV_PATTERN = re.compile(
    r"\b((?:arXiv:)?\d{4}\.\d{4,5}(?:v\d+)?)\b",
    re.IGNORECASE,
)
_RFC_PATTERN = re.compile(
    r"\b(RFC\s*\d{1,5})\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"(https?://[^\s<>\"')\]]+)")


def extract_citations(text: str) -> list[Citation]:
    """Extract citations and references from text.

    Detects URLs, DOIs, ISBNs, arXiv IDs, and RFCs.

    Args:
        text: Input text to scan.

    Returns:
        List of Citation objects.
    """
    citations: list[Citation] = []
    seen: set[str] = set()

    patterns: list[tuple[re.Pattern[str], str]] = [
        (_DOI_PATTERN, "doi"),
        (_ISBN_PATTERN, "isbn"),
        (_ARXIV_PATTERN, "arxiv"),
        (_RFC_PATTERN, "rfc"),
        (_URL_PATTERN, "url"),
    ]

    for pattern, ctype in patterns:
        for m in pattern.finditer(text):
            identifier = m.group(1).strip()
            if identifier in seen:
                continue
            seen.add(identifier)

            # Extract surrounding context
            start = max(0, m.start() - 50)
            end = min(len(text), m.end() + 50)
            context = text[start:end].strip()

            citations.append(
                Citation(
                    text=m.group(0),
                    citation_type=ctype,
                    identifier=identifier,
                    context=context,
                )
            )

    return citations


# ── #95: Fact-checking cross-reference ───────────────────────────


@dataclass
class FactCheckResult:
    """Result of cross-referencing a claim across sources."""

    claim: str
    supporting_sources: int
    contradicting_sources: int
    confidence: float  # 0.0 to 1.0
    sources: list[str] = field(default_factory=list)
    verdict: str = ""  # "supported", "disputed", "unverified"


def cross_reference_results(
    claim: str,
    results: list[RankedResult],
    *,
    min_overlap: float = 0.3,
) -> FactCheckResult:
    """Cross-reference a claim against search results.

    Simple heuristic: checks how many results contain
    the key terms from the claim.

    Args:
        claim: The claim to verify.
        results: Search results to check against.
        min_overlap: Minimum term overlap ratio.

    Returns:
        FactCheckResult with verdict.
    """
    claim_terms = set(claim.lower().split())
    if not claim_terms:
        return FactCheckResult(
            claim=claim,
            supporting_sources=0,
            contradicting_sources=0,
            confidence=0.0,
            verdict="unverified",
        )

    supporting = 0
    sources: list[str] = []

    for r in results:
        text_terms = set((r.snippet or "").lower().split())
        overlap = len(claim_terms & text_terms) / len(claim_terms)
        if overlap >= min_overlap:
            supporting += 1
            sources.append(r.url)

    total = len(results)
    if total == 0:
        confidence = 0.0
        verdict = "unverified"
    elif supporting >= total * 0.5:
        confidence = min(supporting / total, 1.0)
        verdict = "supported"
    elif supporting == 0:
        confidence = 0.1
        verdict = "unverified"
    else:
        confidence = supporting / total
        verdict = "disputed"

    return FactCheckResult(
        claim=claim,
        supporting_sources=supporting,
        contradicting_sources=max(0, total - supporting),
        confidence=round(confidence, 3),
        sources=sources[:10],
        verdict=verdict,
    )
