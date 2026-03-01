"""NLP-based language detection for crawled content.

Feature #14: Detects the language of crawled text using
character and word frequency analysis.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageDetectionResult:
    """Result of language detection."""

    language: str  # ISO 639-1 code
    confidence: float  # 0.0 to 1.0
    script: str  # "Latin", "CJK", "Cyrillic", etc.


# Common words by language for detection
_COMMON_WORDS: dict[str, set[str]] = {
    "en": {
        "the",
        "is",
        "are",
        "was",
        "were",
        "have",
        "has",
        "been",
        "will",
        "would",
        "could",
        "should",
        "can",
        "this",
        "that",
        "with",
        "from",
        "they",
        "which",
        "not",
        "but",
        "for",
        "and",
    },
    "ko": {
        "이",
        "그",
        "는",
        "을",
        "를",
        "에",
        "의",
        "가",
        "한",
        "에서",
        "으로",
        "하는",
        "있는",
        "것",
        "수",
        "등",
        "및",
        "위",
        "대",
        "중",
    },
    "ja": {
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
        "する",
        "も",
        "な",
        "か",
        "から",
        "よう",
    },
    "zh": {
        "的",
        "了",
        "在",
        "是",
        "有",
        "不",
        "这",
        "人",
        "中",
        "大",
        "会",
        "来",
        "也",
        "就",
        "上",
        "到",
        "个",
        "为",
        "和",
        "说",
    },
    "es": {
        "el",
        "la",
        "de",
        "en",
        "los",
        "las",
        "un",
        "una",
        "por",
        "con",
        "es",
        "que",
        "del",
        "para",
        "como",
        "más",
        "fue",
        "pero",
    },
    "de": {
        "der",
        "die",
        "das",
        "und",
        "ist",
        "von",
        "den",
        "mit",
        "ein",
        "eine",
        "auf",
        "dem",
        "für",
        "nicht",
        "auch",
        "sich",
        "als",
        "noch",
    },
    "fr": {
        "le",
        "la",
        "les",
        "des",
        "de",
        "un",
        "une",
        "du",
        "est",
        "et",
        "en",
        "que",
        "qui",
        "dans",
        "pour",
        "pas",
        "sur",
        "par",
        "avec",
    },
    "pt": {
        "de",
        "da",
        "do",
        "em",
        "os",
        "as",
        "um",
        "uma",
        "que",
        "por",
        "com",
        "para",
        "foi",
        "mais",
        "não",
        "como",
        "mas",
        "seu",
        "sua",
    },
    "ru": {
        "и",
        "в",
        "на",
        "с",
        "не",
        "что",
        "он",
        "как",
        "это",
        "по",
        "но",
        "из",
        "от",
        "за",
        "для",
        "все",
        "был",
        "она",
        "так",
        "его",
    },
}

# Unicode block patterns for script detection
_CJK_RANGES = (
    ("\u4e00", "\u9fff"),  # CJK Unified Ideographs
    ("\u3400", "\u4dbf"),  # CJK Extension A
    ("\u3000", "\u303f"),  # CJK Symbols and Punctuation
)

_HANGUL_RANGES = (
    ("\uac00", "\ud7af"),  # Hangul Syllables
    ("\u1100", "\u11ff"),  # Hangul Jamo
    ("\u3130", "\u318f"),  # Hangul Compatibility Jamo
)

_HIRAGANA_KATAKANA = (
    ("\u3040", "\u309f"),  # Hiragana
    ("\u30a0", "\u30ff"),  # Katakana
)

_CYRILLIC_RANGE = ("\u0400", "\u04ff")


def _detect_script(text: str) -> str:
    """Detect the dominant script in text."""
    if not text:
        return "Unknown"

    counts: Counter[str] = Counter()
    for ch in text:
        if ch.isspace() or ch in ".,;:!?()[]{}\"'-":
            continue

        cp = ord(ch)

        # Check CJK
        for lo, hi in _CJK_RANGES:
            if ord(lo) <= cp <= ord(hi):
                counts["CJK"] += 1
                break
        else:
            # Check Hangul
            for lo, hi in _HANGUL_RANGES:
                if ord(lo) <= cp <= ord(hi):
                    counts["Hangul"] += 1
                    break
            else:
                # Check Hiragana/Katakana
                for lo, hi in _HIRAGANA_KATAKANA:
                    if ord(lo) <= cp <= ord(hi):
                        counts["Kana"] += 1
                        break
                else:
                    lo_c, hi_c = _CYRILLIC_RANGE
                    if ord(lo_c) <= cp <= ord(hi_c):
                        counts["Cyrillic"] += 1
                    elif ch.isalpha():
                        counts["Latin"] += 1

    if not counts:
        return "Unknown"
    return counts.most_common(1)[0][0]


def detect_language(
    text: str,
    *,
    min_text_length: int = 20,
) -> LanguageDetectionResult:
    """Detect the language of text.

    Uses a combination of script detection and common word
    frequency analysis.

    Args:
        text: Input text.
        min_text_length: Minimum text length for reliable detection.

    Returns:
        LanguageDetectionResult with language code and confidence.
    """
    if len(text) < min_text_length:
        return LanguageDetectionResult(
            language="en",
            confidence=0.0,
            script="Unknown",
        )

    script = _detect_script(text)

    # Script-based shortcuts
    if script == "Hangul":
        return LanguageDetectionResult(
            language="ko",
            confidence=0.95,
            script=script,
        )
    if script == "Kana":
        return LanguageDetectionResult(
            language="ja",
            confidence=0.95,
            script=script,
        )
    if script == "CJK":
        # Could be zh or ja — check for kana presence
        has_kana = any(
            ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff") for ch in text
        )
        lang = "ja" if has_kana else "zh"
        return LanguageDetectionResult(
            language=lang,
            confidence=0.85,
            script=script,
        )

    # Word-based detection for Latin/Cyrillic scripts
    words = re.findall(r"\b\w+\b", text.lower())
    word_set = set(words)

    scores: dict[str, float] = {}
    for lang, common in _COMMON_WORDS.items():
        overlap = len(word_set & common)
        if len(words) > 0:
            scores[lang] = overlap / min(len(words), 50)

    if not scores:
        return LanguageDetectionResult(
            language="en",
            confidence=0.1,
            script=script,
        )

    best_lang = max(scores, key=lambda k: scores[k])
    best_score = scores[best_lang]

    # Confidence based on match ratio
    confidence = min(best_score * 5, 0.95)

    return LanguageDetectionResult(
        language=best_lang,
        confidence=round(confidence, 3),
        script=script,
    )
