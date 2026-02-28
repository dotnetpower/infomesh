"""mDNS local peer discovery — find InfoMesh nodes on the LAN.

Uses standard multicast DNS (mDNS) with service type ``_infomesh._tcp.local.``
to advertise and discover peers without a bootstrap server.

LAN peers can exchange indexes directly using low-latency local networking,
making initial synchronization much faster than going through the internet.

Usage::

    discovery = MDNSDiscovery(peer_id="abc123", port=4001)
    discovery.start()       # advertise + listen
    peers = discovery.discovered_peers   # dict[peer_id, (host, port)]
    discovery.stop()
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE_TYPE = "_infomesh._tcp.local."
ANNOUNCE_INTERVAL = 30.0  # seconds between announcements
PEER_TTL = 120.0  # seconds before a peer is considered stale

# Custom lightweight mDNS packet format for InfoMesh
# Instead of full DNS record parsing, we use a simple JSON-like announce
# over multicast UDP.  This is pragmatic and avoids a zeroconf dependency.
MAGIC = b"INFOMESH"  # 8-byte magic header


@dataclass
class DiscoveredPeer:
    """A peer discovered via mDNS."""

    peer_id: str
    host: str
    port: int
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def is_stale(self) -> bool:
        return time.monotonic() - self.last_seen > PEER_TTL


class MDNSDiscovery:
    """Lightweight mDNS peer discovery for InfoMesh.

    Announces this node's presence on the LAN and listens for
    other InfoMesh nodes.  Uses a simple UDP multicast protocol
    with an 8-byte magic header followed by msgpack payload.

    This avoids requiring the ``zeroconf`` dependency while still
    enabling automatic LAN discovery.
    """

    def __init__(self, peer_id: str, port: int = 4001) -> None:
        self._peer_id = peer_id
        self._port = port
        self._peers: dict[str, DiscoveredPeer] = {}
        self._lock = threading.Lock()
        self._running = False
        self._announce_thread: threading.Thread | None = None
        self._listen_thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    @property
    def discovered_peers(self) -> dict[str, DiscoveredPeer]:
        """Return a snapshot of currently known peers (excluding stale)."""
        with self._lock:
            now = time.monotonic()
            # Prune stale
            stale = [
                pid for pid, p in self._peers.items() if now - p.last_seen > PEER_TTL
            ]
            for pid in stale:
                del self._peers[pid]
            return dict(self._peers)

    @property
    def peer_count(self) -> int:
        """Number of currently known live peers."""
        return len(self.discovered_peers)

    def start(self) -> None:
        """Start announcing and listening for peers."""
        if self._running:
            return

        self._running = True
        self._sock = self._create_multicast_socket()

        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="mdns-listen",
        )
        self._announce_thread = threading.Thread(
            target=self._announce_loop,
            daemon=True,
            name="mdns-announce",
        )

        self._listen_thread.start()
        self._announce_thread.start()
        logger.info(
            "mdns_started",
            peer_id=self._peer_id[:16],
            port=self._port,
        )

    def stop(self) -> None:
        """Stop mDNS discovery."""
        self._running = False
        if self._sock:
            with contextlib.suppress(OSError):
                self._sock.close()
        self._sock = None
        # Join background threads to ensure clean shutdown
        for t in (self._listen_thread, self._announce_thread):
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
        logger.info("mdns_stopped")

    def _create_multicast_socket(self) -> socket.socket:
        """Create a UDP socket joined to the mDNS multicast group."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Allow multiple processes on same host (dev mode)
        if hasattr(socket, "SO_REUSEPORT"):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind(("", MDNS_PORT))

        # Join multicast group on all interfaces
        group = socket.inet_aton(MDNS_GROUP)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        # Don't loop back our own packets
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)

        # Set TTL to 1 (LAN only)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

        # Set timeout for recv so stop() works
        sock.settimeout(2.0)

        return sock

    def _build_announce(self) -> bytes:
        """Build an announcement packet."""
        import msgpack

        payload = msgpack.packb(
            {
                "peer_id": self._peer_id,
                "port": self._port,
                "ts": time.time(),
            },
            use_bin_type=True,
        )
        return bytes(MAGIC + payload)

    def _parse_announce(
        self, data: bytes, addr: tuple[str, int]
    ) -> DiscoveredPeer | None:
        """Parse an announcement packet.

        Returns:
            A ``DiscoveredPeer`` or ``None`` if invalid/self.
        """
        if len(data) < len(MAGIC) + 3 or data[: len(MAGIC)] != MAGIC:
            return None

        import msgpack

        try:
            payload = msgpack.unpackb(data[len(MAGIC) :], raw=False)
        except Exception:
            return None

        pid = payload.get("peer_id", "")
        port = payload.get("port", 0)

        if not pid or not port:
            return None

        # Ignore our own announcements
        if pid == self._peer_id:
            return None

        return DiscoveredPeer(
            peer_id=pid,
            host=addr[0],
            port=port,
        )

    def _announce_loop(self) -> None:
        """Periodically send multicast announcements."""
        while self._running:
            try:
                packet = self._build_announce()
                if self._sock:
                    self._sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            except OSError:
                if not self._running:
                    break
            # Sleep in small increments so stop() is responsive
            for _ in range(int(ANNOUNCE_INTERVAL)):
                if not self._running:
                    break
                time.sleep(1.0)

    def _listen_loop(self) -> None:
        """Listen for multicast announcements from other peers."""
        while self._running:
            try:
                if not self._sock:
                    break
                data, addr = self._sock.recvfrom(1024)
                peer = self._parse_announce(data, addr)
                if peer:
                    with self._lock:
                        existing = self._peers.get(peer.peer_id)
                        if existing is None:
                            logger.info(
                                "mdns_peer_discovered",
                                peer_id=peer.peer_id[:16],
                                host=peer.host,
                                port=peer.port,
                            )
                        self._peers[peer.peer_id] = peer
            except TimeoutError:
                continue
            except OSError:
                if not self._running:
                    break
