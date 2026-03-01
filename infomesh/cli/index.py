"""CLI commands: index stats, export, import, import-wet, import-urls."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from infomesh.config import load_config


@click.group("index")
def index_group() -> None:
    """Index management."""


@index_group.command("stats")
def index_stats() -> None:
    """Show detailed index statistics."""
    from infomesh.index.local_store import LocalStore

    config = load_config()
    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )
    try:
        stats = store.get_stats()

        click.echo("Index Statistics")
        click.echo(f"{'=' * 30}")
        click.echo(f"Database:        {config.index.db_path}")
        click.echo(f"Documents:       {stats['document_count']}")
        click.echo(f"Tokenizer:       {config.index.fts_tokenizer}")
        click.echo(
            f"Compression:     {'on' if config.storage.compression_enabled else 'off'}"
        )

        db_file = Path(config.index.db_path)
        if db_file.exists():
            size_mb = db_file.stat().st_size / (1024 * 1024)
            click.echo(f"DB size:         {size_mb:.2f} MB")
    finally:
        store.close()


@index_group.command("export")
@click.argument("output", default="infomesh-index.infomesh-snapshot")
def index_export(output: str) -> None:
    """Export the local index to a snapshot file."""
    from infomesh.index.local_store import LocalStore
    from infomesh.index.snapshot import export_snapshot

    config = load_config()
    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )
    try:
        stats = export_snapshot(store, output)
    finally:
        store.close()

    size_mb = stats.file_size_bytes / (1024 * 1024)
    click.echo(f"Exported {stats.total_documents} documents to {output}")
    click.echo(f"  File size: {size_mb:.2f} MB ({stats.elapsed_ms:.0f}ms)")


@index_group.command("import")
@click.argument("input_path")
@click.option("--info", is_flag=True, default=False, help="Show snapshot metadata only")
def index_import(input_path: str, info: bool) -> None:
    """Import documents from a snapshot file."""
    from infomesh.index.snapshot import import_snapshot, read_snapshot_metadata

    if info:
        meta = read_snapshot_metadata(input_path)
        click.echo(f"Snapshot: {input_path}")
        click.echo(f"  Format version: {meta.get('format_version')}")
        click.echo(f"  Documents:      {meta.get('document_count')}")
        import datetime

        ts = meta.get("created_at", 0)
        click.echo(
            f"  Created:        "
            f"{datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).isoformat()}"
        )
        return

    from infomesh.services import AppContext

    config = load_config()
    ctx = AppContext(config)
    try:
        stats = import_snapshot(ctx.store, input_path, vector_store=ctx.vector_store)
    finally:
        ctx.close()

    click.echo(f"Imported {stats.exported} documents from {input_path}")
    click.echo(f"  Skipped (duplicate): {stats.skipped}")
    click.echo(f"  Total in snapshot:   {stats.total_documents}")
    click.echo(f"  Time: {stats.elapsed_ms:.0f}ms")


@index_group.command("import-wet")
@click.argument("path_or_url")
def index_import_wet(path_or_url: str) -> None:
    """Import documents from a Common Crawl WET file (local or URL)."""

    async def _do_import() -> None:
        from infomesh.index.commoncrawl import CommonCrawlImporter
        from infomesh.services import AppContext

        config = load_config()
        ctx = AppContext(config)
        importer = CommonCrawlImporter(
            ctx.store, ctx.dedup, vector_store=ctx.vector_store
        )
        try:
            stats = await importer.import_wet_file(path_or_url)
        finally:
            ctx.close()

        click.echo(f"Imported {stats.imported} documents from WET file")
        click.echo(f"  Total records:    {stats.total_records}")
        click.echo(f"  Skipped (dup):    {stats.skipped_duplicate}")
        click.echo(f"  Skipped (short):  {stats.skipped_too_short}")
        click.echo(f"  Skipped (error):  {stats.skipped_error}")
        click.echo(f"  Time: {stats.elapsed_ms:.0f}ms")

    asyncio.run(_do_import())


@index_group.command("import-urls")
@click.argument("url_file")
@click.option("--max", "-m", "max_urls", default=10000, help="Maximum URLs to import")
def index_import_urls(url_file: str, max_urls: int) -> None:
    """Import URLs from a text file (one per line) into the crawl queue."""

    async def _do_import() -> None:
        from infomesh.index.commoncrawl import CommonCrawlImporter
        from infomesh.services import AppContext

        config = load_config()
        ctx = AppContext(config)
        importer = CommonCrawlImporter(
            ctx.store, ctx.dedup, vector_store=ctx.vector_store
        )
        try:
            stats = await importer.import_url_list(url_file, max_urls=max_urls)
        finally:
            ctx.close()

        click.echo(f"Registered {stats.imported} URLs from {url_file}")
        click.echo(f"  Skipped (already seen): {stats.skipped_duplicate}")

    asyncio.run(_do_import())
