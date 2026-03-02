"""Service layer — centralizes component wiring and shared business logic.

Extracted from CLI and MCP handlers to enforce SRP:
- CLI commands are thin wrappers that parse args and format output.
- MCP tool handlers dispatch to service functions and format responses.
- Business logic lives here, in one place.
- Crawl loop logic lives in ``crawler.crawl_loop``.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass

import structlog

from infomesh.config import Config, NodeRole, load_config
from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.crawler.parser import ParsedPage
from infomesh.crawler.robots import RobotsChecker
from infomesh.crawler.scheduler import Scheduler
from infomesh.crawler.worker import CrawlWorker
from infomesh.credits.github_identity import resolve_github_email
from infomesh.credits.ledger import CreditLedger
from infomesh.index.link_graph import LinkGraph
from infomesh.index.local_store import LocalStore
from infomesh.p2p.keys import ensure_keys
from infomesh.security import SSRFError, validate_url
from infomesh.types import KeyPairLike, VectorStoreLike

logger = structlog.get_logger()

# ─── Content utility functions ─────────────────────────────

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
            try:
                link_graph.add_links(url, result.discovered_links)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "link_graph_update_failed",
                    url=url,
                )
        try:
            index_document(result.page, store, vector_store)
        except Exception:  # noqa: BLE001
            logger.error(
                "index_document_failed",
                url=url,
                msg="Page crawled but indexing failed",
            )
            return CrawlAndIndexResult(
                success=False,
                url=url,
                error="index_failed",
            )
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

        # ── LLM backend (optional, for re-ranking etc.) ────
        self.llm_backend: object | None = None
        if c.llm.enabled:
            try:
                from infomesh.summarizer.engine import create_backend

                self.llm_backend = create_backend(
                    c.llm.runtime,
                    c.llm.model,
                )
            except Exception:  # noqa: BLE001
                logger.warning("llm_backend_unavailable")

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

        # ── Credit sync (cross-node credit aggregation) ────
        self.credit_sync_manager: object | None = None
        if self.ledger is not None and self.github_email:
            try:
                from infomesh.credits.sync import (
                    CreditSyncManager,
                    CreditSyncStore,
                )

                sync_store = CreditSyncStore(
                    c.node.data_dir / "credit_sync.db",
                )
                self.credit_sync_manager = CreditSyncManager(
                    ledger=self.ledger,
                    store=sync_store,
                    owner_email=self.github_email,
                    key_pair=self.key_pair,
                )
            except Exception:  # noqa: BLE001
                logger.warning("credit_sync_unavailable")

        # ── P2P components (set externally via bootstrap_p2p) ──
        self.distributed_index: object | None = None
        self.p2p_node: object | None = None

        logger.info(
            "app_context_initialized",
            role=role,
            has_crawler=self.worker is not None,
            has_search=self.link_graph is not None,
            has_index_sender=self.index_submit_sender is not None,
            has_index_receiver=self.index_submit_receiver is not None,
            has_credit_sync=self.credit_sync_manager is not None,
        )

    def close(self) -> None:
        """Release all resources (sync).

        .. note:: Prefer :meth:`close_async` when running inside an
           event loop — this does **not** close the async HTTP client
           held by the crawl worker.
        """
        if self.credit_sync_manager is not None:
            with contextlib.suppress(Exception):
                self.credit_sync_manager.close()  # type: ignore[attr-defined]
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
        if self.llm_backend is not None:
            try:
                from infomesh.summarizer.engine import LLMBackend

                if isinstance(self.llm_backend, LLMBackend):
                    await self.llm_backend.close()
            except Exception:  # noqa: BLE001
                pass
        self.close()

    def __enter__(self) -> AppContext:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def __aenter__(self) -> AppContext:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close_async()


# ─── P2P helpers (shared by CLI commands) ─────────────────────


def create_local_search_fn(
    config: Config,
) -> object | None:
    """Build an async local-search function for P2P peer requests.

    Constructs a ``LocalStore``, wraps the sync ``search_local`` call in
    an async function, and returns it.  Returns ``None`` if the store
    cannot be opened (missing dependencies, corrupt DB, etc.).

    .. note:: Opens its own ``LocalStore`` connection because the P2P node
       starts *before* ``AppContext`` is created.  SQLite WAL mode supports
       concurrent readers, so this is safe.  The connection is intentionally
       kept open for the lifetime of the P2P node.
    """
    try:
        from infomesh.search.query import search_local

        ls = LocalStore(
            db_path=config.index.db_path,
            tokenizer=config.index.fts_tokenizer,
            compression_enabled=config.storage.compression_enabled,
            compression_level=config.storage.compression_level,
        )

        async def _local_search(
            query: str,
            limit: int = 10,
        ) -> list[dict[str, object]]:
            """Async wrapper around sync search_local for P2P handler."""
            qr = search_local(ls, query, limit=limit)
            return [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "score": r.combined_score,
                    "doc_id": r.doc_id,
                }
                for r in qr.results
            ]

        return _local_search
    except Exception:  # noqa: BLE001
        logger.debug("local_search_fn_unavailable")
        return None


def bootstrap_p2p(
    config: Config,
    *,
    credit_sync_manager: object | None = None,
    local_search_fn: object | None = None,
) -> tuple[object | None, object | None]:
    """Best-effort P2P node + distributed index bootstrap.

    Creates an ``InfoMeshNode``, starts it in a background thread, and
    builds a ``DistributedIndex`` wrapping the node's DHT.

    Returns:
        ``(p2p_node, distributed_index)`` — either or both may be
        ``None`` if P2P is unavailable.
    """
    try:
        from infomesh.p2p.node import InfoMeshNode
    except ImportError:
        logger.warning(
            "p2p_unavailable",
            reason="libp2p not installed",
            hint="pip install 'infomesh[p2p]'",
        )
        return None, None

    try:
        node = InfoMeshNode(
            config,
            credit_sync_manager=credit_sync_manager,
            local_search_fn=local_search_fn,
        )
        node.start(blocking=False)
        logger.info(
            "p2p_started",
            peer_id=node.peer_id,
            listen_port=config.node.listen_port,
            bootstrap_nodes=len(config.network.bootstrap_nodes),
        )
        if not config.network.bootstrap_nodes:
            logger.warning(
                "p2p_no_bootstrap",
                msg=(
                    "No bootstrap nodes configured. "
                    "Add [network] bootstrap_nodes in "
                    "~/.infomesh/config.toml to connect to peers."
                ),
            )
    except Exception as exc:
        logger.warning(
            "p2p_start_failed",
            error=str(exc),
            msg=(
                "P2P node failed to start — running in local-only mode. "
                "Crawling, indexing, and local search still work."
            ),
        )
        return None, None

    # Build DistributedIndex from the node's DHT
    distributed_index: object | None = None
    try:
        dht = getattr(node, "dht", None)
        pid = getattr(node, "peer_id", "")
        if dht is not None and pid:
            from infomesh.index.distributed import DistributedIndex

            distributed_index = DistributedIndex(dht, pid)
            logger.info("distributed_index_ready")
    except Exception:  # noqa: BLE001
        logger.debug("distributed_index_unavailable")

    return node, distributed_index


# ─── Backward-compatible re-exports from crawler.crawl_loop ───
# Placed at end-of-module to avoid circular import (crawl_loop
# imports AppContext which is defined above).

from infomesh.crawler.crawl_loop import (  # noqa: E402, I001
    _reseed_queue as _reseed_queue,
    seed_and_crawl_loop as seed_and_crawl_loop,
)
