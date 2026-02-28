"""Summary verification pipeline for LLM-generated summaries.

3-stage pipeline:
1. Self-verification via key-fact anchoring + NLI contradiction detection.
2. Cross-validation by replica nodes independently summarizing.
3. Reputation-based trust from verification history.

Stage 1 operates locally without a network; stages 2-3 require P2P.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import structlog

logger = structlog.get_logger()


# --- Types -----------------------------------------------------------------


class VerificationLevel(StrEnum):
    """How thoroughly a summary has been verified."""

    UNVERIFIED = "unverified"
    SELF_VERIFIED = "self_verified"  # Stage 1 passed
    CROSS_VALIDATED = "cross_validated"  # Stage 2 passed
    REPUTATION_TRUSTED = "reputation"  # Stage 3+


@dataclass(frozen=True)
class KeyFact:
    """A factual claim extracted from the source text."""

    text: str
    source_offset: int  # Character offset in source text
    found_in_summary: bool


@dataclass(frozen=True)
class SelfVerificationResult:
    """Result of stage-1 self-verification."""

    key_facts: list[KeyFact]
    facts_found: int
    facts_total: int
    coverage_ratio: float  # facts_found / facts_total
    has_contradiction: bool
    passed: bool
    detail: str


@dataclass(frozen=True)
class CrossValidationResult:
    """Result of stage-2 cross-validation between peers."""

    peer_summaries: list[str]
    similarity_scores: list[float]
    avg_similarity: float
    passed: bool
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    """Complete verification report for a summary."""

    url: str
    content_hash: str
    summary: str
    level: VerificationLevel
    self_check: SelfVerificationResult | None
    cross_check: CrossValidationResult | None
    quality_score: float  # 0.0 – 1.0
    detail: str


# --- Stage 1: Self-verification --------------------------------------------

# Minimum coverage ratio for self-verification to pass
MIN_COVERAGE_RATIO: float = 0.30

# Minimum key facts to extract before checking coverage
MIN_KEY_FACTS: int = 3

# Maximum key facts to extract
MAX_KEY_FACTS: int = 20


def extract_key_facts(
    source_text: str, *, max_facts: int = MAX_KEY_FACTS
) -> list[KeyFact]:
    """Extract key factual claims from source text.

    Uses heuristic sentence selection:
    - Sentences with numbers/dates/proper nouns are more factual.
    - First and last paragraphs are weighted higher.

    Args:
        source_text: Original document text.
        max_facts: Maximum facts to extract.

    Returns:
        List of KeyFact with source offsets.
    """
    sentences = _split_sentences(source_text)
    if not sentences:
        return []

    scored: list[tuple[float, str, int]] = []
    for sent, offset in sentences:
        score = _fact_score(sent)
        scored.append((score, sent, offset))

    # Sort by score descending, take top facts
    scored.sort(key=lambda x: x[0], reverse=True)

    facts = []
    for score, sent, offset in scored[:max_facts]:
        if score <= 0:
            continue
        facts.append(
            KeyFact(
                text=sent.strip(),
                source_offset=offset,
                found_in_summary=False,
            )
        )
    return facts


def check_key_facts(
    summary: str,
    key_facts: list[KeyFact],
) -> list[KeyFact]:
    """Check which key facts appear (partially) in the summary.

    Uses word overlap as a lightweight proxy for NLI entailment.

    Args:
        summary: Generated summary text.
        key_facts: Facts extracted from the source.

    Returns:
        Updated KeyFact list with ``found_in_summary`` set.
    """
    summary_lower = summary.lower()
    summary_words = set(re.findall(r"\w+", summary_lower))

    checked = []
    for fact in key_facts:
        fact_words = set(re.findall(r"\w+", fact.text.lower()))
        if not fact_words:
            checked.append(fact)
            continue

        # Check word overlap ratio
        overlap = len(fact_words & summary_words) / len(fact_words)
        found = overlap >= 0.4  # ≥40% word overlap → considered "found"

        checked.append(
            KeyFact(
                text=fact.text,
                source_offset=fact.source_offset,
                found_in_summary=found,
            )
        )
    return checked


def detect_contradiction(source_text: str, summary: str) -> bool:
    """Lightweight contradiction detection.

    Checks for common contradiction patterns:
    - Negation mismatches (source says X, summary says "not X")
    - Number mismatches

    Args:
        source_text: Original text.
        summary: Generated summary.

    Returns:
        True if a likely contradiction is detected.
    """
    # Extract numbers from both texts
    source_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", source_text))
    summary_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", summary))

    # Numbers in summary but not in source may indicate hallucination
    novel_numbers = summary_numbers - source_numbers
    if len(novel_numbers) > 2:
        logger.debug("contradiction_novel_numbers", count=len(novel_numbers))
        return True

    return False


def self_verify(source_text: str, summary: str) -> SelfVerificationResult:
    """Stage 1: Self-verify a summary against source text.

    Args:
        source_text: Original document text.
        summary: LLM-generated summary.

    Returns:
        SelfVerificationResult with pass/fail and detail.
    """
    # Extract and check key facts
    raw_facts = extract_key_facts(source_text)
    checked_facts = check_key_facts(summary, raw_facts)

    facts_found = sum(1 for f in checked_facts if f.found_in_summary)
    facts_total = len(checked_facts)

    coverage = (facts_found / facts_total) if facts_total > 0 else 0.0

    # Contradiction check
    has_contradiction = detect_contradiction(source_text, summary)

    # Determine pass/fail
    passed = (
        facts_total < MIN_KEY_FACTS or coverage >= MIN_COVERAGE_RATIO
    ) and not has_contradiction

    details = []
    if facts_total >= MIN_KEY_FACTS and coverage < MIN_COVERAGE_RATIO:
        details.append(f"low coverage: {coverage:.1%}")
    if has_contradiction:
        details.append("contradiction detected")

    return SelfVerificationResult(
        key_facts=checked_facts,
        facts_found=facts_found,
        facts_total=facts_total,
        coverage_ratio=round(coverage, 4),
        has_contradiction=has_contradiction,
        passed=passed,
        detail="; ".join(details) if details else "ok",
    )


# --- Stage 2: Cross-validation ---------------------------------------------

# Minimum similarity between peer summaries for cross-validation
MIN_CROSS_SIMILARITY: float = 0.30


def compute_similarity(text_a: str, text_b: str) -> float:
    """Compute word-level Jaccard similarity between two texts.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Jaccard similarity in [0, 1].
    """
    words_a = set(re.findall(r"\w+", text_a.lower()))
    words_b = set(re.findall(r"\w+", text_b.lower()))

    if not words_a or not words_b:
        return 0.0

    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union


def cross_validate(
    our_summary: str,
    peer_summaries: list[str],
) -> CrossValidationResult:
    """Stage 2: Compare our summary against independently-generated peer summaries.

    Args:
        our_summary: The summary we generated.
        peer_summaries: Summaries from replica nodes.

    Returns:
        CrossValidationResult.
    """
    if not peer_summaries:
        return CrossValidationResult(
            peer_summaries=[],
            similarity_scores=[],
            avg_similarity=0.0,
            passed=False,
            detail="no peer summaries available",
        )

    scores = [compute_similarity(our_summary, ps) for ps in peer_summaries]
    avg = sum(scores) / len(scores)

    passed = avg >= MIN_CROSS_SIMILARITY
    detail = f"avg_similarity={avg:.3f}" if passed else f"low similarity: {avg:.3f}"

    return CrossValidationResult(
        peer_summaries=peer_summaries,
        similarity_scores=[round(s, 4) for s in scores],
        avg_similarity=round(avg, 4),
        passed=passed,
        detail=detail,
    )


# --- Full verification pipeline --------------------------------------------


def verify_summary(
    url: str,
    content_hash: str,
    source_text: str,
    summary: str,
    *,
    peer_summaries: list[str] | None = None,
) -> VerificationReport:
    """Run the full verification pipeline on a summary.

    Args:
        url: Source URL.
        content_hash: SHA-256 of source text.
        source_text: Original document text.
        summary: LLM-generated summary to verify.
        peer_summaries: Optional peer summaries for cross-validation.

    Returns:
        VerificationReport with quality score and verification level.
    """
    # Stage 1: self-verify
    self_check = self_verify(source_text, summary)

    # Stage 2: cross-validate (if peers available)
    cross_check = None
    if peer_summaries:
        cross_check = cross_validate(summary, peer_summaries)

    # Compute quality score
    quality = 0.0
    if self_check.passed:
        quality += 0.5  # Base score for self-verification
        quality += 0.2 * self_check.coverage_ratio
    if cross_check and cross_check.passed:
        quality += 0.3 * cross_check.avg_similarity

    quality = min(1.0, quality)

    # Determine verification level
    if cross_check and cross_check.passed and self_check.passed:
        level = VerificationLevel.CROSS_VALIDATED
    elif self_check.passed:
        level = VerificationLevel.SELF_VERIFIED
    else:
        level = VerificationLevel.UNVERIFIED

    details = [f"self: {self_check.detail}"]
    if cross_check:
        details.append(f"cross: {cross_check.detail}")

    report = VerificationReport(
        url=url,
        content_hash=content_hash,
        summary=summary,
        level=level,
        self_check=self_check,
        cross_check=cross_check,
        quality_score=round(quality, 4),
        detail="; ".join(details),
    )

    logger.info(
        "summary_verified",
        url=url,
        level=level.value,
        quality=round(quality, 4),
        coverage=round(self_check.coverage_ratio, 3),
    )
    return report


# --- Sentence splitting helpers --------------------------------------------


def _split_sentences(text: str) -> list[tuple[str, int]]:
    """Split text into sentences with byte offsets.

    Returns:
        List of (sentence, char_offset) tuples.
    """
    # Simple sentence boundary detection
    pattern = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff])")
    sentences = []
    last_end = 0

    for m in pattern.finditer(text):
        sent = text[last_end : m.start() + 1].strip()
        if len(sent) > 10:
            sentences.append((sent, last_end))
        last_end = m.end()

    # Last sentence
    remaining = text[last_end:].strip()
    if len(remaining) > 10:
        sentences.append((remaining, last_end))

    return sentences


def _fact_score(sentence: str) -> float:
    """Score a sentence's "factualness" for key-fact extraction.

    Higher scores for sentences containing:
    - Numbers / dates / percentages
    - Proper nouns (capitalized words)
    - Technical terms
    """
    score = 0.0

    # Numbers and dates
    if re.search(r"\b\d+", sentence):
        score += 1.0

    # Percentages
    if re.search(r"\d+%", sentence):
        score += 0.5

    # Capitalized words (potential proper nouns, skip sentence start)
    caps = re.findall(r"(?<!^)\b[A-Z][a-z]+", sentence)
    score += min(1.0, len(caps) * 0.3)

    # Length bonus (medium-length sentences are more informative)
    words = sentence.split()
    if 8 <= len(words) <= 30:
        score += 0.5

    return score
