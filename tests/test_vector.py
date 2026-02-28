"""Tests for vector store (ChromaDB) and hybrid search merge."""

from __future__ import annotations

import pytest

chromadb = pytest.importorskip("chromadb", reason="chromadb not installed")

from infomesh.index.vector_store import VectorSearchResult, VectorStore
from infomesh.search.merge import merge_results

# ---------------------------------------------------------------------------
# VectorStore tests (ChromaDB ephemeral, no sentence-transformers needed)
# ---------------------------------------------------------------------------


class TestVectorStoreBasic:
    """Tests using ChromaDB built-in default embeddings (no model download)."""

    def test_init_ephemeral(self) -> None:
        """VectorStore can be created with ephemeral (in-memory) client."""
        store = VectorStore(persist_dir=None, model_name="all-MiniLM-L6-v2")
        stats = store.get_stats()
        assert stats["document_count"] == 0
        store.close()

    def test_init_persistent(self, tmp_path) -> None:
        """VectorStore creates persist directory."""
        persist = tmp_path / "chroma"
        store = VectorStore(persist_dir=persist, model_name="all-MiniLM-L6-v2")
        assert persist.exists()
        store.close()


class TestVectorStoreWithEmbeddings:
    """Tests that require sentence-transformers model (downloads ~80MB on first run)."""

    @pytest.fixture
    def store(self) -> VectorStore:
        import uuid

        # Unique collection name per test to avoid cross-test contamination
        collection = f"test_{uuid.uuid4().hex[:8]}"
        s = VectorStore(persist_dir=None, collection_name=collection)
        yield s
        s.close()

    def test_add_and_search(self, store: VectorStore) -> None:
        """Add docs and verify semantic search returns them."""
        store.add_document(
            doc_id=1,
            url="https://example.com/python",
            title="Python Tutorial",
            text=(
                "Python is a programming language used"
                " for web development and data science."
            ),
        )
        store.add_document(
            doc_id=2,
            url="https://example.com/cooking",
            title="Cooking Guide",
            text="How to make pasta: boil water, add noodles, cook for 10 minutes.",
        )

        # Semantic search should rank "programming" closer to Python doc
        results = store.search("how to code in python", limit=5)
        assert len(results) >= 1
        assert results[0].url == "https://example.com/python"
        assert results[0].score > 0

    def test_search_empty_collection(self, store: VectorStore) -> None:
        """Search on empty collection returns empty list."""
        results = store.search("anything", limit=5)
        assert results == []

    def test_add_duplicate_upserts(self, store: VectorStore) -> None:
        """Adding same doc_id again should upsert, not duplicate."""
        store.add_document(
            doc_id=1, url="https://x.com/a", title="V1", text="Version one"
        )
        store.add_document(
            doc_id=1, url="https://x.com/a", title="V2", text="Version two updated"
        )

        # Search should only return one result for doc_id=1
        results = store.search("version", limit=10)
        doc_ids = [r.doc_id for r in results]
        assert doc_ids.count("1") == 1

    def test_delete_document(self, store: VectorStore) -> None:
        """Deleted docs should not appear in search."""
        store.add_document(
            doc_id=1,
            url="https://x.com/a",
            title="Delete Target",
            text="Content to delete here",
        )
        assert store.search("delete target content", limit=5) != []

        store.delete_document(1)

        # After deletion, searching for the deleted content should return nothing
        results = store.search("delete target content", limit=5)
        matching = [r for r in results if r.doc_id == "1"]
        assert matching == []

    def test_min_score_filter(self, store: VectorStore) -> None:
        """min_score should filter low-similarity results."""
        store.add_document(
            doc_id=1,
            url="https://x.com/a",
            title="Cats",
            text="Cats are furry animals.",
        )

        # Completely unrelated query with high threshold
        results = store.search("quantum physics black holes", limit=5, min_score=0.9)
        # Should be empty or at least filtered
        for r in results:
            assert r.score >= 0.9

    def test_language_metadata(self, store: VectorStore) -> None:
        """Language metadata should be stored and returned."""
        store.add_document(
            doc_id=1,
            url="https://x.com/ko",
            title="한국어 문서",
            text="이것은 한국어 테스트입니다.",
            language="ko",
        )
        results = store.search("한국어 테스트", limit=5)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# Merge tests (no embedding needed — uses pre-built result objects)
# ---------------------------------------------------------------------------


class TestMergeResults:
    """Tests for Reciprocal Rank Fusion merge logic."""

    def _make_fts(self, doc_id: int, url: str, score: float) -> object:
        from infomesh.index.local_store import SearchResult

        return SearchResult(
            doc_id=doc_id,
            url=url,
            title=f"FTS Doc {doc_id}",
            snippet=f"FTS snippet for {url}",
            score=score,
            language=None,
            crawled_at=0.0,
        )

    def _make_vec(self, doc_id: int, url: str, score: float) -> VectorSearchResult:
        return VectorSearchResult(
            doc_id=str(doc_id),
            url=url,
            title=f"Vec Doc {doc_id}",
            text_preview=f"Vector preview for {url}",
            score=score,
        )

    def test_fts_only(self) -> None:
        """Merge with only FTS results."""
        fts = [self._make_fts(1, "https://a.com", 5.0)]
        merged = merge_results(fts, [])
        assert len(merged) == 1
        assert merged[0].source == "fts"
        assert merged[0].fts_score == 5.0
        assert merged[0].vector_score is None

    def test_vector_only(self) -> None:
        """Merge with only vector results."""
        vec = [self._make_vec(1, "https://a.com", 0.95)]
        merged = merge_results([], vec)
        assert len(merged) == 1
        assert merged[0].source == "vector"
        assert merged[0].vector_score == 0.95
        assert merged[0].fts_score is None

    def test_hybrid_merge(self) -> None:
        """Document appearing in both FTS and vector should be marked hybrid."""
        fts = [self._make_fts(1, "https://a.com", 5.0)]
        vec = [self._make_vec(1, "https://a.com", 0.9)]
        merged = merge_results(fts, vec)
        assert len(merged) == 1
        assert merged[0].source == "hybrid"
        assert merged[0].fts_score == 5.0
        assert merged[0].vector_score == 0.9

    def test_hybrid_ranks_shared_higher(self) -> None:
        """Documents in both sources should rank higher than single-source."""
        fts = [
            self._make_fts(1, "https://a.com", 5.0),
            self._make_fts(2, "https://b.com", 4.0),
        ]
        vec = [
            self._make_vec(1, "https://a.com", 0.9),
            self._make_vec(3, "https://c.com", 0.95),
        ]
        merged = merge_results(fts, vec, limit=10)

        # a.com appears in both → should be ranked first
        assert merged[0].url == "https://a.com"
        assert merged[0].source == "hybrid"

    def test_limit_applied(self) -> None:
        """Limit should cap returned results."""
        fts = [self._make_fts(i, f"https://fts{i}.com", 5.0 - i) for i in range(5)]
        vec = [
            self._make_vec(i + 10, f"https://vec{i}.com", 0.9 - i * 0.1)
            for i in range(5)
        ]
        merged = merge_results(fts, vec, limit=3)
        assert len(merged) == 3

    def test_empty_inputs(self) -> None:
        """Both empty → empty result."""
        merged = merge_results([], [])
        assert merged == []

    def test_weights_influence_ranking(self) -> None:
        """Higher weight for a source should boost its results."""
        fts = [self._make_fts(1, "https://fts-only.com", 5.0)]
        vec = [self._make_vec(2, "https://vec-only.com", 0.9)]

        # Heavily favor vector
        merged = merge_results(fts, vec, fts_weight=0.1, vector_weight=10.0)
        assert merged[0].url == "https://vec-only.com"

        # Heavily favor FTS
        merged = merge_results(fts, vec, fts_weight=10.0, vector_weight=0.1)
        assert merged[0].url == "https://fts-only.com"
