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
    node.connected_peers  # Currently connected peers
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from infomesh.config import Config, NodeRole
from infomesh.crawler.url_assigner import UrlAssigner
from infomesh.p2p.dht import InfoMeshDHT
from infomesh.p2p.mdns import MDNSDiscovery
from infomesh.p2p.protocol import (
    PROTOCOL_INDEX_SUBMIT,
    PROTOCOL_PING,
    PROTOCOL_REPLICATE,
    PROTOCOL_SEARCH,
    MessageType,
    encode_message,
)
from infomesh.p2p.replication import Replicator
from infomesh.p2p.routing import QueryRouter
from infomesh.p2p.sybil import SubnetLimiter, generate_pow
from infomesh.p2p.throttle import BandwidthThrottle

logger = structlog.get_logger()


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
    ) -> None:
        self._config = config
        self._local_search_fn = local_search_fn
        self._store_fn = store_fn
        self._index_submit_receiver = index_submit_receiver

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
        self._throttle = BandwidthThrottle(
            upload_mbps=config.network.upload_limit_mbps,
            download_mbps=config.network.download_limit_mbps,
        )
        self._pow_nonce: int | None = None
        self._url_assigner: UrlAssigner | None = None

        # Background thread for trio
        self._trio_thread: threading.Thread | None = None
        self._trio_cancel_scope: object | None = None
        self._started_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def state(self) -> NodeState:
        """Current node state."""
        return self._state

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
            peers = len(self.get_connected_peers()) if self._host else 0
            addrs: list[str] = []
            if self._host and self._state == NodeState.RUNNING:
                import contextlib

                with contextlib.suppress(Exception):
                    addrs = [
                        str(a)
                        for a in self._host.get_addrs()  # type: ignore[attr-defined]
                    ]
            data = {
                "state": state or str(self._state),
                "peer_id": self._peer_id,
                "peers": peers,
                "listen_addrs": addrs,
                "timestamp": time.time(),
                "error": error,
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

    # ─── Lifecycle ─────────────────────────────────────────

    def start(self, *, blocking: bool = False) -> None:
        """Start the P2P node.

        Launches the trio event loop in a background thread (unless
        ``blocking=True``).  The trio loop initializes the libp2p host,
        bootstraps the DHT, and registers protocol handlers.

        Args:
            blocking: If True, run in the current thread (blocks).
        """
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
            # Wait for node to be ready
            self._started_event.wait(timeout=30)
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
        from libp2p import (  # type: ignore[attr-defined]
            create_new_ed25519_key_pair,
            new_host,
        )
        from libp2p.kad_dht import KadDHT
        from libp2p.kad_dht.kad_dht import DHTMode
        from libp2p.records.validator import NamespacedValidator, Validator
        from libp2p.tools.async_service.trio_service import background_trio_service
        from multiaddr import Multiaddr

        # Create Ed25519 key pair for libp2p
        key_pair = create_new_ed25519_key_pair()

        # ── Sybil PoW: generate proof-of-work for node identity ──
        pub_key_bytes = key_pair.public_key.to_bytes()
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

        # Create host
        listen_addr = Multiaddr(
            f"/ip4/{self._config.node.listen_address}/tcp/{self._config.node.listen_port}"
        )
        self._host = new_host(key_pair=key_pair)

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
                # Initialize subsystems
                self._dht = InfoMeshDHT(kad_dht, self._peer_id)
                self._router = QueryRouter(self._dht, self._host, self._peer_id)
                self._replicator = Replicator(
                    self._host,
                    self._dht,
                    self._peer_id,
                    replication_factor=self._config.network.replication_factor,
                )
                self._url_assigner = UrlAssigner(self._peer_id)

                # Register protocol handlers
                self._register_handlers()

                # Mark node as RUNNING before bootstrap — bootstrap is
                # best-effort and must not block startup.  The node is
                # fully operational once handlers are registered.
                self._start_time = time.time()
                self._state = NodeState.RUNNING
                self._started_event.set()
                self._write_status_file()

                # Bootstrap — connect to known peers (non-blocking)
                await self._bootstrap()

                # ── mDNS: start LAN peer discovery ──
                self._mdns = MDNSDiscovery(
                    peer_id=self._peer_id,
                    port=self._config.node.listen_port,
                )
                self._mdns.start()
                logger.info("mdns_integration_started")

                # ── Publish pending key revocations to DHT ──
                await self._publish_pending_revocations()

                # Update status after bootstrap (peer count may have changed)
                self._write_status_file()

                # ── Main loop with periodic routing refresh ──
                refresh_interval = 300  # refresh routing every 5 min
                status_interval = 10  # update status file every 10s
                last_refresh = time.time()
                last_status = time.time()

                while not self._stop_event.is_set():
                    await trio.sleep(1)

                    # Periodic routing table refresh
                    now = time.time()
                    if now - last_refresh >= refresh_interval:
                        last_refresh = now
                        await self._refresh_routing_table()

                    # Update status file periodically
                    if now - last_status >= status_interval:
                        last_status = now
                        self._write_status_file()

                    # Integrate mDNS-discovered peers
                    await self._connect_mdns_peers()

                # Cleanup mDNS
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
                _ = await stream.read(1024)  # type: ignore[attr-defined]
                pong = encode_message(MessageType.PONG, {"peer_id": self._peer_id})
                await stream.write(pong)  # type: ignore[attr-defined]
            except Exception:
                pass
            finally:
                await stream.close()  # type: ignore[attr-defined]

        self._host.set_stream_handler(PROTOCOL_PING, _handle_ping)  # type: ignore[attr-defined]

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
                    data = await stream.read(1024 * 1024)  # type: ignore[attr-defined]  # max 1MB
                    from infomesh.p2p.protocol import decode_message

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

        logger.info(
            "handlers_registered",
            role=role,
            protocols=self._get_registered_protocols(),
        )

    def _get_registered_protocols(self) -> list[str]:
        """Return list of protocol IDs registered for this node's role."""
        role = self._config.node.role
        protocols = [PROTOCOL_PING]
        if role in (NodeRole.FULL, NodeRole.SEARCH):
            if self._local_search_fn is not None:
                protocols.append(PROTOCOL_SEARCH)
            if self._store_fn is not None:
                protocols.append(PROTOCOL_REPLICATE)
        if role == NodeRole.SEARCH and self._index_submit_receiver is not None:
            protocols.append(PROTOCOL_INDEX_SUBMIT)
        return protocols

    async def _bootstrap(self) -> None:
        """Connect to bootstrap nodes from config."""
        import trio as _trio
        from libp2p.peer.peerinfo import info_from_p2p_addr
        from multiaddr import Multiaddr

        bootstrap_addrs = self._config.network.bootstrap_nodes

        if not bootstrap_addrs:
            logger.info("no_bootstrap_nodes", note="running in standalone mode")
            return

        for addr_str in bootstrap_addrs:
            try:
                maddr = Multiaddr(addr_str)
                peer_info = info_from_p2p_addr(maddr)
                with _trio.move_on_after(10):  # 10s timeout per peer
                    await self._host.connect(peer_info)  # type: ignore[union-attr]
                    logger.info("bootstrap_connected", addr=addr_str)
                    continue
                # If we get here, the connect timed out
                logger.warning("bootstrap_timeout", addr=addr_str, timeout_sec=10)
            except Exception:
                logger.warning("bootstrap_failed", addr=addr_str)

    # ─── Public API (thread-safe) ──────────────────────────

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
