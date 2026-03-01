"""Document replication — N=3 redundancy across peers.

Ensures that every indexed document is stored on at least N peers
(default N=3).  Replica placement uses DHT distance: the 3 peers
closest to ``hash(url)`` are responsible for hosting the document.

Replication flow:
  1. After indexing a document locally, compute ``hash(url)``.
  2. Find N closest peers in the DHT.
  3. Send ``REPLICATE_REQUEST`` to each via libp2p stream.
  4. Peers store the document and ACK.

**NOTE**: This module uses trio async (py-libp2p requirement).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import structlog

from infomesh.p2p.protocol import (
    PROTOCOL_REPLICATE,
    MessageType,
    ReplicateRequest,
    dataclass_to_payload,
    decode_message,
    encode_message,
    safe_unpackb,
    url_to_dht_key,
)

logger = structlog.get_logger()

# Default replication factor
DEFAULT_REPLICATION_FACTOR = 3

# Replication timeout per peer (seconds)
REPLICATE_TIMEOUT_SECONDS = 10


@dataclass
class ReplicationStats:
    """Statistics for the replication subsystem."""

    documents_replicated: int = 0
    replicas_sent: int = 0
    replicas_received: int = 0
    replicas_failed: int = 0
    avg_replicate_ms: float = 0.0
    _times: deque[float] = field(
        default_factory=lambda: deque(maxlen=10_000), repr=False
    )

    def record_time(self, ms: float) -> None:
        """Record a replication time."""
        self._times.append(ms)
        self.avg_replicate_ms = sum(self._times) / len(self._times)


class Replicator:
    """Manages N=3 document replication across the P2P network.

    Args:
        host: libp2p host for opening streams.
        dht: InfoMeshDHT instance.
        local_peer_id: This node's peer ID.
        replication_factor: Number of replicas (default 3).
    """

    def __init__(
        self,
        host: object,
        dht: object,
        local_peer_id: str,
        *,
        replication_factor: int = DEFAULT_REPLICATION_FACTOR,
    ) -> None:
        self._host = host
        self._dht = dht
        self._peer_id = local_peer_id
        self._replication_factor = replication_factor
        self._stats = ReplicationStats()

    @property
    def stats(self) -> ReplicationStats:
        """Current replication statistics."""
        return self._stats

    async def replicate_document(
        self,
        doc_id: int,
        url: str,
        title: str,
        text: str,
        text_hash: str,
        language: str = "",
    ) -> int:
        """Replicate a document to N closest peers.

        Args:
            doc_id: Local document ID.
            url: Document URL.
            title: Document title.
            text: Full extracted text.
            text_hash: SHA-256 of extracted text.
            language: Document language code.

        Returns:
            Number of successful replications (target: replication_factor).
        """
        import trio

        # Find candidate peers for replication
        target_peers = await self._find_replica_peers(url)

        if not target_peers:
            logger.debug("replicate_no_peers", url=url)
            return 0

        success_count = 0

        async def _send_replica(peer_id: str, replica_index: int) -> bool:
            """Send a document replica to a specific peer."""
            start = time.monotonic()
            try:
                request = ReplicateRequest(
                    doc_id=doc_id,
                    url=url,
                    title=title,
                    text=text,
                    text_hash=text_hash,
                    language=language,
                    source_peer_id=self._peer_id,
                    replica_index=replica_index,
                )
                ok = await self._send_replicate_request(peer_id, request)
                elapsed = (time.monotonic() - start) * 1000
                if ok:
                    self._stats.replicas_sent += 1
                    self._stats.record_time(elapsed)
                else:
                    self._stats.replicas_failed += 1
                return ok
            except Exception:
                self._stats.replicas_failed += 1
                logger.debug("replica_send_failed", peer_id=peer_id, url=url)
                return False

        # Send replicas concurrently
        results: list[bool] = []

        async with trio.open_nursery() as nursery:
            result_send, result_recv = trio.open_memory_channel[bool](
                max_buffer_size=len(target_peers),
            )

            async def _task(pid: str, idx: int, send_chan: object) -> None:
                with trio.move_on_after(REPLICATE_TIMEOUT_SECONDS):
                    ok = await _send_replica(pid, idx)
                    await send_chan.send(ok)  # type: ignore[attr-defined]

            async with result_send:
                for idx, pid in enumerate(target_peers):
                    nursery.start_soon(_task, pid, idx, result_send.clone())

            async with result_recv:
                async for ok in result_recv:
                    results.append(ok)

        success_count = sum(1 for r in results if r)

        if success_count > 0:
            self._stats.documents_replicated += 1

        logger.debug(
            "document_replicated",
            url=url,
            target=self._replication_factor,
            success=success_count,
        )
        return success_count

    async def _find_replica_peers(self, url: str) -> list[str]:
        """Find N peers closest to hash(url) for replication.

        Uses XOR distance between the DHT key of the URL and each
        peer's ID (interpreted as an integer) to select the closest
        peers for replica placement.

        Returns:
            List of peer ID strings (may be fewer than replication_factor).
        """
        import hashlib

        dht_key = url_to_dht_key(url)
        key_int = int(hashlib.sha256(dht_key.encode()).hexdigest(), 16)
        connected_peers = self._host.get_connected_peers()  # type: ignore[attr-defined]

        # Select up to N peers (excluding self), sorted by XOR distance
        candidates = [str(pid) for pid in connected_peers if str(pid) != self._peer_id]

        def _xor_distance(peer_id_str: str) -> int:
            peer_int = int(hashlib.sha256(peer_id_str.encode()).hexdigest(), 16)
            return key_int ^ peer_int

        candidates.sort(key=_xor_distance)
        return candidates[: self._replication_factor]

    async def _send_replicate_request(
        self,
        peer_id: str,
        request: ReplicateRequest,
    ) -> bool:
        """Send a replication request to a specific peer.

        Args:
            peer_id: Target peer ID.
            request: Replication request with full document data.

        Returns:
            True if peer acknowledged successful storage.
        """
        from libp2p.peer.id import ID as PeerID

        target_id = PeerID.from_base58(peer_id)

        stream = await self._host.new_stream(target_id, [PROTOCOL_REPLICATE])  # type: ignore[attr-defined]
        try:
            payload = dataclass_to_payload(request)
            msg = encode_message(MessageType.REPLICATE_REQUEST, payload)
            await stream.write(msg)

            # Wait for ACK
            ack_data = await stream.read(1024)
            if ack_data:
                ack = safe_unpackb(ack_data)
                return ack.get("type") == int(MessageType.REPLICATE_RESPONSE)  # type: ignore[no-any-return]
            return False
        except Exception:
            logger.exception("replicate_request_failed", peer_id=peer_id)
            return False
        finally:
            await stream.close()

    async def handle_replicate_request(
        self,
        stream: object,
        store_fn: object,
    ) -> None:
        """Handle an incoming replication request — store the document locally.

        Args:
            stream: libp2p stream.
            store_fn: Callable(url, title, text, text_hash, language) → bool.
        """
        try:
            data = await stream.read(1024 * 1024)  # type: ignore[attr-defined]  # 1MB max
            if not data:
                return

            # Try decoding with length-prefix support
            try:
                _, unpacked_payload = decode_message(data)
                # Wrap to match expected format
                unpacked = {
                    "type": int(MessageType.REPLICATE_REQUEST),
                    "payload": unpacked_payload,
                }
            except (ValueError, Exception):
                unpacked = safe_unpackb(data)
            unpacked_dict: dict[str, object] = (
                unpacked if isinstance(unpacked, dict) else {}
            )
            msg_type = unpacked_dict.get("type", -1)
            payload_raw = unpacked_dict.get("payload", {})
            payload: dict[str, object] = (
                payload_raw if isinstance(payload_raw, dict) else {}
            )

            if msg_type != int(MessageType.REPLICATE_REQUEST):
                return

            # Store locally
            ok = await store_fn(  # type: ignore[operator]
                url=str(payload.get("url", "")),
                title=str(payload.get("title", "")),
                text=str(payload.get("text", "")),
                text_hash=str(payload.get("text_hash", "")),
                language=str(payload.get("language", "")),
            )

            self._stats.replicas_received += 1

            # Send ACK
            ack = encode_message(
                MessageType.REPLICATE_RESPONSE,
                {"success": ok, "peer_id": self._peer_id},
            )
            await stream.write(ack)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("handle_replicate_request_failed")
        finally:
            await stream.close()  # type: ignore[attr-defined]
