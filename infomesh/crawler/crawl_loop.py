"""Continuous crawl loop — seeds, scheduling, and background crawling.

Extracted from ``services.py`` to enforce SRP.  The crawl loop
manages seed loading, link rediscovery, idle-timeout re-seeding,
and credit recording.  Indexing is delegated to
``services.index_document()``.

Also manages RSS/Atom feed polling and priority recrawl queue
for real-time content freshness (Issue #4).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, cast

import httpx
import structlog

from infomesh.crawler.parser import extract_links
from infomesh.crawler.seeds import CATEGORIES, load_seeds
from infomesh.credits.ledger import ActionType
from infomesh.resources.preflight import is_disk_critically_low

if TYPE_CHECKING:
    from infomesh.crawler.feed_monitor import FeedMonitor
    from infomesh.crawler.freshness import PriorityRecrawlQueue
    from infomesh.services import AppContext

logger = structlog.get_logger()


# ── Feed poll loop ──────────────────────────────────────────────────────


async def feed_poll_loop(
    ctx: AppContext,
) -> None:
    """Continuously poll RSS/Atom feeds and enqueue new URLs.

    Runs as a background task alongside the main crawl loop.
    Only active when ``rss_enabled=true`` in config.
    """
    _logger = structlog.get_logger()

    monitor: FeedMonitor | None = None
    queue: PriorityRecrawlQueue | None = None

    if hasattr(ctx, "feed_monitor") and ctx.feed_monitor is not None:
        monitor = cast("FeedMonitor", ctx.feed_monitor)
    if hasattr(ctx, "priority_queue") and ctx.priority_queue is not None:
        queue = cast("PriorityRecrawlQueue", ctx.priority_queue)

    if monitor is None or queue is None:
        _logger.debug("feed_poll_loop_disabled", reason="RSS not enabled")
        return

    if ctx.worker is None:
        _logger.debug("feed_poll_loop_disabled", reason="no crawler worker")
        return

    _logger.info("feed_poll_loop_started")

    while True:
        due_feeds = monitor.get_due_feeds()
        if not due_feeds:
            await asyncio.sleep(10)
            continue

        for feed in due_feeds:
            try:
                client = await ctx.worker.get_http_client()
                resp = await client.get(feed.url, timeout=30.0)
                if resp.status_code < 400:
                    update = monitor.process_feed_response(
                        feed.url,
                        resp.text,
                    )
                    # Enqueue new URLs for priority crawl
                    for url in update.new_urls:
                        from infomesh.crawler.freshness import RecrawlTrigger

                        queue.enqueue(
                            url,
                            RecrawlTrigger.RSS_UPDATE,
                            source_feed=feed.url,
                        )
                else:
                    _logger.debug(
                        "feed_poll_http_error",
                        url=feed.url,
                        status=resp.status_code,
                    )
                    feed.error_count += 1
                    feed.last_poll_at = time.time()
            except (httpx.HTTPError, OSError):  # noqa: BLE001
                _logger.debug("feed_poll_failed", url=feed.url)
                feed.error_count += 1
                feed.last_poll_at = time.time()

        # Sleep briefly before next check
        await asyncio.sleep(5)


# ── Priority recrawl processing ─────────────────────────────────────────


async def _process_priority_queue(
    ctx: AppContext,
    _logger: structlog.stdlib.BoundLogger,
) -> int:
    """Process items from the priority recrawl queue.

    Crawls up to ``batch_size`` URLs from the priority queue.

    Returns:
        Number of URLs successfully crawled.
    """
    queue: PriorityRecrawlQueue | None = None
    if hasattr(ctx, "priority_queue") and ctx.priority_queue is not None:
        queue = cast("PriorityRecrawlQueue", ctx.priority_queue)
    if queue is None or queue.size == 0:
        return 0
    if ctx.worker is None:
        return 0

    processed = 0
    batch_size = 5  # Process up to 5 priority items per cycle

    for _ in range(batch_size):
        item = queue.dequeue()
        if item is None:
            break

        try:
            result = await ctx.worker.crawl_url(item.url, depth=0)
            if result.success and result.page:
                from infomesh.services import (
                    index_document,
                    publish_document_to_network,
                )

                doc_id = index_document(
                    result.page,
                    ctx.store,
                    ctx.vector_store,
                    js_required=result.js_required,
                )
                await publish_document_to_network(
                    result.page,
                    doc_id,
                    p2p_node=ctx.p2p_node,
                    distributed_index=ctx.distributed_index,
                )
                processed += 1
                if ctx.ledger is not None:
                    try:
                        ctx.ledger.record_action(
                            ActionType.CRAWL,
                            quantity=1.0,
                            note=f"priority:{item.trigger}:{item.url[:100]}",
                            key_pair=ctx.key_pair,
                        )
                    except Exception:  # noqa: BLE001
                        _logger.debug(
                            "credit_record_failed",
                            url=item.url,
                        )
            _logger.info(
                "priority_crawl",
                url=item.url,
                trigger=item.trigger,
                success=result.success,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("priority_crawl_error", url=item.url)

    return processed


async def _apply_governor_backpressure(
    ctx: AppContext,
    _logger: structlog.stdlib.BoundLogger,
) -> bool:
    """Apply resource-governor pause/throttle decisions.

    Returns ``True`` when the current crawl-loop iteration should be skipped.
    """
    gov = getattr(ctx, "governor", None)
    if gov is None:
        return False

    state = gov.check_and_adjust()
    level = state.degrade_level.name
    cpu = f"{state.cpu_percent:.0f}%"
    mem = f"{state.memory_percent:.0f}%"

    if gov.should_pause_crawl:
        _logger.warning(
            "governor_pause",
            level=level,
            cpu=cpu,
            mem=mem,
            msg="Pausing crawl — resource overload",
        )
        await asyncio.sleep(10)
        return True

    if gov.should_throttle_crawl:
        throttle_sleep = max(0.1, (1.0 - state.throttle_factor) * 2.0)
        _logger.info(
            "governor_throttle",
            level=level,
            factor=f"{state.throttle_factor:.1f}",
            sleep=f"{throttle_sleep:.1f}s",
        )
        await asyncio.sleep(throttle_sleep)

    return False


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

    # Start feed poll loop as background task if RSS enabled
    feed_task: asyncio.Task[None] | None = None
    if (
        hasattr(ctx, "feed_monitor")
        and ctx.feed_monitor is not None
        and ctx.config.crawl.rss_enabled
    ):
        feed_task = asyncio.create_task(feed_poll_loop(ctx))
        _logger.info("feed_poll_task_started")

    crawl_count = 0
    priority_count = 0
    disk_check_interval = 60
    last_disk_check = 0.0
    last_crawl_at = time.monotonic()
    last_fts_optimize = time.monotonic()
    last_priority_check = time.monotonic()
    idle_restart_threshold = 10.0
    fts_optimize_interval = 3600.0  # optimize FTS5 every hour
    priority_check_interval = 2.0  # check priority queue every 2s
    governor_check_interval = 5.0  # check resource governor every 5s
    last_governor_check = 0.0

    try:
        while True:
            now = time.monotonic()

            # ── Resource governor check (CPU / memory) ─────
            if now - last_governor_check >= governor_check_interval:
                last_governor_check = now
                if await _apply_governor_backpressure(ctx, _logger):
                    continue

            if now - last_disk_check > disk_check_interval:
                last_disk_check = now
                if is_disk_critically_low(ctx.config.node.data_dir):
                    _logger.warning(
                        "disk_space_critical",
                        msg=("Pausing crawl — disk space below 200 MB"),
                    )
                    await asyncio.sleep(30)
                    continue

            # ── Priority recrawl queue (RSS, user requests) ────
            if now - last_priority_check >= priority_check_interval:
                last_priority_check = now
                try:
                    pcount = await _process_priority_queue(ctx, _logger)
                    if pcount > 0:
                        priority_count += pcount
                        last_crawl_at = time.monotonic()
                except Exception:  # noqa: BLE001
                    _logger.exception("priority_queue_error")

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
                        from infomesh.services import (
                            index_document,
                            publish_document_to_network,
                        )

                        doc_id = index_document(
                            result.page,
                            ctx.store,
                            ctx.vector_store,
                            js_required=result.js_required,
                        )
                        await publish_document_to_network(
                            result.page,
                            doc_id,
                            p2p_node=ctx.p2p_node,
                            distributed_index=ctx.distributed_index,
                        )
                        # Auto-register discovered RSS feeds
                        if (
                            ctx.config.crawl.rss_enabled
                            and ctx.config.crawl.rss_discovery
                            and hasattr(ctx, "feed_monitor")
                            and ctx.feed_monitor is not None
                            and result.discovered_feeds
                        ):
                            monitor = cast("FeedMonitor", ctx.feed_monitor)
                            for feed_url in result.discovered_feeds:
                                if len(monitor.feeds) < ctx.config.crawl.rss_max_feeds:
                                    monitor.add_feed(feed_url)
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
    finally:
        if feed_task is not None:
            feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feed_task
