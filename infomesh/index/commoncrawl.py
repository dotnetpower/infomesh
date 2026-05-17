"""Common Crawl data importer — build indexes from public crawl data.

Common Crawl (https://commoncrawl.org) publishes monthly web crawls
on AWS S3 in WARC/WET/WAT format.  This module downloads WET files
(extracted text) filtered by domain, then indexes them locally.

WET format stores pre-extracted text (no HTML parsing needed), making
it ideal for bulk index bootstrapping.

Usage::

    importer = CommonCrawlImporter(store, config)
    stats = await importer.import_from_url_list(urls)
    # or
    stats = await importer.import_wet_file(wet_path)
"""

from __future__ import annotations

import gzip
import io
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
import structlog

from infomesh.crawler import create_ssl_context
from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.hashing import content_hash
from infomesh.index.local_store import LocalStore
from infomesh.types import VectorStoreLike

logger = structlog.get_logger()

# Maximum text size per document (100KB)
_MAX_TEXT_SIZE = 102_400

# Maximum WET payload size to hold in memory. The importer still parses WET
# files in-memory, so keep both compressed downloads and decompressed text bound.
_MAX_WET_FILE_BYTES = 256 * 1024 * 1024
_WET_CHUNK_SIZE = 1024 * 1024

# WARC record boundary
_WARC_RECORD_RE = re.compile(r"^WARC/1\.0\r?\n", re.MULTILINE)
_WARC_HEADER_RE = re.compile(r"^([A-Za-z-]+):\s*(.+?)\s*$", re.MULTILINE)


class _BinaryReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...


def _format_bytes(size: int) -> str:
    """Return a compact MiB string for diagnostics."""
    return f"{size / (1024 * 1024):.0f} MiB"


def _too_large_message(source: str, limit: int | None = None) -> str:
    """Build a consistent size-limit error message."""
    max_bytes = _MAX_WET_FILE_BYTES if limit is None else limit
    return f"WET input exceeds {_format_bytes(max_bytes)} limit: {source}"


def _read_binary_limited(
    stream: _BinaryReader,
    source: str,
    *,
    limit: int | None = None,
) -> bytes:
    """Read a binary stream while enforcing a hard byte limit."""
    max_bytes = _MAX_WET_FILE_BYTES if limit is None else limit
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = stream.read(_WET_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(_too_large_message(source, max_bytes))
        chunks.append(chunk)

    return b"".join(chunks)


def _decode_gzip_limited(data: bytes, source: str) -> str:
    """Decode gzip content while bounding the decompressed payload size."""
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        raw = _read_binary_limited(stream, source)
    return raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ImportStats:
    """Statistics from a Common Crawl import operation."""

    total_records: int
    imported: int
    skipped_duplicate: int
    skipped_too_short: int
    skipped_error: int
    elapsed_ms: float


@dataclass
class WETRecord:
    """A single record from a WET (WARC Extracted Text) file."""

    url: str
    text: str
    date: str
    content_length: int


def parse_wet_content(data: str) -> list[WETRecord]:
    """Parse WET file content into individual records.

    WET files contain WARC-format records with pre-extracted text.
    Each record has a WARC header block followed by text content.

    Args:
        data: Raw WET file text content.

    Returns:
        List of parsed WET records.
    """
    records: list[WETRecord] = []

    # Split on WARC record boundaries
    raw_records = re.split(r"WARC/1\.0\r?\n", data)

    for raw in raw_records:
        if not raw.strip():
            continue

        # Parse headers (up to first double-newline)
        header_end = raw.find("\r\n\r\n")
        if header_end == -1:
            header_end = raw.find("\n\n")
        if header_end == -1:
            continue

        header_text = raw[:header_end]
        body = raw[header_end:].strip()

        # Extract key headers
        headers: dict[str, str] = {}
        for match in _WARC_HEADER_RE.finditer(header_text):
            headers[match.group(1).lower()] = match.group(2)

        warc_type = headers.get("warc-type", "")
        if warc_type != "conversion":
            continue  # Only process text conversion records

        url = headers.get("warc-target-uri", "")
        date = headers.get("warc-date", "")
        length = int(headers.get("content-length", "0"))

        if url and body and len(body) >= 50:
            records.append(
                WETRecord(
                    url=url,
                    text=body[:_MAX_TEXT_SIZE],
                    date=date,
                    content_length=length,
                )
            )

    return records


class CommonCrawlImporter:
    """Import documents from Common Crawl WET files or URL lists.

    Supports two import modes:
      1. **WET file import**: Download and parse WET files directly.
      2. **URL list import**: Read a list of URLs and crawl them.
    """

    def __init__(
        self,
        store: LocalStore,
        dedup: DeduplicatorDB | None = None,
        *,
        vector_store: VectorStoreLike | None = None,
    ) -> None:
        self._store = store
        self._dedup = dedup or DeduplicatorDB()
        self._vector_store = vector_store

    async def import_wet_file(self, path_or_url: str) -> ImportStats:
        """Import documents from a WET file (local or remote).

        Args:
            path_or_url: Local file path or HTTP(S) URL to a .wet or .wet.gz file.

        Returns:
            ImportStats with counts of processed records.
        """
        start = time.monotonic()

        if path_or_url.startswith(("http://", "https://")):
            data = await self._download_wet(path_or_url)
        else:
            data = self._read_local_wet(path_or_url)

        records = parse_wet_content(data)

        imported = 0
        skipped_dup = 0
        skipped_short = 0
        skipped_err = 0

        for record in records:
            try:
                if len(record.text.strip()) < 50:
                    skipped_short += 1
                    continue

                text_hash = content_hash(record.text)

                if self._dedup.is_content_seen(text_hash):
                    skipped_dup += 1
                    continue

                if self._dedup.is_near_duplicate(record.text):
                    skipped_dup += 1
                    continue

                # Derive title from first line or URL
                title = record.text.split("\n", 1)[0][:200].strip()
                if not title or len(title) < 5:
                    from urllib.parse import urlparse

                    parsed = urlparse(record.url)
                    title = parsed.path.rsplit("/", 1)[-1] or parsed.netloc

                raw_hash = content_hash(record.url + record.date)

                doc_id = self._store.add_document(
                    url=record.url,
                    title=title,
                    text=record.text,
                    raw_html_hash=raw_hash,
                    text_hash=text_hash,
                    language=None,
                )

                if doc_id is None:
                    skipped_dup += 1
                    continue

                self._dedup.mark_seen(record.url, text_hash, record.text)

                # Optional vector indexing
                if self._vector_store is not None and doc_id is not None:
                    from infomesh.index.vector_store import VectorStore

                    if isinstance(self._vector_store, VectorStore):
                        self._vector_store.add_document(
                            doc_id=doc_id,
                            url=record.url,
                            title=title,
                            text=record.text,
                            language=None,
                        )

                imported += 1

            except Exception:
                logger.debug("wet_record_error", url=record.url, exc_info=True)
                skipped_err += 1

        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "wet_import_complete",
            total=len(records),
            imported=imported,
            skipped_dup=skipped_dup,
            skipped_short=skipped_short,
        )

        return ImportStats(
            total_records=len(records),
            imported=imported,
            skipped_duplicate=skipped_dup,
            skipped_too_short=skipped_short,
            skipped_error=skipped_err,
            elapsed_ms=elapsed,
        )

    async def import_url_list(
        self,
        path: str | Path,
        *,
        max_urls: int = 10_000,
    ) -> ImportStats:
        """Import URLs from a text file (one URL per line).

        Each URL is looked up in the store; if not found, it's added
        to the crawl queue but NOT actually crawled here (that's the
        crawler's job).  This method only registers the URLs.

        Args:
            path: Path to text file with one URL per line.
            max_urls: Maximum URLs to process.

        Returns:
            ImportStats with counts.
        """
        start = time.monotonic()
        path = Path(path)

        urls: list[str] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                url = line.strip()
                if url and not url.startswith("#"):
                    urls.append(url)
                if len(urls) >= max_urls:
                    break

        imported = 0
        skipped = 0

        for url in urls:
            if self._dedup.is_url_seen(url):
                skipped += 1
                continue
            # Mark as seen so it won't be re-queued
            self._dedup.mark_seen(url, "pending")
            imported += 1

        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "url_list_import_complete",
            total=len(urls),
            imported=imported,
            skipped=skipped,
        )

        return ImportStats(
            total_records=len(urls),
            imported=imported,
            skipped_duplicate=skipped,
            skipped_too_short=0,
            skipped_error=0,
            elapsed_ms=elapsed,
        )

    async def _download_wet(self, url: str) -> str:
        """Download a WET file from a URL (supports .gz)."""
        async with httpx.AsyncClient(
            timeout=120.0, verify=create_ssl_context()
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()

                content_length = resp.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if declared_size > _MAX_WET_FILE_BYTES:
                        raise ValueError(_too_large_message(url))

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=_WET_CHUNK_SIZE):
                    total += len(chunk)
                    if total > _MAX_WET_FILE_BYTES:
                        raise ValueError(_too_large_message(url))
                    chunks.append(chunk)

            raw = b"".join(chunks)

            if url.endswith(".gz"):
                return _decode_gzip_limited(raw, url)
            return raw.decode("utf-8", errors="replace")

    def _read_local_wet(self, path: str) -> str:
        """Read a local WET file (supports .gz)."""
        source = str(path)
        file_size = Path(path).stat().st_size
        if file_size > _MAX_WET_FILE_BYTES:
            raise ValueError(_too_large_message(source))

        if path.endswith(".gz"):
            with open(path, "rb") as f:
                compressed = _read_binary_limited(f, source)
            return _decode_gzip_limited(compressed, source)
        with open(path, "rb") as f:
            raw = _read_binary_limited(f, source)
        return raw.decode("utf-8", errors="replace")
