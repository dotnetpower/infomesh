"""Kademlia DHT wrapper for py-libp2p.

Provides a high-level interface over libp2p's KadDHT for:
- Publishing and querying inverted-index entries.
- Crawl lock acquisition and release.
- Content attestation storage.

**NOTE**: py-libp2p uses **trio** (not asyncio).  All methods in this
module are trio-async and must be called from a trio context.
The ``InfoMeshDHT`` class is created by ``Node`` which manages the
trio event loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import msgpack
import structlog

from infomesh.hashing import content_hash
from infomesh.p2p.protocol import (
    keyword_to_dht_key,
)

logger = structlog.get_logger()

# DHT key prefixes
_PREFIX_CRAWL_LOCK = "/infomesh/lock/"
_PREFIX_ATTESTATION = "/infomesh/att/"

# Lock TTL default (5 minutes)
_LOCK_TTL_SECONDS = 300

# Maximum pointers per keyword entry
MAX_POINTERS_PER_KEYWORD = 100

# DHT publish rate limit (per keyword per node per hour)
MAX_PUBLISHES_PER_KEYWORD_HR = 10


@dataclass
class DHTStats:
    """Runtime statistics for the DHT layer."""

    keys_stored: int = 0
    keys_published: int = 0
    gets_performed: int = 0
    puts_performed: int = 0
    locks_acquired: int = 0
    locks_released: int = 0


class InfoMeshDHT:
    """High-level DHT operations for InfoMesh.

    Wraps py-libp2p KadDHT with InfoMesh-specific key namespaces,
    rate limiting, and crawl lock semantics.

    All async methods are **trio-async** — must be called from trio context.

    Args:
        kad_dht: A libp2p KadDHT instance (already started via
                 ``background_trio_service``).
        local_peer_id: This node's peer ID string.
    """

    def __init__(self, kad_dht: object, local_peer_id: str) -> None:
        self._dht = kad_dht
        self._peer_id = local_peer_id
        self._stats = DHTStats()
        # Rate limiting: keyword -> list of publish timestamps
        self._publish_times: dict[str, list[float]] = {}

    @property
    def stats(self) -> DHTStats:
        """Current DHT statistics."""
        return self._stats

    # ─── Inverted-Index operations ─────────────────────────

    async def publish_keyword(
        self,
        keyword: str,
        pointers: list[dict],
        *,
        signature: bytes = b"",
    ) -> bool:
        """Publish keyword → peer pointers to the DHT.

        Each pointer is a dict with: peer_id, doc_id, url, score, title.

        Args:
            keyword: The keyword to index.
            pointers: List of PeerPointer dicts.
            signature: Optional Ed25519 signature for tamper detection.

        Returns:
            True if published successfully.
        """
        if not self._check_publish_rate(keyword):
            logger.warning(
                "dht_publish_rate_limited",
                keyword=keyword,
                peer_id=self._peer_id,
            )
            return False

        dht_key = keyword_to_dht_key(keyword)
        entry = {
            "keyword": keyword,
            "pointers": pointers[:MAX_POINTERS_PER_KEYWORD],
            "peer_id": self._peer_id,
            "timestamp": time.time(),
            "signature": signature,
        }
        value = msgpack.packb(entry, use_bin_type=True)

        try:
            await self._dht.put_value(dht_key, value)
            self._stats.puts_performed += 1
            self._stats.keys_published += 1
            self._record_publish(keyword)
            logger.debug(
                "dht_keyword_published", keyword=keyword, pointers=len(pointers)
            )
            return True
        except Exception:
            logger.exception("dht_publish_failed", keyword=keyword)
            return False

    async def query_keyword(self, keyword: str) -> list[dict]:
        """Query the DHT for peer pointers associated with a keyword.

        Returns:
            List of PeerPointer dicts, or empty list if not found.
        """
        dht_key = keyword_to_dht_key(keyword)

        try:
            raw = await self._dht.get_value(dht_key)
            self._stats.gets_performed += 1
            if raw is None:
                return []
            entry = msgpack.unpackb(raw, raw=False)
            return entry.get("pointers", [])
        except Exception:
            logger.exception("dht_query_failed", keyword=keyword)
            return []

    # ─── Crawl Lock operations ─────────────────────────────

    async def acquire_crawl_lock(
        self,
        url: str,
        ttl_seconds: int = _LOCK_TTL_SECONDS,
    ) -> bool:
        """Acquire a crawl lock for a URL.

        Publishes ``hash(url) = CRAWLING:{peer_id}:{timestamp}`` to DHT.
        If another node holds the lock (within TTL), acquisition fails.

        Args:
            url: URL to lock for crawling.
            ttl_seconds: Lock expiry in seconds (default 300 = 5 min).

        Returns:
            True if lock acquired, False if already locked.
        """
        url_hash = content_hash(url)
        lock_key = f"{_PREFIX_CRAWL_LOCK}{url_hash}"

        # Check existing lock
        try:
            existing = await self._dht.get_value(lock_key)
            if existing is not None:
                lock_data = msgpack.unpackb(existing, raw=False)
                lock_time = lock_data.get("timestamp", 0)
                if time.time() - lock_time < ttl_seconds:
                    logger.debug(
                        "crawl_lock_held",
                        url=url,
                        holder=lock_data.get("peer_id"),
                    )
                    return False
        except Exception as exc:
            logger.debug("crawl_lock_check_failed", url=url, error=str(exc))

        # Acquire lock
        lock_data = {
            "peer_id": self._peer_id,
            "url": url,
            "timestamp": time.time(),
            "ttl": ttl_seconds,
        }
        value = msgpack.packb(lock_data, use_bin_type=True)

        try:
            await self._dht.put_value(lock_key, value)
            self._stats.locks_acquired += 1
            logger.debug("crawl_lock_acquired", url=url, ttl=ttl_seconds)
            return True
        except Exception:
            logger.exception("crawl_lock_acquire_failed", url=url)
            return False

    async def release_crawl_lock(self, url: str) -> bool:
        """Release a crawl lock for a URL.

        Only the lock holder (this node) should release the lock.

        Args:
            url: URL to unlock.

        Returns:
            True if released successfully.
        """
        url_hash = content_hash(url)
        lock_key = f"{_PREFIX_CRAWL_LOCK}{url_hash}"

        # Publish empty lock (expired)
        unlock_data = {
            "peer_id": self._peer_id,
            "url": url,
            "timestamp": 0,  # Epoch = expired
            "ttl": 0,
        }
        value = msgpack.packb(unlock_data, use_bin_type=True)

        try:
            await self._dht.put_value(lock_key, value)
            self._stats.locks_released += 1
            logger.debug("crawl_lock_released", url=url)
            return True
        except Exception:
            logger.exception("crawl_lock_release_failed", url=url)
            return False

    # ─── Content Attestation ───────────────────────────────

    async def publish_attestation(
        self,
        url: str,
        raw_hash: str,
        text_hash: str,
        signature: bytes = b"",
    ) -> bool:
        """Publish a content attestation to the DHT.

        Records that this peer crawled a URL and computed specific hashes.

        Args:
            url: Crawled URL.
            raw_hash: SHA-256 of raw HTTP response body.
            text_hash: SHA-256 of extracted text.
            signature: Ed25519 signature over (url + raw_hash + text_hash).

        Returns:
            True if published.
        """
        url_hash = content_hash(url)
        att_key = f"{_PREFIX_ATTESTATION}{url_hash}"

        att_data = {
            "url": url,
            "raw_hash": raw_hash,
            "text_hash": text_hash,
            "peer_id": self._peer_id,
            "timestamp": time.time(),
            "signature": signature,
        }
        value = msgpack.packb(att_data, use_bin_type=True)

        try:
            await self._dht.put_value(att_key, value)
            self._stats.puts_performed += 1
            logger.debug("attestation_published", url=url)
            return True
        except Exception:
            logger.exception("attestation_publish_failed", url=url)
            return False

    async def get_attestation(self, url: str) -> dict | None:
        """Retrieve the attestation record for a URL.

        Returns:
            Attestation dict or None if not found.
        """
        url_hash = content_hash(url)
        att_key = f"{_PREFIX_ATTESTATION}{url_hash}"

        try:
            raw = await self._dht.get_value(att_key)
            self._stats.gets_performed += 1
            if raw is None:
                return None
            return msgpack.unpackb(raw, raw=False)
        except Exception:
            logger.exception("attestation_get_failed", url=url)
            return None

    # ─── Generic DHT operations ────────────────────────────

    async def put(self, key: str, value: bytes) -> bool:
        """Store a raw value in the DHT.

        Args:
            key: DHT key (must start with '/').
            value: Raw bytes to store.

        Returns:
            True if stored successfully.
        """
        try:
            await self._dht.put_value(key, value)
            self._stats.puts_performed += 1
            return True
        except Exception:
            logger.exception("dht_put_failed", key=key)
            return False

    async def get(self, key: str) -> bytes | None:
        """Retrieve a raw value from the DHT.

        Args:
            key: DHT key.

        Returns:
            Raw bytes or None if not found.
        """
        try:
            result = await self._dht.get_value(key)
            self._stats.gets_performed += 1
            return result
        except Exception:
            logger.exception("dht_get_failed", key=key)
            return None

    # ─── Rate limiting helpers ─────────────────────────────

    def _check_publish_rate(self, keyword: str) -> bool:
        """Check if publishing this keyword is within rate limits."""
        now = time.time()
        times = self._publish_times.get(keyword, [])
        # Remove entries older than 1 hour
        times = [t for t in times if now - t < 3600]
        self._publish_times[keyword] = times
        return len(times) < MAX_PUBLISHES_PER_KEYWORD_HR

    def _record_publish(self, keyword: str) -> None:
        """Record a publish timestamp for rate limiting."""
        self._publish_times.setdefault(keyword, []).append(time.time())
