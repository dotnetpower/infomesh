"""Distributed search query routing.

Routes keyword hashes via DHT to responsible nodes, collects results,
and merges them with local search results.

The routing flow:
  1. Extract keywords from query.
  2. For each keyword, compute ``keyword_to_dht_key(kw)`` → find the
     responsible peer(s) in the DHT.
  3. Send ``SEARCH_REQUEST`` to those peers via libp2p streams.
  4. Collect ``SEARCH_RESPONSE`` messages (with timeout).
  5. Merge remote results with local results using RRF.

**NOTE**: This module uses trio async.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import msgpack
import structlog

from infomesh.p2p.peer_profile import PeerProfileTracker
from infomesh.p2p.protocol import (
    PROTOCOL_SEARCH,
    MessageType,
    SearchRequest,
    SearchResponse,
    dataclass_to_payload,
    encode_message,
)

logger = structlog.get_logger()

# Timeout for remote search responses (ms)
SEARCH_TIMEOUT_MS = 2000

# Maximum peers to fan-out a query to
MAX_FANOUT = 5

# Maximum results from a single peer
MAX_RESULTS_PER_PEER = 20

# Hedged request: if first peer doesn't respond within this fraction
# of the adaptive timeout, also send to a backup peer.
HEDGE_TIMEOUT_FRACTION = 0.5


@dataclass
class RoutingStats:
    """Statistics for query routing."""

    queries_routed: int = 0
    queries_local_only: int = 0
    peers_contacted: int = 0
    peers_responded: int = 0
    peers_timed_out: int = 0
    avg_response_ms: float = 0.0
    _response_times: list[float] = field(default_factory=list, repr=False)

    def record_response(self, elapsed_ms: float) -> None:
        """Record a peer response time."""
        self._response_times.append(elapsed_ms)
        self.peers_responded += 1
        self.avg_response_ms = sum(self._response_times) / len(self._response_times)


@dataclass(frozen=True)
class RemoteSearchResult:
    """Search result received from a remote peer."""

    url: str
    title: str
    snippet: str
    score: float
    peer_id: str
    doc_id: int = 0


class QueryRouter:
    """Routes search queries to relevant peers via DHT.

    Uses the DHT to find which peers have indexed documents matching
    the query keywords, then fans out search requests via libp2p streams.

    Args:
        dht: InfoMeshDHT instance.
        host: libp2p host for opening streams.
        local_peer_id: This node's peer ID.
        timeout_ms: Per-peer response timeout.
        max_fanout: Maximum peers to query.
    """

    def __init__(
        self,
        dht: object,
        host: object,
        local_peer_id: str,
        *,
        timeout_ms: int = SEARCH_TIMEOUT_MS,
        max_fanout: int = MAX_FANOUT,
        profile_tracker: PeerProfileTracker | None = None,
    ) -> None:
        self._dht = dht
        self._host = host
        self._peer_id = local_peer_id
        self._timeout_ms = timeout_ms
        self._max_fanout = max_fanout
        self._stats = RoutingStats()
        self._profiles = profile_tracker or PeerProfileTracker()

    @property
    def stats(self) -> RoutingStats:
        """Current routing statistics."""
        return self._stats

    @property
    def profile_tracker(self) -> PeerProfileTracker:
        """Peer performance tracker."""
        return self._profiles

    async def route_query(
        self,
        query: str,
        keywords: list[str],
        limit: int = 10,
    ) -> list[RemoteSearchResult]:
        """Route a query to relevant peers and collect results.

        1. For each keyword, query DHT for peer pointers.
        2. Identify unique peers that have relevant documents.
        3. Send SEARCH_REQUEST to top-N peers.
        4. Collect and merge responses.

        Args:
            query: Original search query string.
            keywords: Extracted search keywords.
            limit: Max total results to return.

        Returns:
            List of RemoteSearchResult from peers.
        """
        import trio

        self._stats.queries_routed += 1

        # Step 1: Find candidate peers via DHT
        peer_scores: dict[str, float] = {}
        for kw in keywords:
            pointers = await self._dht.query_keyword(kw)  # type: ignore[attr-defined]
            for ptr in pointers:
                pid = ptr.get("peer_id", "")
                if pid and pid != self._peer_id:
                    peer_scores[pid] = peer_scores.get(pid, 0) + ptr.get("score", 0.5)

        if not peer_scores:
            logger.debug("route_query_no_peers", query=query)
            self._stats.queries_local_only += 1
            return []

        # Step 2: Select top-N peers — latency-aware ranking
        ranked_peers = sorted(peer_scores.items(), key=lambda x: x[1], reverse=True)
        candidate_pids = [pid for pid, _ in ranked_peers[: self._max_fanout * 2]]

        # Re-rank by latency (fast peers first, with diversity)
        target_peers = self._profiles.rank_by_latency(
            candidate_pids,
            diversity=True,
        )[: self._max_fanout]
        self._stats.peers_contacted += len(target_peers)

        # Step 3: Fan-out requests with timeout
        request = SearchRequest(
            query=query,
            keywords=keywords,
            limit=min(limit, MAX_RESULTS_PER_PEER),
            request_id=f"{self._peer_id}:{time.time():.0f}",
        )

        all_results: list[RemoteSearchResult] = []

        async def _query_peer(peer_id: str) -> list[RemoteSearchResult]:
            """Send search request to a single peer and collect response."""
            start = time.monotonic()
            try:
                results = await self._send_search_request(peer_id, request)
                elapsed = (time.monotonic() - start) * 1000
                self._stats.record_response(elapsed)
                self._profiles.record(peer_id, elapsed, success=True)
                return results
            except Exception:
                elapsed = (time.monotonic() - start) * 1000
                self._stats.peers_timed_out += 1
                self._profiles.record(peer_id, elapsed, success=False)
                logger.debug(
                    "peer_query_failed",
                    peer_id=peer_id,
                    elapsed_ms=elapsed,
                )
                return []

        # Run queries concurrently with timeout
        results_per_peer: list[list[RemoteSearchResult]] = []

        async with trio.open_nursery() as nursery:
            results_chan_send, results_chan_recv = trio.open_memory_channel[
                list[RemoteSearchResult]
            ](
                max_buffer_size=len(target_peers),
            )

            async def _task(pid: str, send_chan: object) -> None:
                peer_timeout = self._profiles.adaptive_timeout(
                    pid,
                    base_ms=float(self._timeout_ms),
                )
                with trio.move_on_after(peer_timeout / 1000):
                    res = await _query_peer(pid)
                    await send_chan.send(res)  # type: ignore[attr-defined]

            async with results_chan_send:
                for pid in target_peers:
                    nursery.start_soon(_task, pid, results_chan_send.clone())

            async with results_chan_recv:
                async for batch in results_chan_recv:
                    results_per_peer.append(batch)

        for batch in results_per_peer:
            all_results.extend(batch)

        # Step 4: Sort by score and limit
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:limit]

    async def _send_search_request(
        self,
        peer_id: str,
        request: SearchRequest,
    ) -> list[RemoteSearchResult]:
        """Send a search request to a specific peer via libp2p stream.

        Args:
            peer_id: Target peer's ID.
            request: Search request to send.

        Returns:
            List of results from the peer.
        """
        from libp2p.peer.id import ID as PeerID

        target_id = PeerID.from_base58(peer_id)

        stream = await self._host.new_stream(target_id, [PROTOCOL_SEARCH])  # type: ignore[attr-defined]
        try:
            # Send request
            payload = dataclass_to_payload(request)
            msg = encode_message(MessageType.SEARCH_REQUEST, payload)
            await stream.write(msg)

            # Read response
            response_data = await stream.read(1024 * 64)  # 64KB max
            if not response_data:
                return []

            unpacked = msgpack.unpackb(response_data, raw=False)
            results = []
            for r in unpacked.get("payload", {}).get("results", []):
                results.append(
                    RemoteSearchResult(
                        url=r.get("url", ""),
                        title=r.get("title", ""),
                        snippet=r.get("snippet", ""),
                        score=r.get("score", 0.0),
                        peer_id=peer_id,
                        doc_id=r.get("doc_id", 0),
                    )
                )
            return results
        finally:
            await stream.close()

    async def handle_search_request(
        self,
        stream: object,
        local_search_fn: object,
    ) -> None:
        """Handle an incoming search request from a peer.

        Reads the request from the stream, performs local search,
        and writes the response back.

        Args:
            stream: libp2p stream.
            local_search_fn: Async function(query, limit) → list of SearchResult dicts.
        """
        try:
            data = await stream.read(1024 * 64)  # type: ignore[attr-defined]
            if not data:
                return

            unpacked = msgpack.unpackb(data, raw=False)
            msg_type = unpacked.get("type", -1)
            payload = unpacked.get("payload", {})

            if msg_type != int(MessageType.SEARCH_REQUEST):
                return

            query = payload.get("query", "")
            limit = payload.get("limit", 10)
            request_id = payload.get("request_id", "")

            # Perform local search
            start = time.monotonic()
            results = await local_search_fn(query, limit)  # type: ignore[operator]
            elapsed = (time.monotonic() - start) * 1000

            # Build response
            response = SearchResponse(
                request_id=request_id,
                results=results,
                peer_id=self._peer_id,
                elapsed_ms=elapsed,
            )
            resp_payload = dataclass_to_payload(response)
            resp_msg = encode_message(MessageType.SEARCH_RESPONSE, resp_payload)

            await stream.write(resp_msg)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("handle_search_request_failed")
        finally:
            await stream.close()  # type: ignore[attr-defined]
