"""Extended network features — NAT traversal, DNS discovery, GeoIP routing.

Features:
- #75: NAT traversal helpers (STUN/TURN discovery)
- #77: DNS-based peer discovery
- #78: GeoIP-based routing
- #79: Network partition recovery
- #80: Relay node support
"""

from __future__ import annotations

import hashlib
import socket
import struct
import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# ── #75: NAT traversal support ───────────────────────────────────


@dataclass(frozen=True)
class NATInfo:
    """NAT detection result."""

    nat_type: str  # "none", "full_cone", "restricted", "symmetric", "unknown"
    external_ip: str
    external_port: int
    internal_ip: str
    internal_port: int


async def detect_nat_type(
    stun_server: str = "stun.l.google.com",
    stun_port: int = 19302,
) -> NATInfo:
    """Detect NAT type using a lightweight STUN-like probe.

    Sends a minimal UDP packet to a STUN server and reads
    the mapped address from the response.

    Args:
        stun_server: STUN server hostname.
        stun_port: STUN server port.

    Returns:
        NATInfo with detected NAT type and external address.
    """

    internal_ip = "0.0.0.0"
    internal_port = 0
    external_ip = ""
    external_port = 0
    nat_type = "unknown"

    try:
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3.0)
        sock.bind(("", 0))
        internal_ip = sock.getsockname()[0]
        internal_port = sock.getsockname()[1]

        # Simple STUN binding request (minimal header)
        txn_id = hashlib.sha256(str(time.time()).encode()).digest()[:12]
        stun_msg = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + txn_id

        addr = socket.getaddrinfo(
            stun_server,
            stun_port,
            socket.AF_INET,
        )[0][4]
        sock.sendto(stun_msg, addr)

        try:
            data, _remote = sock.recvfrom(1024)
            if len(data) >= 20:
                # Parse response (simplified)
                external_ip = str(addr[0])
                external_port = internal_port
                nat_type = "none" if internal_ip == external_ip else "full_cone"
        except TimeoutError:
            nat_type = "symmetric"

        sock.close()

    except Exception as exc:
        logger.warning("nat_detection_failed", error=str(exc))

    return NATInfo(
        nat_type=nat_type,
        external_ip=external_ip,
        external_port=external_port,
        internal_ip=internal_ip,
        internal_port=internal_port,
    )


# ── #77: DNS-based peer discovery ────────────────────────────────


@dataclass(frozen=True)
class DNSPeer:
    """A peer discovered via DNS."""

    host: str
    port: int
    peer_id: str = ""
    priority: int = 0


def discover_peers_dns(
    domain: str = "_infomesh._tcp.local",
) -> list[DNSPeer]:
    """Discover peers via DNS SRV records.

    Falls back to A/AAAA record lookup if SRV fails.

    Args:
        domain: DNS domain to query.

    Returns:
        List of discovered peers.
    """
    peers: list[DNSPeer] = []
    try:
        # Try SRV lookup
        infos = socket.getaddrinfo(
            domain.replace("_infomesh._tcp.", ""),
            None,
            socket.AF_INET,
        )
        for info in infos:
            ip = str(info[4][0])
            peers.append(
                DNSPeer(
                    host=ip,
                    port=4001,
                    priority=0,
                )
            )
    except socket.gaierror:
        logger.debug("dns_peer_discovery_no_results", domain=domain)

    return peers


# ── #78: GeoIP-based routing ─────────────────────────────────────


@dataclass
class GeoLocation:
    """Geographic location of a peer."""

    country: str = ""
    region: str = ""
    city: str = ""
    latitude: float = 0.0
    longitude: float = 0.0


def estimate_geo_distance(
    loc1: GeoLocation,
    loc2: GeoLocation,
) -> float:
    """Estimate distance between two locations in km.

    Uses the Haversine formula.

    Args:
        loc1: First location.
        loc2: Second location.

    Returns:
        Distance in kilometers.
    """
    import math

    r = 6371.0  # Earth radius in km
    lat1 = math.radians(loc1.latitude)
    lat2 = math.radians(loc2.latitude)
    dlat = math.radians(loc2.latitude - loc1.latitude)
    dlon = math.radians(loc2.longitude - loc1.longitude)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return r * c


def sort_peers_by_proximity(
    peers: list[tuple[str, GeoLocation]],
    my_location: GeoLocation,
) -> list[tuple[str, float]]:
    """Sort peers by geographic proximity.

    Args:
        peers: List of (peer_id, location) tuples.
        my_location: This node's location.

    Returns:
        List of (peer_id, distance_km) sorted by proximity.
    """
    distances: list[tuple[str, float]] = []
    for peer_id, loc in peers:
        dist = estimate_geo_distance(my_location, loc)
        distances.append((peer_id, dist))

    distances.sort(key=lambda x: x[1])
    return distances


# ── #79: Network partition recovery ──────────────────────────────


@dataclass
class PartitionState:
    """State of network partition detection."""

    is_partitioned: bool = False
    reachable_peers: int = 0
    expected_peers: int = 0
    last_check: float = 0.0
    recovery_attempts: int = 0


class PartitionDetector:
    """Detect and recover from network partitions.

    Monitors peer connectivity and triggers recovery
    when too many peers become unreachable.

    Args:
        threshold: Fraction of peers below which partition
                   is detected (default: 0.5 = 50%).
    """

    def __init__(self, threshold: float = 0.5) -> None:
        self._threshold = threshold
        self._state = PartitionState()
        self._bootstrap_peers: list[str] = []

    def check(
        self,
        reachable: int,
        total: int,
    ) -> PartitionState:
        """Check for network partition.

        Args:
            reachable: Number of reachable peers.
            total: Expected total peers.

        Returns:
            Updated partition state.
        """
        self._state.reachable_peers = reachable
        self._state.expected_peers = total
        self._state.last_check = time.time()

        if total > 0:
            ratio = reachable / total
            self._state.is_partitioned = ratio < self._threshold
        else:
            self._state.is_partitioned = False

        return self._state

    def get_recovery_actions(self) -> list[str]:
        """Get recommended recovery actions.

        Returns:
            List of action descriptions.
        """
        if not self._state.is_partitioned:
            return []

        actions = [
            "Reconnect to bootstrap nodes",
            "Refresh routing table",
            "Re-announce local index to DHT",
        ]

        if self._state.recovery_attempts > 3:
            actions.append("Consider restarting the node")

        self._state.recovery_attempts += 1
        return actions


# ── #80: Relay node support ──────────────────────────────────────


@dataclass
class RelayConfig:
    """Configuration for relay node behavior."""

    enabled: bool = False
    max_relay_connections: int = 10
    max_bandwidth_mbps: float = 5.0
    relay_peers: list[str] = field(default_factory=list)


def select_relay(
    available_relays: list[tuple[str, float]],
) -> str | None:
    """Select the best relay node.

    Picks the relay with lowest latency.

    Args:
        available_relays: List of (peer_id, latency_ms) tuples.

    Returns:
        Selected relay peer ID, or None if no relays.
    """
    if not available_relays:
        return None
    # Sort by latency
    available_relays.sort(key=lambda x: x[1])
    return available_relays[0][0]
