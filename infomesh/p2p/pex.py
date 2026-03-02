"""Peer Exchange (PEX) — gossip-based peer discovery protocol.

Allows nodes to share their known peer addresses with each other,
enabling network resilience when bootstrap servers are unavailable.

Protocol flow::

    Node A ──► Node B: PEX_REQUEST  {"max_peers": 10}
    Node B ──► Node A: PEX_RESPONSE {"peers": [{"peer_id": "...", "multiaddr": "..."}]}

Security considerations:

* **Rate limiting**: Each peer can request PEX at most once per
  ``PEX_MIN_INTERVAL`` seconds to prevent abuse.
* **Max peers per response**: Responses are capped at ``PEX_MAX_PEERS``
  to limit bandwidth.
* **No self-advertisement**: A node never includes itself in PEX responses.
* **Subnet limiting**: Received peers are checked against the subnet
  limiter before connecting.
* **Validation**: Only well-formed multiaddrs with ``/p2p/`` component
  are accepted.

Usage::

    pex = PeerExchange(peer_id="12D3KooW...", peer_store=store)
    # Build response for an incoming PEX request
    response = pex.build_response(connected_peers)
    # Process received peers from a PEX response
    new_peers = pex.process_response(sender_id, peers_data)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

PEX_MAX_PEERS = 10  # Max peers to share per exchange
PEX_MIN_INTERVAL = 60  # Min seconds between PEX requests from same peer
PEX_ROUND_INTERVAL = 300  # Seconds between active PEX rounds (5 min)
PEX_MAX_PEERS_PER_ROUND = 3  # Max peers to PEX with per round


@dataclass(frozen=True)
class PEXPeerInfo:
    """A peer entry shared via PEX."""

    peer_id: str
    multiaddr: str


class PeerExchange:
    """Manages Peer Exchange (PEX) protocol logic.

    Handles building PEX responses, processing received peer lists,
    and rate-limiting to prevent abuse.
    """

    def __init__(self, peer_id: str) -> None:
        self._peer_id = peer_id
        self._last_request: dict[str, float] = {}

    def check_rate_limit(self, requester_id: str) -> bool:
        """Check if a PEX request from this peer is allowed.

        Returns:
            ``True`` if the request is within rate limits.
        """
        now = time.time()
        last = self._last_request.get(requester_id, 0.0)
        if now - last < PEX_MIN_INTERVAL:
            logger.debug(
                "pex_rate_limited",
                requester=requester_id[:16],
                wait=round(PEX_MIN_INTERVAL - (now - last)),
            )
            return False
        self._last_request[requester_id] = now
        return True

    def build_response(
        self,
        connected_peers: list[tuple[str, str]],
        max_peers: int = PEX_MAX_PEERS,
    ) -> list[dict[str, str]]:
        """Build a PEX response from currently connected peers.

        Args:
            connected_peers: List of ``(peer_id, multiaddr)`` tuples.
            max_peers: Max peers to include in the response.

        Returns:
            List of peer dicts ``{"peer_id": ..., "multiaddr": ...}``.
        """
        result: list[dict[str, str]] = []
        for pid, maddr in connected_peers:
            if pid == self._peer_id:
                continue
            if not _is_valid_multiaddr(maddr):
                continue
            result.append({"peer_id": pid, "multiaddr": maddr})
            if len(result) >= max_peers:
                break
        return result

    def process_response(
        self,
        sender_id: str,
        peers_data: list[dict[str, object]],
        known_peers: set[str] | None = None,
    ) -> list[PEXPeerInfo]:
        """Process a PEX response and extract new peers to try.

        Args:
            sender_id: The peer who sent the response.
            peers_data: Raw peer list from the PEX response.
            known_peers: Already-known peer IDs to skip.

        Returns:
            List of new :class:`PEXPeerInfo` entries to connect to.
        """
        if known_peers is None:
            known_peers = set()

        new_peers: list[PEXPeerInfo] = []
        for entry in peers_data[:PEX_MAX_PEERS]:
            pid = str(entry.get("peer_id", ""))
            maddr = str(entry.get("multiaddr", ""))

            if not pid or not maddr:
                continue
            if pid == self._peer_id or pid == sender_id:
                continue
            if pid in known_peers:
                continue
            if not _is_valid_multiaddr(maddr):
                continue

            new_peers.append(PEXPeerInfo(peer_id=pid, multiaddr=maddr))

        logger.info(
            "pex_processed",
            sender=sender_id[:16],
            received=len(peers_data),
            new=len(new_peers),
        )
        return new_peers

    def cleanup_rate_limits(self) -> None:
        """Remove stale rate-limit entries (older than 10 min)."""
        cutoff = time.time() - PEX_MIN_INTERVAL * 10
        stale = [pid for pid, ts in self._last_request.items() if ts < cutoff]
        for pid in stale:
            del self._last_request[pid]


def _is_valid_multiaddr(maddr: str) -> bool:
    """Check if a multiaddr string looks valid for PEX.

    Must contain ``/p2p/`` and start with ``/ip4/`` or ``/ip6/``.
    """
    return (
        isinstance(maddr, str)
        and "/p2p/" in maddr
        and (maddr.startswith("/ip4/") or maddr.startswith("/ip6/"))
    )
