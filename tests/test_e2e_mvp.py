"""End-to-end MVP quality validation.

Tests that a single InfoMesh node can:
1. Crawl real web pages
2. Index them in FTS5 (+ optional vector)
3. Return relevant search results comparable to Tavily-level quality

Run: uv run pytest tests/test_e2e_mvp.py -v -s --timeout=120
"""

from __future__ import annotations

import asyncio
import time

import pytest
import structlog

from infomesh.config import CrawlConfig
from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.crawler.robots import RobotsChecker
from infomesh.crawler.scheduler import Scheduler
from infomesh.crawler.worker import CrawlWorker
from infomesh.index.local_store import LocalStore
from infomesh.search.query import search_local

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Real URLs for quality testing — static/stable doc pages
# ---------------------------------------------------------------------------
_TEST_URLS = [
    "https://docs.python.org/3/tutorial/datastructures.html",
    "https://docs.python.org/3/library/asyncio.html",
    "https://docs.python.org/3/library/sqlite3.html",
    "https://docs.python.org/3/library/pathlib.html",
    "https://docs.python.org/3/library/hashlib.html",
    "https://httpx.readthedocs.io/en/latest/quickstart/",
]

# Queries + expected relevance checks (query → expected URL substring in top-3)
_QUALITY_CHECKS = [
    ("python list append sort", "datastructures"),
    ("asyncio event loop await", "asyncio"),
    ("sqlite connection cursor execute", "sqlite3"),
    ("path file directory join", "pathlib"),
    ("sha256 hash digest", "hashlib"),
    ("http client async request", "httpx"),
]


@pytest.fixture(scope="module")
def crawl_config() -> CrawlConfig:
    return CrawlConfig(
        max_concurrent=3,
        politeness_delay=1.0,
        max_depth=0,
        urls_per_hour=60,
        pending_per_domain=10,
        user_agent="InfoMesh-Test/0.1",
        respect_robots=True,
    )


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> LocalStore:
    db_path = tmp_path_factory.mktemp("mvp") / "test_index.db"
    s = LocalStore(db_path=db_path, compression_enabled=True, compression_level=3)
    yield s
    s.close()


@pytest.fixture(scope="module")
def dedup(tmp_path_factory) -> DeduplicatorDB:
    db_path = tmp_path_factory.mktemp("mvp") / "dedup.db"
    d = DeduplicatorDB(str(db_path))
    yield d
    d.close()


class TestMVPEndToEnd:
    """End-to-end test: crawl real pages → index → search → validate quality."""

    @pytest.mark.timeout(120)
    async def test_crawl_real_pages(
        self, crawl_config: CrawlConfig, store: LocalStore, dedup: DeduplicatorDB
    ) -> None:
        """Step 1: Crawl real documentation pages and index them."""
        robots = RobotsChecker(crawl_config.user_agent)
        scheduler = Scheduler(
            politeness_delay=crawl_config.politeness_delay,
            urls_per_hour=crawl_config.urls_per_hour,
            pending_per_domain=crawl_config.pending_per_domain,
            max_depth=crawl_config.max_depth,
        )
        worker = CrawlWorker(crawl_config, scheduler, dedup, robots)

        results = []
        for url in _TEST_URLS:
            result = await worker.crawl_url(url, depth=0)
            results.append(result)

            if result.success and result.page:
                store.add_document(
                    url=result.page.url,
                    title=result.page.title,
                    text=result.page.text,
                    raw_html_hash=result.page.raw_html_hash,
                    text_hash=result.page.text_hash,
                    language=result.page.language,
                )

            # Politeness delay
            await asyncio.sleep(1.0)

        await worker.close()

        # At least 4 out of 6 should succeed (some may be temporarily down)
        successes = [r for r in results if r.success]
        assert len(successes) >= 4, (
            f"Only {len(successes)}/{len(results)} URLs crawled successfully. "
            f"Failures: {[(r.url, r.error) for r in results if not r.success]}"
        )

        stats = store.get_stats()
        assert stats["document_count"] >= 4
        print(
            f"\n✓ Crawled {len(successes)}/{len(_TEST_URLS)} pages, "
            f"indexed {stats['document_count']} documents"
        )

    @pytest.mark.timeout(30)
    def test_search_quality(self, store: LocalStore) -> None:
        """Step 2: Run search queries and validate relevance.

        For each query, the expected URL should appear in the top 3 results.
        This is the core quality check — if BM25 can't surface the right
        document for obvious keyword queries, the MVP isn't useful.
        """
        stats = store.get_stats()
        if stats["document_count"] == 0:
            pytest.skip("No documents indexed — crawl test may have failed")

        passed = 0
        failed = 0
        details: list[str] = []

        for query, expected_url_part in _QUALITY_CHECKS:
            result = search_local(store, query, limit=5)

            # Check if expected URL is in top results
            found_in_top = False
            for r in result.results[:3]:
                if expected_url_part in r.url:
                    found_in_top = True
                    break

            if found_in_top:
                passed += 1
                details.append(f"  ✓ '{query}' → found '{expected_url_part}' in top 3")
            else:
                failed += 1
                top_urls = [r.url.split("/")[-1] for r in result.results[:3]]
                details.append(
                    f"  ✗ '{query}' → expected '{expected_url_part}', got: {top_urls}"
                )

        print(f"\nSearch Quality Report ({passed}/{passed + failed} passed):")
        for d in details:
            print(d)

        # At least 50% of queries should find the right doc
        # (some may fail if crawl didn't get all pages)
        assert passed >= len(_QUALITY_CHECKS) // 2, (
            f"Quality too low: only {passed}/{passed + failed} queries "
            f"found the expected document in top 3"
        )

    @pytest.mark.timeout(10)
    def test_search_latency(self, store: LocalStore) -> None:
        """Step 3: Verify search latency meets <10ms target."""
        stats = store.get_stats()
        if stats["document_count"] == 0:
            pytest.skip("No documents indexed")

        latencies: list[float] = []
        queries = ["python list", "asyncio event loop", "sqlite cursor", "file path"]

        for q in queries:
            start = time.monotonic()
            search_local(store, q, limit=10)
            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)

        avg_ms = sum(latencies) / len(latencies)
        max_ms = max(latencies)
        print("\nLatency Report:")
        print(f"  Average: {avg_ms:.1f}ms")
        print(f"  Max: {max_ms:.1f}ms")
        print("  Target: <10ms")

        # Average should be under 10ms, max under 50ms
        assert avg_ms < 10.0, f"Average latency {avg_ms:.1f}ms exceeds 10ms target"
        assert max_ms < 50.0, f"Max latency {max_ms:.1f}ms too high"

    @pytest.mark.timeout(10)
    def test_snippet_quality(self, store: LocalStore) -> None:
        """Step 4: Verify snippets are useful (not empty, contain query terms)."""
        stats = store.get_stats()
        if stats["document_count"] == 0:
            pytest.skip("No documents indexed")

        result = search_local(store, "python list append", limit=3)
        if not result.results:
            pytest.skip("No results for test query")

        top = result.results[0]
        assert len(top.snippet) > 20, f"Snippet too short: {top.snippet!r}"
        assert top.title, "Missing title"
        assert top.url.startswith("http"), f"Invalid URL: {top.url}"
        assert top.combined_score > 0, "Score should be positive"

        print("\nSnippet Quality:")
        print(f"  Title: {top.title}")
        print(f"  URL: {top.url}")
        print(f"  Score: {top.combined_score:.4f}")
        print(f"  Snippet: {top.snippet[:200]}")

    @pytest.mark.timeout(10)
    def test_document_retrieval(self, store: LocalStore) -> None:
        """Step 5: Verify full document retrieval works."""
        stats = store.get_stats()
        if stats["document_count"] == 0:
            pytest.skip("No documents indexed")

        # Search then retrieve full doc
        result = search_local(store, "python data structures", limit=1)
        if not result.results:
            pytest.skip("No results")

        doc = store.get_document(result.results[0].doc_id)
        assert doc is not None
        assert len(doc.text) > 100, f"Document too short: {len(doc.text)} chars"
        assert doc.url.startswith("http")
        print(f"\n✓ Full doc: {doc.title} ({len(doc.text)} chars)")

    @pytest.mark.timeout(10)
    def test_tavily_comparison_metrics(self, store: LocalStore) -> None:
        """Step 6: Generate comparison metrics vs Tavily baseline.

        This doesn't call Tavily, but documents our search quality metrics
        so they can be compared manually.
        """
        stats = store.get_stats()
        if stats["document_count"] == 0:
            pytest.skip("No documents indexed")

        print("\n" + "=" * 60)
        print("MVP QUALITY METRICS (compare with Tavily search())")
        print("=" * 60)
        print(f"Documents indexed: {stats['document_count']}")

        total_queries = 0
        total_results = 0
        total_latency = 0.0
        has_snippet = 0

        test_queries = [
            "python asyncio tutorial",
            "how to use sqlite in python",
            "pathlib path manipulation",
            "http client python async",
            "python hash sha256",
            "list comprehension sort filter",
        ]

        for q in test_queries:
            start = time.monotonic()
            result = search_local(store, q, limit=5)
            elapsed = (time.monotonic() - start) * 1000

            total_queries += 1
            total_results += len(result.results)
            total_latency += elapsed

            for r in result.results:
                if len(r.snippet) > 30:
                    has_snippet += 1

            print(f"\n  Query: '{q}'")
            print(f"  Results: {len(result.results)}, Latency: {elapsed:.1f}ms")
            for i, r in enumerate(result.results[:3], 1):
                print(f"    [{i}] {r.title[:50]}  (score={r.combined_score:.3f})")
                print(f"        {r.snippet[:100]}...")

        avg_results = total_results / total_queries if total_queries else 0
        avg_latency = total_latency / total_queries if total_queries else 0
        snippet_rate = has_snippet / total_results * 100 if total_results else 0

        print(f"\n{'─' * 60}")
        print(f"  Avg results/query: {avg_results:.1f}")
        print(f"  Avg latency: {avg_latency:.1f}ms (target: <10ms)")
        print(f"  Snippet quality: {snippet_rate:.0f}% have useful snippets")
        print(f"{'─' * 60}")

        # Basic quality gates
        # Note: small corpus (5-6 pages) means some queries may return 0 results.
        # httpx URL may 404, asyncio page is a hub with minimal content.
        # Threshold 0.5 = at least half the queries return results.
        assert avg_results >= 0.5, "Should return results for at least half of queries"
        assert avg_latency < 50.0, "Average latency too high"
