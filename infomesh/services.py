"""Service layer — centralizes component wiring and shared business logic.

Extracted from CLI and MCP handlers to enforce SRP:
- CLI commands are thin wrappers that parse args and format output.
- MCP tool handlers dispatch to service functions and format responses.
- Business logic lives here, in one place.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import structlog

from infomesh.config import Config, NodeRole, load_config
from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.crawler.parser import ParsedPage
from infomesh.crawler.robots import RobotsChecker
from infomesh.crawler.scheduler import Scheduler
from infomesh.crawler.worker import CrawlWorker
from infomesh.credits.github_identity import resolve_github_email
from infomesh.credits.ledger import ActionType, CreditLedger
from infomesh.index.link_graph import LinkGraph
from infomesh.index.local_store import LocalStore
from infomesh.p2p.keys import ensure_keys
from infomesh.security import SSRFError, validate_url
from infomesh.types import KeyPairLike, VectorStoreLike

logger = structlog.get_logger()

# Paywall signal strings (case-insensitive match)
_PAYWALL_SIGNALS = (
    "subscribe to continue",
    "sign in to read",
    "create a free account",
    "this content is for subscribers",
)


def is_paywall_content(text: str) -> bool:
    """Check if content contains paywall signals."""
    lower = text.lower()
    return any(sig in lower for sig in _PAYWALL_SIGNALS)


def _truncate_to_bytes(text: str, max_bytes: int) -> str:
    """Truncate text to fit within ``max_bytes`` when encoded as UTF-8.

    Avoids cutting multi-byte characters in the middle.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ─── index_document: the single "crawl → index → vector-index" pattern ────


def index_document(
    page: ParsedPage,
    store: LocalStore,
    vector_store: VectorStoreLike | None = None,
) -> int | None:
    """Index a crawled page into FTS5 and optionally vector store.

    This is the **single source of truth** for the pattern:
        store.add_document(...)
        if vector_store and doc_id:
            vector_store.add_document(...)

    Args:
        page: Parsed crawl result.
        store: FTS5 local store.
        vector_store: Optional VectorStore instance. Pass None to skip.

    Returns:
        Document ID if indexed, None if duplicate.
    """
    doc_id = store.add_document(
        url=page.url,
        title=page.title,
        text=page.text,
        raw_html_hash=page.raw_html_hash,
        text_hash=page.text_hash,
        language=page.language,
    )

    if vector_store is not None and doc_id is not None:
        vector_store.add_document(
            doc_id=doc_id,
            url=page.url,
            title=page.title,
            text=page.text,
            language=page.language,
        )

    return doc_id


# ─── FetchResult: unified return type for fetch_page ──────


@dataclass(frozen=True)
class FetchPageResult:
    """Result of fetching a page (from cache or live crawl)."""

    success: bool
    title: str = ""
    url: str = ""
    text: str = ""
    is_cached: bool = False
    is_stale: bool = False
    is_paywall: bool = False
    crawled_at: float = 0.0
    error: str | None = None


def fetch_page(
    url: str,
    *,
    store: LocalStore,
    worker: CrawlWorker,
    vector_store: VectorStoreLike | None = None,
    max_size_bytes: int = 102_400,
    cache_ttl_seconds: int = 604_800,
) -> FetchPageResult:
    """Fetch page content — from local cache if fresh, else live crawl.

    This is a sync wrapper; the caller must await the crawl separately
    if not cached. For the async version, see ``fetch_page_async``.
    """
    # SSRF protection
    try:
        validate_url(url)
    except SSRFError as exc:
        return FetchPageResult(success=False, url=url, error=f"blocked: {exc}")

    # Try local cache first
    doc = store.get_document_by_url(url)
    if doc:
        age = time.time() - doc.crawled_at
        return FetchPageResult(
            success=True,
            title=doc.title,
            url=doc.url,
            text=_truncate_to_bytes(doc.text, max_size_bytes),
            is_cached=True,
            is_stale=age > cache_ttl_seconds,
            crawled_at=doc.crawled_at,
        )
    # Caller must handle live crawl (async)
    return FetchPageResult(success=False, url=url, error="not_cached")


async def fetch_page_async(
    url: str,
    *,
    store: LocalStore,
    worker: CrawlWorker,
    vector_store: VectorStoreLike | None = None,
    max_size_bytes: int = 102_400,
    cache_ttl_seconds: int = 604_800,
) -> FetchPageResult:
    """Fetch page content — cache first, then live crawl.

    Combines cache lookup + crawl + indexing + paywall detection.
    """
    # SSRF protection
    try:
        validate_url(url)
    except SSRFError as exc:
        return FetchPageResult(success=False, url=url, error=f"blocked: {exc}")

    # Try cache
    cached = fetch_page(
        url,
        store=store,
        worker=worker,
        vector_store=vector_store,
        max_size_bytes=max_size_bytes,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    if cached.success:
        return cached

    # Live crawl
    result = await worker.crawl_url(url)

    # HTTP paywall detection
    if not result.success and result.error:
        if result.error in ("http_402", "http_403"):
            return FetchPageResult(
                success=False,
                url=url,
                error=f"paywall:{result.error}",
                is_paywall=True,
            )
        return FetchPageResult(success=False, url=url, error=result.error)

    if result.success and result.page:
        paywall = is_paywall_content(result.page.text)
        index_document(result.page, store, vector_store)
        return FetchPageResult(
            success=True,
            title=result.page.title,
            url=url,
            text=_truncate_to_bytes(result.page.text, max_size_bytes),
            is_cached=False,
            is_paywall=paywall,
            crawled_at=time.time(),
        )

    return FetchPageResult(success=False, url=url, error=result.error)


# ─── crawl_and_index: crawl + link graph + index in one call ───


@dataclass(frozen=True)
class CrawlAndIndexResult:
    """Result of a crawl-and-index operation."""

    success: bool
    title: str = ""
    url: str = ""
    text_length: int = 0
    links_discovered: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None


async def crawl_and_index(
    url: str,
    *,
    worker: CrawlWorker,
    store: LocalStore,
    vector_store: VectorStoreLike | None = None,
    link_graph: LinkGraph | None = None,
    depth: int = 0,
    force: bool = False,
) -> CrawlAndIndexResult:
    """Crawl a URL, update link graph, and index the page.

    Single entry point for the crawl→link-graph→index pipeline,
    eliminates duplication between MCP and CLI handlers.
    """
    from infomesh.crawler.worker import CrawlResult  # avoid circular

    result: CrawlResult = await worker.crawl_url(url, depth=depth, force=force)

    if result.success and result.page:
        if link_graph and result.discovered_links:
            link_graph.add_links(url, result.discovered_links)
        index_document(result.page, store, vector_store)
        return CrawlAndIndexResult(
            success=True,
            title=result.page.title,
            url=url,
            text_length=len(result.page.text),
            links_discovered=len(result.discovered_links),
            elapsed_ms=result.elapsed_ms,
        )

    return CrawlAndIndexResult(
        success=False,
        url=url,
        error=result.error,
    )


# ─── seed_and_crawl_loop: the main crawl loop ─────────────


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
    import asyncio

    from infomesh.crawler.parser import extract_links
    from infomesh.crawler.seeds import load_seeds
    from infomesh.resources.preflight import is_disk_critically_low

    _logger = structlog.get_logger()

    if ctx.worker is None or ctx.scheduler is None or ctx.dedup is None:
        _logger.error(
            "seed_and_crawl_loop_skipped",
            reason="crawler components not initialized (search-only role?)",
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
    # Disable hourly rate limit for the background crawl loop.
    # The 60 URLs/hr limit (config.crawl.urls_per_hour) is intended
    # for the crawl_url() MCP API, not the continuous background crawler.
    # Per-domain politeness delays still apply.
    ctx.scheduler.set_urls_per_hour(0)

    crawl_count = 0
    disk_check_interval = 60
    last_disk_check = 0.0

    while True:
        now = time.monotonic()
        if now - last_disk_check > disk_check_interval:
            last_disk_check = now
            if is_disk_critically_low(ctx.config.node.data_dir):
                _logger.warning(
                    "disk_space_critical",
                    msg="Pausing crawl — disk space below 200 MB",
                )
                await asyncio.sleep(30)
                continue

        try:
            url, depth = await asyncio.wait_for(
                ctx.scheduler.get_url(),
                timeout=5.0,
            )
        except TimeoutError:
            _logger.debug("serve_idle", crawled=crawl_count, msg="waiting for URLs")
            await asyncio.sleep(1)
            continue

        try:
            result = await ctx.worker.crawl_url(url, depth=depth)
            if result.success and result.page:
                # Crawler-only role: forward to indexer peers
                if ctx.index_submit_sender is not None:
                    ctx.index_submit_sender.build_submit_message(
                        result.page,
                        result.discovered_links,
                    )
                    _logger.info(
                        "index_submit_queued",
                        url=url,
                        targets=len(ctx.config.network.index_submit_peers),
                    )
                    # TODO: send msg to index_submit_peers via P2P stream
                else:
                    # Full role: index locally
                    index_document(result.page, ctx.store, ctx.vector_store)
                crawl_count += 1
                # Record crawl credit (1.0 per page)
                if ctx.ledger is not None:
                    try:
                        ctx.ledger.record_action(
                            ActionType.CRAWL,
                            quantity=1.0,
                            note=url[:120],
                            key_pair=ctx.key_pair,
                        )
                    except Exception:  # noqa: BLE001
                        _logger.debug("credit_record_failed", url=url)
            elif not result.success:
                _logger.debug("crawl_skipped", url=url, reason=result.error)
        except Exception:
            _logger.exception("crawl_error", url=url)


# ─── AppContext: unified component factory ─────────────────


class AppContext:
    """Wire up all InfoMesh components from Config.

    Replaces the repeated initialization blocks scattered across
    CLI commands and the MCP server.  Call ``close()`` or use as
    a context manager to release resources.

    Usage::

        ctx = AppContext(config)
        result = await ctx.worker.crawl_url("https://example.com")
        index_document(result.page, ctx.store, ctx.vector_store)
        ctx.close()
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        c = self.config
        role = c.node.role

        # ── Resolve GitHub identity for cross-node credits ─
        self.github_email: str = resolve_github_email(c) or ""

        # ── Always needed: local store + keys ──────────────
        self.store = LocalStore(
            db_path=c.index.db_path,
            tokenizer=c.index.fts_tokenizer,
            compression_enabled=c.storage.compression_enabled,
            compression_level=c.storage.compression_level,
        )

        # Node key pair (for signing credit entries, attestation, etc.)
        self.key_pair: KeyPairLike | None = None
        try:
            self.key_pair = ensure_keys(c.node.data_dir)
        except Exception:  # noqa: BLE001
            logger.warning("keypair_unavailable")

        # ── Crawler components (full + crawler roles) ──────
        self.dedup: DeduplicatorDB | None = None
        self.robots: RobotsChecker | None = None
        self.scheduler: Scheduler | None = None
        self.worker: CrawlWorker | None = None

        if role in (NodeRole.FULL, NodeRole.CRAWLER):
            self.dedup = DeduplicatorDB(str(c.node.data_dir / "dedup.db"))
            self.robots = RobotsChecker(c.crawl.user_agent)
            self.scheduler = Scheduler(
                politeness_delay=c.crawl.politeness_delay,
                urls_per_hour=c.crawl.urls_per_hour,
                pending_per_domain=c.crawl.pending_per_domain,
                max_depth=c.crawl.max_depth,
            )
            self.worker = CrawlWorker(c.crawl, self.scheduler, self.dedup, self.robots)

        # ── Search / index components (full + search roles) ─
        self.link_graph: LinkGraph | None = None
        self.ledger: CreditLedger | None = None
        self.vector_store: VectorStoreLike | None = None

        if role in (NodeRole.FULL, NodeRole.SEARCH):
            self.link_graph = LinkGraph(str(c.node.data_dir / "links.db"))

            try:
                self.ledger = CreditLedger(
                    c.node.data_dir / "credits.db",
                    owner_email=self.github_email,
                )
            except Exception:  # noqa: BLE001
                logger.warning("credit_ledger_unavailable")

            if c.index.vector_search:
                try:
                    from infomesh.index.vector_store import VectorStore

                    self.vector_store = VectorStore(  # type: ignore[assignment]
                        persist_dir=c.node.data_dir / "chroma",
                        model_name=c.index.embedding_model,
                    )
                except ImportError:
                    logger.warning(
                        "vector_search_unavailable",
                        reason="chromadb not installed",
                    )

        # ── Index submit (crawler → indexer bridge) ────────
        self.index_submit_sender = None
        self.index_submit_receiver = None

        if role == NodeRole.CRAWLER and c.network.index_submit_peers:
            from infomesh.p2p.index_submit import IndexSubmitSender

            self.index_submit_sender = IndexSubmitSender(c, self.key_pair)

        if role == NodeRole.SEARCH:
            from infomesh.p2p.index_submit import IndexSubmitReceiver

            self.index_submit_receiver = IndexSubmitReceiver(
                c,
                self.store,
                self.vector_store,
                self.key_pair,
            )

        logger.info(
            "app_context_initialized",
            role=role,
            has_crawler=self.worker is not None,
            has_search=self.link_graph is not None,
            has_index_sender=self.index_submit_sender is not None,
            has_index_receiver=self.index_submit_receiver is not None,
        )

    def close(self) -> None:
        """Release all resources (sync).

        .. note:: Prefer :meth:`close_async` when running inside an
           event loop — this does **not** close the async HTTP client
           held by the crawl worker.
        """
        if self.vector_store is not None:
            self.vector_store.close()
        if self.ledger is not None:
            self.ledger.close()
        if self.link_graph is not None:
            self.link_graph.close()
        self.store.close()
        if self.dedup is not None:
            self.dedup.close()

    async def close_async(self) -> None:
        """Release all resources including async HTTP client."""
        if self.worker is not None:
            await self.worker.close()
        self.close()

    def __enter__(self) -> AppContext:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def __aenter__(self) -> AppContext:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close_async()
