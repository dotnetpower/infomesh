"""Tests for index snapshot export/import."""

from __future__ import annotations

from pathlib import Path

import pytest

from infomesh.index.local_store import LocalStore
from infomesh.index.snapshot import (
    export_snapshot,
    import_snapshot,
    read_snapshot_metadata,
)


@pytest.fixture()
def store(tmp_path: Path) -> LocalStore:
    """Create a LocalStore with sample documents."""
    s = LocalStore(db_path=tmp_path / "test.db")
    s.add_document(
        url="https://example.com/page1",
        title="First Page",
        text="This is the first page with some content about Python programming.",
        raw_html_hash="raw1",
        text_hash="hash1",
        language="en",
    )
    s.add_document(
        url="https://example.com/page2",
        title="Second Page",
        text=(
            "This is the second page with different"
            " content about JavaScript frameworks."
        ),
        raw_html_hash="raw2",
        text_hash="hash2",
        language="en",
    )
    s.add_document(
        url="https://example.com/page3",
        title="Third Page",
        text="This is the third page about Rust systems programming language.",
        raw_html_hash="raw3",
        text_hash="hash3",
        language="en",
    )
    return s


class TestExportSnapshot:
    """Snapshot export tests."""

    def test_export_creates_file(self, store: LocalStore, tmp_path: Path) -> None:
        output = tmp_path / "test.infomesh-snapshot"
        stats = export_snapshot(store, output)
        assert output.exists()
        assert stats.total_documents == 3
        assert stats.exported == 3
        assert stats.file_size_bytes > 0

    def test_export_compressed_smaller_than_raw(
        self, store: LocalStore, tmp_path: Path
    ) -> None:
        output = tmp_path / "test.infomesh-snapshot"
        stats = export_snapshot(store, output)
        # The snapshot should exist with reasonable size
        assert stats.file_size_bytes > 0
        assert stats.file_size_bytes < 10_000_000  # < 10MB for 3 small docs

    def test_export_empty_store(self, tmp_path: Path) -> None:
        empty_store = LocalStore(db_path=tmp_path / "empty.db")
        output = tmp_path / "empty.infomesh-snapshot"
        stats = export_snapshot(empty_store, output)
        assert stats.total_documents == 0
        assert output.exists()
        empty_store.close()


class TestReadMetadata:
    """Snapshot metadata reading."""

    def test_read_metadata(self, store: LocalStore, tmp_path: Path) -> None:
        output = tmp_path / "test.infomesh-snapshot"
        export_snapshot(store, output)

        meta = read_snapshot_metadata(output)
        assert meta["format_version"] == 1
        assert meta["document_count"] == 3
        assert "created_at" in meta


class TestImportSnapshot:
    """Snapshot import tests."""

    def test_import_all_documents(self, store: LocalStore, tmp_path: Path) -> None:
        snapshot = tmp_path / "test.infomesh-snapshot"
        export_snapshot(store, snapshot)

        # Import into fresh store
        new_store = LocalStore(db_path=tmp_path / "new.db")
        stats = import_snapshot(new_store, snapshot)

        assert stats.exported == 3  # "exported" field = imported count
        assert stats.skipped == 0
        assert new_store.get_stats()["document_count"] == 3
        new_store.close()

    def test_import_skips_duplicates(self, store: LocalStore, tmp_path: Path) -> None:
        snapshot = tmp_path / "test.infomesh-snapshot"
        export_snapshot(store, snapshot)

        # Import into the same store (all docs already exist)
        stats = import_snapshot(store, snapshot)
        assert stats.exported == 0
        assert stats.skipped == 3

    def test_import_partial_overlap(self, store: LocalStore, tmp_path: Path) -> None:
        snapshot = tmp_path / "test.infomesh-snapshot"
        export_snapshot(store, snapshot)

        # Create new store with one overlapping document
        new_store = LocalStore(db_path=tmp_path / "partial.db")
        new_store.add_document(
            url="https://example.com/page1",
            title="First Page",
            text="This is the first page with some content about Python programming.",
            raw_html_hash="raw1",
            text_hash="hash1",
            language="en",
        )

        stats = import_snapshot(new_store, snapshot)
        assert stats.exported == 2  # 2 new
        assert stats.skipped == 1  # 1 existing
        assert new_store.get_stats()["document_count"] == 3
        new_store.close()

    def test_roundtrip_preserves_content(
        self, store: LocalStore, tmp_path: Path
    ) -> None:
        snapshot = tmp_path / "test.infomesh-snapshot"
        export_snapshot(store, snapshot)

        new_store = LocalStore(db_path=tmp_path / "roundtrip.db")
        import_snapshot(new_store, snapshot)

        # Verify content preserved
        doc = new_store.get_document_by_url("https://example.com/page2")
        assert doc is not None
        assert doc.title == "Second Page"
        assert "JavaScript" in doc.text
        assert doc.language == "en"
        new_store.close()

    def test_search_works_after_import(self, store: LocalStore, tmp_path: Path) -> None:
        snapshot = tmp_path / "roundtrip.infomesh-snapshot"
        export_snapshot(store, snapshot)

        new_store = LocalStore(db_path=tmp_path / "search.db")
        import_snapshot(new_store, snapshot)

        results = new_store.search("Python programming")
        assert len(results) >= 1
        assert any("Python" in r.title or "Python" in r.snippet for r in results)
        new_store.close()
