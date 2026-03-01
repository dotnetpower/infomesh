"""HTML → text extraction and link discovery using trafilatura."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import structlog
import trafilatura

logger = structlog.get_logger()


@dataclass(frozen=True)
class ParsedPage:
    """Extracted content from an HTML page."""

    url: str
    title: str
    text: str
    language: str | None
    raw_html_hash: str  # SHA-256 of raw HTML
    text_hash: str  # SHA-256 of extracted text


def extract_content(html: str, url: str, *, raw_hash: str = "") -> ParsedPage | None:
    """Extract main content from HTML using trafilatura.

    Args:
        html: Raw HTML string.
        url: Source URL.
        raw_hash: Pre-computed SHA-256 of the raw HTML.

    Returns:
        ParsedPage if extraction succeeds, None otherwise.
    """
    from infomesh.hashing import content_hash

    try:
        result = trafilatura.extract(
            html,
            url=url,
            include_links=False,
            include_images=False,
            include_tables=True,
            output_format="txt",
            favor_recall=True,
        )

        if not result or len(result.strip()) < 50:
            logger.debug("parse_empty", url=url)
            return None

        # Extract title from trafilatura metadata (avoids a second extract call)
        title = ""
        traf_meta = trafilatura.extract_metadata(html, default_url=url)
        if traf_meta and traf_meta.title:
            title = traf_meta.title

        if not title:
            # Fallback: extract from HTML <title> tag
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
            )
            if title_match:
                title = title_match.group(1).strip()

        text = result.strip()
        text_hash = content_hash(text)

        if not raw_hash:
            raw_hash = content_hash(html)

        # Detect language
        lang_info = trafilatura.utils.load_html(html)
        language = None
        if lang_info is not None:
            lang_attr = lang_info.get("lang") or lang_info.get("xml:lang")
            if lang_attr:
                language = lang_attr[:2]  # e.g., "en-US" → "en"

        return ParsedPage(
            url=url,
            title=title,
            text=text,
            language=language,
            raw_html_hash=raw_hash,
            text_hash=text_hash,
        )

    except Exception as exc:
        logger.error("parse_error", url=url, error=str(exc))
        return None


# Pattern for valid HTTP(S) links
_HREF_RE = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\']', re.IGNORECASE)

# Pattern for <link rel="canonical" href="...">
_CANONICAL_RE = re.compile(
    r'<link\s[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Alternative order: href before rel
_CANONICAL_RE_ALT = re.compile(
    r'<link\s[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']',
    re.IGNORECASE,
)

# Skip these extensions — not crawlable content
_SKIP_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".zip",
        ".tar",
        ".gz",
        ".exe",
        ".dmg",
        ".iso",
        ".css",
        ".js",
        ".woff",
        ".woff2",
    }
)


def extract_canonical(html: str, page_url: str) -> str | None:
    """Extract the canonical URL from ``<link rel="canonical">``.

    Args:
        html: Raw HTML string.
        page_url: The URL of the page (used to resolve relative canonicals).

    Returns:
        Absolute canonical URL, or None if not found.
    """
    match = _CANONICAL_RE.search(html) or _CANONICAL_RE_ALT.search(html)
    if not match:
        return None

    href = match.group(1).strip()
    if not href:
        return None

    # Resolve relative canonical URLs
    canonical = urljoin(page_url, href)
    parsed = urlparse(canonical)

    # Only accept HTTP(S) canonical URLs
    if parsed.scheme not in ("http", "https"):
        return None

    return canonical


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract unique HTTP(S) links from HTML.

    Only returns absolute HTTP/HTTPS URLs. Skips anchors, mailto,
    javascript, and binary file extensions.

    Args:
        html: Raw HTML string.
        base_url: Base URL for resolving relative links.

    Returns:
        Deduplicated list of discovered URLs.
    """
    seen: set[str] = set()
    links: list[str] = []

    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()

        # Skip non-HTTP schemes
        if href.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue

        # Resolve relative URLs
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)

        # Only HTTP(S)
        if parsed.scheme not in ("http", "https"):
            continue

        # Skip binary file extensions
        path_lower = parsed.path.lower()
        _, ext = os.path.splitext(path_lower)
        if ext in _SKIP_EXTENSIONS:
            continue

        # Strip fragment
        clean = absolute.split("#")[0]

        if clean not in seen:
            seen.add(clean)
            links.append(clean)

    logger.debug("links_extracted", base_url=base_url, count=len(links))
    return links
