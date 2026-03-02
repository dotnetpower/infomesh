"""Seed URL management and category selection."""

from __future__ import annotations

import sysconfig
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Bundled seed directory — try dev checkout first, then installed shared-data
_SEEDS_DIR_DEV = Path(__file__).parent.parent.parent / "seeds"
_DATA_PREFIX = sysconfig.get_path("data") or ""
_SEEDS_DIR_INSTALLED = Path(_DATA_PREFIX) / "share" / "infomesh" / "seeds"
_SEEDS_DIR = _SEEDS_DIR_DEV if _SEEDS_DIR_DEV.exists() else _SEEDS_DIR_INSTALLED

CATEGORIES = {
    "tech-docs": "Technology documentation",
    "academic": "Academic paper sources",
    "encyclopedia": "Encyclopedia sources",
    "quickstart": (
        "Lightweight seed pack (~100 curated content pages for instant start)"
    ),
    "search-strategy": "Search strategy and optimization seeds",
}


def load_seeds(category: str | None = None, seeds_dir: Path | None = None) -> list[str]:
    """Load seed URLs from bundled seed files.

    Args:
        category: Specific category to load, or None for all.
        seeds_dir: Override seed directory path.

    Returns:
        List of seed URLs.
    """
    base = seeds_dir or _SEEDS_DIR
    urls: list[str] = []

    if not base.exists():
        logger.warning("seeds_dir_missing", path=str(base))
        return urls

    if category:
        # Validate category name to prevent path traversal
        if category not in CATEGORIES:
            logger.warning("invalid_seed_category", category=category)
            return urls
        seed_file = base / f"{category}.txt"
        if seed_file.exists():
            urls.extend(_parse_seed_file(seed_file))
        else:
            logger.warning("seed_file_missing", category=category, path=str(seed_file))
    else:
        for seed_file in sorted(base.glob("*.txt")):
            urls.extend(_parse_seed_file(seed_file))

    logger.info("seeds_loaded", count=len(urls), category=category or "all")
    return urls


def _parse_seed_file(path: Path) -> list[str]:
    """Parse a seed file, stripping comments and empty lines."""
    urls: list[str] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                # Basic URL validation
                if line.startswith(("http://", "https://")):
                    urls.append(line)
                else:
                    logger.debug("seed_invalid_url", url=line, file=str(path))
    return urls
