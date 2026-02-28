"""Tests for Common Crawl importer."""

from __future__ import annotations

from pathlib import Path

import pytest

from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.index.commoncrawl import (
    CommonCrawlImporter,
    parse_wet_content,
)
from infomesh.index.local_store import LocalStore

# Sample WET file content (simplified WARC format)
_SAMPLE_WET = """\
WARC/1.0
WARC-Type: warcinfo
WARC-Date: 2024-01-01T00:00:00Z
Content-Length: 0

WARC/1.0
WARC-Type: conversion
WARC-Target-URI: https://example.com/article1
WARC-Date: 2024-01-15T10:30:00Z
Content-Length: 150

This is the extracted text from example.com article one.
It contains enough words to pass the minimum length filter.
Some additional content here to ensure it is not too short for indexing.

WARC/1.0
WARC-Type: conversion
WARC-Target-URI: https://example.com/article2
WARC-Date: 2024-01-15T11:00:00Z
Content-Length: 120

Second article from example dot com about machine learning.
Neural networks and deep learning are transforming the industry.
More text to meet the minimum length requirement for this document.

WARC/1.0
WARC-Type: conversion
WARC-Target-URI: https://example.com/tiny
WARC-Date: 2024-01-15T12:00:00Z
Content-Length: 10

Too short
"""


class TestParseWETContent:
    """WET file parsing."""

    def test_parse_conversion_records(self) -> None:
        records = parse_wet_content(_SAMPLE_WET)
        # Should find 2 conversion records (tiny one is < 50 chars)
        assert len(records) == 2

    def test_record_urls(self) -> None:
        records = parse_wet_content(_SAMPLE_WET)
        urls = {r.url for r in records}
        assert "https://example.com/article1" in urls
        assert "https://example.com/article2" in urls

    def test_record_text_content(self) -> None:
        records = parse_wet_content(_SAMPLE_WET)
        article1 = next(r for r in records if "article1" in r.url)
        assert "extracted text" in article1.text

    def test_skips_warcinfo_records(self) -> None:
        records = parse_wet_content(_SAMPLE_WET)
        # warcinfo record should not be included
        for r in records:
            assert r.url != ""

    def test_skips_short_content(self) -> None:
        records = parse_wet_content(_SAMPLE_WET)
        # The "Too short" record should be filtered out
        urls = {r.url for r in records}
        assert "https://example.com/tiny" not in urls

    def test_empty_input(self) -> None:
        assert parse_wet_content("") == []

    def test_no_conversion_records(self) -> None:
        data = "WARC/1.0\nWARC-Type: warcinfo\n\n"
        assert parse_wet_content(data) == []


class TestCommonCrawlImporter:
    """Common Crawl importer integration tests."""

    @pytest.fixture()
    def store(self, tmp_path: Path) -> LocalStore:
        return LocalStore(db_path=tmp_path / "test.db")

    @pytest.fixture()
    def dedup(self, tmp_path: Path) -> DeduplicatorDB:
        return DeduplicatorDB(str(tmp_path / "dedup.db"))

    @pytest.mark.asyncio()
    async def test_import_wet_file(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB,
        tmp_path: Path,
    ) -> None:
        # Write sample WET to disk
        wet_path = tmp_path / "sample.wet"
        wet_path.write_text(_SAMPLE_WET, encoding="utf-8")

        importer = CommonCrawlImporter(store, dedup)
        stats = await importer.import_wet_file(str(wet_path))

        assert stats.imported == 2
        assert stats.skipped_too_short == 0  # Already filtered by parser
        assert store.get_stats()["document_count"] == 2

    @pytest.mark.asyncio()
    async def test_import_deduplicates(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB,
        tmp_path: Path,
    ) -> None:
        wet_path = tmp_path / "sample.wet"
        wet_path.write_text(_SAMPLE_WET, encoding="utf-8")

        importer = CommonCrawlImporter(store, dedup)
        # Import twice
        await importer.import_wet_file(str(wet_path))
        stats = await importer.import_wet_file(str(wet_path))

        # Second import should skip all
        assert stats.imported == 0
        assert stats.skipped_duplicate == 2

    @pytest.mark.asyncio()
    async def test_import_url_list(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB,
        tmp_path: Path,
    ) -> None:
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "https://example.com/page1\n"
            "https://example.com/page2\n"
            "# comment line\n"
            "https://example.com/page3\n",
            encoding="utf-8",
        )

        importer = CommonCrawlImporter(store, dedup)
        stats = await importer.import_url_list(url_file)

        assert stats.imported == 3
        assert stats.skipped_duplicate == 0

    @pytest.mark.asyncio()
    async def test_import_url_list_deduplicates(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB,
        tmp_path: Path,
    ) -> None:
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "https://example.com/page1\nhttps://example.com/page2\n",
            encoding="utf-8",
        )

        importer = CommonCrawlImporter(store, dedup)
        await importer.import_url_list(url_file)
        stats = await importer.import_url_list(url_file)

        assert stats.imported == 0
        assert stats.skipped_duplicate == 2

    @pytest.mark.asyncio()
    async def test_import_url_list_respects_max(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB,
        tmp_path: Path,
    ) -> None:
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "\n".join(f"https://example.com/page{i}" for i in range(100)),
            encoding="utf-8",
        )

        importer = CommonCrawlImporter(store, dedup)
        stats = await importer.import_url_list(url_file, max_urls=10)

        assert stats.total_records == 10

    @pytest.mark.asyncio()
    async def test_import_wet_gz(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB,
        tmp_path: Path,
    ) -> None:
        """Test gzipped WET file import."""
        import gzip

        wet_gz_path = tmp_path / "sample.wet.gz"
        with gzip.open(wet_gz_path, "wt", encoding="utf-8") as f:
            f.write(_SAMPLE_WET)

        importer = CommonCrawlImporter(store, dedup)
        stats = await importer.import_wet_file(str(wet_gz_path))

        assert stats.imported == 2
