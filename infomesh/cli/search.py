"""CLI command: search (local / hybrid)."""

from __future__ import annotations

import click

from infomesh.config import load_config


@click.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Maximum results")
@click.option("--local", is_flag=True, default=False, help="Search local index only")
@click.option(
    "--vector", is_flag=True, default=False, help="Include vector/semantic search"
)
def search(query: str, limit: int, local: bool, vector: bool) -> None:
    """Search the local index."""
    from infomesh.index.local_store import LocalStore
    from infomesh.search.formatter import format_fts_results, format_hybrid_results
    from infomesh.search.query import search_local

    config = load_config()
    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )

    use_vector = vector or config.index.vector_search
    if use_vector:
        try:
            from infomesh.index.vector_store import VectorStore
            from infomesh.search.query import search_hybrid

            vec_store = VectorStore(
                persist_dir=config.node.data_dir / "chroma",
                model_name=config.index.embedding_model,
            )
            try:
                hybrid = search_hybrid(store, vec_store, query, limit=limit)  # type: ignore[arg-type]
                click.echo(format_hybrid_results(hybrid))
            finally:
                vec_store.close()
                store.close()
            return
        except ImportError:
            click.echo(
                "Warning: chromadb not installed, falling back to keyword search."
            )

    try:
        result = search_local(store, query, limit=limit)
        click.echo(format_fts_results(result))
    finally:
        store.close()
