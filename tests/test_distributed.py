"""Tests for the distributed inverted index module.

Tests keyword extraction and DHT publish/query logic using a mock DHT.
"""

from __future__ import annotations

import pytest

from infomesh.index.distributed import (
    _STOP_WORDS,
    MIN_KEYWORD_LENGTH,
    DistributedIndex,
    extract_keywords,
)


class MockInfoMeshDHT:
    """Mock InfoMeshDHT for testing distributed index."""

    def __init__(self) -> None:
        self._index: dict[str, list[dict]] = {}

    async def publish_keyword(self, keyword: str, pointers: list[dict]) -> bool:
        if keyword not in self._index:
            self._index[keyword] = []
        self._index[keyword].extend(pointers)
        return True

    async def query_keyword(self, keyword: str) -> list[dict]:
        return self._index.get(keyword, [])


@pytest.fixture
def mock_dht() -> MockInfoMeshDHT:
    return MockInfoMeshDHT()


@pytest.fixture
def dist_index(mock_dht: MockInfoMeshDHT) -> DistributedIndex:
    return DistributedIndex(mock_dht, "test-peer")


class TestExtractKeywords:
    """Test keyword extraction."""

    def test_basic_extraction(self) -> None:
        text = "Python is a great programming language for async programming"
        keywords = extract_keywords(text)
        assert "python" in keywords
        assert "programming" in keywords
        assert "language" in keywords

    def test_stop_words_excluded(self) -> None:
        text = "the quick brown fox jumps over the lazy dog"
        keywords = extract_keywords(text)
        for kw in keywords:
            assert kw not in _STOP_WORDS

    def test_short_words_excluded(self) -> None:
        text = "I am a cat in a hat on a mat"
        keywords = extract_keywords(text)
        for kw in keywords:
            assert len(kw) >= MIN_KEYWORD_LENGTH

    def test_max_keywords_limit(self) -> None:
        text = " ".join(f"word{i}" for i in range(200))
        keywords = extract_keywords(text, max_keywords=10)
        assert len(keywords) <= 10

    def test_empty_text(self) -> None:
        assert extract_keywords("") == []

    def test_frequency_ordering(self) -> None:
        text = "python python python rust rust javascript"
        keywords = extract_keywords(text)
        assert keywords[0] == "python"  # Most frequent
        assert keywords[1] == "rust"

    def test_case_insensitive(self) -> None:
        text = "Python PYTHON python"
        keywords = extract_keywords(text)
        assert "python" in keywords


class TestDistributedIndex:
    """Test DistributedIndex publish and query."""

    @pytest.mark.asyncio
    async def test_publish_document(self, dist_index: DistributedIndex) -> None:
        count = await dist_index.publish_document(
            doc_id=1,
            url="https://example.com/python",
            title="Python Tutorial",
            text="Python is a great programming language for building applications",
        )
        assert count > 0
        assert dist_index.stats.documents_published == 1
        assert dist_index.stats.keywords_published == count

    @pytest.mark.asyncio
    async def test_query_published_document(
        self, dist_index: DistributedIndex, mock_dht: MockInfoMeshDHT
    ) -> None:
        await dist_index.publish_document(
            doc_id=1,
            url="https://example.com/python",
            title="Python Tutorial",
            text="Python programming language tutorial guide",
        )

        results = await dist_index.query(["python"])
        assert len(results) > 0
        assert results[0].peer_id == "test-peer"
        assert results[0].doc_id == 1
        assert results[0].url == "https://example.com/python"

    @pytest.mark.asyncio
    async def test_query_multiple_keywords(self, dist_index: DistributedIndex) -> None:
        await dist_index.publish_document(
            doc_id=1,
            url="https://example.com/python",
            title="Python Async",
            text="Python asyncio programming with async await patterns",
        )

        results = await dist_index.query(["python", "asyncio"])
        assert len(results) > 0
        # Score should be higher with multiple matching keywords
        assert results[0].score > 0

    @pytest.mark.asyncio
    async def test_query_nonexistent(self, dist_index: DistributedIndex) -> None:
        results = await dist_index.query(["nonexistent_keyword_xyz"])
        assert results == []

    @pytest.mark.asyncio
    async def test_publish_batch(self, dist_index: DistributedIndex) -> None:
        docs = [
            {
                "doc_id": 1,
                "url": "https://a.com",
                "title": "A",
                "text": "Python programming",
            },
            {
                "doc_id": 2,
                "url": "https://b.com",
                "title": "B",
                "text": "Rust systems programming",
            },
        ]
        total = await dist_index.publish_batch(docs)
        assert total > 0
        assert dist_index.stats.documents_published == 2

    @pytest.mark.asyncio
    async def test_deduplicate_query_results(
        self, dist_index: DistributedIndex
    ) -> None:
        """Same doc+peer should be deduplicated in query results."""
        # Publish same doc under multiple keywords
        await dist_index.publish_document(
            doc_id=1,
            url="https://example.com/python",
            title="Python",
            text="Python programming language tutorial Python guide Python",
        )

        results = await dist_index.query(["python", "programming", "tutorial"])
        # Should be deduplicated by (peer_id, doc_id)
        unique_keys = {(r.peer_id, r.doc_id) for r in results}
        assert len(unique_keys) == len(results)

    @pytest.mark.asyncio
    async def test_stats_tracking(self, dist_index: DistributedIndex) -> None:
        await dist_index.publish_document(
            doc_id=1,
            url="https://example.com",
            title="Test",
            text="Python programming language",
        )
        await dist_index.query(["python"])

        assert dist_index.stats.documents_published == 1
        assert dist_index.stats.queries_performed == 1
        assert dist_index.stats.pointers_found > 0

    @pytest.mark.asyncio
    async def test_empty_text_no_publish(self, dist_index: DistributedIndex) -> None:
        count = await dist_index.publish_document(
            doc_id=1,
            url="https://example.com",
            title="Empty",
            text="",
        )
        assert count == 0
