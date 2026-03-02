"""Continuous crawl loop — seeds, scheduling, and background crawling.

Extracted from ``services.py`` to enforce SRP.  The crawl loop
manages seed loading, link rediscovery, idle-timeout re-seeding,
and credit recording.  Indexing is delegated to
``services.index_document()``.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from infomesh.crawler.parser import extract_links
from infomesh.crawler.seeds import CATEGORIES, load_seeds
from infomesh.credits.ledger import ActionType
from infomesh.resources.preflight import is_disk_critically_low
from infomesh.services import AppContext, index_document

logger = structlog.get_logger()


async def _reseed_queue(
    ctx: AppContext,
    _logger: structlog.stdlib.BoundLogger,
) -> int:
    """Re-populate the scheduler queue by rediscovering links.

    Called when the crawl loop has been idle for too long.  Iterates
    through *all* seed categories and, for each already-crawled seed
    URL, re-fetches it to extract fresh child links that haven't been
    seen yet.  New (unseen) seed URLs are also enqueued directly.

    Returns:
        Number of new URLs added to the queue.
    """
    if ctx.scheduler is None or ctx.dedup is None or ctx.worker is None:
        return 0

    added = 0
    for category in CATEGORIES:
        seed_urls = load_seeds(category=category)
        for url in seed_urls:
            if ctx.dedup.is_url_seen(url):
                try:
                    client = await ctx.worker.get_http_client()
                    resp = await client.get(url, timeout=30.0)
                    if resp.status_code < 400:
                        links = extract_links(resp.text, url)
                        for link in links:
                            if not ctx.dedup.is_url_seen(
                                link
                            ) and await ctx.scheduler.add_url(link, depth=1):
                                added += 1
                except (httpx.HTTPError, OSError):  # noqa: BLE001
                    _logger.debug("reseed_fetch_failed", url=url)
            else:
                if await ctx.scheduler.add_url(url, depth=0):
                    added += 1

    return added


async def seed_and_crawl_loop(
    ctx: AppContext,
    seed_category: str = "tech-docs",
) -> None:
    """Load seeds, schedule URLs, and run the continuous crawl loop.

    Extracted from ``cli/serve.py`` so both CLI and future daemon code
    share the same logic.

    Requires crawler components (worker, scheduler, dedup).
    Search-only nodes should not call this function.
    """
    _logger = structlog.get_logger()

    if ctx.worker is None or ctx.scheduler is None or ctx.dedup is None:
        _logger.error(
            "seed_and_crawl_loop_skipped",
            reason=("crawler components not initialized (search-only role?)"),
        )
        return

    # ── Phase 1: seed loading & rediscovery ────────────────
    seed_urls = load_seeds(category=seed_category)
    if seed_urls:
        queued = 0
        rediscovered = 0
        for url in seed_urls:
            if ctx.dedup.is_url_seen(url):
                try:
                    client = await ctx.worker.get_http_client()
                    resp = await client.get(url, timeout=30.0)
                    if resp.status_code < 400:
                        links = extract_links(resp.text, url)
                        for link in links:
                            if not ctx.dedup.is_url_seen(
                                link
                            ) and await ctx.scheduler.add_url(link, depth=1):
                                rediscovered += 1
                except (httpx.HTTPError, OSError):  # noqa: BLE001
                    _logger.debug("seed_rediscovery_failed", url=url)
            elif await ctx.scheduler.add_url(url, depth=0):
                queued += 1

        _logger.info(
            "seeds_queued",
            category=seed_category,
            total=len(seed_urls),
            new=queued,
            rediscovered=rediscovered,
        )
    else:
        _logger.warning("no_seeds_found", category=seed_category)

    # ── Phase 2: continuous crawl loop ─────────────────────
    ctx.scheduler.set_urls_per_hour(0)

    crawl_count = 0
    disk_check_interval = 60
    last_disk_check = 0.0
    last_crawl_at = time.monotonic()
    last_fts_optimize = time.monotonic()
    idle_restart_threshold = 10.0
    fts_optimize_interval = 3600.0  # optimize FTS5 every hour

    while True:
        now = time.monotonic()
        if now - last_disk_check > disk_check_interval:
            last_disk_check = now
            if is_disk_critically_low(ctx.config.node.data_dir):
                _logger.warning(
                    "disk_space_critical",
                    msg=("Pausing crawl — disk space below 200 MB"),
                )
                await asyncio.sleep(30)
                continue

        try:
            url, depth = await asyncio.wait_for(
                ctx.scheduler.get_url(),
                timeout=5.0,
            )
        except TimeoutError:
            idle_secs = time.monotonic() - last_crawl_at
            if idle_secs >= idle_restart_threshold:
                _logger.info(
                    "crawl_idle_restart",
                    idle_secs=round(idle_secs, 1),
                    crawled=crawl_count,
                    msg="Re-seeding queue after idle timeout",
                )
                try:
                    reseed_count = await _reseed_queue(ctx, _logger)
                except Exception:  # noqa: BLE001
                    _logger.exception("reseed_queue_error")
                    reseed_count = 0
                if reseed_count > 0:
                    last_crawl_at = time.monotonic()
                    _logger.info(
                        "crawl_reseed_complete",
                        new_urls=reseed_count,
                    )
                else:
                    _logger.debug(
                        "crawl_reseed_empty",
                        msg="no new URLs found",
                    )
                    await asyncio.sleep(5)
            else:
                _logger.debug(
                    "serve_idle",
                    crawled=crawl_count,
                    msg="waiting for URLs",
                )
                await asyncio.sleep(1)
            continue

        last_crawl_at = time.monotonic()

        try:
            result = await ctx.worker.crawl_url(url, depth=depth)
            if result.success and result.page:
                if ctx.index_submit_sender is not None:
                    msg = ctx.index_submit_sender.build_submit_message(
                        result.page,
                        result.discovered_links,
                    )
                    ack_count = await ctx.index_submit_sender.send_to_peers(msg)
                    _logger.info(
                        "index_submit_sent",
                        url=url,
                        targets=len(ctx.config.network.index_submit_peers),
                        acked=ack_count,
                    )
                else:
                    index_document(
                        result.page,
                        ctx.store,
                        ctx.vector_store,
                    )
                crawl_count += 1
                if ctx.ledger is not None:
                    try:
                        ctx.ledger.record_action(
                            ActionType.CRAWL,
                            quantity=1.0,
                            note=url[:120],
                            key_pair=ctx.key_pair,
                        )
                    except Exception:  # noqa: BLE001
                        _logger.debug(
                            "credit_record_failed",
                            url=url,
                        )
            elif not result.success:
                _logger.debug(
                    "crawl_skipped",
                    url=url,
                    reason=result.error,
                )
        except Exception:
            _logger.exception("crawl_error", url=url)

        # Periodic FTS5 optimization — merge segments to avoid search slowdown
        now_opt = time.monotonic()
        if now_opt - last_fts_optimize >= fts_optimize_interval:
            last_fts_optimize = now_opt
            try:
                ctx.store.optimize()
                _logger.info(
                    "fts5_optimize_done",
                    crawl_count=crawl_count,
                )
            except Exception:  # noqa: BLE001
                _logger.warning("fts5_optimize_error", exc_info=True)
