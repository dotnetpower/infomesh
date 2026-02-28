"""Adaptive auto-recrawl scheduler.

Learns per-URL change frequency and schedules re-crawls accordingly:
- Frequently-changing pages → short intervals (6 h).
- Stable pages → long intervals (30 d, alive check only).
- Deleted pages → progressive penalty then soft-delete.

Uses HTTP conditional requests (ETag / If-Modified-Since) to save bandwidth.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import structlog

from infomesh.crawler import MAX_RESPONSE_BYTES
from infomesh.hashing import content_hash
from infomesh.security import SSRFError, validate_url

logger = structlog.get_logger()

# ── Recrawl interval tiers ──────────────────────────────────────────────

# Default intervals in seconds
INTERVAL_HIGH = 6 * 3600  # 6 hours  — change_frequency > 0.50
INTERVAL_MEDIUM = 24 * 3600  # 24 hours — change_frequency 0.10–0.50
INTERVAL_LOW = 7 * 24 * 3600  # 7 days   — change_frequency < 0.10
INTERVAL_STATIC = 30 * 24 * 3600  # 30 days  — change_frequency == 0.0

# Number of consecutive failures before soft-delete
STALE_THRESHOLD = 3


@dataclass
class RecrawlCandidate:
    """A document eligible for re-crawling."""

    doc_id: int
    url: str
    text_hash: str
    etag: str | None
    last_modified: str | None
    recrawl_interval: int
    stale_count: int
    change_frequency: float
    crawled_at: float
    last_recrawl_at: float | None


@dataclass
class RecrawlOutcome:
    """Result of a single recrawl attempt."""

    url: str
    status: str  # "not_modified" | "updated" | "deleted" | "error"
    new_text_hash: str | None = None
    new_etag: str | None = None
    new_last_modified: str | None = None
    stale_count: int = 0
    elapsed_ms: float = 0.0


# ── Interval computation ────────────────────────────────────────────────


def compute_recrawl_interval(change_frequency: float) -> int:
    """Compute the optimal recrawl interval based on observed change rate.

    Args:
        change_frequency: Ratio of times content changed over total recrawls
                          (0.0 = never changes, 1.0 = always changes).

    Returns:
        Interval in seconds.
    """
    if change_frequency <= 0.0:
        return INTERVAL_STATIC
    if change_frequency < 0.10:
        return INTERVAL_LOW
    if change_frequency <= 0.50:
        return INTERVAL_MEDIUM
    return INTERVAL_HIGH


def update_change_frequency(
    old_freq: float,
    changed: bool,
    *,
    alpha: float = 0.3,
) -> float:
    """Update change frequency using exponential moving average.

    Args:
        old_freq: Previous change frequency.
        changed: Whether content changed in this recrawl.
        alpha: EMA smoothing factor (higher = more weight on latest).

    Returns:
        Updated change frequency in ``[0.0, 1.0]``.
    """
    new_val = 1.0 if changed else 0.0
    return alpha * new_val + (1.0 - alpha) * old_freq


# ── Recrawl logic ───────────────────────────────────────────────────────


async def recrawl_url(
    url: str,
    etag: str | None,
    last_modified: str | None,
    old_text_hash: str,
    stale_count: int,
    *,
    client: httpx.AsyncClient | None = None,
    user_agent: str = "InfoMesh/0.1",
    extract_fn: Callable[[str, str], str | None] | None = None,
) -> RecrawlOutcome:
    """Re-crawl a single URL, using conditional HTTP when possible.

    Args:
        url: URL to recrawl.
        etag: Previously stored ETag header value.
        last_modified: Previously stored Last-Modified header value.
        old_text_hash: SHA-256 hash of previously extracted text.
        stale_count: Current consecutive failure count.
        client: Optional shared httpx.AsyncClient.
        user_agent: User-Agent header string.
        extract_fn: Content extraction callable ``(html, url) → text|None``.

    Returns:
        :class:`RecrawlOutcome` describing what happened.
    """
    start = time.monotonic()

    # SSRF protection
    try:
        validate_url(url)
    except SSRFError as exc:
        logger.warning("recrawl_ssrf_blocked", url=url, reason=str(exc))
        return RecrawlOutcome(
            url=url,
            status="error",
            stale_count=stale_count,
            elapsed_ms=(time.monotonic() - start) * 1000,
        )

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=30.0,
        )

    try:
        return await _do_recrawl(
            client,  # type: ignore[arg-type]
            url,
            etag,
            last_modified,
            old_text_hash,
            stale_count,
            extract_fn=extract_fn,
            start=start,
        )
    finally:
        if own_client:
            await client.aclose()  # type: ignore[union-attr]


async def _do_recrawl(
    client: httpx.AsyncClient,
    url: str,
    etag: str | None,
    last_modified: str | None,
    old_text_hash: str,
    stale_count: int,
    *,
    extract_fn: Callable[[str, str], str | None] | None,
    start: float,
) -> RecrawlOutcome:
    """Internal recrawl implementation."""
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        resp = await client.get(url, headers=headers, timeout=30.0)
    except httpx.HTTPError as exc:
        elapsed = (time.monotonic() - start) * 1000
        logger.warning("recrawl_network_error", url=url, error=str(exc))
        return RecrawlOutcome(
            url=url,
            status="error",
            stale_count=stale_count + 1,
            elapsed_ms=elapsed,
        )

    elapsed = (time.monotonic() - start) * 1000

    # 304 Not Modified — bandwidth saved, just refresh timestamp
    if resp.status_code == 304:
        logger.debug("recrawl_not_modified", url=url)
        return RecrawlOutcome(
            url=url,
            status="not_modified",
            new_etag=etag,
            new_last_modified=last_modified,
            stale_count=0,
            elapsed_ms=elapsed,
        )

    # 4xx / 5xx — page might be deleted or temporarily down
    if resp.status_code >= 400:
        new_stale = stale_count + 1
        logger.info(
            "recrawl_error_status",
            url=url,
            status=resp.status_code,
            stale_count=new_stale,
        )
        status = "deleted" if new_stale >= STALE_THRESHOLD else "error"
        return RecrawlOutcome(
            url=url,
            status=status,
            stale_count=new_stale,
            elapsed_ms=elapsed,
        )

    # 2xx — content fetched
    html = resp.text
    # Enforce response size limit
    if len(html.encode("utf-8", errors="replace")) > MAX_RESPONSE_BYTES:
        logger.warning("recrawl_response_too_large", url=url)
        return RecrawlOutcome(
            url=url,
            status="error",
            stale_count=stale_count,
            elapsed_ms=elapsed,
        )
    new_etag = resp.headers.get("etag")
    new_last_modified = resp.headers.get("last-modified")

    # Extract text
    text = extract_fn(html, url) if extract_fn is not None else html

    if text is None:
        return RecrawlOutcome(
            url=url,
            status="error",
            stale_count=stale_count,
            elapsed_ms=elapsed,
        )

    new_hash = content_hash(text)

    if new_hash == old_text_hash:
        logger.debug("recrawl_unchanged", url=url)
        return RecrawlOutcome(
            url=url,
            status="not_modified",
            new_text_hash=new_hash,
            new_etag=new_etag,
            new_last_modified=new_last_modified,
            stale_count=0,
            elapsed_ms=elapsed,
        )

    logger.info(
        "recrawl_updated", url=url, old_hash=old_text_hash[:12], new_hash=new_hash[:12]
    )
    return RecrawlOutcome(
        url=url,
        status="updated",
        new_text_hash=new_hash,
        new_etag=new_etag,
        new_last_modified=new_last_modified,
        stale_count=0,
        elapsed_ms=elapsed,
    )


# ── Candidate selection ─────────────────────────────────────────────────


def select_candidates(
    docs: list[RecrawlCandidate],
    *,
    now: float | None = None,
    max_batch: int = 50,
) -> list[RecrawlCandidate]:
    """Select documents due for recrawling from a candidate list.

    A document is due when ``now - last_recrawl_at >= recrawl_interval``
    (falls back to ``crawled_at`` if never recrawled).

    Args:
        docs: All documents with recrawl metadata.
        now: Current timestamp.
        max_batch: Maximum candidates to return.

    Returns:
        Sorted list of overdue candidates (most overdue first).
    """
    now = now or time.time()
    overdue: list[tuple[float, RecrawlCandidate]] = []

    for doc in docs:
        last = doc.last_recrawl_at or doc.crawled_at
        due_at = last + doc.recrawl_interval
        if now >= due_at:
            overdue_by = now - due_at
            overdue.append((overdue_by, doc))

    # Sort by most overdue first
    overdue.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in overdue[:max_batch]]
