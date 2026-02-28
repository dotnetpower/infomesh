"""Tests for DHT wrapper — unit tests using a mock KadDHT.

These tests verify InfoMeshDHT logic (rate limiting, key formatting,
crawl locks, attestations) without requiring a real libp2p network.
"""

from __future__ import annotations

import time

import msgpack
import pytest

from infomesh.p2p.dht import (
    _LOCK_TTL_SECONDS,
    MAX_PUBLISHES_PER_KEYWORD_HR,
    InfoMeshDHT,
)


class MockKadDHT:
    """In-memory mock of libp2p KadDHT for unit testing."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def put_value(self, key: str, value: bytes) -> None:
        self._store[key] = value

    async def get_value(self, key: str) -> bytes | None:
        return self._store.get(key)


@pytest.fixture
def mock_dht() -> MockKadDHT:
    return MockKadDHT()


@pytest.fixture
def infomesh_dht(mock_dht: MockKadDHT) -> InfoMeshDHT:
    return InfoMeshDHT(mock_dht, "test-peer-id")


class TestInfoMeshDHT:
    """Test InfoMeshDHT operations with mock backend."""

    @pytest.mark.asyncio
    async def test_publish_keyword(self, infomesh_dht: InfoMeshDHT) -> None:
        pointers = [
            {
                "peer_id": "test-peer-id",
                "doc_id": 1,
                "url": "https://example.com",
                "score": 0.9,
            }
        ]
        ok = await infomesh_dht.publish_keyword("python", pointers)
        assert ok is True
        assert infomesh_dht.stats.puts_performed == 1
        assert infomesh_dht.stats.keys_published == 1

    @pytest.mark.asyncio
    async def test_query_keyword(self, infomesh_dht: InfoMeshDHT) -> None:
        # Publish first
        pointers = [
            {
                "peer_id": "test-peer-id",
                "doc_id": 1,
                "url": "https://example.com",
                "score": 0.9,
            }
        ]
        await infomesh_dht.publish_keyword("python", pointers)

        # Query
        result = await infomesh_dht.query_keyword("python")
        assert len(result) == 1
        assert result[0]["peer_id"] == "test-peer-id"
        assert result[0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_query_nonexistent_keyword(self, infomesh_dht: InfoMeshDHT) -> None:
        result = await infomesh_dht.query_keyword("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_publish_rate_limiting(self, infomesh_dht: InfoMeshDHT) -> None:
        pointers = [
            {
                "peer_id": "test-peer-id",
                "doc_id": 1,
                "url": "https://example.com",
                "score": 0.9,
            }
        ]

        # Publish up to the rate limit
        for _i in range(MAX_PUBLISHES_PER_KEYWORD_HR):
            ok = await infomesh_dht.publish_keyword("rate-test", pointers)
            assert ok is True

        # Next publish should be rate-limited
        ok = await infomesh_dht.publish_keyword("rate-test", pointers)
        assert ok is False

    @pytest.mark.asyncio
    async def test_rate_limit_per_keyword(self, infomesh_dht: InfoMeshDHT) -> None:
        """Rate limit is per-keyword, not global."""
        pointers = [
            {
                "peer_id": "test-peer-id",
                "doc_id": 1,
                "url": "https://example.com",
                "score": 0.9,
            }
        ]

        for _ in range(MAX_PUBLISHES_PER_KEYWORD_HR):
            await infomesh_dht.publish_keyword("keyword-a", pointers)

        # Different keyword should still be allowed
        ok = await infomesh_dht.publish_keyword("keyword-b", pointers)
        assert ok is True


class TestCrawlLock:
    """Test crawl lock acquire/release."""

    @pytest.mark.asyncio
    async def test_acquire_lock(self, infomesh_dht: InfoMeshDHT) -> None:
        ok = await infomesh_dht.acquire_crawl_lock("https://example.com/page")
        assert ok is True
        assert infomesh_dht.stats.locks_acquired == 1

    @pytest.mark.asyncio
    async def test_acquire_already_locked(self, infomesh_dht: InfoMeshDHT) -> None:
        """Cannot acquire a lock that another peer holds."""
        # Manually insert a lock from another peer
        import hashlib

        url = "https://example.com/locked"
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        lock_key = f"/infomesh/lock/{url_hash}"
        lock_data = {
            "peer_id": "other-peer",
            "url": url,
            "timestamp": time.time(),  # Fresh lock
            "ttl": _LOCK_TTL_SECONDS,
        }
        await infomesh_dht._dht.put_value(lock_key, msgpack.packb(lock_data))

        # Should fail
        ok = await infomesh_dht.acquire_crawl_lock(url)
        assert ok is False

    @pytest.mark.asyncio
    async def test_acquire_expired_lock(self, infomesh_dht: InfoMeshDHT) -> None:
        """Can acquire a lock that has expired."""
        import hashlib

        url = "https://example.com/expired"
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        lock_key = f"/infomesh/lock/{url_hash}"
        lock_data = {
            "peer_id": "other-peer",
            "url": url,
            "timestamp": time.time() - 600,  # 10 minutes ago = expired
            "ttl": _LOCK_TTL_SECONDS,
        }
        await infomesh_dht._dht.put_value(lock_key, msgpack.packb(lock_data))

        # Should succeed (lock expired)
        ok = await infomesh_dht.acquire_crawl_lock(url)
        assert ok is True

    @pytest.mark.asyncio
    async def test_release_lock(self, infomesh_dht: InfoMeshDHT) -> None:
        await infomesh_dht.acquire_crawl_lock("https://example.com/release")
        ok = await infomesh_dht.release_crawl_lock("https://example.com/release")
        assert ok is True
        assert infomesh_dht.stats.locks_released == 1

    @pytest.mark.asyncio
    async def test_lock_release_allows_reacquire(
        self, infomesh_dht: InfoMeshDHT
    ) -> None:
        url = "https://example.com/cycle"
        await infomesh_dht.acquire_crawl_lock(url)
        await infomesh_dht.release_crawl_lock(url)

        # After release, should be able to acquire again
        ok = await infomesh_dht.acquire_crawl_lock(url)
        assert ok is True


class TestAttestation:
    """Test content attestation publish/get."""

    @pytest.mark.asyncio
    async def test_publish_attestation(self, infomesh_dht: InfoMeshDHT) -> None:
        ok = await infomesh_dht.publish_attestation(
            url="https://example.com",
            raw_hash="abc123",
            text_hash="def456",
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_get_attestation(self, infomesh_dht: InfoMeshDHT) -> None:
        await infomesh_dht.publish_attestation(
            url="https://example.com",
            raw_hash="abc123",
            text_hash="def456",
        )

        att = await infomesh_dht.get_attestation("https://example.com")
        assert att is not None
        assert att["raw_hash"] == "abc123"
        assert att["text_hash"] == "def456"
        assert att["peer_id"] == "test-peer-id"

    @pytest.mark.asyncio
    async def test_get_nonexistent_attestation(self, infomesh_dht: InfoMeshDHT) -> None:
        att = await infomesh_dht.get_attestation("https://nonexistent.com")
        assert att is None


class TestGenericDHT:
    """Test generic put/get operations."""

    @pytest.mark.asyncio
    async def test_put_get(self, infomesh_dht: InfoMeshDHT) -> None:
        ok = await infomesh_dht.put("/infomesh/test/key1", b"value1")
        assert ok is True

        result = await infomesh_dht.get("/infomesh/test/key1")
        assert result == b"value1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, infomesh_dht: InfoMeshDHT) -> None:
        result = await infomesh_dht.get("/infomesh/test/nokey")
        assert result is None

    @pytest.mark.asyncio
    async def test_stats_tracking(self, infomesh_dht: InfoMeshDHT) -> None:
        await infomesh_dht.put("/infomesh/test/k1", b"v1")
        await infomesh_dht.put("/infomesh/test/k2", b"v2")
        await infomesh_dht.get("/infomesh/test/k1")

        assert infomesh_dht.stats.puts_performed == 2
        assert infomesh_dht.stats.gets_performed == 1
