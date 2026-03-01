"""RSS/Atom feed discovery and crawling.

Feature #15: Discover and parse RSS/Atom feeds for real-time indexing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeedItem:
    """A single item from an RSS/Atom feed."""

    title: str
    url: str
    summary: str = ""
    published: str = ""
    author: str = ""


@dataclass
class FeedResult:
    """Parsed feed result."""

    title: str
    url: str
    items: list[FeedItem] = field(default_factory=list)
    feed_type: str = "unknown"  # "rss" | "atom"


# Feed link discovery regex
_FEED_LINK_RE = re.compile(
    r'<link[^>]*type=["\']application/'
    r"(?:rss|atom)\+xml[\"'][^>]*>",
    re.IGNORECASE | re.DOTALL,
)

_HREF_RE = re.compile(
    r'href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def discover_feeds(html: str, base_url: str) -> list[str]:
    """Discover RSS/Atom feed URLs from HTML page.

    Args:
        html: Raw HTML content.
        base_url: Page URL for resolving relative links.

    Returns:
        List of feed URLs.
    """
    from urllib.parse import urljoin

    feeds: list[str] = []
    for m in _FEED_LINK_RE.finditer(html):
        tag = m.group(0)
        href_m = _HREF_RE.search(tag)
        if href_m:
            url = urljoin(base_url, href_m.group(1))
            if url not in feeds:
                feeds.append(url)
    return feeds


def parse_feed_xml(xml_text: str, feed_url: str) -> FeedResult:
    """Parse an RSS or Atom feed from XML text.

    Simple regex-based parser (no lxml dependency needed).

    Args:
        xml_text: Raw XML feed content.
        feed_url: URL of the feed.

    Returns:
        FeedResult with parsed items.
    """
    result = FeedResult(title="", url=feed_url)

    # Detect feed type
    if "<feed" in xml_text[:500]:
        result.feed_type = "atom"
    elif "<rss" in xml_text[:500] or "<channel" in xml_text[:500]:
        result.feed_type = "rss"

    # Extract feed title
    title_m = re.search(
        r"<title[^>]*>(.*?)</title>",
        xml_text[:2000],
        re.DOTALL,
    )
    if title_m:
        result.title = _strip_cdata(title_m.group(1)).strip()

    # Extract items
    item_tag = "entry" if result.feed_type == "atom" else "item"

    item_re = re.compile(
        rf"<{item_tag}[^>]*>(.*?)</{item_tag}>",
        re.DOTALL | re.IGNORECASE,
    )

    for m in item_re.finditer(xml_text):
        block = m.group(1)
        item = _parse_item(block, result.feed_type)
        if item.url:
            result.items.append(item)

    return result


def _parse_item(block: str, feed_type: str) -> FeedItem:
    """Parse a single RSS/Atom item block."""
    title = ""
    url = ""
    summary = ""
    published = ""
    author = ""

    title_m = re.search(
        r"<title[^>]*>(.*?)</title>",
        block,
        re.DOTALL,
    )
    if title_m:
        title = _strip_cdata(title_m.group(1)).strip()

    if feed_type == "atom":
        link_m = re.search(
            r'<link[^>]*href=["\']([^"\']+)["\']',
            block,
        )
        if link_m:
            url = link_m.group(1)
        sum_m = re.search(
            r"<summary[^>]*>(.*?)</summary>",
            block,
            re.DOTALL,
        )
        if sum_m:
            summary = _strip_cdata(sum_m.group(1)).strip()
        pub_m = re.search(
            r"<published[^>]*>(.*?)</published>",
            block,
            re.DOTALL,
        )
        if pub_m:
            published = pub_m.group(1).strip()
    else:
        link_m = re.search(
            r"<link[^>]*>(.*?)</link>",
            block,
            re.DOTALL,
        )
        if link_m:
            url = _strip_cdata(link_m.group(1)).strip()
        desc_m = re.search(
            r"<description[^>]*>(.*?)</description>",
            block,
            re.DOTALL,
        )
        if desc_m:
            summary = _strip_cdata(desc_m.group(1)).strip()
        pub_m = re.search(
            r"<pubDate[^>]*>(.*?)</pubDate>",
            block,
            re.DOTALL,
        )
        if pub_m:
            published = pub_m.group(1).strip()

    author_m = re.search(
        r"<author[^>]*>(.*?)</author>",
        block,
        re.DOTALL,
    )
    if author_m:
        author = _strip_cdata(author_m.group(1)).strip()

    return FeedItem(
        title=title,
        url=url,
        summary=summary[:500],
        published=published,
        author=author,
    )


def _strip_cdata(text: str) -> str:
    """Strip CDATA wrappers and HTML tags from text."""
    text = re.sub(r"<!\[CDATA\[", "", text)
    text = re.sub(r"\]\]>", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
