"""NLP utilities for search — stop words, query expansion, typo correction.

Features:
- #2: Multilingual stop words (en, ko, ja, zh, es, de, fr, pt, ru)
- #4: Query expansion via synonym/embedding similarity
- #5: "Did you mean?" typo correction (edit distance)
- #8: Natural language query understanding (date/domain extraction)
- #10: Related searches from co-occurrence
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# ── #2: Multilingual stop words ────────────────────────────────────

STOP_WORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        {
            "a",
            "an",
            "the",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "i",
            "me",
            "my",
            "we",
            "our",
            "you",
            "your",
            "he",
            "she",
            "him",
            "her",
            "they",
            "them",
            "their",
            "what",
            "which",
            "who",
            "whom",
            "how",
            "when",
            "where",
            "why",
            "if",
            "then",
            "so",
            "no",
            "not",
            "as",
            "up",
            "about",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "same",
            "than",
            "too",
            "very",
            "just",
            "also",
            "more",
            "most",
            "other",
            "some",
            "such",
            "only",
            "own",
            "each",
            "every",
            "all",
            "any",
            "both",
            "few",
            "many",
            "much",
            "over",
            "under",
            "again",
            "further",
            "once",
            "here",
            "there",
            "out",
            "off",
        }
    ),
    "ko": frozenset(
        {
            "이",
            "그",
            "저",
            "것",
            "수",
            "등",
            "들",
            "및",
            "에",
            "의",
            "를",
            "을",
            "는",
            "은",
            "가",
            "이다",
            "하다",
            "있다",
            "없다",
            "되다",
            "않다",
            "에서",
            "으로",
            "로",
            "와",
            "과",
            "도",
            "만",
            "까지",
            "부터",
            "보다",
            "처럼",
            "같이",
            "하고",
            "이고",
            "인",
            "한",
            "할",
            "합니다",
            "있습니다",
            "됩니다",
            "대한",
            "위한",
            "통한",
            "때문",
        }
    ),
    "ja": frozenset(
        {
            "の",
            "に",
            "は",
            "を",
            "た",
            "が",
            "で",
            "て",
            "と",
            "し",
            "れ",
            "さ",
            "ある",
            "いる",
            "も",
            "する",
            "から",
            "な",
            "こと",
            "として",
            "い",
            "や",
            "など",
            "なっ",
            "ない",
            "この",
            "ため",
            "その",
            "あっ",
            "よう",
            "また",
            "もの",
            "という",
            "あり",
            "まで",
            "られ",
            "なる",
            "へ",
            "か",
            "だ",
            "これ",
            "によって",
            "により",
            "おり",
            "より",
        }
    ),
    "zh": frozenset(
        {
            "的",
            "了",
            "在",
            "是",
            "我",
            "有",
            "和",
            "就",
            "不",
            "人",
            "都",
            "一",
            "一个",
            "上",
            "也",
            "很",
            "到",
            "说",
            "要",
            "去",
            "你",
            "会",
            "着",
            "没有",
            "看",
            "好",
            "自己",
            "这",
            "他",
            "她",
            "它",
            "们",
            "那",
            "些",
            "被",
            "从",
            "把",
            "但",
            "还",
            "可以",
            "对",
            "于",
            "所以",
            "因为",
        }
    ),
    "es": frozenset(
        {
            "el",
            "la",
            "los",
            "las",
            "un",
            "una",
            "unos",
            "unas",
            "de",
            "del",
            "al",
            "en",
            "y",
            "o",
            "que",
            "es",
            "por",
            "con",
            "para",
            "como",
            "pero",
            "su",
            "se",
            "no",
            "más",
            "lo",
            "ya",
            "me",
            "le",
            "les",
            "nos",
            "te",
            "mi",
            "si",
            "este",
            "esta",
            "estos",
            "estas",
            "ese",
            "esa",
            "aquel",
            "ser",
            "estar",
            "haber",
            "tener",
            "hacer",
            "poder",
        }
    ),
    "de": frozenset(
        {
            "der",
            "die",
            "das",
            "ein",
            "eine",
            "und",
            "oder",
            "aber",
            "in",
            "von",
            "zu",
            "für",
            "mit",
            "auf",
            "an",
            "ist",
            "sind",
            "war",
            "hat",
            "haben",
            "wird",
            "werden",
            "kann",
            "nicht",
            "auch",
            "als",
            "nach",
            "wie",
            "noch",
            "bei",
            "nur",
            "über",
            "so",
            "sich",
            "es",
            "ich",
            "er",
            "sie",
            "wir",
            "ihr",
            "den",
            "dem",
            "des",
            "einer",
            "einem",
        }
    ),
    "fr": frozenset(
        {
            "le",
            "la",
            "les",
            "un",
            "une",
            "des",
            "de",
            "du",
            "au",
            "aux",
            "et",
            "ou",
            "mais",
            "en",
            "dans",
            "sur",
            "pour",
            "par",
            "avec",
            "que",
            "qui",
            "est",
            "sont",
            "a",
            "ont",
            "ce",
            "cette",
            "ces",
            "il",
            "elle",
            "ils",
            "elles",
            "je",
            "tu",
            "nous",
            "vous",
            "ne",
            "pas",
            "plus",
            "si",
            "son",
            "sa",
            "ses",
            "leur",
            "leurs",
            "se",
            "être",
            "avoir",
        }
    ),
    "pt": frozenset(
        {
            "o",
            "a",
            "os",
            "as",
            "um",
            "uma",
            "de",
            "do",
            "da",
            "em",
            "no",
            "na",
            "por",
            "para",
            "com",
            "que",
            "é",
            "são",
            "foi",
            "tem",
            "não",
            "mais",
            "se",
            "como",
            "mas",
            "ou",
            "este",
            "esta",
            "esse",
            "essa",
            "seu",
            "sua",
            "ele",
            "ela",
            "nos",
            "ao",
            "dos",
            "das",
            "já",
            "também",
            "muito",
        }
    ),
    "ru": frozenset(
        {
            "и",
            "в",
            "не",
            "на",
            "с",
            "что",
            "как",
            "но",
            "по",
            "это",
            "он",
            "она",
            "они",
            "мы",
            "вы",
            "я",
            "из",
            "за",
            "от",
            "до",
            "для",
            "был",
            "была",
            "были",
            "быть",
            "есть",
            "все",
            "так",
            "его",
            "её",
            "их",
            "при",
            "уже",
            "ещё",
            "бы",
            "же",
            "ли",
            "только",
            "или",
            "то",
            "тот",
        }
    ),
}


def get_stop_words(language: str | None = None) -> frozenset[str]:
    """Get stop words for a language (defaults to English)."""
    if language and language in STOP_WORDS:
        return STOP_WORDS[language]
    return STOP_WORDS["en"]


def remove_stop_words(
    tokens: list[str],
    language: str | None = None,
) -> list[str]:
    """Remove stop words from a token list."""
    sw = get_stop_words(language)
    return [t for t in tokens if t.lower() not in sw]


# ── #4: Query expansion (synonym-based) ───────────────────────────

# Simple synonym dictionary for common technical terms
_SYNONYMS: dict[str, list[str]] = {
    "error": ["exception", "bug", "issue", "fault"],
    "bug": ["error", "defect", "issue"],
    "api": ["endpoint", "interface", "service"],
    "database": ["db", "datastore", "storage"],
    "db": ["database", "datastore"],
    "function": ["method", "procedure", "routine"],
    "method": ["function", "procedure"],
    "server": ["backend", "service", "host"],
    "client": ["frontend", "consumer", "user"],
    "config": ["configuration", "settings", "options"],
    "configuration": ["config", "settings"],
    "test": ["spec", "unittest", "testing"],
    "deploy": ["release", "ship", "publish"],
    "install": ["setup", "configure"],
    "search": ["query", "find", "lookup"],
    "async": ["asynchronous", "concurrent"],
    "sync": ["synchronous", "blocking"],
    "cache": ["buffer", "memoize"],
    "auth": ["authentication", "authorization", "login"],
    "docs": ["documentation", "manual", "guide"],
    "performance": ["speed", "latency", "throughput"],
}


def expand_query(query: str, *, max_expansions: int = 3) -> list[str]:
    """Expand query with synonyms.

    Returns a list of additional terms to include in search.
    """
    tokens = query.lower().split()
    expansions: list[str] = []
    for tok in tokens:
        synonyms = _SYNONYMS.get(tok, [])
        for syn in synonyms[:max_expansions]:
            if syn not in tokens and syn not in expansions:
                expansions.append(syn)
    return expansions[: max_expansions * 2]


# ── #5: "Did you mean?" typo correction ──────────────────────────


def _edit_distance(s1: str, s2: str) -> int:
    """Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(
                min(
                    curr_row[j] + 1,
                    prev_row[j + 1] + 1,
                    prev_row[j] + cost,
                )
            )
        prev_row = curr_row
    return prev_row[-1]


def did_you_mean(
    query: str,
    vocabulary: list[str],
    *,
    max_distance: int = 2,
    max_suggestions: int = 3,
) -> list[str]:
    """Suggest corrections for misspelled query terms.

    Args:
        query: User query string.
        vocabulary: Known terms from the index.
        max_distance: Maximum edit distance for suggestions.
        max_suggestions: Maximum number of suggestions.

    Returns:
        List of corrected query suggestions.
    """
    tokens = query.lower().split()
    suggestions: list[str] = []

    for tok in tokens:
        if tok in vocabulary:
            continue
        candidates: list[tuple[int, str]] = []
        for word in vocabulary:
            dist = _edit_distance(tok, word)
            if 0 < dist <= max_distance:
                candidates.append((dist, word))
        candidates.sort()
        for _, word in candidates[:1]:
            corrected = query.replace(tok, word)
            if corrected != query and corrected not in suggestions:
                suggestions.append(corrected)

    return suggestions[:max_suggestions]


# ── #8: Natural language query understanding ──────────────────────

_DATE_PATTERNS = [
    (r"\brecent(?:ly)?\b", "date_hint", "recent"),
    (r"\blast\s+(\d+)\s+days?\b", "date_days", None),
    (r"\blast\s+week\b", "date_days", "7"),
    (r"\blast\s+month\b", "date_days", "30"),
    (r"\blast\s+year\b", "date_days", "365"),
    (r"\btoday\b", "date_days", "1"),
    (r"\byesterday\b", "date_days", "2"),
    (r"\bthis\s+week\b", "date_days", "7"),
    (r"\bthis\s+month\b", "date_days", "30"),
    (r"\bthis\s+year\b", "date_days", "365"),
]

_DOMAIN_PATTERN = re.compile(
    r"\bsite:(\S+)\b|\bfrom\s+([\w.-]+\.(?:com|org|net|io|dev|edu|gov))\b",
    re.IGNORECASE,
)

_LANG_PATTERN = re.compile(
    r"\bin\s+(english|korean|japanese|chinese|spanish|german|french"
    r"|portuguese|russian)\b",
    re.IGNORECASE,
)

_LANG_MAP = {
    "english": "en",
    "korean": "ko",
    "japanese": "ja",
    "chinese": "zh",
    "spanish": "es",
    "german": "de",
    "french": "fr",
    "portuguese": "pt",
    "russian": "ru",
}


@dataclass
class ParsedQuery:
    """Result of natural language query parsing."""

    cleaned_query: str
    date_from: float | None = None
    date_to: float | None = None
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    language: str | None = None
    original_query: str = ""


def parse_natural_query(query: str) -> ParsedQuery:
    """Parse natural language hints from a query string.

    Extracts date ranges, domain filters, and language hints,
    returning a cleaned query with extracted metadata.
    """
    import time as _time

    result = ParsedQuery(
        cleaned_query=query,
        original_query=query,
    )
    cleaned = query

    # Date extraction
    now = _time.time()
    for pattern, kind, default in _DATE_PATTERNS:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            if kind == "date_hint" and default == "recent":
                result.date_from = now - 7 * 86400
            elif kind == "date_days":
                days_str = m.group(1) if m.lastindex else default
                if days_str:
                    days = int(days_str)
                    result.date_from = now - days * 86400
            cleaned = cleaned[: m.start()] + cleaned[m.end() :]

    # Domain extraction
    for m in _DOMAIN_PATTERN.finditer(cleaned):
        domain = m.group(1) or m.group(2)
        if domain:
            result.include_domains.append(domain)
    cleaned = _DOMAIN_PATTERN.sub("", cleaned)

    # Language extraction
    m = _LANG_PATTERN.search(cleaned)
    if m:
        lang_name = m.group(1).lower()
        result.language = _LANG_MAP.get(lang_name)
        cleaned = cleaned[: m.start()] + cleaned[m.end() :]

    result.cleaned_query = re.sub(r"\s+", " ", cleaned).strip()
    return result


# ── #10: Related searches ─────────────────────────────────────────


class RelatedSearchTracker:
    """Track co-occurring search terms for related searches."""

    def __init__(self, max_pairs: int = 10000) -> None:
        self._pairs: Counter[tuple[str, str]] = Counter()
        self._max_pairs = max_pairs

    def record(self, query: str) -> None:
        """Record a search query for co-occurrence tracking."""
        tokens = sorted(set(query.lower().split()))
        for i, t1 in enumerate(tokens):
            for t2 in tokens[i + 1 :]:
                self._pairs[(t1, t2)] += 1
        if len(self._pairs) > self._max_pairs:
            # Keep top half
            keep = self._max_pairs // 2
            common = self._pairs.most_common(keep)
            self._pairs = Counter(dict(common))

    def related(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> list[str]:
        """Get related search terms for a query."""
        tokens = set(query.lower().split())
        candidates: Counter[str] = Counter()
        for (t1, t2), count in self._pairs.items():
            if t1 in tokens and t2 not in tokens:
                candidates[t2] += count
            elif t2 in tokens and t1 not in tokens:
                candidates[t1] += count
        return [term for term, _ in candidates.most_common(limit)]
