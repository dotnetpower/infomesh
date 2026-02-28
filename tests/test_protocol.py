"""Tests for P2P protocol module — message encoding, decoding, key hashing."""

from __future__ import annotations

import pytest

from infomesh.p2p.protocol import (
    Attestation,
    CrawlLock,
    MessageType,
    PeerPointer,
    ReplicateRequest,
    SearchRequest,
    dataclass_to_payload,
    decode_message,
    encode_message,
    keyword_to_dht_key,
    url_to_dht_key,
)


class TestMessageType:
    """Test MessageType enum."""

    def test_all_types_have_unique_values(self) -> None:
        values = [m.value for m in MessageType]
        assert len(values) == len(set(values))

    def test_ping_pong_are_0_1(self) -> None:
        assert MessageType.PING == 0
        assert MessageType.PONG == 1

    def test_search_types_are_10s(self) -> None:
        assert MessageType.SEARCH_REQUEST == 10
        assert MessageType.SEARCH_RESPONSE == 11

    def test_error_is_99(self) -> None:
        assert MessageType.ERROR == 99


class TestMessageEncoding:
    """Test message serialization and deserialization."""

    def test_roundtrip_ping(self) -> None:
        payload = {"peer_id": "test-peer-123"}
        encoded = encode_message(MessageType.PING, payload)
        msg_type, decoded = decode_message(encoded)
        assert msg_type == MessageType.PING
        assert decoded["peer_id"] == "test-peer-123"

    def test_roundtrip_search_request(self) -> None:
        payload = {
            "query": "python asyncio tutorial",
            "keywords": ["python", "asyncio", "tutorial"],
            "limit": 10,
        }
        encoded = encode_message(MessageType.SEARCH_REQUEST, payload)
        msg_type, decoded = decode_message(encoded)
        assert msg_type == MessageType.SEARCH_REQUEST
        assert decoded["query"] == "python asyncio tutorial"
        assert decoded["keywords"] == ["python", "asyncio", "tutorial"]
        assert decoded["limit"] == 10

    def test_roundtrip_with_bytes_payload(self) -> None:
        payload = {"data": b"\x00\x01\x02\xff", "peer_id": "abc"}
        encoded = encode_message(MessageType.REPLICATE_REQUEST, payload)
        msg_type, decoded = decode_message(encoded)
        assert msg_type == MessageType.REPLICATE_REQUEST
        assert decoded["data"] == b"\x00\x01\x02\xff"

    def test_length_prefix_format(self) -> None:
        encoded = encode_message(MessageType.PING, {"ok": True})
        # First 4 bytes = length prefix
        length = int.from_bytes(encoded[:4], byteorder="big")
        assert length == len(encoded) - 4
        assert length > 0

    def test_empty_payload(self) -> None:
        encoded = encode_message(MessageType.PONG, {})
        msg_type, decoded = decode_message(encoded)
        assert msg_type == MessageType.PONG
        assert decoded == {}

    def test_decode_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            decode_message(b"\x00\x01")

    def test_message_too_large_raises(self) -> None:
        # 11 MB payload
        huge_payload = {"data": "x" * (11 * 1024 * 1024)}
        with pytest.raises(ValueError, match="too large"):
            encode_message(MessageType.SEARCH_RESPONSE, huge_payload)

    def test_all_message_types_roundtrip(self) -> None:
        for mt in MessageType:
            payload = {"type_test": int(mt)}
            encoded = encode_message(mt, payload)
            decoded_type, decoded_payload = decode_message(encoded)
            assert decoded_type == mt
            assert decoded_payload["type_test"] == int(mt)


class TestDataclassPayload:
    """Test dataclass to payload conversion."""

    def test_peer_pointer(self) -> None:
        ptr = PeerPointer(
            peer_id="peer-1",
            doc_id=42,
            url="https://example.com",
            score=0.95,
            title="Example",
        )
        payload = dataclass_to_payload(ptr)
        assert payload["peer_id"] == "peer-1"
        assert payload["doc_id"] == 42
        assert payload["score"] == 0.95

    def test_search_request(self) -> None:
        req = SearchRequest(
            query="python",
            keywords=["python"],
            limit=5,
            request_id="req-1",
        )
        payload = dataclass_to_payload(req)
        assert payload["query"] == "python"
        assert payload["limit"] == 5
        assert payload["request_id"] == "req-1"
        assert "timestamp" in payload

    def test_crawl_lock(self) -> None:
        lock = CrawlLock(
            url="https://example.com/page",
            peer_id="peer-1",
            ttl_seconds=300,
        )
        payload = dataclass_to_payload(lock)
        assert payload["url"] == "https://example.com/page"
        assert payload["ttl_seconds"] == 300

    def test_attestation_with_signature(self) -> None:
        att = Attestation(
            url="https://example.com",
            raw_hash="abc123",
            text_hash="def456",
            peer_id="peer-1",
            signature=b"\x00\x01\x02",
        )
        payload = dataclass_to_payload(att)
        assert payload["signature"] == b"\x00\x01\x02"

    def test_replicate_request(self) -> None:
        req = ReplicateRequest(
            doc_id=1,
            url="https://example.com",
            title="Test",
            text="Some content",
            text_hash="hash123",
            language="en",
            source_peer_id="peer-1",
            replica_index=0,
        )
        payload = dataclass_to_payload(req)
        assert payload["doc_id"] == 1
        assert payload["replica_index"] == 0


class TestDHTKeyHashing:
    """Test URL and keyword hashing for DHT keys."""

    def test_url_to_dht_key_format(self) -> None:
        key = url_to_dht_key("https://example.com")
        assert key.startswith("/infomesh/url/")
        # SHA-256 hex = 64 chars
        assert len(key.split("/")[-1]) == 64

    def test_url_to_dht_key_deterministic(self) -> None:
        k1 = url_to_dht_key("https://example.com/page")
        k2 = url_to_dht_key("https://example.com/page")
        assert k1 == k2

    def test_different_urls_different_keys(self) -> None:
        k1 = url_to_dht_key("https://example.com/a")
        k2 = url_to_dht_key("https://example.com/b")
        assert k1 != k2

    def test_keyword_to_dht_key_format(self) -> None:
        key = keyword_to_dht_key("python")
        assert key.startswith("/infomesh/kw/")
        assert len(key.split("/")[-1]) == 64

    def test_keyword_case_insensitive(self) -> None:
        k1 = keyword_to_dht_key("Python")
        k2 = keyword_to_dht_key("python")
        k3 = keyword_to_dht_key("PYTHON")
        assert k1 == k2 == k3

    def test_keyword_deterministic(self) -> None:
        k1 = keyword_to_dht_key("asyncio")
        k2 = keyword_to_dht_key("asyncio")
        assert k1 == k2
