"""Passage extraction and best-snippet selection.

Splits document text into passage-level chunks (paragraphs or sections),
scores each passage against the query, and returns the highest-scoring
passage as the snippet.  This produces *far* more relevant snippets than
FTS5's default ``snippet()`` function which simply returns ~40 tokens
around the first match.

Features:
- Paragraph / section splitting with configurable max length
- TF-based passage scoring against query tokens
- Query-term highlighting (``<b>`` tags)
- Title-match and URL-path relevance bonus helpers
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# ── Passage extraction ──────────────────────────────────────────────


_HEADING_RE = re.compile(r"\n#{1,6}\s+|\n[A-Z][^\n]{3,60}\n[=\-]{3,}")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class ScoredPassage:
    """A passage with its relevance score."""

    text: str
    score: float
    start: int  # character offset in original text
    end: int


def split_passages(
    text: str,
    *,
    max_length: int = 500,
    min_length: int = 40,
) -> list[str]:
    """Split document text into passage chunks.

    Strategy: split on double-newlines (paragraph boundaries) first.
    If a resulting chunk exceeds *max_length*, split further on
    single newlines, then on sentence boundaries.

    Args:
        text: Full document text.
        max_length: Maximum characters per passage.
        min_length: Minimum characters for a passage to be useful.

    Returns:
        List of passage strings.
    """
    if not text or not text.strip():
        return []

    # First pass: split on paragraph breaks
    raw_chunks = _PARAGRAPH_BREAK.split(text)
    passages: list[str] = []

    for chunk in raw_chunks:
        chunk = chunk.strip()
        if len(chunk) < min_length:
            # Merge tiny chunks with previous passage if possible
            if passages:
                passages[-1] = passages[-1] + " " + chunk
            continue

        if len(chunk) <= max_length:
            passages.append(chunk)
        else:
            # Split oversized chunks on sentence boundaries
            _split_long_chunk(chunk, max_length, min_length, passages)

    return passages


def _split_long_chunk(
    chunk: str,
    max_length: int,
    min_length: int,
    out: list[str],
) -> None:
    """Split a long chunk into smaller pieces via sentence boundaries."""
    # Try sentence splitting: ". " or "! " or "? " followed by a
    # capital letter or end of text.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\u3131-\u318E\u4E00-\u9FFF])", chunk)

    # If no sentence boundaries found, fall back to word-boundary splitting
    if len(sentences) <= 1 and len(chunk) > max_length:
        words = chunk.split()
        buf: list[str] = []
        buf_len = 0
        for w in words:
            if buf_len + len(w) > max_length and buf:
                out.append(" ".join(buf))
                buf = []
                buf_len = 0
            buf.append(w)
            buf_len += len(w) + 1
        if buf:
            out.append(" ".join(buf))
        return

    buf = []
    buf_len = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if buf_len + len(sent) > max_length and buf:
            out.append(" ".join(buf))
            buf = []
            buf_len = 0
        buf.append(sent)
        buf_len += len(sent) + 1  # +1 for space

    if buf:
        combined = " ".join(buf)
        if len(combined) >= min_length or not out:
            out.append(combined)
        elif out:
            out[-1] = out[-1] + " " + combined


# ── Passage scoring ────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(
        r"[a-zA-Z0-9\u3131-\u318E\uAC00-\uD7A3\u4E00-\u9FFF]+",
        text.lower(),
    )


def score_passage(
    passage: str,
    query_tokens: list[str],
) -> float:
    """Score a passage against query tokens using term frequency.

    Scoring = (matched_unique_terms / total_query_terms)
            + 0.1 * (total_hit_count / passage_token_count)

    The first component rewards coverage (how many query terms
    appear), the second rewards density (how concentrated they are).

    Args:
        passage: Passage text.
        query_tokens: Lowercased query term list.

    Returns:
        Score in [0, ~1.5] range, higher is better.
    """
    if not query_tokens or not passage:
        return 0.0

    p_tokens = _tokenize(passage)
    if not p_tokens:
        return 0.0

    p_set = set(p_tokens)
    query_set = set(query_tokens)

    # Coverage: fraction of query terms found
    matched = query_set & p_set
    coverage = len(matched) / len(query_set) if query_set else 0.0

    # Density: how many query token hits relative to passage length
    hit_count = sum(1 for t in p_tokens if t in query_set)
    density = hit_count / len(p_tokens)

    return coverage + 0.1 * density


def select_best_passage(
    text: str,
    query: str,
    *,
    max_length: int = 300,
    fallback_length: int = 200,
) -> str:
    """Select the most query-relevant passage from document text.

    If no passage scores above zero (no term overlap), returns the
    first *fallback_length* characters of the text.

    Args:
        text: Full document text.
        query: User search query.
        max_length: Maximum passage length.
        fallback_length: Characters to return when no passage matches.

    Returns:
        Best passage string (may be highlighted).
    """
    if not text or not query:
        return text[:fallback_length] if text else ""

    query_tokens = _tokenize(query)
    if not query_tokens:
        return text[:fallback_length]

    passages = split_passages(text, max_length=max_length)
    if not passages:
        return text[:fallback_length]

    best_passage = ""
    best_score = -1.0

    for p in passages:
        sc = score_passage(p, query_tokens)
        if sc > best_score:
            best_score = sc
            best_passage = p

    if best_score <= 0:
        return text[:fallback_length]

    return best_passage[:max_length]


# ── Highlighting ───────────────────────────────────────────────────


def highlight_terms(text: str, query_tokens: list[str]) -> str:
    """Wrap query term occurrences in ``<b>`` tags.

    Case-insensitive matching; preserves original casing.

    Args:
        text: Text to highlight.
        query_tokens: Lowercased query terms.

    Returns:
        Text with ``<b>...</b>`` around matched terms.
    """
    if not query_tokens or not text:
        return text

    # Build OR pattern from unique tokens, escape for regex safety
    unique = sorted(set(query_tokens), key=len, reverse=True)
    pattern = "|".join(re.escape(t) for t in unique if t)
    if not pattern:
        return text

    return re.sub(
        rf"\b({pattern})\b",
        r"<b>\1</b>",
        text,
        flags=re.IGNORECASE,
    )


# ── Title / URL relevance helpers ──────────────────────────────────


def title_match_score(title: str, query_tokens: list[str]) -> float:
    """Compute a 0–1 bonus score for title-query overlap.

    Returns the fraction of query tokens found in the title.
    A full match returns 1.0, partial returns proportionally.

    Args:
        title: Document title.
        query_tokens: Lowercased query terms.

    Returns:
        Score in [0, 1].
    """
    if not title or not query_tokens:
        return 0.0
    title_tokens = set(_tokenize(title))
    query_set = set(query_tokens)
    if not query_set:
        return 0.0
    return len(query_set & title_tokens) / len(query_set)


def url_path_score(url: str, query_tokens: list[str]) -> float:
    """Compute a 0–1 bonus score for URL path-query overlap.

    Extracts path segments from the URL and checks how many
    query tokens appear as substrings in path segments.

    Example: query "react hooks" + URL ".../docs/hooks/" → 0.5

    Args:
        url: Document URL.
        query_tokens: Lowercased query terms.

    Returns:
        Score in [0, 1].
    """
    if not url or not query_tokens:
        return 0.0

    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
    except Exception:
        return 0.0

    if not path or path == "/":
        return 0.0

    # Split path into segments and flatten to words
    segments = re.findall(r"[a-z0-9]+", path)
    if not segments:
        return 0.0

    seg_text = " ".join(segments)
    query_set = set(query_tokens)
    matched = sum(1 for t in query_set if t in seg_text)
    return matched / len(query_set) if query_set else 0.0


# ── Intent Classification ──────────────────────────────────────────


class QueryIntent:
    """Query intent classification result."""

    INFORMATIONAL = "informational"
    NAVIGATIONAL = "navigational"
    TRANSACTIONAL = "transactional"


_NAV_PATTERNS = [
    re.compile(r"\b(login|signin|sign\s+in|homepage|official)\b", re.I),
    re.compile(r"\b(go\s+to|open|visit|navigate)\b", re.I),
    re.compile(r"^[a-zA-Z0-9.-]+\.(com|org|net|io|dev|edu|gov)$"),
]

_TRANS_PATTERNS = [
    re.compile(
        r"\b(download|install|buy|purchase|subscribe|pricing)\b",
        re.I,
    ),
    re.compile(r"\b(free|trial|demo|signup|register)\b", re.I),
]


def classify_intent(query: str) -> str:
    """Classify query intent as informational/navigational/transactional.

    Uses pattern matching on the query text.  Default is informational.

    Args:
        query: User search query.

    Returns:
        One of ``QueryIntent.INFORMATIONAL``, ``NAVIGATIONAL``,
        or ``TRANSACTIONAL``.
    """
    if not query:
        return QueryIntent.INFORMATIONAL

    for pat in _NAV_PATTERNS:
        if pat.search(query):
            return QueryIntent.NAVIGATIONAL

    for pat in _TRANS_PATTERNS:
        if pat.search(query):
            return QueryIntent.TRANSACTIONAL

    return QueryIntent.INFORMATIONAL
