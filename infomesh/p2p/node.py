"""InfoMesh P2P node — the main peer lifecycle manager.

Manages:
  - libp2p host creation and configuration.
  - DHT bootstrap and maintenance.
  - Protocol handler registration (search, index, crawl, replicate, ping).
  - Background trio event loop (bridging asyncio ↔ trio).

**Architecture note**: py-libp2p uses trio internally. This module runs a
trio event loop in a background thread and exposes synchronous + asyncio
bridge methods for integration with the rest of InfoMesh (which uses asyncio).

Usage::

    node = InfoMeshNode(config)
    node.start()          # Starts trio loop in background thread
    node.stop()           # Cleanly shuts down
    node.peer_id          # This node's peer ID
    node.connected_peers  # Currently connected peers (list)
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

from infomesh.config import Config, NodeRole
from infomesh.crawler.url_assigner import UrlAssigner
from infomesh.p2p.dht import InfoMeshDHT
from infomesh.p2p.mdns import MDNSDiscovery
from infomesh.p2p.peer_store import PeerStore
from infomesh.p2p.pex import (
    PEX_MAX_PEERS,
    PEX_MAX_PEERS_PER_ROUND,
    PEX_ROUND_INTERVAL,
    PeerExchange,
)
from infomesh.p2p.protocol import (
    PROTOCOL_CREDIT_SYNC,
    PROTOCOL_INDEX_SUBMIT,
    PROTOCOL_PEX,
    PROTOCOL_PING,
    PROTOCOL_REPLICATE,
    PROTOCOL_SEARCH,
    MessageType,
    decode_message,
    encode_message,
)
from infomesh.p2p.replication import Replicator
from infomesh.p2p.routing import QueryRouter
from infomesh.p2p.sybil import SubnetLimiter, compute_pow_hash, generate_pow
from infomesh.p2p.throttle import BandwidthThrottle
from infomesh.version_check import PeerVersionTracker

logger = structlog.get_logger()

# ── Module-level constants ─────────────────────────────────────────
_ROUTING_REFRESH_INTERVAL = 300  # Refresh routing table every 5 min
_STATUS_WRITE_INTERVAL = 10  # Write status file every 10 s
_CREDIT_SYNC_INTERVAL = 300  # Credit sync every 5 min


class NodeState(StrEnum):
    """Lifecycle states for the P2P node."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class NodeInfo:
    """Public information about this node."""

    peer_id: str = ""
    listen_addrs: list[str] = field(default_factory=list)
    connected_peers: int = 0
    state: str = NodeState.STOPPED
    uptime_seconds: float = 0.0
    dht_keys_stored: int = 0


class InfoMeshNode:
    """P2P node lifecycle manager.

    Creates a libp2p host, connects to the DHT, registers protocol
    handlers, and bridges the trio event loop to the rest of InfoMesh.

    Args:
        config: InfoMesh configuration.
        local_search_fn: Optional async function(query, limit) for handling
                         incoming search requests. If not provided, search
                         requests from peers are rejected.
        store_fn: Optional async function for handling replication requests.
    """

    def __init__(
        self,
        config: Config,
        *,
        local_search_fn: object | None = None,
        store_fn: object | None = None,
        index_submit_receiver: object | None = None,
        credit_sync_manager: object | None = None,
    ) -> None:
        self._config = config
        self._local_search_fn = local_search_fn
        self._store_fn = store_fn
        self._index_submit_receiver = index_submit_receiver
        self._credit_sync_manager = credit_sync_manager

        self._state = NodeState.STOPPED
        self._peer_id: str = ""
        self._start_time: float = 0.0

        # libp2p objects (initialized in trio context)
        self._host: object | None = None
        self._dht: InfoMeshDHT | None = None
        self._router: QueryRouter | None = None
        self._replicator: Replicator | None = None

        # Integration subsystems
        self._subnet_limiter = SubnetLimiter(
            max_per_subnet=config.network.subnet_max_per_bucket
            if hasattr(config.network, "subnet_max_per_bucket")
            else 3,
        )
        self._mdns: MDNSDiscovery | None = None
        self._peer_store: PeerStore | None = None
        self._pex: PeerExchange | None = None
        self._throttle = BandwidthThrottle(
            upload_mbps=config.network.upload_limit_mbps,
            download_mbps=config.network.download_limit_mbps,
        )
        self._pow_nonce: int | None = None
        self._url_assigner: UrlAssigner | None = None

        # Bootstrap status tracking
        self._bootstrap_results: dict[str, object] = {}

        # Peer version tracking (for update notifications)
        self._peer_version_tracker = PeerVersionTracker()

        # Background thread for trio
        self._trio_thread: threading.Thread | None = None
        self._trio_cancel_scope: object | None = None
        self._trio_token: object | None = None
        self._started_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def state(self) -> NodeState:
        """Current node state."""
        return self._state

    @property
    def version_tracker(self) -> PeerVersionTracker:
        """Peer version tracker for update notifications."""
        return self._peer_version_tracker

    def _write_status_file(
        self,
        *,
        state: str | None = None,
        error: str = "",
    ) -> None:
        """Write P2P status to a JSON file for the status command."""
        import json

        status_path = self._config.node.data_dir / "p2p_status.json"
        try:
            peer_ids: list[str] = []
            if self._host and self._state == NodeState.RUNNING:
                try:
                    peer_ids = self.get_connected_peers()
                except Exception:
                    logger.debug("status_peers_failed")

            peers = len(peer_ids)
            addrs: list[str] = []
            if self._host and self._state == NodeState.RUNNING:
                try:
                    addrs = [
                        str(a)
                        for a in self._host.get_addrs()  # type: ignore[attr-defined]
                    ]
                except Exception:
                    logger.debug("status_addrs_failed")

            # DHT stats
            dht_data: dict[str, int] = {}
            if self._dht is not None:
                ds = self._dht.stats
                dht_data = {
                    "keys_stored": ds.keys_stored,
                    "keys_published": ds.keys_published,
                    "gets_performed": ds.gets_performed,
                    "puts_performed": ds.puts_performed,
                }

            # Bandwidth stats
            bw_data: dict[str, int] = {}
            ts = self._throttle.stats
            bw_data = {
                "upload_bytes": ts.upload_bytes,
                "download_bytes": ts.download_bytes,
                "upload_waits": ts.upload_waits,
                "download_waits": ts.download_waits,
            }

            data = {
                "state": state or str(self._state),
                "peer_id": self._peer_id,
                "peers": peers,
                "peer_ids": peer_ids,
                "listen_addrs": addrs,
                "timestamp": time.time(),
                "error": error,
                "dht": dht_data,
                "bandwidth": bw_data,
                "bootstrap": self._bootstrap_results,
                "peer_versions": self._peer_version_tracker.peer_versions,
            }
            status_path.write_text(json.dumps(data))
        except OSError:
            pass  # non-critical

    @property
    def peer_id(self) -> str:
        """This node's libp2p peer ID."""
        return self._peer_id

    @property
    def dht(self) -> InfoMeshDHT | None:
        """The DHT instance (available when running)."""
        return self._dht

    @property
    def router(self) -> QueryRouter | None:
        """The query router (available when running)."""
        return self._router

    @property
    def replicator(self) -> Replicator | None:
        """The replicator (available when running)."""
        return self._replicator

    @property
    def throttle(self) -> BandwidthThrottle:
        """Bandwidth throttle for P2P transfers."""
        return self._throttle

    @property
    def subnet_limiter(self) -> SubnetLimiter:
        """Subnet rate limiter for Sybil defense."""
        return self._subnet_limiter

    @property
    def mdns(self) -> MDNSDiscovery | None:
        """mDNS discovery instance (available when running)."""
        return self._mdns

    @property
    def pow_nonce(self) -> int | None:
        """The PoW nonce used for this node's identity."""
        return self._pow_nonce

    @property
    def url_assigner(self) -> UrlAssigner | None:
        """URL→node assigner based on Kademlia XOR distance."""
        return self._url_assigner

    def get_info(self) -> NodeInfo:
        """Get node status information."""
        info = NodeInfo(
            peer_id=self._peer_id,
            state=str(self._state),
        )
        if self._state == NodeState.RUNNING and self._host is not None:
            info.listen_addrs = [str(a) for a in self._host.get_addrs()]  # type: ignore[attr-defined]
            info.connected_peers = len(self._host.get_connected_peers())  # type: ignore[attr-defined]
            info.uptime_seconds = time.time() - self._start_time
            if self._dht:
                info.dht_keys_stored = self._dht.stats.keys_stored
        return info

    # ─── Asyncio ↔ Trio bridge ─────────────────────────────

    async def search_network(
        self,
        query: str,
        keywords: list[str],
        limit: int = 10,
    ) -> list[dict[str, object]]:
        """Search the P2P network for results (asyncio-safe).

        Bridges from the caller's asyncio event loop into the trio
        event loop running the P2P node.  Returns a list of result
        dicts with url, title, snippet, score, peer_id, doc_id.

        Safe to call from asyncio — internally uses
        ``trio.from_thread.run()`` via ``asyncio.to_thread()``.

        Returns an empty list when P2P is not running.
        """
        if (
            self._router is None
            or self._trio_token is None
            or self._state != NodeState.RUNNING
        ):
            return []

        import asyncio

        _SEARCH_NETWORK_TIMEOUT = 30  # seconds

        def _sync_bridge() -> list[dict[str, object]]:
            import trio

            from infomesh.p2p.routing import RemoteSearchResult

            async def _do() -> list[RemoteSearchResult]:
                assert self._router is not None  # noqa: S101
                return await self._router.route_query(
                    query,
                    keywords,
                    limit,
                )

            results: list[RemoteSearchResult] = trio.from_thread.run(
                _do,
                trio_token=self._trio_token,  # type: ignore[arg-type]
            )
            return [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "score": r.score,
                    "peer_id": r.peer_id,
                    "doc_id": r.doc_id,
                }
                for r in results
            ]

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_sync_bridge),
                timeout=_SEARCH_NETWORK_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                "search_network_timeout",
                query=query[:60],
                timeout=_SEARCH_NETWORK_TIMEOUT,
            )
            return []

    # ─── Lifecycle ─────────────────────────────────────────

    def start(self, *, blocking: bool = False) -> None:
        """Start the P2P node.

        Launches the trio event loop in a background thread (unless
        ``blocking=True``).  The trio loop initializes the libp2p host,
        bootstraps the DHT, and registers protocol handlers.

        Args:
            blocking: If True, run in the current thread (blocks).

        Raises:
            ImportError: If ``libp2p`` is not installed.
        """
        # Early check — fail fast with clear error instead of timing
        # out in the background thread.
        try:
            import libp2p  # noqa: F401
        except ImportError:
            raise ImportError(
                "libp2p is required for P2P networking. "
                "Install with: pip install 'infomesh[p2p]'"
            ) from None

        if self._state in (NodeState.RUNNING, NodeState.STARTING):
            logger.warning("node_already_running", state=self._state)
            return

        self._state = NodeState.STARTING
        self._stop_event.clear()
        self._started_event.clear()

        if blocking:
            self._run_trio_loop()
        else:
            self._trio_thread = threading.Thread(
                target=self._run_trio_loop,
                name="infomesh-p2p",
                daemon=True,
            )
            self._trio_thread.start()
            # Wait for node to be ready.
            # 120s allows time for libp2p import + PoW generation
            # on low-spec VMs (e.g. Azure B1s with 1 vCPU).
            self._started_event.wait(timeout=120)
            if self._state != NodeState.RUNNING:
                raise RuntimeError(f"Node failed to start: {self._state}")

    def stop(self) -> None:
        """Stop the P2P node cleanly."""
        if self._state != NodeState.RUNNING:
            return

        self._state = NodeState.STOPPING
        self._stop_event.set()

        if self._trio_cancel_scope is not None:
            self._trio_cancel_scope.cancel()  # type: ignore[attr-defined]

        if self._trio_thread is not None:
            self._trio_thread.join(timeout=10)
            if self._trio_thread.is_alive():
                logger.warning(
                    "trio_thread_timeout",
                    msg=(
                        "P2P thread did not exit within 10s; "
                        "it will be abandoned as a daemon thread"
                    ),
                )
            self._trio_thread = None

        self._state = NodeState.STOPPED
        self._write_status_file(state="stopped")
        logger.info("node_stopped", peer_id=self._peer_id)

    # ─── Trio event loop ───────────────────────────────────

    def _run_trio_loop(self) -> None:
        """Run the trio event loop (called in background thread or blocking)."""
        try:
            import trio

            trio.run(self._trio_main)
        except Exception as exc:
            self._state = NodeState.ERROR
            self._write_status_file(state="error", error=str(exc))
            logger.exception("trio_loop_failed")

    async def _trio_main(self) -> None:
        """Main trio async entry point — sets up libp2p and runs until stopped."""
        import trio
        from libp2p import new_host
        from libp2p.kad_dht import KadDHT
        from libp2p.kad_dht.kad_dht import DHTMode
        from libp2p.records.validator import NamespacedValidator, Validator
        from libp2p.tools.async_service.trio_service import background_trio_service

        # Save trio token so asyncio code can submit work to this loop.
        self._trio_token = trio.lowlevel.current_trio_token()

        # ── Sybil PoW + host creation ──
        key_pair, listen_addr = self._prepare_identity()

        self._host = new_host(key_pair=key_pair)  # type: ignore[arg-type]

        async with self._host.run([listen_addr]):
            self._peer_id = str(self._host.get_id())
            logger.info(
                "node_started",
                peer_id=self._peer_id,
                addrs=[str(a) for a in self._host.get_addrs()],
            )

            # Create DHT with InfoMesh namespace validator
            class InfoMeshValidator(Validator):
                _MAX_VALUE_SIZE = 1024 * 1024  # 1 MB

                def validate(self, key: str, value: bytes) -> None:
                    if len(value) > self._MAX_VALUE_SIZE:
                        raise ValueError(f"DHT value too large: {len(value)} bytes")

                def select(self, key: str, values: list[bytes]) -> int:
                    return 0

            validator = NamespacedValidator(
                {
                    "pk": InfoMeshValidator(),
                    "infomesh": InfoMeshValidator(),
                }
            )
            kad_dht = KadDHT(
                self._host,
                mode=DHTMode.SERVER,
                validator=validator,
                validator_changed=True,
            )

            async with background_trio_service(kad_dht):
                self._init_subsystems(kad_dht)
                self._register_handlers()

                # Mark node as RUNNING before bootstrap — bootstrap is
                # best-effort and must not block startup.  The node is
                # fully operational once handlers are registered.
                self._start_time = time.time()
                self._state = NodeState.RUNNING
                self._started_event.set()
                self._write_status_file()

                await self._post_bootstrap_setup()
                await self._run_main_loop()

    def _prepare_identity(self) -> tuple[object, object]:
        """Load keys, compute PoW, and return (key_pair, listen_addr).

        Called at the beginning of ``_trio_main`` to keep that method
        focused on the host/DHT lifecycle.
        """
        from multiaddr import Multiaddr

        key_pair = self._load_or_create_libp2p_key()

        pub_key_bytes = key_pair.public_key.to_bytes()  # type: ignore[attr-defined]
        pow_cache_path = self._config.node.data_dir / "keys" / "pow_cache.bin"
        cached_nonce = self._load_cached_pow(pow_cache_path, pub_key_bytes)

        if cached_nonce is not None:
            pow_hash = compute_pow_hash(pub_key_bytes, cached_nonce)
            self._pow_nonce = cached_nonce
            node_id = pow_hash.hex()[:40]
            logger.info(
                "pow_cached",
                nonce=cached_nonce,
                node_id=node_id,
            )
        else:
            logger.info("pow_generating", difficulty=20)
            pow_result = generate_pow(pub_key_bytes, difficulty_bits=20)
            self._pow_nonce = pow_result.nonce
            node_id = pow_result.hash_hex[:40]
            logger.info(
                "pow_complete",
                nonce=pow_result.nonce,
                node_id=node_id,
                elapsed=round(pow_result.elapsed_seconds, 1),
            )
            self._save_cached_pow(pow_cache_path, pub_key_bytes, pow_result.nonce)

        listen_addr = Multiaddr(
            f"/ip4/{self._config.node.listen_address}"
            f"/tcp/{self._config.node.listen_port}"
        )
        return key_pair, listen_addr

    def _init_subsystems(self, kad_dht: object) -> None:
        """Initialize DHT, router, replicator, and URL assigner."""
        self._dht = InfoMeshDHT(kad_dht, self._peer_id)
        self._router = QueryRouter(self._dht, self._host, self._peer_id)
        self._replicator = Replicator(
            self._host,
            self._dht,
            self._peer_id,
            replication_factor=self._config.network.replication_factor,
        )
        self._url_assigner = UrlAssigner(self._peer_id)

    async def _post_bootstrap_setup(self) -> None:
        """Peer store, bootstrap, mDNS, revocations, credit sync."""
        self._peer_store = PeerStore(self._config.node.data_dir)
        self._pex = PeerExchange(peer_id=self._peer_id)

        # Bootstrap — connect to known peers (non-blocking)
        await self._bootstrap()

        # If bootstrap failed, try cached peers
        if (
            self._bootstrap_results.get("connected", 0) == 0
            and self._peer_store is not None
        ):
            await self._connect_cached_peers()

        # mDNS: start LAN peer discovery
        self._mdns = MDNSDiscovery(
            peer_id=self._peer_id,
            port=self._config.node.listen_port,
        )
        self._mdns.start()
        logger.info("mdns_integration_started")

        # Publish pending key revocations to DHT
        await self._publish_pending_revocations()

        # Announce credit identity to connected peers
        await self._announce_credit_sync()

        # Update status after bootstrap (peer count may have changed)
        self._write_status_file()

    async def _run_main_loop(self) -> None:
        """Periodic maintenance loop — routing refresh, PEX, credit sync."""
        import trio

        last_refresh = time.time()
        last_status = time.time()
        last_pex = time.time()
        last_credit_sync = time.time()
        last_version_check = time.time()
        _VERSION_CHECK_INTERVAL = 3600  # Check peer versions hourly

        while not self._stop_event.is_set():
            await trio.sleep(1)

            now = time.time()

            # Periodic routing table refresh
            if now - last_refresh >= _ROUTING_REFRESH_INTERVAL:
                last_refresh = now
                await self._refresh_routing_table()
                await self._save_connected_peers()
                if self._peer_store is not None:
                    try:
                        self._peer_store.prune()
                    except Exception:  # noqa: BLE001
                        logger.warning("peer_store_prune_failed")

            # Periodic PEX round
            if now - last_pex >= PEX_ROUND_INTERVAL:
                last_pex = now
                try:
                    await self._run_pex_round()
                except Exception:  # noqa: BLE001
                    logger.warning("pex_round_failed")

            # Periodic credit sync with same-owner peers
            if now - last_credit_sync >= _CREDIT_SYNC_INTERVAL:
                last_credit_sync = now
                try:
                    await self._run_credit_sync_round()
                except Exception:  # noqa: BLE001
                    logger.warning("credit_sync_round_failed")

            # Update status file periodically
            if now - last_status >= _STATUS_WRITE_INTERVAL:
                last_status = now
                self._write_status_file()

            # Integrate mDNS-discovered peers
            try:
                await self._connect_mdns_peers()
            except Exception:  # noqa: BLE001
                logger.debug("mdns_connect_failed")

            # Periodic peer version check — log if peers run newer
            if now - last_version_check >= _VERSION_CHECK_INTERVAL:
                last_version_check = now
                _peer_update = self._peer_version_tracker.check_peer_update()
                if _peer_update is not None:
                    logger.info(
                        "update_available_from_peers",
                        current=_peer_update.current,
                        latest=_peer_update.latest,
                        msg=(
                            "A connected peer is running a newer "
                            "version. Run: infomesh update"
                        ),
                    )

        # ── Shutdown: save connected peers + cleanup ──
        await self._save_connected_peers()

        if self._peer_store is not None:
            self._peer_store.close()
            self._peer_store = None

        if self._mdns:
            self._mdns.stop()
            self._mdns = None

        self._write_status_file(state="stopped")
        logger.info("node_shutting_down", peer_id=self._peer_id)

    def _register_handlers(self) -> None:
        """Register libp2p protocol stream handlers based on node role."""
        if self._host is None:
            return

        role = self._config.node.role

        # Ping handler — always registered
        async def _handle_ping(stream: object) -> None:
            try:
                raw = await stream.read(1024)  # type: ignore[attr-defined]
                # Extract sender version if present
                try:
                    _, ping_payload = decode_message(raw)
                    sender_pid = ping_payload.get("peer_id", "")
                    sender_ver = ping_payload.get("version", "")
                    if sender_pid and sender_ver:
                        self._peer_version_tracker.record(
                            str(sender_pid), str(sender_ver)
                        )
                except Exception:  # noqa: BLE001
                    pass
                from infomesh import __version__

                pong = encode_message(
                    MessageType.PONG,
                    {"peer_id": self._peer_id, "version": __version__},
                )
                await stream.write(pong)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("ping_handler_error")
            finally:
                await stream.close()  # type: ignore[attr-defined]

        self._host.set_stream_handler(PROTOCOL_PING, _handle_ping)  # type: ignore[attr-defined]

        # PEX handler — always registered (peer exchange)
        pex = self._pex

        async def _handle_pex(stream: object) -> None:
            try:
                data = await stream.read(4096)  # type: ignore[attr-defined]
                msg_type, payload = decode_message(data)
                if msg_type != MessageType.PEX_REQUEST:
                    return

                remote_id = str(
                    payload.get("peer_id", "") if isinstance(payload, dict) else ""
                )
                if not remote_id:
                    return

                # Track sender's version
                if isinstance(payload, dict):
                    remote_ver = str(payload.get("version", ""))
                    if remote_ver:
                        self._peer_version_tracker.record(remote_id, remote_ver)

                if pex is not None and not pex.check_rate_limit(remote_id):
                    return

                max_p = PEX_MAX_PEERS
                if isinstance(payload, dict):
                    raw = payload.get("max_peers", PEX_MAX_PEERS)
                    max_p = min(
                        int(raw)
                        if isinstance(raw, (int, float, str))
                        else PEX_MAX_PEERS,
                        PEX_MAX_PEERS,
                    )

                connected = self._get_connected_peer_addrs()
                peers_list = (
                    pex.build_response(connected, max_p) if pex is not None else []
                )
                from infomesh import __version__

                resp = encode_message(
                    MessageType.PEX_RESPONSE,
                    {"peers": peers_list, "version": __version__},
                )
                await stream.write(resp)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("pex_handler_error")
            finally:
                await stream.close()  # type: ignore[attr-defined]

        self._host.set_stream_handler(PROTOCOL_PEX, _handle_pex)  # type: ignore[attr-defined]

        # Search handler — full + search roles
        if (
            role in (NodeRole.FULL, NodeRole.SEARCH)
            and self._local_search_fn is not None
            and self._router is not None
        ):
            search_fn = self._local_search_fn

            async def _handle_search(stream: object) -> None:
                await self._router.handle_search_request(stream, search_fn)  # type: ignore[union-attr]

            self._host.set_stream_handler(PROTOCOL_SEARCH, _handle_search)  # type: ignore[attr-defined]

        # Replication handler — full + search roles
        if (
            role in (NodeRole.FULL, NodeRole.SEARCH)
            and self._store_fn is not None
            and self._replicator is not None
        ):
            store_fn = self._store_fn

            async def _handle_replicate(stream: object) -> None:
                await self._replicator.handle_replicate_request(stream, store_fn)  # type: ignore[union-attr]

            self._host.set_stream_handler(PROTOCOL_REPLICATE, _handle_replicate)  # type: ignore[attr-defined]

        # Index submit handler — search role only (receives from DMZ crawlers)
        if role in (NodeRole.SEARCH,) and self._index_submit_receiver is not None:
            receiver = self._index_submit_receiver

            async def _handle_index_submit(stream: object) -> None:
                try:
                    chunks: list[bytes] = []
                    total = 0
                    max_size = 1024 * 1024  # 1 MB
                    while total < max_size:
                        chunk = await stream.read(max_size - total)  # type: ignore[attr-defined]
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total += len(chunk)
                    data = b"".join(chunks)
                    msg_type, payload = decode_message(data)
                    if msg_type == MessageType.INDEX_SUBMIT:
                        ack = await receiver.handle_submit(payload)  # type: ignore[attr-defined]
                        ack_msg = receiver.build_ack_message(ack)  # type: ignore[attr-defined]
                        await stream.write(ack_msg)  # type: ignore[attr-defined]
                except Exception:
                    logger.exception("index_submit_handler_error")
                finally:
                    await stream.close()  # type: ignore[attr-defined]

            self._host.set_stream_handler(PROTOCOL_INDEX_SUBMIT, _handle_index_submit)  # type: ignore[attr-defined]

        # Credit sync handler — always registered if manager is available
        if self._credit_sync_manager is not None:
            sync_mgr = self._credit_sync_manager

            async def _handle_credit_sync(stream: object) -> None:
                try:
                    data = await stream.read(4096)  # type: ignore[attr-defined]
                    msg_type, payload = decode_message(data)

                    if msg_type == MessageType.CREDIT_SYNC_ANNOUNCE:
                        # Peer announced their email hash
                        remote_hash = str(
                            payload.get("owner_email_hash", "")
                            if isinstance(payload, dict)
                            else ""
                        )
                        remote_peer = str(
                            payload.get("peer_id", "")
                            if isinstance(payload, dict)
                            else ""
                        )
                        if (
                            remote_hash and remote_hash == sync_mgr.owner_email_hash  # type: ignore[attr-defined]
                        ):
                            # Same owner! Send back our announce + summary
                            sync_mgr.register_same_owner_peer(remote_peer)  # type: ignore[attr-defined]
                            summary = sync_mgr.build_summary()  # type: ignore[attr-defined]
                            resp = encode_message(
                                MessageType.CREDIT_SYNC_EXCHANGE,
                                summary.to_dict(),
                            )
                            await stream.write(resp)  # type: ignore[attr-defined]
                        else:
                            # Different owner, send empty response
                            resp = encode_message(
                                MessageType.CREDIT_SYNC_ANNOUNCE,
                                {
                                    "peer_id": self._peer_id,
                                    "owner_email_hash": "",
                                },
                            )
                            await stream.write(resp)  # type: ignore[attr-defined]

                    elif msg_type == MessageType.CREDIT_SYNC_EXCHANGE:
                        # Peer sent their credit summary
                        from infomesh.credits.sync import (
                            CreditSummary,
                        )

                        if isinstance(payload, dict):
                            summary = CreditSummary.from_dict(payload)
                            sync_mgr.receive_summary(  # type: ignore[attr-defined]
                                summary,
                                verify_signature=False,
                            )
                except Exception:
                    logger.debug("credit_sync_handler_error")
                finally:
                    await stream.close()  # type: ignore[attr-defined]

            self._host.set_stream_handler(PROTOCOL_CREDIT_SYNC, _handle_credit_sync)  # type: ignore[attr-defined]

        logger.info(
            "handlers_registered",
            role=role,
            protocols=self._get_registered_protocols(),
        )

    def _get_registered_protocols(self) -> list[str]:
        """Return list of protocol IDs registered for this node's role."""
        role = self._config.node.role
        protocols = [PROTOCOL_PING, PROTOCOL_PEX]
        if role in (NodeRole.FULL, NodeRole.SEARCH):
            if self._local_search_fn is not None:
                protocols.append(PROTOCOL_SEARCH)
            if self._store_fn is not None:
                protocols.append(PROTOCOL_REPLICATE)
        if role == NodeRole.SEARCH and self._index_submit_receiver is not None:
            protocols.append(PROTOCOL_INDEX_SUBMIT)
        if self._credit_sync_manager is not None:
            protocols.append(PROTOCOL_CREDIT_SYNC)
        return protocols

    async def _bootstrap(self) -> None:
        """Connect to bootstrap nodes from config."""
        import trio as _trio
        from libp2p.peer.peerinfo import info_from_p2p_addr
        from multiaddr import Multiaddr

        bootstrap_addrs = list(self._config.network.bootstrap_nodes)

        # Resolve "default" keyword → load bundled bootstrap nodes
        if "default" in bootstrap_addrs:
            from infomesh.config import _load_default_bootstrap_nodes

            defaults = _load_default_bootstrap_nodes()
            bootstrap_addrs = [a for a in bootstrap_addrs if a != "default"] + defaults

        if not bootstrap_addrs:
            logger.info("no_bootstrap_nodes", note="running in standalone mode")
            self._bootstrap_results = {"configured": 0, "connected": 0, "failed": 0}
            return

        connected = 0
        failed = 0
        failed_addrs: list[str] = []

        for addr_str in bootstrap_addrs:
            try:
                maddr = Multiaddr(addr_str)
                peer_info = info_from_p2p_addr(maddr)
                timed_out = True
                with _trio.move_on_after(10):  # 10s timeout per peer
                    await self._host.connect(peer_info)  # type: ignore[union-attr]
                    logger.info("bootstrap_connected", addr=addr_str)
                    connected += 1
                    timed_out = False
                if timed_out:
                    logger.warning(
                        "bootstrap_timeout",
                        addr=addr_str,
                        timeout_sec=10,
                        hint=(
                            "Check that the bootstrap node is running "
                            "and port 4001 is open (firewall/NSG)"
                        ),
                    )
                    failed += 1
                    failed_addrs.append(addr_str)
            except Exception as exc:
                logger.warning(
                    "bootstrap_failed",
                    addr=addr_str,
                    error=str(exc),
                )
                failed += 1
                failed_addrs.append(addr_str)

        self._bootstrap_results = {
            "configured": len(bootstrap_addrs),
            "connected": connected,
            "failed": failed,
            "failed_addrs": failed_addrs,
        }

        if connected == 0 and failed > 0:
            logger.error(
                "bootstrap_all_failed",
                configured=len(bootstrap_addrs),
                failed=failed,
                hint=(
                    "No bootstrap nodes reachable. Possible causes: "
                    "(1) Bootstrap node not running, "
                    "(2) Firewall/NSG blocking TCP 4001, "
                    "(3) Wrong IP or peer ID in config. "
                    "Check with: nc -zv <ip> 4001"
                ),
            )

    # ─── PoW cache ──────────────────────────────────────────

    @staticmethod
    def _load_cached_pow(
        cache_path: Path,
        pub_key_bytes: bytes,
    ) -> int | None:
        """Load cached PoW nonce if it matches the current public key.

        The cache file stores:
        ``pub_key_hash (32 bytes) + nonce (8 bytes LE) + difficulty (1 byte)``.
        Falls back to 40-byte legacy format (assumes difficulty=20).
        Returns the nonce if valid, None otherwise.
        """
        import struct

        if not cache_path.exists():
            return None
        try:
            data = cache_path.read_bytes()
            if len(data) == 41:
                stored_hash = data[:32]
                nonce = struct.unpack("<Q", data[32:40])[0]
                difficulty = data[40]
            elif len(data) == 40:
                stored_hash = data[:32]
                nonce = struct.unpack("<Q", data[32:])[0]
                difficulty = 20
            else:
                return None
            expected_hash = hashlib.sha256(pub_key_bytes).digest()
            if stored_hash != expected_hash:
                return None
            # Verify the cached nonce is still valid
            pow_hash = compute_pow_hash(pub_key_bytes, nonce)
            from infomesh.p2p.sybil import _count_leading_zero_bits_fast

            if _count_leading_zero_bits_fast(pow_hash) >= difficulty:
                return int(nonce)
            return None
        except Exception:
            return None

    @staticmethod
    def _save_cached_pow(
        cache_path: Path,
        pub_key_bytes: bytes,
        nonce: int,
        difficulty: int = 20,
    ) -> None:
        """Persist PoW nonce to disk for fast restart."""
        import struct

        try:
            key_hash = hashlib.sha256(pub_key_bytes).digest()
            cache_path.write_bytes(
                key_hash + struct.pack("<Q", nonce) + bytes([difficulty])
            )
        except Exception:
            logger.debug("pow_cache_save_failed", path=str(cache_path))

    # ─── Public API (thread-safe) ──────────────────────────

    @property
    def connected_peers(self) -> list[str]:
        """Currently connected peer IDs (property)."""
        return self.get_connected_peers()

    def get_connected_peers(self) -> list[str]:
        """Get list of connected peer IDs."""
        if self._host is None or self._state != NodeState.RUNNING:
            return []
        try:
            return [str(pid) for pid in self._host.get_connected_peers()]  # type: ignore[attr-defined]
        except Exception:
            return []

    def check_subnet(self, ip: str, peer_id: str, bucket_id: int = 0) -> bool:
        """Check and register a peer against the subnet limiter.

        Returns True if the peer was accepted, False if the /24 subnet
        limit has been reached.
        """
        return self._subnet_limiter.add(ip, peer_id, bucket_id)

    # ─── Internal helpers ──────────────────────────────────

    def _load_or_create_libp2p_key(self) -> object:
        """Load or create the libp2p Ed25519 key pair.

        The key is persisted to ``<data_dir>/keys/libp2p_key.bin`` so
        that the node's peer ID stays stable across restarts.

        Returns:
            A libp2p ``Ed25519KeyPair`` (or compatible key pair object).
        """
        from libp2p import (  # type: ignore[attr-defined]
            create_new_ed25519_key_pair,
        )

        keys_dir = self._config.node.data_dir / "keys"
        key_path = keys_dir / "libp2p_key.bin"

        if key_path.exists():
            try:
                raw = key_path.read_bytes()
                from libp2p.crypto.ed25519 import (
                    Ed25519PrivateKey,
                )
                from libp2p.crypto.keys import KeyPair

                priv = Ed25519PrivateKey.from_bytes(raw)
                logger.info("libp2p_key_loaded", path=str(key_path))
                return KeyPair(priv, priv.get_public_key())
            except Exception:
                logger.warning(
                    "libp2p_key_load_failed",
                    path=str(key_path),
                    msg="generating new key pair",
                )

        key_pair = create_new_ed25519_key_pair()

        # Persist for next restart
        try:
            keys_dir.mkdir(parents=True, exist_ok=True)
            raw_priv = key_pair.private_key.to_bytes()
            key_path.write_bytes(raw_priv)
            import os
            import stat

            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
            logger.info("libp2p_key_saved", path=str(key_path))
        except Exception:
            logger.warning(
                "libp2p_key_save_failed",
                path=str(key_path),
                msg="peer ID will change on restart",
            )

        return key_pair

    async def _refresh_routing_table(self) -> None:
        """Periodic routing table refresh — re-bootstraps to discover new peers."""
        try:
            await self._bootstrap()
            logger.debug("routing_table_refreshed")
        except Exception:
            logger.debug("routing_refresh_failed")

    async def _connect_mdns_peers(self) -> None:
        """Connect to peers discovered via mDNS on the local network."""
        if self._mdns is None or self._host is None:
            return

        peers = self._mdns.discovered_peers
        connected = set(self.get_connected_peers())

        for pid, peer in peers.items():
            if pid in connected or pid == self._peer_id:
                continue

            # Subnet check
            if not self._subnet_limiter.can_add(peer.host, bucket_id=0):
                continue

            try:
                from libp2p.peer.peerinfo import info_from_p2p_addr
                from multiaddr import Multiaddr

                addr = Multiaddr(f"/ip4/{peer.host}/tcp/{peer.port}/p2p/{pid}")
                peer_info = info_from_p2p_addr(addr)
                await self._host.connect(peer_info)  # type: ignore[attr-defined]
                self._subnet_limiter.add(peer.host, pid, bucket_id=0)
                if self._url_assigner is not None:
                    self._url_assigner.add_peer(pid)
                logger.info("mdns_peer_connected", peer_id=pid[:16], host=peer.host)
            except Exception:
                logger.debug("mdns_peer_connect_failed", peer_id=pid[:16])

    def _get_connected_peer_addrs(self) -> list[tuple[str, str]]:
        """Return ``(peer_id, multiaddr)`` tuples for connected peers."""
        if self._host is None:
            return []
        result: list[tuple[str, str]] = []
        for pid in self.get_connected_peers():
            try:
                peerstore = self._host.get_peerstore()  # type: ignore[attr-defined]
                addrs = peerstore.addrs(pid)
                if addrs:
                    maddr = f"{addrs[0]}/p2p/{pid}"
                    result.append((str(pid), str(maddr)))
            except Exception:
                pass
        return result

    async def _run_pex_round(self) -> None:
        """Run one PEX round — ask a few peers for their peer lists."""
        if self._pex is None or self._host is None:
            return

        connected = self.get_connected_peers()
        if not connected:
            return

        import random

        import trio as _trio

        # Pick up to PEX_MAX_PEERS_PER_ROUND peers to exchange with
        targets = random.sample(
            connected,
            min(PEX_MAX_PEERS_PER_ROUND, len(connected)),
        )
        known = set(connected) | {self._peer_id}
        total_new = 0

        for target_pid in targets:
            try:
                from libp2p.peer.id import ID as PeerID

                stream = await self._host.new_stream(  # type: ignore[attr-defined]
                    PeerID.from_base58(target_pid),
                    [PROTOCOL_PEX],
                )
                from infomesh import __version__ as _ver

                req = encode_message(
                    MessageType.PEX_REQUEST,
                    {
                        "peer_id": self._peer_id,
                        "max_peers": PEX_MAX_PEERS,
                        "version": _ver,
                    },
                )
                await stream.write(req)

                data = b""
                timed_out = True
                with _trio.move_on_after(5):
                    data = await stream.read(65536)
                    timed_out = False
                await stream.close()

                if timed_out or not data:
                    continue

                msg_type, payload = decode_message(data)
                if msg_type != MessageType.PEX_RESPONSE:
                    continue

                peers_data: list[dict[str, object]] = []
                if isinstance(payload, dict):
                    raw = payload.get("peers", [])
                    if isinstance(raw, list):
                        peers_data = raw
                    # Track responder's version
                    resp_ver = str(payload.get("version", ""))
                    if resp_ver:
                        self._peer_version_tracker.record(target_pid, resp_ver)

                new_peers = self._pex.process_response(
                    target_pid,
                    peers_data,
                    known,
                )

                # Save discovered peers to store + try connecting
                for p in new_peers:
                    if self._peer_store is not None:
                        self._peer_store.upsert(p.peer_id, p.multiaddr)
                    known.add(p.peer_id)
                    total_new += 1

            except Exception:
                logger.debug(
                    "pex_round_peer_failed",
                    target=target_pid[:16],
                )

        if total_new > 0:
            logger.info("pex_round_complete", new_peers=total_new)
        # Clean up stale rate limit entries
        self._pex.cleanup_rate_limits()

    async def _connect_cached_peers(self) -> None:
        """Try connecting to previously known peers from the peer store.

        Called when bootstrap nodes are unreachable, providing an
        alternative path to rejoin the network.
        """
        if self._peer_store is None or self._host is None:
            return

        cached = self._peer_store.load_recent(limit=20)
        if not cached:
            logger.info("peer_store_empty", note="no cached peers available")
            return

        logger.info("peer_store_trying_cached", count=len(cached))

        import trio as _trio
        from libp2p.peer.peerinfo import info_from_p2p_addr
        from multiaddr import Multiaddr

        connected = 0
        for entry in cached:
            if entry.peer_id == self._peer_id:
                continue
            try:
                maddr = Multiaddr(entry.multiaddr)
                peer_info = info_from_p2p_addr(maddr)
                timed_out = True
                with _trio.move_on_after(5):
                    await self._host.connect(peer_info)  # type: ignore[attr-defined]
                    connected += 1
                    timed_out = False
                    self._peer_store.upsert(entry.peer_id, entry.multiaddr)
                    logger.info(
                        "cached_peer_connected",
                        peer_id=entry.peer_id[:16],
                    )
                if timed_out:
                    self._peer_store.record_failure(entry.peer_id)
            except Exception:
                self._peer_store.record_failure(entry.peer_id)
                logger.debug(
                    "cached_peer_failed",
                    peer_id=entry.peer_id[:16],
                )

        logger.info(
            "peer_store_reconnect_result",
            tried=len(cached),
            connected=connected,
        )

    async def _save_connected_peers(self) -> None:
        """Save currently connected peers to the persistent store."""
        if self._peer_store is None or self._host is None:
            return

        try:
            peer_ids = self.get_connected_peers()
            if not peer_ids:
                return

            peers_to_save: list[tuple[str, str]] = []
            for pid in peer_ids:
                # Build multiaddr: /ip4/0.0.0.0/tcp/4001/p2p/<peer_id>
                # We use the peer's network stream addresses when available
                try:
                    peerstore = self._host.get_peerstore()  # type: ignore[attr-defined]
                    peer_addrs = peerstore.addrs(pid)
                    if peer_addrs:
                        maddr = f"{peer_addrs[0]}/p2p/{pid}"
                        peers_to_save.append((str(pid), str(maddr)))
                        continue
                except Exception:
                    pass
                # Fallback: store peer_id only with a placeholder addr
                # — will be skipped on load if addr is unreachable
                peers_to_save.append((str(pid), f"/p2p/{pid}"))

            self._peer_store.save_connected(peers_to_save)
        except Exception:
            logger.debug("peer_store_save_failed")

    async def _publish_pending_revocations(self) -> None:
        """Publish any pending key revocation records to the DHT."""
        if self._dht is None:
            return

        revocation_dir = self._config.node.data_dir / "keys" / "revocations"
        if not revocation_dir.exists():
            return

        import msgpack

        for rev_file in revocation_dir.glob("*.json"):
            try:
                import json

                data = json.loads(rev_file.read_text())
                old_key_hex = data.get("old_public_key", "")
                if old_key_hex:
                    dht_key = f"/infomesh/revoke/{old_key_hex[:32]}"
                    value = msgpack.packb(data, use_bin_type=True)
                    await self._dht.put(dht_key, value)
                    logger.info("revocation_published", key=old_key_hex[:16])
            except Exception:
                logger.debug("revocation_publish_failed", file=str(rev_file))

    async def _announce_credit_sync(self) -> None:
        """Announce owner email hash to all connected peers.

        Called once after bootstrap to discover same-owner nodes.
        """
        if self._credit_sync_manager is None or self._host is None:
            return

        mgr = self._credit_sync_manager
        if not mgr.has_identity:  # type: ignore[attr-defined]
            return

        connected = self.get_connected_peers()
        if not connected:
            return

        import trio as _trio
        from libp2p.peer.id import ID as PeerID

        for target_pid in connected:
            try:
                stream = await self._host.new_stream(  # type: ignore[attr-defined]
                    PeerID.from_base58(target_pid),
                    [PROTOCOL_CREDIT_SYNC],
                )
                announce = encode_message(
                    MessageType.CREDIT_SYNC_ANNOUNCE,
                    {
                        "peer_id": self._peer_id,
                        "owner_email_hash": (
                            mgr.owner_email_hash  # type: ignore[attr-defined]
                        ),
                    },
                )
                await stream.write(announce)

                # Read response (may be a summary exchange or empty)
                data = b""
                timed_out = True
                with _trio.move_on_after(5):
                    data = await stream.read(65536)
                    timed_out = False
                await stream.close()

                if timed_out or not data:
                    continue

                msg_type, payload = decode_message(data)
                if msg_type == MessageType.CREDIT_SYNC_EXCHANGE and isinstance(
                    payload, dict
                ):
                    from infomesh.credits.sync import CreditSummary

                    summary = CreditSummary.from_dict(payload)
                    mgr.receive_summary(  # type: ignore[attr-defined]
                        summary,
                        verify_signature=False,
                    )
                    logger.info(
                        "credit_sync_peer_matched",
                        peer_id=target_pid[:16],
                    )

            except Exception:
                logger.debug(
                    "credit_sync_announce_failed",
                    target=target_pid[:16],
                )

    async def _run_credit_sync_round(self) -> None:
        """Periodic credit sync: re-exchange summaries with same-owner peers."""
        if self._credit_sync_manager is None or self._host is None:
            return

        mgr = self._credit_sync_manager
        if not mgr.has_identity:  # type: ignore[attr-defined]
            return

        # Purge stale summaries
        mgr.purge_stale()  # type: ignore[attr-defined]

        same_owner = mgr.get_same_owner_peers()  # type: ignore[attr-defined]
        if not same_owner:
            return

        import trio as _trio
        from libp2p.peer.id import ID as PeerID

        for target_pid in same_owner:
            if not mgr.needs_sync(target_pid):  # type: ignore[attr-defined]
                continue
            try:
                stream = await self._host.new_stream(  # type: ignore[attr-defined]
                    PeerID.from_base58(target_pid),
                    [PROTOCOL_CREDIT_SYNC],
                )
                # Send our summary
                summary = mgr.build_summary()  # type: ignore[attr-defined]
                msg = encode_message(
                    MessageType.CREDIT_SYNC_EXCHANGE,
                    summary.to_dict(),
                )
                await stream.write(msg)

                # Read peer's updated summary
                data = b""
                timed_out = True
                with _trio.move_on_after(5):
                    data = await stream.read(65536)
                    timed_out = False
                await stream.close()

                if timed_out or not data:
                    continue

                msg_type, payload = decode_message(data)
                if msg_type == MessageType.CREDIT_SYNC_EXCHANGE and isinstance(
                    payload, dict
                ):
                    from infomesh.credits.sync import CreditSummary

                    peer_summary = CreditSummary.from_dict(payload)
                    mgr.receive_summary(  # type: ignore[attr-defined]
                        peer_summary,
                        verify_signature=False,
                    )

            except Exception:
                logger.debug(
                    "credit_sync_round_failed",
                    target=target_pid[:16],
                )
