#!/usr/bin/env python3
"""Build a starter index snapshot from curated seed URLs.

This script crawls a curated list of seed URLs and exports the
resulting index as a starter snapshot for new InfoMesh nodes.

Usage::

    python scripts/build_starter.py
    python scripts/build_starter.py --seeds seeds/quickstart.txt --output starter.infomesh-snapshot
    python scripts/build_starter.py --max-pages 1000

The output file can be uploaded to GitHub Releases as
``starter.infomesh-snapshot`` for ``infomesh index import --starter``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


async def build_starter(
    seed_files: list[Path],
    output: Path,
    max_pages: int,
    depth: int,
) -> None:
    """Crawl seed URLs and export as starter snapshot."""
    from infomesh.config import (
        Config,
        CrawlConfig,
        IndexConfig,
        NodeConfig,
        StorageConfig,
    )
    from dataclasses import replace as dc_replace
    import tempfile

    with tempfile.TemporaryDirectory(prefix="infomesh-starter-") as tmpdir:
        tmp = Path(tmpdir)
        data_dir = tmp / "data"
        data_dir.mkdir()

        config = Config(
            node=NodeConfig(data_dir=data_dir),
            index=IndexConfig(db_path=data_dir / "index.db"),
            crawl=CrawlConfig(
                max_depth=depth,
                urls_per_hour=600,
                politeness_delay=0.5,
            ),
            storage=StorageConfig(
                compression_enabled=True,
                compression_level=6,
            ),
        )

        from infomesh.services import AppContext

        ctx = AppContext(config)
        try:
            # Load seed URLs
            urls: list[str] = []
            for seed_file in seed_files:
                if seed_file.exists():
                    for line in seed_file.read_text().splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            urls.append(line)
                    print(f"  Loaded {seed_file.name}: {len(urls)} URLs total")

            if not urls:
                print("Error: No seed URLs found.")
                return

            print(f"  Crawling up to {max_pages} pages from {len(urls)} seeds...")
            start = time.monotonic()

            # Crawl pages
            crawled = 0
            if ctx.worker is not None:
                for url in urls[:max_pages]:
                    if crawled >= max_pages:
                        break
                    try:
                        result = await ctx.worker.crawl_url(url)
                        if result.success:
                            from infomesh.services import index_document

                            index_document(result.page, ctx.store, ctx.vector_store)
                            crawled += 1
                            if crawled % 50 == 0:
                                print(f"  ... {crawled} pages crawled")
                    except Exception as exc:
                        print(f"  Skip {url}: {exc}")

            elapsed = time.monotonic() - start
            print(f"  Crawled {crawled} pages in {elapsed:.1f}s")

            # Export snapshot
            from infomesh.index.snapshot import export_snapshot

            stats = export_snapshot(ctx.store, output)
            size_mb = stats.file_size_bytes / (1024 * 1024)
            print(f"\n  Exported: {output}")
            print(f"  Documents: {stats.total_documents}")
            print(f"  File size: {size_mb:.1f} MB")
            print(f"  Export time: {stats.elapsed_ms:.0f}ms")

        finally:
            await ctx.close_async()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a starter index snapshot for new InfoMesh nodes"
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        default=["seeds/quickstart.txt", "seeds/tech-docs.txt"],
        help="Seed URL files (default: quickstart.txt + tech-docs.txt)",
    )
    parser.add_argument(
        "--output",
        default="starter.infomesh-snapshot",
        help="Output snapshot file path",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help="Maximum pages to crawl",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Link-follow depth per seed URL",
    )
    args = parser.parse_args()

    seed_files = [Path(s) for s in args.seeds]
    output = Path(args.output)

    print("InfoMesh Starter Snapshot Builder")
    print("=" * 40)
    asyncio.run(build_starter(seed_files, output, args.max_pages, args.depth))


if __name__ == "__main__":
    main()
