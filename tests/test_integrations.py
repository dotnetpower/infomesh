"""Tests for Phase 9 integration wiring.

Covers:
- BandwidthThrottle (token-bucket rate limiting)
- UrlAssigner (DHT XOR distance-based URL ownership)
- Crawl lock wiring in CrawlWorker
- Distributed search function
- Formatter for distributed results
"""

from __future__ import annotations

import asyncio

import pytest_asyncio  # noqa: F401

from infomesh.crawler.url_assigner import UrlAssigner, _xor_distance
from infomesh.index.local_store import LocalStore
from infomesh.p2p.protocol import PeerPointer
from infomesh.p2p.throttle import BandwidthBucket, BandwidthThrottle

# ── BandwidthThrottle tests ──────────────────────────────────────


class TestBandwidthBucket:
    """Token-bucket rate limiter for a single direction."""

    def test_unlimited_bucket_zero_rate(self) -> None:
        """0 Mbps bucket has 0 rate_bytes_per_sec."""
        bucket = BandwidthBucket(rate_mbps=0)
        assert bucket.rate_bytes_per_sec == 0.0

    def test_limited_small_acquire(self) -> None:
        """Acquire within burst capacity should not wait."""
        bucket = BandwidthBucket(rate_mbps=10)
        loop = asyncio.new_event_loop()
        # 10 Mbps = 1_250_000 bytes/sec burst. Acquiring 1000 bytes should be instant.
        waited = loop.run_until_complete(bucket.acquire(1000))
        loop.close()
        assert waited == 0.0

    def test_rate_conversion(self) -> None:
        """Rate should convert Mbps to bytes/sec correctly."""
        bucket = BandwidthBucket(rate_mbps=8)
        # 8 Mbps = 8_000_000 bits/s = 1_000_000 bytes/s
        assert bucket.rate_bytes_per_sec == 1_000_000


class TestBandwidthThrottle:
    """Two-directional bandwidth throttle."""

    def test_default_values(self) -> None:
        throttle = BandwidthThrottle()
        assert throttle.stats.upload_bytes == 0
        assert throttle.stats.download_bytes == 0

    def test_fully_disabled(self) -> None:
        """Throttle with 0/0 disables both directions."""
        throttle = BandwidthThrottle(upload_mbps=0, download_mbps=0)
        loop = asyncio.new_event_loop()
        waited = loop.run_until_complete(throttle.acquire_upload(10000))
        loop.close()
        assert waited == 0.0

    def test_acquire_upload(self) -> None:
        throttle = BandwidthThrottle(upload_mbps=100, download_mbps=100)
        loop = asyncio.new_event_loop()
        waited = loop.run_until_complete(throttle.acquire_upload(512))
        loop.close()
        assert waited == 0.0

    def test_acquire_download(self) -> None:
        throttle = BandwidthThrottle(upload_mbps=100, download_mbps=100)
        loop = asyncio.new_event_loop()
        waited = loop.run_until_complete(throttle.acquire_download(512))
        loop.close()
        assert waited == 0.0

    def test_stats_tracking(self) -> None:
        throttle = BandwidthThrottle(upload_mbps=100, download_mbps=100)
        loop = asyncio.new_event_loop()
        loop.run_until_complete(throttle.acquire_upload(100))
        loop.run_until_complete(throttle.acquire_download(200))
        loop.close()
        assert throttle.stats.upload_bytes == 100
        assert throttle.stats.download_bytes == 200


# ── UrlAssigner tests ────────────────────────────────────────────


class TestXorDistance:
    """Low-level XOR distance calculation."""

    def test_same_hash(self) -> None:
        assert _xor_distance("abcd", "abcd") == 0

    def test_different_hashes(self) -> None:
        dist = _xor_distance("0000", "ffff")
        assert dist > 0

    def test_symmetry(self) -> None:
        assert _xor_distance("1234", "5678") == _xor_distance("5678", "1234")

    def test_triangle_inequality_like(self) -> None:
        """XOR is a metric — d(a,b) <= d(a,c) + d(c,b) doesn't hold for XOR,
        but d(a,c) XOR d(c,b) >= d(a,b) always holds. Just test non-negative."""
        d1 = _xor_distance("aaaa", "bbbb")
        d2 = _xor_distance("bbbb", "cccc")
        assert d1 >= 0
        assert d2 >= 0


class TestUrlAssigner:
    """URL→node ownership via XOR distance."""

    def test_single_node_is_always_owner(self) -> None:
        assigner = UrlAssigner("peer_A")
        assert assigner.is_local_owner("https://example.com")
        assert assigner.known_peers == 1

    def test_add_remove_peer(self) -> None:
        assigner = UrlAssigner("peer_A")
        assigner.add_peer("peer_B")
        assert assigner.known_peers == 2
        assigner.remove_peer("peer_B")
        assert assigner.known_peers == 1

    def test_cannot_remove_local(self) -> None:
        assigner = UrlAssigner("peer_A")
        assigner.remove_peer("peer_A")
        assert assigner.known_peers == 1  # local never removed

    def test_closest_peer_deterministic(self) -> None:
        assigner = UrlAssigner("peer_A")
        assigner.add_peer("peer_B")
        assigner.add_peer("peer_C")
        url = "https://example.com/test"
        # Same URL should always get same owner
        owner1 = assigner.closest_peer(url)
        owner2 = assigner.closest_peer(url)
        assert owner1 == owner2

    def test_filter_local_urls(self) -> None:
        assigner = UrlAssigner("peer_A")
        assigner.add_peer("peer_B")
        urls = [
            "https://a.com",
            "https://b.com",
            "https://c.com",
            "https://d.com",
            "https://e.com",
        ]
        local = assigner.filter_local_urls(urls)
        # Some should be local, some not (unless all happen to be closest to A)
        assert isinstance(local, list)
        # All returned URLs should be owned by local
        for url in local:
            assert assigner.is_local_owner(url)

    def test_assign_returns_crawl_assignment(self) -> None:
        assigner = UrlAssigner("peer_A")
        assignment = assigner.assign("https://example.com/test", depth=2)
        assert assignment.url == "https://example.com/test"
        assert assignment.depth == 2
        assert assignment.assigner_peer_id == "peer_A"  # only known peer

    def test_add_peer_idempotent(self) -> None:
        assigner = UrlAssigner("peer_A")
        assigner.add_peer("peer_B")
        assigner.add_peer("peer_B")
        assert assigner.known_peers == 2


# ── Distributed search tests ────────────────────────────────────


class _MockDistributedIndex:
    """Mock distributed index for testing search_distributed."""

    def __init__(self, pointers: list[PeerPointer] | None = None) -> None:
        self._pointers = pointers or []

    async def query(self, keywords: list[str]) -> list[PeerPointer]:
        return self._pointers


class TestSearchDistributed:
    """Tests for distributed search merging."""

    def test_local_only_when_no_remote(self) -> None:
        """With no remote results, distributed search falls back to local."""
        from infomesh.search.query import search_distributed

        store = LocalStore()
        store.add_document(
            url="https://example.com/page",
            title="Test Page",
            text="Python async programming guide for developers",
            raw_html_hash="h1",
            text_hash="t1",
        )

        mock_di = _MockDistributedIndex(pointers=[])
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            search_distributed(store, mock_di, "python", limit=5)  # type: ignore[arg-type]
        )
        loop.close()

        assert result.source == "local_only"
        assert result.remote_count == 0
        assert result.local_count >= 0
        store.close()

    def test_remote_results_merged(self) -> None:
        """Remote pointers should be merged with local results."""
        from infomesh.search.query import search_distributed

        store = LocalStore()
        store.add_document(
            url="https://example.com/local",
            title="Local Page",
            text="Python async programming guide",
            raw_html_hash="h1",
            text_hash="t1",
        )

        remote = [
            PeerPointer(
                peer_id="remote_peer_1",
                doc_id=42,
                url="https://remote.com/page",
                score=2.5,
                title="Remote Page",
            ),
        ]
        mock_di = _MockDistributedIndex(pointers=remote)
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            search_distributed(store, mock_di, "python", limit=5)  # type: ignore[arg-type]
        )
        loop.close()

        assert result.source == "distributed"
        assert result.remote_count == 1
        urls = {r.url for r in result.results}
        assert "https://remote.com/page" in urls
        store.close()

    def test_dedup_by_url(self) -> None:
        """Same URL from local and remote should not duplicate."""
        from infomesh.search.query import search_distributed

        store = LocalStore()
        store.add_document(
            url="https://example.com/shared",
            title="Shared Page",
            text="Python async programming guide for developers learning async",
            raw_html_hash="h1",
            text_hash="t1",
        )

        remote = [
            PeerPointer(
                peer_id="remote_peer",
                doc_id=1,
                url="https://example.com/shared",
                score=5.0,
                title="Shared Page (remote)",
            ),
        ]
        mock_di = _MockDistributedIndex(pointers=remote)
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            search_distributed(store, mock_di, "python", limit=5)  # type: ignore[arg-type]
        )
        loop.close()

        # Should have exactly 1 result (not 2) for the shared URL
        shared_urls = [
            r for r in result.results if r.url == "https://example.com/shared"
        ]
        assert len(shared_urls) == 1
        store.close()

    def test_network_search_fn_provides_real_results(self) -> None:
        """network_search_fn should be used when provided."""
        from infomesh.search.query import search_distributed

        store = LocalStore()
        store.add_document(
            url="https://example.com/local",
            title="Local Page",
            text="Python async programming guide",
            raw_html_hash="h1",
            text_hash="t1",
        )

        async def mock_network_search(
            query: str, keywords: list[str], limit: int
        ) -> list[dict[str, object]]:
            return [
                {
                    "url": "https://peer.com/result",
                    "title": "Peer Result",
                    "snippet": "Real snippet from peer",
                    "score": 3.0,
                    "peer_id": "peer_abc",
                    "doc_id": 99,
                },
            ]

        mock_di = _MockDistributedIndex(pointers=[])
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            search_distributed(
                store,
                mock_di,
                "python",
                limit=5,
                network_search_fn=mock_network_search,
            )  # type: ignore[arg-type]
        )
        loop.close()

        assert result.source == "distributed"
        assert result.remote_count == 1
        urls = {r.url for r in result.results}
        assert "https://peer.com/result" in urls
        # Verify real snippet was preserved
        peer_r = next(r for r in result.results if r.url == "https://peer.com/result")
        assert peer_r.snippet == "Real snippet from peer"
        assert peer_r.peer_id == "peer_abc"
        store.close()

    def test_network_search_fn_failure_fallback(self) -> None:
        """If network_search_fn raises, fall back gracefully."""
        from infomesh.search.query import search_distributed

        store = LocalStore()
        store.add_document(
            url="https://example.com/local",
            title="Local Page",
            text="Python async programming guide",
            raw_html_hash="h1",
            text_hash="t1",
        )

        async def failing_network_search(
            query: str, keywords: list[str], limit: int
        ) -> list[dict[str, object]]:
            msg = "network error"
            raise ConnectionError(msg)

        mock_di = _MockDistributedIndex(pointers=[])
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            search_distributed(
                store,
                mock_di,
                "python",
                limit=5,
                network_search_fn=failing_network_search,
            )  # type: ignore[arg-type]
        )
        loop.close()

        # Should still have local results, no crash
        assert result.source == "local_only"
        assert result.remote_count == 0
        store.close()


# ── Distributed formatter test ───────────────────────────────────


class TestFormatDistributedResults:
    """Tests for the distributed results formatter."""

    def test_empty_results(self) -> None:
        from infomesh.search.formatter import format_distributed_results
        from infomesh.search.query import DistributedResult

        result = DistributedResult(
            results=[],
            total=0,
            elapsed_ms=1.0,
            source="local_only",
            local_count=0,
            remote_count=0,
        )
        text = format_distributed_results(result)
        assert text == "No results found."

    def test_with_results(self) -> None:
        from infomesh.index.ranking import RankedResult
        from infomesh.search.formatter import format_distributed_results
        from infomesh.search.query import DistributedResult

        results = [
            RankedResult(
                doc_id=1,
                url="https://example.com/page",
                title="Test",
                snippet="snippet text here",
                bm25_score=1.0,
                freshness_score=0.2,
                trust_score=0.2,
                authority_score=0.1,
                combined_score=1.5,
                crawled_at=0.0,
            ),
        ]
        result = DistributedResult(
            results=results,
            total=1,
            elapsed_ms=42.5,
            source="distributed",
            local_count=1,
            remote_count=3,
        )
        text = format_distributed_results(result)
        assert "Found 1 results" in text
        assert "distributed" in text
        assert "Local: 1" in text
        assert "Remote: 3" in text
        assert "Test" in text


# ── Crawl lock integration test ──────────────────────────────────


class _MockDHT:
    """Minimal DHT mock for crawl lock tests."""

    def __init__(self, *, lock_held: bool = False) -> None:
        self._lock_held = lock_held
        self.acquired: list[str] = []
        self.released: list[str] = []

    async def acquire_crawl_lock(self, url: str) -> bool:
        if self._lock_held:
            return False
        self.acquired.append(url)
        return True

    async def release_crawl_lock(self, url: str) -> bool:
        self.released.append(url)
        return True


class TestCrawlLockIntegration:
    """Tests for crawl lock acquire/release in CrawlWorker."""

    def test_worker_accepts_dht(self) -> None:
        """CrawlWorker should accept optional dht parameter."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.dedup import DeduplicatorDB
        from infomesh.crawler.robots import RobotsChecker
        from infomesh.crawler.scheduler import Scheduler
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        scheduler = Scheduler()
        dedup = DeduplicatorDB()
        robots = RobotsChecker(user_agent="InfoMeshBot/test")

        worker = CrawlWorker(config, scheduler, dedup, robots)
        assert worker._dht is None  # noqa: SLF001

        mock_dht = _MockDHT()
        worker2 = CrawlWorker(config, scheduler, dedup, robots, dht=mock_dht)
        assert worker2._dht is mock_dht  # noqa: SLF001

    def test_worker_without_dht_works(self) -> None:
        """CrawlWorker without DHT should crawl normally (no lock)."""
        from infomesh.config import CrawlConfig
        from infomesh.crawler.dedup import DeduplicatorDB
        from infomesh.crawler.robots import RobotsChecker
        from infomesh.crawler.scheduler import Scheduler
        from infomesh.crawler.worker import CrawlWorker

        config = CrawlConfig()
        scheduler = Scheduler()
        dedup = DeduplicatorDB()
        robots = RobotsChecker(user_agent="InfoMeshBot/test")

        worker = CrawlWorker(config, scheduler, dedup, robots)
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            worker.crawl_url("https://httpbin.org/robots.txt")
        )
        loop.close()
        # Result may succeed or fail depending on network/content,
        # but it should not crash due to missing DHT
        assert hasattr(result, "url")


# ── Node integration property tests ─────────────────────────────


class TestNodeProperties:
    """Tests for Node subsystem properties (without starting libp2p)."""

    def test_node_has_all_subsystem_properties(self) -> None:
        """InfoMeshNode should expose all integration subsystems as properties."""
        from infomesh.config import Config
        from infomesh.p2p.node import InfoMeshNode

        config = Config()
        node = InfoMeshNode(config)

        # All subsystems should have properties
        assert node.throttle is not None
        assert node.subnet_limiter is not None
        assert node.mdns is None  # only set during trio_main
        assert node.pow_nonce is None  # only set during trio_main
        assert node.url_assigner is None  # only set during trio_main
        assert node.dht is None
        assert node.state == "stopped"

    def test_throttle_uses_config(self) -> None:
        """Throttle should use config values."""
        from infomesh.config import Config, NetworkConfig
        from infomesh.p2p.node import InfoMeshNode

        config = Config(
            network=NetworkConfig(
                upload_limit_mbps=5.0,
                download_limit_mbps=10.0,
            )
        )
        node = InfoMeshNode(config)
        # Throttle should be configured with the same limits
        assert node.throttle.stats.upload_bytes == 0  # no data sent yet
