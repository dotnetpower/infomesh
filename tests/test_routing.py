"""Tests for P2P query routing behavior."""

from __future__ import annotations

import pytest
import trio

from infomesh.p2p.protocol import (
    MessageType,
    SearchRequest,
    dataclass_to_payload,
    decode_message,
    encode_message,
)
from infomesh.p2p.routing import (
    _SEARCH_STREAM_MAX_BYTES,
    QueryRouter,
    RemoteSearchResult,
    _payload_float,
    _payload_int,
    _read_stream_message,
)


class _EmptyDHT:
    async def query_keyword(self, keyword: str) -> list[dict[str, object]]:
        return []


class _MalformedPointerDHT:
    async def query_keyword(self, keyword: str) -> list[dict[str, object]]:
        return [
            {"peer_id": 123, "score": "bad-score"},
            {"peer_id": "peer-remote", "score": "nan"},
        ]


class _ConnectedHost:
    def __init__(self, peers: list[str]) -> None:
        self._peers = peers

    def get_connected_peers(self) -> list[str]:
        return self._peers


class _MemoryStream:
    def __init__(self, data: bytes, *, chunk_size: int | None = None) -> None:
        self._data = data
        self._chunk_size = chunk_size
        self.written = b""
        self.closed = False

    async def read(self, max_bytes: int) -> bytes:
        size = max_bytes
        if self._chunk_size is not None:
            size = min(size, self._chunk_size)
        chunk = self._data[:size]
        self._data = self._data[size:]
        return chunk

    async def write(self, data: bytes) -> None:
        self.written += data

    async def close(self) -> None:
        self.closed = True


class _OverreadStream(_MemoryStream):
    async def read(self, max_bytes: int) -> bytes:
        chunk = self._data
        self._data = b""
        return chunk


def test_route_query_falls_back_to_connected_peers_on_dht_miss() -> None:
    async def _run() -> None:
        router = QueryRouter(
            _EmptyDHT(),
            _ConnectedHost(["peer-remote"]),
            "peer-local",
        )

        async def _fake_send_search_request(
            peer_id: str,
            request: object,
        ) -> list[RemoteSearchResult]:
            return [
                RemoteSearchResult(
                    url="https://example.com/asyncio",
                    title="Asyncio",
                    snippet="asyncio search result",
                    score=1.0,
                    peer_id=peer_id,
                    doc_id=7,
                )
            ]

        router._send_search_request = _fake_send_search_request  # type: ignore[method-assign]

        results = await router.route_query("asyncio", ["asyncio"], limit=5)

        assert len(results) == 1
        assert results[0].peer_id == "peer-remote"
        assert results[0].title == "Asyncio"

    trio.run(_run)


def test_route_query_records_peer_timeout() -> None:
    async def _run() -> None:
        router = QueryRouter(
            _EmptyDHT(),
            _ConnectedHost(["peer-slow"]),
            "peer-local",
            timeout_ms=1,
        )

        async def _slow_send_search_request(
            peer_id: str,
            request: object,
        ) -> list[RemoteSearchResult]:
            await trio.sleep(0.05)
            return [
                RemoteSearchResult(
                    url="https://example.com/slow",
                    title="Slow",
                    snippet="late result",
                    score=1.0,
                    peer_id=peer_id,
                    doc_id=9,
                )
            ]

        router._send_search_request = _slow_send_search_request  # type: ignore[method-assign]

        results = await router.route_query("asyncio", ["asyncio"], limit=5)

        assert results == []
        assert router.stats.peers_timed_out == 1

    trio.run(_run)


def test_route_query_rejects_non_positive_limit() -> None:
    async def _run() -> None:
        router = QueryRouter(
            _EmptyDHT(),
            _ConnectedHost(["peer-remote"]),
            "peer-local",
        )

        async def _unexpected_send_search_request(
            peer_id: str,
            request: object,
        ) -> list[RemoteSearchResult]:
            raise AssertionError("non-positive limits should not query peers")

        router._send_search_request = _unexpected_send_search_request  # type: ignore[method-assign]

        results = await router.route_query("asyncio", ["asyncio"], limit=0)

        assert results == []

    trio.run(_run)


def test_route_query_ignores_malformed_dht_pointer_scores() -> None:
    async def _run() -> None:
        router = QueryRouter(
            _MalformedPointerDHT(),
            _ConnectedHost([]),
            "peer-local",
        )

        async def _fake_send_search_request(
            peer_id: str,
            request: object,
        ) -> list[RemoteSearchResult]:
            return [
                RemoteSearchResult(
                    url="https://example.com/asyncio",
                    title="Asyncio",
                    snippet="safe malformed pointer handling",
                    score=1.0,
                    peer_id=peer_id,
                    doc_id=7,
                )
            ]

        router._send_search_request = _fake_send_search_request  # type: ignore[method-assign]

        results = await router.route_query("asyncio", ["asyncio"], limit=5)

        assert len(results) == 1
        assert results[0].peer_id == "peer-remote"

    trio.run(_run)


def test_handle_search_request_decodes_length_prefixed_messages() -> None:
    async def _run() -> None:
        router = QueryRouter(_EmptyDHT(), _ConnectedHost([]), "peer-local")
        request = SearchRequest(
            query="asyncio",
            keywords=["asyncio"],
            limit=5,
            request_id="req-1",
        )
        stream = _MemoryStream(
            encode_message(MessageType.SEARCH_REQUEST, dataclass_to_payload(request)),
            chunk_size=3,
        )

        async def _local_search(
            query: str,
            limit: int,
        ) -> list[dict[str, object]]:
            return [
                {
                    "url": "https://example.com/asyncio",
                    "title": "Asyncio",
                    "snippet": f"result for {query}",
                    "score": 1.0,
                    "doc_id": limit,
                }
            ]

        await router.handle_search_request(stream, _local_search)

        msg_type, payload = decode_message(stream.written)
        assert msg_type == MessageType.SEARCH_RESPONSE
        assert payload["request_id"] == "req-1"
        assert payload["results"][0]["title"] == "Asyncio"
        assert payload["results"][0]["doc_id"] == 5
        assert stream.closed is True

    trio.run(_run)


def test_handle_search_request_defaults_malformed_limit() -> None:
    async def _run() -> None:
        router = QueryRouter(_EmptyDHT(), _ConnectedHost([]), "peer-local")
        stream = _MemoryStream(
            encode_message(
                MessageType.SEARCH_REQUEST,
                {
                    "query": "asyncio",
                    "keywords": ["asyncio"],
                    "limit": "bad-limit",
                    "request_id": "req-2",
                },
            ),
            chunk_size=2,
        )

        async def _local_search(
            query: str,
            limit: int,
        ) -> list[dict[str, object]]:
            return [
                {
                    "url": "https://example.com/asyncio",
                    "title": query,
                    "snippet": "safe default limit",
                    "score": 1.0,
                    "doc_id": limit,
                }
            ]

        await router.handle_search_request(stream, _local_search)

        msg_type, payload = decode_message(stream.written)
        assert msg_type == MessageType.SEARCH_RESPONSE
        assert payload["request_id"] == "req-2"
        assert payload["results"][0]["doc_id"] == 10

    trio.run(_run)


def test_handle_search_request_clamps_negative_limit() -> None:
    async def _run() -> None:
        router = QueryRouter(_EmptyDHT(), _ConnectedHost([]), "peer-local")
        stream = _MemoryStream(
            encode_message(
                MessageType.SEARCH_REQUEST,
                {
                    "query": "asyncio",
                    "keywords": ["asyncio"],
                    "limit": -50,
                    "request_id": "req-negative",
                },
            )
        )

        async def _local_search(
            query: str,
            limit: int,
        ) -> list[dict[str, object]]:
            return [
                {
                    "url": "https://example.com/asyncio",
                    "title": query,
                    "snippet": "clamped limit",
                    "score": 1.0,
                    "doc_id": limit,
                }
            ]

        await router.handle_search_request(stream, _local_search)

        msg_type, payload = decode_message(stream.written)
        assert msg_type == MessageType.SEARCH_RESPONSE
        assert payload["request_id"] == "req-negative"
        assert payload["results"][0]["doc_id"] == 1

    trio.run(_run)


def test_handle_search_request_returns_empty_for_blank_query() -> None:
    async def _run() -> None:
        router = QueryRouter(_EmptyDHT(), _ConnectedHost([]), "peer-local")
        stream = _MemoryStream(
            encode_message(
                MessageType.SEARCH_REQUEST,
                {
                    "query": "   ",
                    "keywords": [],
                    "limit": 10,
                    "request_id": "req-blank",
                },
            )
        )

        async def _unexpected_local_search(
            query: str,
            limit: int,
        ) -> list[dict[str, object]]:
            raise AssertionError("blank peer queries should not hit local search")

        await router.handle_search_request(stream, _unexpected_local_search)

        msg_type, payload = decode_message(stream.written)
        assert msg_type == MessageType.SEARCH_RESPONSE
        assert payload["request_id"] == "req-blank"
        assert payload["results"] == []

    trio.run(_run)


def test_read_stream_message_rejects_oversized_message() -> None:
    async def _run() -> None:
        length = _SEARCH_STREAM_MAX_BYTES + 1
        stream = _MemoryStream(length.to_bytes(4, byteorder="big"))

        with pytest.raises(ValueError, match="too large"):
            await _read_stream_message(stream)

    trio.run(_run)


def test_read_stream_message_rejects_truncated_message() -> None:
    async def _run() -> None:
        stream = _MemoryStream((10).to_bytes(4, byteorder="big") + b"abc")

        with pytest.raises(EOFError, match="before message was complete"):
            await _read_stream_message(stream)

    trio.run(_run)


def test_read_stream_message_rejects_overread_stream() -> None:
    async def _run() -> None:
        stream = _OverreadStream((1).to_bytes(4, byteorder="big") + b"x")

        with pytest.raises(ValueError, match="more bytes than requested"):
            await _read_stream_message(stream)

    trio.run(_run)


def test_payload_numeric_parsing_rejects_bool_and_non_finite() -> None:
    assert _payload_int(True, default=10) == 10
    assert _payload_float(True, default=1.5) == 1.5
    assert _payload_float("nan", default=2.5) == 2.5
    assert _payload_float("inf", default=3.5) == 3.5
