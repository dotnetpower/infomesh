"""Content diff detection for recrawled pages.

Feature #16: Detect and report changes when recrawling a URL.
Feature #20: WARC export format support.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC


@dataclass
class ContentDiff:
    """Diff between two versions of a page."""

    url: str
    has_changed: bool
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    change_ratio: float = 0.0
    old_length: int = 0
    new_length: int = 0


def compute_diff(
    old_text: str,
    new_text: str,
    url: str = "",
) -> ContentDiff:
    """Compute diff between old and new page text.

    Uses line-based comparison for efficiency.

    Args:
        old_text: Previous version text.
        new_text: New version text.
        url: Page URL for reference.

    Returns:
        ContentDiff with change details.
    """
    if old_text == new_text:
        return ContentDiff(
            url=url,
            has_changed=False,
            old_length=len(old_text),
            new_length=len(new_text),
        )

    old_lines = set(old_text.splitlines())
    new_lines = set(new_text.splitlines())

    added = [ln for ln in new_lines - old_lines if ln.strip()]
    removed = [ln for ln in old_lines - new_lines if ln.strip()]

    total_lines = max(len(old_lines | new_lines), 1)
    changed = len(added) + len(removed)
    ratio = changed / total_lines

    return ContentDiff(
        url=url,
        has_changed=True,
        added_lines=added[:100],  # Cap for memory
        removed_lines=removed[:100],
        change_ratio=round(ratio, 3),
        old_length=len(old_text),
        new_length=len(new_text),
    )


# ── #20: WARC export ──────────────────────────────────────────────


def export_warc_record(
    url: str,
    text: str,
    crawled_at: float,
) -> str:
    """Export a document as a WARC/1.0 record string.

    Simplified WARC format for text-only export.

    Args:
        url: Source URL.
        text: Extracted text content.
        crawled_at: Unix timestamp of crawl.

    Returns:
        WARC-formatted string.
    """
    from datetime import datetime

    dt = datetime.fromtimestamp(crawled_at, tz=UTC)
    date_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    content = text.encode("utf-8")
    record_id = f"<urn:uuid:{_simple_uuid(url, crawled_at)}>"

    header = (
        "WARC/1.0\r\n"
        "WARC-Type: conversion\r\n"
        f"WARC-Target-URI: {url}\r\n"
        f"WARC-Date: {date_str}\r\n"
        f"WARC-Record-ID: {record_id}\r\n"
        f"Content-Length: {len(content)}\r\n"
        "Content-Type: text/plain\r\n"
        "\r\n"
    )

    return header + text + "\r\n\r\n"


def _simple_uuid(url: str, ts: float) -> str:
    """Generate a deterministic UUID-like string."""
    import hashlib

    h = hashlib.sha256(f"{url}:{ts}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def export_warc_file(
    documents: list[dict[str, object]],
) -> str:
    """Export multiple documents as a WARC file string.

    Args:
        documents: List of dicts with url, text, crawled_at.

    Returns:
        Multi-record WARC string.
    """
    warcinfo = (
        "WARC/1.0\r\n"
        "WARC-Type: warcinfo\r\n"
        f"WARC-Date: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\r\n"
        "Content-Type: application/warc-fields\r\n"
        "Content-Length: 0\r\n"
        "\r\n\r\n"
    )

    parts = [warcinfo]
    for doc in documents:
        url = str(doc.get("url", ""))
        text = str(doc.get("text", ""))
        ts_raw = doc.get("crawled_at", 0)
        ts = float(ts_raw) if isinstance(ts_raw, (int, float, str)) else 0.0
        if url and text:
            parts.append(export_warc_record(url, text, ts))

    return "".join(parts)
