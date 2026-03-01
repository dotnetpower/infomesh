"""Structured data extraction from HTML — JSON-LD, Schema.org.

Feature #13: Parse JSON-LD and OpenGraph metadata from HTML pages.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class StructuredData:
    """Extracted structured data from a page."""

    json_ld: list[dict[str, object]] = field(default_factory=list)
    opengraph: dict[str, str] = field(default_factory=dict)
    meta_description: str = ""
    meta_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "json_ld": self.json_ld,
            "opengraph": self.opengraph,
            "meta_description": self.meta_description,
            "meta_keywords": self.meta_keywords,
        }


_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_OG_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']og:([^"\']+)["\']'
    r'\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)

_META_DESC_RE = re.compile(
    r'<meta\s+name=["\']description["\']'
    r'\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)

_META_KW_RE = re.compile(
    r'<meta\s+name=["\']keywords["\']'
    r'\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def extract_structured_data(html: str) -> StructuredData:
    """Extract JSON-LD, OpenGraph, and meta tags from HTML.

    Args:
        html: Raw HTML string.

    Returns:
        StructuredData with parsed metadata.
    """
    result = StructuredData()

    # JSON-LD
    for m in _JSON_LD_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                result.json_ld.extend(data)
            elif isinstance(data, dict):
                result.json_ld.append(data)
        except (json.JSONDecodeError, ValueError):
            pass

    # OpenGraph
    for m in _OG_RE.finditer(html):
        result.opengraph[m.group(1)] = m.group(2)

    # Meta description
    desc_m = _META_DESC_RE.search(html)
    if desc_m:
        result.meta_description = desc_m.group(1).strip()

    # Meta keywords
    kw_m = _META_KW_RE.search(html)
    if kw_m:
        keywords = kw_m.group(1).strip()
        result.meta_keywords = [kw.strip() for kw in keywords.split(",") if kw.strip()]

    return result
