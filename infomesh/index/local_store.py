"""SQLite FTS5 local index for full-text search with optional zstd compression."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from infomesh.compression.zstd import Compressor

logger = structlog.get_logger()

# Allowed FTS5 tokenizer names (whitelist to prevent SQL injection)
_ALLOWED_TOKENIZERS = frozenset(
    {
        "unicode61",
        "ascii",
        "porter",
        "trigram",
    }
)


@dataclass(frozen=True)
class IndexedDocument:
    """A document stored in the local index."""

    doc_id: int
    url: str
    title: str
    text: str
    language: str | None
    raw_html_hash: str
    text_hash: str
    crawled_at: float
    # Recrawl metadata (optional — may be absent in older schemas)
    etag: str | None = None
    last_modified: str | None = None
    recrawl_interval: int = 604800
    stale_count: int = 0
    last_recrawl_at: float | None = None
    change_frequency: float = 0.0


@dataclass(frozen=True)
class SearchResult:
    """A single search result from the local index."""

    doc_id: int
    url: str
    title: str
    snippet: str
    score: float
    language: str | None
    crawled_at: float


class LocalStore:
    """SQLite FTS5 based local document store and search index.

    Provides:
    - Document storage with metadata
    - Full-text search via FTS5 with BM25 ranking
    - Snippet generation for search results
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        tokenizer: str = "unicode61",
        *,
        compression_enabled: bool = False,
        compression_level: int = 3,
    ) -> None:
        self._db_path = str(db_path) if db_path else ":memory:"

        # Validate tokenizer against whitelist to prevent SQL injection
        if tokenizer not in _ALLOWED_TOKENIZERS:
            raise ValueError(
                f"Invalid tokenizer '{tokenizer}'; "
                f"allowed: {sorted(_ALLOWED_TOKENIZERS)}"
            )
        self._tokenizer = tokenizer

        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrent reads (dashboard) while writing (crawler)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._compressor: Compressor | None = None
        if compression_enabled:
            self._compressor = Compressor(level=compression_level)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and FTS5 index if they don't exist."""
        self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                compressed_text BLOB,
                language TEXT,
                raw_html_hash TEXT NOT NULL,
                text_hash TEXT UNIQUE NOT NULL,
                crawled_at REAL NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title,
                text,
                content='documents',
                content_rowid='doc_id',
                tokenize='{self._tokenizer}'
            );

            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, title, text)
                VALUES (new.doc_id, new.title, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, text)
                VALUES ('delete', old.doc_id, old.title, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, text)
                VALUES ('delete', old.doc_id, old.title, old.text);
                INSERT INTO documents_fts(rowid, title, text)
                VALUES (new.doc_id, new.title, new.text);
            END;
        """)
        self._conn.commit()

        # Migrate older schemas: add columns that may not exist yet.
        self._migrate_schema()

        logger.debug("local_store_initialized", db=self._db_path)

    def _migrate_schema(self) -> None:
        """Add missing columns to existing databases (backward compat)."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        migrations: list[tuple[str, str]] = [
            (
                "compressed_text",
                "ALTER TABLE documents ADD COLUMN compressed_text BLOB",
            ),
            (
                "raw_html_hash",
                "ALTER TABLE documents"
                " ADD COLUMN raw_html_hash TEXT"
                " NOT NULL DEFAULT ''",
            ),
            ("etag", "ALTER TABLE documents ADD COLUMN etag TEXT"),
            ("last_modified", "ALTER TABLE documents ADD COLUMN last_modified TEXT"),
            (
                "recrawl_interval",
                "ALTER TABLE documents"
                " ADD COLUMN recrawl_interval"
                " INTEGER DEFAULT 604800",
            ),
            (
                "stale_count",
                "ALTER TABLE documents ADD COLUMN stale_count INTEGER DEFAULT 0",
            ),
            (
                "last_recrawl_at",
                "ALTER TABLE documents ADD COLUMN last_recrawl_at REAL",
            ),
            (
                "change_frequency",
                "ALTER TABLE documents ADD COLUMN change_frequency REAL DEFAULT 0.0",
            ),
        ]
        for col, ddl in migrations:
            if col not in existing:
                self._conn.execute(ddl)
                logger.info("schema_migrated", column=col)
        self._conn.commit()

    def add_document(
        self,
        url: str,
        title: str,
        text: str,
        raw_html_hash: str,
        text_hash: str,
        *,
        language: str | None = None,
    ) -> int | None:
        """Add a document to the local index.

        Args:
            url: Source URL.
            title: Page title.
            text: Extracted text.
            raw_html_hash: SHA-256 of raw HTML.
            text_hash: SHA-256 of extracted text.
            language: ISO language code.

        Returns:
            Document ID if inserted, None if duplicate.
        """
        try:
            compressed = None
            if self._compressor:
                compressed = self._compressor.compress_text(text)
            cursor = self._conn.execute(
                """INSERT INTO documents
                   (url, title, text, compressed_text,
                    language, raw_html_hash,
                    text_hash, crawled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    url,
                    title,
                    text,
                    compressed,
                    language,
                    raw_html_hash,
                    text_hash,
                    time.time(),
                ),
            )
            self._conn.commit()
            doc_id = cursor.lastrowid
            logger.info("doc_indexed", doc_id=doc_id, url=url, text_len=len(text))
            return doc_id
        except sqlite3.IntegrityError:
            logger.debug("doc_duplicate", url=url)
            return None

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Search the local index using FTS5 with BM25 ranking.

        Args:
            query: Search query string.
            limit: Maximum results to return (capped at 1000).

        Returns:
            List of search results ordered by relevance.
        """
        # Validate inputs
        limit = max(1, min(limit, 1000))

        try:
            rows = self._conn.execute(
                """SELECT
                       d.doc_id,
                       d.url,
                       d.title,
                       snippet(documents_fts, 1, '<b>', '</b>', '...', 40) AS snippet,
                       bm25(documents_fts) AS score,
                       d.language,
                       d.crawled_at
                   FROM documents_fts
                   JOIN documents d ON d.doc_id = documents_fts.rowid
                   WHERE documents_fts MATCH ?
                   ORDER BY bm25(documents_fts)
                   LIMIT ?""",
                (query, limit),
            ).fetchall()

            results = [
                SearchResult(
                    doc_id=row["doc_id"],
                    url=row["url"],
                    title=row["title"],
                    snippet=row["snippet"],
                    score=abs(row["score"]),  # BM25 returns negative scores
                    language=row["language"],
                    crawled_at=row["crawled_at"],
                )
                for row in rows
            ]

            logger.info("local_search", query=query, results=len(results))
            return results

        except sqlite3.OperationalError as exc:
            logger.error("search_error", query=query, error=str(exc))
            return []

    def _row_to_document(self, row: sqlite3.Row) -> IndexedDocument:
        """Convert a database row to an IndexedDocument, decompressing if needed."""
        data = dict(row)
        compressed = data.pop("compressed_text", None)
        if compressed and self._compressor:
            data["text"] = self._compressor.decompress_text(compressed)
        return IndexedDocument(**data)

    def get_document(self, doc_id: int) -> IndexedDocument | None:
        """Retrieve a document by ID."""
        row = self._conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_document(row)

    def get_document_by_url(self, url: str) -> IndexedDocument | None:
        """Retrieve a document by URL."""
        row = self._conn.execute(
            "SELECT * FROM documents WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_document(row)

    def get_stats(self) -> dict[str, int]:
        """Get index statistics."""
        row = self._conn.execute("SELECT COUNT(*) as count FROM documents").fetchone()
        return {"document_count": row["count"] if row else 0}

    def export_documents(self) -> list[dict[str, object]]:
        """Export all documents as a list of dicts (for snapshot/backup).

        Returns column subset: url, title, text, language,
        raw_html_hash, text_hash, crawled_at — ordered by doc_id.
        """
        rows = self._conn.execute(
            "SELECT url, title, text, language, raw_html_hash, text_hash, crawled_at "
            "FROM documents ORDER BY doc_id"
        ).fetchall()
        return [
            {
                "url": row["url"],
                "title": row["title"],
                "text": row["text"],
                "language": row["language"],
                "raw_html_hash": row["raw_html_hash"],
                "text_hash": row["text_hash"],
                "crawled_at": row["crawled_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> LocalStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── Recrawl support ─────────────────────────────────────────────────

    def update_document(
        self,
        url: str,
        *,
        title: str | None = None,
        text: str | None = None,
        text_hash: str | None = None,
        raw_html_hash: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        recrawl_interval: int | None = None,
        stale_count: int | None = None,
        last_recrawl_at: float | None = None,
        change_frequency: float | None = None,
    ) -> bool:
        """Update specific fields of an existing document.

        Only non-None arguments are written. FTS5 triggers handle
        index updates automatically.

        Returns:
            ``True`` if a row was updated.
        """
        sets: list[str] = []
        params: list[object] = []

        _field_map: dict[str, object] = {
            "title": title,
            "text": text,
            "text_hash": text_hash,
            "raw_html_hash": raw_html_hash,
            "etag": etag,
            "last_modified": last_modified,
            "recrawl_interval": recrawl_interval,
            "stale_count": stale_count,
            "last_recrawl_at": last_recrawl_at,
            "change_frequency": change_frequency,
        }
        for col, val in _field_map.items():
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)

        if not sets:
            return False

        params.append(url)
        sql = f"UPDATE documents SET {', '.join(sets)} WHERE url = ?"
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.debug("doc_updated", url=url, fields=list(_field_map.keys()))
        return updated

    def soft_delete(self, url: str) -> bool:
        """Mark a document as deleted (remove from index but keep metadata).

        Returns:
            ``True`` if a row was deleted.
        """
        cursor = self._conn.execute("DELETE FROM documents WHERE url = ?", (url,))
        self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("doc_soft_deleted", url=url)
        return deleted

    def get_recrawl_candidates(self, *, limit: int = 200) -> list[dict[str, object]]:
        """Retrieve documents eligible for recrawl consideration.

        Returns rows with recrawl metadata as dicts.
        """
        rows = self._conn.execute(
            """SELECT doc_id, url, text_hash, etag, last_modified,
                      recrawl_interval, stale_count, change_frequency,
                      crawled_at, last_recrawl_at
               FROM documents
               WHERE stale_count < 3
               ORDER BY last_recrawl_at ASC NULLS FIRST
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
