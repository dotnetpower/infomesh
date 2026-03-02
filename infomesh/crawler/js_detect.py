"""JavaScript-heavy page detection.

Detects pages that require JavaScript rendering to extract meaningful
content.  Detection signals include:

- Empty or near-empty ``<body>`` (common in React/Vue/Angular SPAs)
- ``<noscript>`` fallback text indicating JS is required
- Low text-to-HTML ratio (< 5 %)
- Framework-specific markers (``<div id="root">``, ``__NEXT_DATA__``, etc.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Detection result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JSDetectionResult:
    """Result of JS-heavy page detection."""

    js_required: bool
    confidence: float  # 0.0 – 1.0
    signals: list[str]  # human-readable signal descriptions


# ---------------------------------------------------------------------------
# Regular expressions for detection signals
# ---------------------------------------------------------------------------

# SPA root mount points
_SPA_ROOT_RE = re.compile(
    r'<div\s+id=["\'](?:root|app|__next|__nuxt|__vue)["\']',
    re.IGNORECASE,
)

# Framework data blobs
_FRAMEWORK_DATA_RE = re.compile(
    r"__NEXT_DATA__|__NUXT__|window\.__INITIAL_STATE__|"
    r"window\.webpackJsonp|window\.__remixContext",
    re.IGNORECASE,
)

# Noscript fallback patterns that suggest JS is needed
_NOSCRIPT_RE = re.compile(
    r"<noscript[^>]*>(.*?)</noscript>",
    re.IGNORECASE | re.DOTALL,
)

_NOSCRIPT_JS_REQUIRED_RE = re.compile(
    r"enable\s+javascript|javascript\s+(?:is\s+)?required|"
    r"(?:need|requires?)\s+javascript|must\s+enable\s+javascript|"
    r"activate\s+javascript|turn\s+on\s+javascript",
    re.IGNORECASE,
)

# Body content extraction
_BODY_RE = re.compile(
    r"<body[^>]*>(.*?)</body>",
    re.IGNORECASE | re.DOTALL,
)

# Strip HTML tags for text length measurement
_TAG_RE = re.compile(r"<[^>]+>")

# Script tags
_SCRIPT_RE = re.compile(
    r"<script[^>]*>.*?</script>",
    re.IGNORECASE | re.DOTALL,
)

# Style tags
_STYLE_RE = re.compile(
    r"<style[^>]*>.*?</style>",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_js_requirement(html: str) -> JSDetectionResult:
    """Analyse raw HTML to determine if JavaScript rendering is needed.

    Uses a weighted scoring system with multiple signals.  A score
    ≥ 0.5 is classified as *js_required*.

    Args:
        html: Raw HTML string from the HTTP response.

    Returns:
        :class:`JSDetectionResult` with detection outcome.
    """
    signals: list[str] = []
    score = 0.0

    # 1. Check for SPA root mount point
    if _SPA_ROOT_RE.search(html):
        signals.append("SPA root element detected (div#root, #app, #__next, …)")
        score += 0.25

    # 2. Check for framework data blobs
    if _FRAMEWORK_DATA_RE.search(html):
        signals.append("Framework data blob detected (__NEXT_DATA__, etc.)")
        score += 0.20

    # 3. Check <noscript> fallback
    for m in _NOSCRIPT_RE.finditer(html):
        noscript_text = m.group(1)
        if _NOSCRIPT_JS_REQUIRED_RE.search(noscript_text):
            signals.append("Noscript fallback says JavaScript is required")
            score += 0.30
            break  # one match is enough

    # 4. Check text-to-HTML ratio
    ratio = _text_to_html_ratio(html)
    if ratio < 0.02:
        signals.append(f"Very low text-to-HTML ratio ({ratio:.1%})")
        score += 0.35
    elif ratio < 0.05:
        signals.append(f"Low text-to-HTML ratio ({ratio:.1%})")
        score += 0.15

    # 5. Empty body check
    body_text_len = _body_text_length(html)
    if body_text_len < 50:
        signals.append(f"Near-empty body ({body_text_len} chars of text)")
        score += 0.30

    confidence = min(score, 1.0)
    return JSDetectionResult(
        js_required=confidence >= 0.5,
        confidence=round(confidence, 2),
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_to_html_ratio(html: str) -> float:
    """Return the ratio of visible text length to total HTML length."""
    if not html:
        return 0.0
    # Remove scripts and styles first
    cleaned = _SCRIPT_RE.sub("", html)
    cleaned = _STYLE_RE.sub("", cleaned)
    text = _TAG_RE.sub("", cleaned).strip()
    text = re.sub(r"\s+", " ", text)
    html_len = len(html)
    if html_len == 0:
        return 0.0
    return len(text) / html_len


def _body_text_length(html: str) -> int:
    """Return the length of visible text inside ``<body>``."""
    body_match = _BODY_RE.search(html)
    if not body_match:
        return 0
    body_html = body_match.group(1)
    # Remove scripts and styles
    cleaned = _SCRIPT_RE.sub("", body_html)
    cleaned = _STYLE_RE.sub("", cleaned)
    text = _TAG_RE.sub("", cleaned).strip()
    text = re.sub(r"\s+", " ", text)
    return len(text)
