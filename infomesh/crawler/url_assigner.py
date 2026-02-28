"""DHT-based URL auto-assignment — maps URLs to responsible nodes.

Uses the Kademlia XOR distance metric to determine which peer should
"own" (crawl) a given URL:

    hash(URL) XOR peer_id → distance

The node with the smallest XOR distance to hash(URL) is the natural
owner of that URL.

This module bridges the gap between the local ``Scheduler`` and the
distributed DHT: instead of every node blindly crawling any URL, nodes
check whether they are the closest known peer for a URL before crawling.
"""

from __future__ import annotations

import structlog

from infomesh.hashing import content_hash
from infomesh.p2p.protocol import CrawlAssignment

logger = structlog.get_logger()


def _xor_distance(hex_a: str, hex_b: str) -> int:
    """Compute XOR distance between two hex-encoded hashes.

    Both hashes are interpreted as big-endian integers and XOR'd.
    Shorter hashes are zero-padded on the left.

    Args:
        hex_a: First hex digest.
        hex_b: Second hex digest.

    Returns:
        Integer XOR distance.
    """
    max_len = max(len(hex_a), len(hex_b))
    int_a = int(hex_a.ljust(max_len, "0"), 16)
    int_b = int(hex_b.ljust(max_len, "0"), 16)
    return int_a ^ int_b


class UrlAssigner:
    """Assigns URLs to the closest known peer using Kademlia XOR distance.

    Maintains a set of known peer IDs (hex-encoded) and determines
    ownership of URLs by computing ``hash(url) XOR hash(peer_id)``
    for each known peer, selecting the closest.

    Args:
        local_peer_id: This node's peer ID string.
    """

    def __init__(self, local_peer_id: str) -> None:
        self._local_peer_id = local_peer_id
        self._local_hash = content_hash(local_peer_id)
        # known peers: peer_id -> peer_id_hash
        self._peers: dict[str, str] = {
            local_peer_id: self._local_hash,
        }

    def add_peer(self, peer_id: str) -> None:
        """Register a known peer for URL assignment.

        Args:
            peer_id: The peer's ID string.
        """
        if peer_id not in self._peers:
            self._peers[peer_id] = content_hash(peer_id)

    def remove_peer(self, peer_id: str) -> None:
        """Remove a disconnected peer from consideration.

        Args:
            peer_id: The peer's ID string (never removes local).
        """
        if peer_id != self._local_peer_id:
            self._peers.pop(peer_id, None)

    @property
    def known_peers(self) -> int:
        """Number of known peers (including local)."""
        return len(self._peers)

    def closest_peer(self, url: str) -> str:
        """Find the closest peer to a URL by XOR distance.

        Args:
            url: The URL to assign.

        Returns:
            Peer ID of the closest node.
        """
        url_hash = content_hash(url)
        best_peer = self._local_peer_id
        best_dist = _xor_distance(url_hash, self._local_hash)

        for peer_id, peer_hash in self._peers.items():
            dist = _xor_distance(url_hash, peer_hash)
            if dist < best_dist:
                best_dist = dist
                best_peer = peer_id

        return best_peer

    def is_local_owner(self, url: str) -> bool:
        """Check if this node is the closest peer for a URL.

        When a URL is submitted for crawling, this method determines
        whether the local node should crawl it or delegate to a closer
        peer.

        Args:
            url: The URL to check ownership for.

        Returns:
            True if the local node is closest (or the only known peer).
        """
        return self.closest_peer(url) == self._local_peer_id

    def assign(self, url: str, *, depth: int = 0) -> CrawlAssignment:
        """Create a CrawlAssignment for a URL.

        Args:
            url: URL to assign.
            depth: Crawl depth.

        Returns:
            CrawlAssignment with the closest peer as the assigner.
        """
        owner = self.closest_peer(url)
        return CrawlAssignment(
            url=url,
            depth=depth,
            assigner_peer_id=owner,
        )

    def filter_local_urls(self, urls: list[str]) -> list[str]:
        """Filter a list of URLs to only those owned by this node.

        Useful for batch URL processing: given a list of discovered
        URLs, return only the ones this node should crawl.

        Args:
            urls: List of candidate URLs.

        Returns:
            Subset of URLs owned by this node.
        """
        return [url for url in urls if self.is_local_owner(url)]
