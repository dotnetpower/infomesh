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
from collections import deque
from dataclasses import dataclass, field
from math import isfinite

import structlog

from infomesh.p2p.peer_profile import PeerProfileTracker
from infomesh.p2p.protocol import (
    MAX_MESSAGE_SIZE,
    PROTOCOL_SEARCH,
    MessageType,
    SearchRequest,
    SearchResponse,
    dataclass_to_payload,
    decode_message,
    encode_message,
)

logger = structlog.get_logger()

# Timeout for remote search responses (ms).  Bootstrap and low-resource peers can
# be busy republishing or crawling, so keep this above transient VM jitter.
SEARCH_TIMEOUT_MS = 5000

# Maximum peers to fan-out a query to
MAX_FANOUT = 5

# Maximum results from a single peer
MAX_RESULTS_PER_PEER = 20

# Hedged request: if first peer doesn't respond within this fraction
# of the adaptive timeout, also send to a backup peer.
HEDGE_TIMEOUT_FRACTION = 0.5

_LENGTH_PREFIX_BYTES = 4
_SEARCH_STREAM_MAX_BYTES = min(MAX_MESSAGE_SIZE, 1024 * 1024)


@dataclass
class RoutingStats:
    """Statistics for query routing."""

    queries_routed: int = 0
    queries_local_only: int = 0
    peers_contacted: int = 0
    peers_responded: int = 0
    peers_timed_out: int = 0
    avg_response_ms: float = 0.0
    _response_times: deque[float] = field(
        default_factory=lambda: deque(maxlen=10_000), repr=False
    )

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
        if limit <= 0:
            return []

        # Step 1: Find candidate peers via DHT
        peer_scores: dict[str, float] = {}
        for kw in keywords:
            pointers = await self._dht.query_keyword(kw)  # type: ignore[attr-defined]
            for ptr in pointers:
                pid = _payload_str(ptr.get("peer_id"))
                if pid and pid != self._peer_id:
                    score = _payload_float(ptr.get("score"), default=0.5)
                    peer_scores[pid] = peer_scores.get(pid, 0.0) + score

        if not peer_scores:
            peer_scores.update(self._connected_peer_scores())
            if not peer_scores:
                logger.debug("route_query_no_peers", query=query)
                self._stats.queries_local_only += 1
                return []
            logger.debug(
                "route_query_connected_peer_fallback",
                query=query,
                peers=len(peer_scores),
            )

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
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                self._stats.peers_timed_out += 1
                self._profiles.record(peer_id, elapsed, success=False)
                logger.debug(
                    "peer_query_failed",
                    peer_id=peer_id,
                    elapsed_ms=elapsed,
                    error=str(exc),
                    error_type=type(exc).__name__,
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
                async with send_chan:  # type: ignore[attr-defined]
                    peer_timeout = self._profiles.adaptive_timeout(
                        pid,
                        base_ms=float(self._timeout_ms),
                    )
                    with trio.move_on_after(peer_timeout / 1000) as cancel_scope:
                        res = await _query_peer(pid)
                        await send_chan.send(res)  # type: ignore[attr-defined]
                    if cancel_scope.cancelled_caught:
                        self._stats.peers_timed_out += 1
                        self._profiles.record(pid, peer_timeout, success=False)
                        logger.debug(
                            "peer_query_timeout",
                            peer_id=pid,
                            timeout_ms=peer_timeout,
                        )
                        await send_chan.send([])  # type: ignore[attr-defined]

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

    def _connected_peer_scores(self) -> dict[str, float]:
        get_connected = getattr(self._host, "get_connected_peers", None)
        if not callable(get_connected):
            return {}
        try:
            peer_ids = get_connected()
        except Exception:
            return {}

        scores: dict[str, float] = {}
        for peer_id in peer_ids:
            pid = str(peer_id)
            if pid and pid != self._peer_id:
                scores[pid] = 0.1
        return scores

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
            msg_type, payload = await _read_stream_message(stream)
            if msg_type != MessageType.SEARCH_RESPONSE:
                return []
            results = []
            response_results = payload.get("results", [])
            if not isinstance(response_results, list):
                return []
            result_limit = min(max(request.limit, 0), MAX_RESULTS_PER_PEER)
            for r in response_results[:result_limit]:
                if not isinstance(r, dict):
                    continue
                results.append(
                    RemoteSearchResult(
                        url=_payload_str(r.get("url")),
                        title=_payload_str(r.get("title")),
                        snippet=_payload_str(r.get("snippet")),
                        score=_payload_float(r.get("score")),
                        peer_id=peer_id,
                        doc_id=_payload_int(r.get("doc_id")),
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
            msg_type, payload = await _read_stream_message(stream)

            if msg_type != MessageType.SEARCH_REQUEST:
                return

            query = _payload_str(payload.get("query"))
            limit = min(max(_payload_int(payload.get("limit"), default=10), 1), 100)
            request_id = _payload_str(payload.get("request_id"))

            # Perform local search
            start = time.monotonic()
            if query.strip():
                results = await local_search_fn(query, limit)  # type: ignore[operator]
            else:
                results = []
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


async def _read_stream_message(stream: object) -> tuple[MessageType, dict[str, object]]:
    prefix = await _read_exact(stream, _LENGTH_PREFIX_BYTES)
    length = int.from_bytes(prefix, byteorder="big")
    if length <= 0:
        raise ValueError("Empty P2P search message")
    if length > _SEARCH_STREAM_MAX_BYTES:
        raise ValueError(f"P2P search message too large: {length} bytes")
    body = await _read_exact(stream, length)
    msg_type, payload = decode_message(prefix + body)
    if not isinstance(payload, dict):
        raise ValueError("P2P search payload must be a map")
    return msg_type, payload


async def _read_exact(stream: object, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = await stream.read(remaining)  # type: ignore[attr-defined]
        if not isinstance(chunk, bytes):
            raise TypeError("P2P stream read returned non-bytes data")
        if not chunk:
            raise EOFError("P2P stream closed before message was complete")
        if len(chunk) > remaining:
            raise ValueError("P2P stream returned more bytes than requested")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _payload_str(value: object, *, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _payload_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _payload_float(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if isfinite(parsed) else default
