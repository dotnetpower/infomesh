"""Index snapshot export/import — zstd-compressed portable index packs.

Export creates a single ``.infomesh-snapshot`` file containing:
  1. A metadata header (JSON, zstd compressed)
  2. All documents from the SQLite local store (msgpack, zstd compressed)

Import reads a snapshot file and merges documents into the local index,
skipping duplicates by ``text_hash``.

File format::

    [4 bytes: header length (big-endian uint32)]
    [header_length bytes: zstd-compressed JSON metadata]
    [remaining bytes: zstd-compressed msgpack array of documents]
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgpack
import structlog

from infomesh.compression.zstd import LEVEL_SNAPSHOT, Compressor
from infomesh.index.local_store import LocalStore
from infomesh.types import VectorStoreLike

logger = structlog.get_logger()

# Snapshot file magic / extension
SNAPSHOT_EXTENSION = ".infomesh-snapshot"

# Current format version
_FORMAT_VERSION = 1

# Admin import guard: snapshots are read into memory after this check.
_MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024 * 1024
_MAX_HEADER_LEN = 10 * 1024 * 1024
_MAX_SNAPSHOT_DOCUMENTS = 100_000


@dataclass(frozen=True)
class SnapshotStats:
    """Statistics about a snapshot operation."""

    total_documents: int
    exported: int  # for export: same as total; for import: newly added
    skipped: int  # for import: duplicates skipped
    file_size_bytes: int
    elapsed_ms: float


def export_snapshot(
    store: LocalStore,
    output_path: Path | str,
    *,
    compression_level: int = LEVEL_SNAPSHOT,
) -> SnapshotStats:
    """Export the entire local index to a zstd-compressed snapshot file.

    Args:
        store: Local document store to export from.
        output_path: Destination file path.
        compression_level: zstd compression level (default: 12).

    Returns:
        SnapshotStats with export metrics.
    """
    start = time.monotonic()
    output_path = Path(output_path)
    compressor = Compressor(level=compression_level)

    # Collect all documents
    documents = store.export_documents()

    # Build metadata header
    metadata = {
        "format_version": _FORMAT_VERSION,
        "created_at": time.time(),
        "document_count": len(documents),
    }
    header_bytes = compressor.compress(json.dumps(metadata).encode("utf-8"))

    # Serialize documents with msgpack then compress
    doc_bytes = compressor.compress(msgpack.packb(documents, use_bin_type=True))

    # Write file: [header_len (4 bytes)] [header] [documents]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(struct.pack(">I", len(header_bytes)))
        f.write(header_bytes)
        f.write(doc_bytes)

    elapsed = (time.monotonic() - start) * 1000
    file_size = output_path.stat().st_size

    logger.info(
        "snapshot_exported",
        documents=len(documents),
        file_size=file_size,
        path=str(output_path),
    )

    return SnapshotStats(
        total_documents=len(documents),
        exported=len(documents),
        skipped=0,
        file_size_bytes=file_size,
        elapsed_ms=elapsed,
    )


def read_snapshot_metadata(snapshot_path: Path | str) -> dict[str, Any]:
    """Read only the metadata header from a snapshot file.

    Args:
        snapshot_path: Path to the snapshot file.

    Returns:
        Metadata dictionary.
    """
    compressor = Compressor(level=LEVEL_SNAPSHOT)
    with open(snapshot_path, "rb") as f:
        header_prefix = f.read(4)
        if len(header_prefix) != 4:
            raise ValueError("Snapshot file is too small to contain a header")
        header_len = struct.unpack(">I", header_prefix)[0]
        if header_len > _MAX_HEADER_LEN:
            raise ValueError(
                f"Snapshot header too large: {header_len} bytes (max {_MAX_HEADER_LEN})"
            )
        header_compressed = f.read(header_len)

    header_json = compressor.decompress(header_compressed)
    return dict(json.loads(header_json))


def import_snapshot(
    store: LocalStore,
    snapshot_path: Path | str,
    *,
    vector_store: VectorStoreLike | None = None,
) -> SnapshotStats:
    """Import documents from a snapshot file into the local index.

    Skips documents that already exist (by text_hash). Optionally also
    indexes into the vector store.

    Args:
        store: Local document store to import into.
        snapshot_path: Path to the snapshot file.
        vector_store: Optional VectorStore instance.

    Returns:
        SnapshotStats with import metrics.
    """
    start = time.monotonic()
    snapshot_path = Path(snapshot_path)
    compressor = Compressor(level=LEVEL_SNAPSHOT)

    file_size = snapshot_path.stat().st_size
    if file_size > _MAX_SNAPSHOT_FILE_BYTES:
        raise ValueError(
            f"Snapshot file too large: {file_size} bytes "
            f"(max {_MAX_SNAPSHOT_FILE_BYTES})"
        )

    with open(snapshot_path, "rb") as f:
        header_prefix = f.read(4)
        if len(header_prefix) != 4:
            raise ValueError("Snapshot file is too small to contain a header")
        header_len = struct.unpack(">I", header_prefix)[0]
        if header_len > _MAX_HEADER_LEN:
            raise ValueError(
                f"Snapshot header too large: {header_len} bytes (max {_MAX_HEADER_LEN})"
            )
        header_compressed = f.read(header_len)
        doc_compressed = f.read()

    # Parse header
    header_json = compressor.decompress(header_compressed)
    metadata = json.loads(header_json)

    fmt_version = metadata.get("format_version", 0)
    if fmt_version > _FORMAT_VERSION:
        raise ValueError(
            f"Snapshot format version {fmt_version} is newer"
            f" than supported ({_FORMAT_VERSION})"
        )

    document_count = metadata.get("document_count", 0)
    if isinstance(document_count, int) and document_count > _MAX_SNAPSHOT_DOCUMENTS:
        raise ValueError(
            f"Snapshot document count too large: {document_count} "
            f"(max {_MAX_SNAPSHOT_DOCUMENTS})"
        )

    # Decompress and unpack documents
    doc_bytes = compressor.decompress(doc_compressed)
    from infomesh.p2p.protocol import _SAFE_UNPACK

    documents = msgpack.unpackb(doc_bytes, raw=False, **_SAFE_UNPACK)
    if not isinstance(documents, list):
        raise ValueError("Snapshot document payload must be a list")
    if len(documents) > _MAX_SNAPSHOT_DOCUMENTS:
        raise ValueError(
            f"Snapshot document count too large: {len(documents)} "
            f"(max {_MAX_SNAPSHOT_DOCUMENTS})"
        )

    imported = 0
    skipped = 0

    for doc in documents:
        doc_id = store.add_document(
            url=doc["url"],
            title=doc["title"],
            text=doc["text"],
            raw_html_hash=doc["raw_html_hash"],
            text_hash=doc["text_hash"],
            language=doc.get("language"),
        )

        if doc_id is None:
            skipped += 1
            continue

        imported += 1

        # Also index in vector store
        if vector_store is not None:
            from infomesh.index.vector_store import VectorStore

            if isinstance(vector_store, VectorStore):
                vector_store.add_document(
                    doc_id=doc_id,
                    url=doc["url"],
                    title=doc["title"],
                    text=doc["text"],
                    language=doc.get("language"),
                )

    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        "snapshot_imported",
        imported=imported,
        skipped=skipped,
        total=len(documents),
        path=str(snapshot_path),
    )

    return SnapshotStats(
        total_documents=len(documents),
        exported=imported,
        skipped=skipped,
        file_size_bytes=file_size,
        elapsed_ms=elapsed,
    )
