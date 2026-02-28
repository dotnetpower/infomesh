"""Tests for the search index."""

from __future__ import annotations

from infomesh.index.local_store import LocalStore


def test_add_and_search() -> None:
    """Adding a document should make it searchable."""
    store = LocalStore()

    doc_id = store.add_document(
        url="https://example.com/python-async",
        title="Python Async Programming",
        text=(
            "Python asyncio provides infrastructure for writing"
            " concurrent code using the async/await syntax."
        ),
        raw_html_hash="abc123",
        text_hash="def456",
        language="en",
    )

    assert doc_id is not None

    results = store.search("python async")
    assert len(results) == 1
    assert results[0].url == "https://example.com/python-async"
    assert results[0].title == "Python Async Programming"

    store.close()


def test_duplicate_url_rejected() -> None:
    """Duplicate URLs should be rejected."""
    store = LocalStore()

    doc_id1 = store.add_document(
        url="https://example.com/page",
        title="Page 1",
        text="Some content for full text search indexing.",
        raw_html_hash="hash1",
        text_hash="hash_a",
    )
    doc_id2 = store.add_document(
        url="https://example.com/page",
        title="Page 1 duplicate",
        text="Different content but same URL.",
        raw_html_hash="hash2",
        text_hash="hash_b",
    )

    assert doc_id1 is not None
    assert doc_id2 is None  # Duplicate rejected
    store.close()


def test_search_no_results() -> None:
    """Searching for nonexistent terms returns empty list."""
    store = LocalStore()
    results = store.search("xyznonexistent")
    assert results == []
    store.close()


def test_multiple_documents_ranked() -> None:
    """Multiple matching documents should be ranked by BM25."""
    store = LocalStore()

    store.add_document(
        url="https://example.com/rust",
        title="Rust Language",
        text=(
            "Rust is a systems programming language"
            " focused on safety and performance."
        ),
        raw_html_hash="h1",
        text_hash="t1",
    )
    store.add_document(
        url="https://example.com/rust-async",
        title="Async Rust Programming",
        text="Rust async programming with tokio runtime for concurrent systems.",
        raw_html_hash="h2",
        text_hash="t2",
    )
    store.add_document(
        url="https://example.com/python",
        title="Python Language",
        text=(
            "Python is a high-level programming language"
            " for general purpose development."
        ),
        raw_html_hash="h3",
        text_hash="t3",
    )

    results = store.search("rust programming")
    assert len(results) >= 2
    # Top results should be about Rust
    assert "rust" in results[0].url

    store.close()


def test_get_document_by_url() -> None:
    """Retrieving a document by URL should work."""
    store = LocalStore()

    store.add_document(
        url="https://example.com/doc",
        title="Test Doc",
        text="This is a test document for retrieval.",
        raw_html_hash="rh1",
        text_hash="th1",
    )

    doc = store.get_document_by_url("https://example.com/doc")
    assert doc is not None
    assert doc.title == "Test Doc"
    assert "test document" in doc.text

    store.close()


def test_get_stats() -> None:
    """Stats should reflect document count."""
    store = LocalStore()
    assert store.get_stats()["document_count"] == 0

    store.add_document(
        url="https://example.com/1",
        title="Doc 1",
        text="First document content for indexing.",
        raw_html_hash="r1",
        text_hash="t1",
    )
    assert store.get_stats()["document_count"] == 1

    store.close()


def test_compression_roundtrip() -> None:
    """Documents should survive compression and decompression."""
    store = LocalStore(compression_enabled=True, compression_level=3)
    original_text = "This is a test document with enough content to compress. " * 10

    doc_id = store.add_document(
        url="https://example.com/compressed",
        title="Compressed Doc",
        text=original_text,
        raw_html_hash="cr1",
        text_hash="ct1",
    )
    assert doc_id is not None

    doc = store.get_document(doc_id)
    assert doc is not None
    assert doc.text == original_text

    # Search should still work with compression
    results = store.search("compress")
    assert len(results) >= 1

    store.close()


def test_compression_disabled_still_works() -> None:
    """Store should work normally when compression is disabled."""
    store = LocalStore(compression_enabled=False)
    text = "Normal uncompressed text document."

    doc_id = store.add_document(
        url="https://example.com/uncompressed",
        title="Uncompressed",
        text=text,
        raw_html_hash="ur1",
        text_hash="ut1",
    )
    doc = store.get_document(doc_id)
    assert doc is not None
    assert doc.text == text

    store.close()


# ── update_document tests ──────────────────────────────────────────────


def test_update_document_single_field() -> None:
    """Updating a single field should work without affecting others."""
    store = LocalStore()
    store.add_document(
        url="https://example.com/update1",
        title="Original",
        text="Original text content for search.",
        raw_html_hash="h1",
        text_hash="th1",
    )
    updated = store.update_document(
        "https://example.com/update1",
        title="Updated Title",
    )
    assert updated is True
    doc = store.get_document_by_url("https://example.com/update1")
    assert doc is not None
    assert doc.title == "Updated Title"
    assert doc.text == "Original text content for search."
    store.close()


def test_update_document_multiple_fields() -> None:
    """Updating multiple fields at once should work."""
    store = LocalStore()
    store.add_document(
        url="https://example.com/update2",
        title="Old",
        text="Old text for full text search.",
        raw_html_hash="h2",
        text_hash="th2",
    )
    updated = store.update_document(
        "https://example.com/update2",
        etag='"v42"',
        last_modified="Mon, 01 Jan 2024",
        recrawl_interval=3600,
        change_frequency=0.5,
    )
    assert updated is True
    store.close()


def test_update_document_nonexistent() -> None:
    """Updating a nonexistent URL should return False."""
    store = LocalStore()
    updated = store.update_document(
        "https://example.com/nonexistent",
        title="Nope",
    )
    assert updated is False
    store.close()


def test_update_document_no_fields() -> None:
    """Calling with no fields should return False."""
    store = LocalStore()
    store.add_document(
        url="https://example.com/noop",
        title="NoOp",
        text="Document for no-op update test.",
        raw_html_hash="h3",
        text_hash="th3",
    )
    updated = store.update_document("https://example.com/noop")
    assert updated is False
    store.close()


# ── soft_delete tests ──────────────────────────────────────────────────


def test_soft_delete_existing() -> None:
    """Deleting an existing document should remove it from search."""
    store = LocalStore()
    store.add_document(
        url="https://example.com/delete-me",
        title="To Delete",
        text="This document will be deleted from index.",
        raw_html_hash="hd1",
        text_hash="thd1",
    )
    deleted = store.soft_delete("https://example.com/delete-me")
    assert deleted is True
    # Should not appear in search
    results = store.search("deleted from index")
    assert len(results) == 0
    store.close()


def test_soft_delete_nonexistent() -> None:
    """Deleting a nonexistent URL should return False."""
    store = LocalStore()
    deleted = store.soft_delete("https://example.com/never-existed")
    assert deleted is False
    store.close()


# ── get_recrawl_candidates tests ───────────────────────────────────────


def test_get_recrawl_candidates_returns_docs() -> None:
    """Should return documents with recrawl metadata."""
    store = LocalStore()
    store.add_document(
        url="https://example.com/recrawl1",
        title="Recrawl Test 1",
        text="Content for recrawl candidate test one.",
        raw_html_hash="hr1",
        text_hash="thr1",
    )
    store.add_document(
        url="https://example.com/recrawl2",
        title="Recrawl Test 2",
        text="Content for recrawl candidate test two.",
        raw_html_hash="hr2",
        text_hash="thr2",
    )
    candidates = store.get_recrawl_candidates(limit=10)
    assert len(candidates) == 2
    # Check required keys exist
    keys = candidates[0].keys()
    assert "url" in keys
    assert "text_hash" in keys
    assert "recrawl_interval" in keys
    assert "change_frequency" in keys
    store.close()


def test_get_recrawl_candidates_excludes_stale() -> None:
    """Documents with stale_count >= 3 should be excluded."""
    store = LocalStore()
    store.add_document(
        url="https://example.com/stale",
        title="Stale Doc",
        text="Document that has gone stale after failures.",
        raw_html_hash="hs1",
        text_hash="ths1",
    )
    # Mark as stale
    store.update_document("https://example.com/stale", stale_count=3)
    candidates = store.get_recrawl_candidates(limit=10)
    urls = [c["url"] for c in candidates]
    assert "https://example.com/stale" not in urls
    store.close()


def test_get_recrawl_candidates_limit() -> None:
    """Should respect the limit parameter."""
    store = LocalStore()
    for i in range(5):
        store.add_document(
            url=f"https://example.com/limit-{i}",
            title=f"Limit Test {i}",
            text=f"Content for limit test number {i} document.",
            raw_html_hash=f"hl{i}",
            text_hash=f"thl{i}",
        )
    candidates = store.get_recrawl_candidates(limit=3)
    assert len(candidates) == 3
    store.close()
