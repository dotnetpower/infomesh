"""CJK tokenization helpers for search and indexing.

SQLite FTS5's default ``unicode61`` tokenizer splits on whitespace,
which works for European languages but fails for CJK (Chinese,
Japanese, Korean) where words are not space-delimited.

This module provides:
- Character n-gram generation for CJK text (bigrams/trigrams)
- Language-aware tokenizer recommendation
- Query-side CJK token expansion

No external dependencies required — uses character-level splitting
as a zero-dependency fallback.  For higher accuracy, optional
packages (``jieba`` for Chinese, ``konlpy`` or ``mecab`` for
Korean/Japanese) can be used.
"""

from __future__ import annotations

import re

# ── CJK Unicode ranges ─────────────────────────────────────────────────

# CJK Unified Ideographs + extensions
_CJK_RE = re.compile(
    r"[\u4E00-\u9FFF"  # CJK Unified Ideographs
    r"\u3400-\u4DBF"  # CJK Extension A
    r"\uF900-\uFAFF"  # CJK Compatibility
    r"\U00020000-\U0002A6DF"  # Extension B
    r"\u3000-\u303F"  # CJK Punctuation
    r"]"
)

_HANGUL_RE = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")

_KANA_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")

# Additional non-space-delimited scripts
_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _is_non_latin_char(ch: str) -> bool:
    """Check if a character belongs to any non-Latin script we handle."""
    return bool(
        _CJK_RE.match(ch)
        or _HANGUL_RE.match(ch)
        or _KANA_RE.match(ch)
        or _THAI_RE.match(ch)
    )


def is_cjk_text(text: str, threshold: float = 0.3) -> bool:
    """Check if text contains a significant proportion of CJK/Thai characters.

    Args:
        text: Input text.
        threshold: Minimum ratio of CJK/Hangul/Kana/Thai chars to total.

    Returns:
        ``True`` if the text is likely CJK or Thai.
    """
    if not text:
        return False
    total = sum(1 for ch in text if not ch.isspace())
    if total == 0:
        return False
    cjk_count = len(_CJK_RE.findall(text))
    hangul_count = len(_HANGUL_RE.findall(text))
    kana_count = len(_KANA_RE.findall(text))
    thai_count = len(_THAI_RE.findall(text))
    return (cjk_count + hangul_count + kana_count + thai_count) / total >= threshold


def cjk_bigrams(text: str) -> list[str]:
    """Generate character bigrams from CJK text.

    Non-CJK tokens (Latin words, numbers) are kept intact.

    Args:
        text: Input text (mixed CJK + Latin is fine).

    Returns:
        List of bigram tokens and preserved Latin tokens.
    """
    tokens: list[str] = []
    cjk_buf: list[str] = []
    latin_buf: list[str] = []

    def _flush_cjk() -> None:
        if cjk_buf:
            tokens.extend(_make_ngrams(cjk_buf, 2))
            cjk_buf.clear()

    def _flush_latin() -> None:
        if latin_buf:
            tokens.append("".join(latin_buf))
            latin_buf.clear()

    for ch in text:
        if _is_non_latin_char(ch):
            _flush_latin()
            cjk_buf.append(ch)
        elif ch.isascii() and ch.isalnum():
            _flush_cjk()
            latin_buf.append(ch)
        else:
            _flush_cjk()
            _flush_latin()

    _flush_cjk()
    _flush_latin()
    return tokens


def cjk_trigrams(text: str) -> list[str]:
    """Generate character trigrams from CJK/Thai text.

    Same as :func:`cjk_bigrams` but with 3-character windows.
    Useful for FTS5 ``trigram`` tokenizer compatibility.
    """
    tokens: list[str] = []
    cjk_buf: list[str] = []
    latin_buf: list[str] = []

    def _flush_cjk() -> None:
        if cjk_buf:
            tokens.extend(_make_ngrams(cjk_buf, 3))
            cjk_buf.clear()

    def _flush_latin() -> None:
        if latin_buf:
            tokens.append("".join(latin_buf))
            latin_buf.clear()

    for ch in text:
        if _is_non_latin_char(ch):
            _flush_latin()
            cjk_buf.append(ch)
        elif ch.isascii() and ch.isalnum():
            _flush_cjk()
            latin_buf.append(ch)
        else:
            _flush_cjk()
            _flush_latin()

    _flush_cjk()
    _flush_latin()
    return tokens


def _make_ngrams(chars: list[str], n: int) -> list[str]:
    """Create n-grams from a list of characters.

    If the character list is shorter than *n*, returns the full
    string as a single token.
    """
    if len(chars) < n:
        return ["".join(chars)]
    return ["".join(chars[i : i + n]) for i in range(len(chars) - n + 1)]


# ── Tokenizer recommendation ───────────────────────────────────────────


def recommend_tokenizer(sample_text: str) -> str:
    """Recommend the best FTS5 tokenizer based on text content.

    Args:
        sample_text: Representative text from the index.

    Returns:
        Tokenizer name: ``"trigram"`` for CJK-heavy content,
        ``"unicode61"`` otherwise.
    """
    if is_cjk_text(sample_text, threshold=0.2):
        return "trigram"
    return "unicode61"


# ── CJK-aware query processing ─────────────────────────────────────────


def tokenize_query_cjk(query: str) -> str:
    """Prepare a query string for FTS5 search with CJK awareness.

    For CJK characters, generates bigrams that can match against
    the trigram tokenizer. Latin words are kept as-is.

    Args:
        query: Raw user query.

    Returns:
        Processed query string suitable for FTS5.
    """
    if not is_cjk_text(query, threshold=0.2):
        return query

    tokens = cjk_bigrams(query)
    # Join with spaces — FTS5 will match any token
    return " ".join(tokens) if tokens else query


# ── Optional integrations (lazy imports) ────────────────────────────────


def segment_chinese(text: str) -> list[str]:
    """Segment Chinese text using jieba (if available).

    Falls back to character bigrams if jieba is not installed.
    """
    try:
        import jieba

        return list(jieba.cut(text))
    except ImportError:
        return cjk_bigrams(text)


def segment_korean(text: str) -> list[str]:
    """Segment Korean text using basic syllable splitting.

    For better accuracy, install ``konlpy`` or ``mecab-python3``.
    Falls back to character bigrams.
    """
    # Simple approach: Korean syllables are self-contained units
    # Each Hangul syllable block is a meaningful unit
    tokens: list[str] = []
    current: list[str] = []

    for ch in text:
        if _HANGUL_RE.match(ch):
            current.append(ch)
        else:
            if current:
                # Group consecutive Hangul into tokens of 2-3 chars
                word = "".join(current)
                if len(word) <= 4:
                    tokens.append(word)
                else:
                    tokens.extend(_make_ngrams(list(word), 2))
                current = []
            if ch.isalnum():
                if (
                    tokens
                    and tokens[-1][-1:].isalnum()
                    and not _HANGUL_RE.match(tokens[-1][-1:])
                ):
                    tokens[-1] += ch
                else:
                    tokens.append(ch)

    if current:
        word = "".join(current)
        if len(word) <= 4:
            tokens.append(word)
        else:
            tokens.extend(_make_ngrams(list(word), 2))

    return tokens
