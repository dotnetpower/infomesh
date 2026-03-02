"""Starter index — download and import a pre-built snapshot for new nodes.

Solves the cold-start problem: new InfoMesh nodes start with an empty
index, producing zero search results until enough pages are crawled.

The starter index is a community-curated snapshot hosted as a GitHub
Release asset (``starter.infomesh-snapshot``).  On first start, if the
local index is empty, the user is prompted to download and import it.

Usage via CLI::

    infomesh index import --starter          # download & import
    infomesh index import --starter --info   # show remote snapshot metadata

Programmatic::

    from infomesh.index.starter import download_starter_snapshot

    path = await download_starter_snapshot(data_dir)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

GITHUB_REPO = "dotnetpower/infomesh"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
SNAPSHOT_ASSET_NAME = "starter.infomesh-snapshot"
_CACHE_FILE = "starter_meta_cache.json"
_CACHE_TTL = 3600  # 1 hour
_REQUEST_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 600.0  # 10 min for large snapshots
_CHUNK_SIZE = 65536  # 64 KB


# ── Data types ──────────────────────────────────────────────────────────


class StarterAssetInfo:
    """Metadata about a remote starter snapshot."""

    __slots__ = ("download_url", "size_bytes", "release_tag", "created_at")

    def __init__(
        self,
        download_url: str,
        size_bytes: int,
        release_tag: str,
        created_at: str,
    ) -> None:
        self.download_url = download_url
        self.size_bytes = size_bytes
        self.release_tag = release_tag
        self.created_at = created_at

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


# ── Remote lookup ───────────────────────────────────────────────────────


async def find_starter_asset(
    *,
    cache_dir: Path | None = None,
) -> StarterAssetInfo | None:
    """Find the latest starter snapshot asset from GitHub Releases.

    Caches the result on disk for 1 hour to avoid hitting the API
    rate limit on repeated calls.

    Returns:
        ``StarterAssetInfo`` if found, ``None`` otherwise.
    """
    # Check disk cache first
    if cache_dir is not None:
        cached = _read_cache(cache_dir)
        if cached is not None:
            return cached

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(
                RELEASES_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            releases: list[dict[str, Any]] = resp.json()
    except Exception:
        logger.warning("starter_api_failed", exc_info=True)
        return None

    for release in releases:
        tag = release.get("tag_name", "")
        for asset in release.get("assets", []):
            name: str = asset.get("name", "")
            if name == SNAPSHOT_ASSET_NAME:
                info = StarterAssetInfo(
                    download_url=asset["browser_download_url"],
                    size_bytes=asset.get("size", 0),
                    release_tag=tag,
                    created_at=asset.get("created_at", ""),
                )
                if cache_dir is not None:
                    _write_cache(cache_dir, info)
                return info

    return None


# ── Download ────────────────────────────────────────────────────────────


async def download_starter_snapshot(
    data_dir: Path,
    *,
    progress_callback: Any | None = None,
) -> Path | None:
    """Download the starter snapshot to ``data_dir/starter.infomesh-snapshot``.

    Args:
        data_dir: InfoMesh data directory (``~/.infomesh``).
        progress_callback: Optional ``(downloaded_bytes, total_bytes) -> None``.

    Returns:
        Path to the downloaded file, or ``None`` on failure.
    """
    asset = await find_starter_asset(cache_dir=data_dir)
    if asset is None:
        logger.info("starter_not_found")
        return None

    dest = data_dir / SNAPSHOT_ASSET_NAME
    # Skip if already downloaded with same size
    if dest.exists() and dest.stat().st_size == asset.size_bytes:
        logger.info("starter_already_downloaded", path=str(dest))
        return dest

    logger.info(
        "starter_downloading",
        url=asset.download_url,
        size_mb=f"{asset.size_mb:.1f}",
    )

    tmp_dest = dest.with_suffix(".tmp")
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT, connect=10.0),
            ) as client,
            client.stream("GET", asset.download_url) as resp,
        ):
            resp.raise_for_status()
            downloaded = 0
            with open(tmp_dest, "wb") as f:
                async for chunk in resp.aiter_bytes(
                    chunk_size=_CHUNK_SIZE,
                ):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, asset.size_bytes)

        # Atomic rename
        tmp_dest.rename(dest)
        logger.info("starter_downloaded", path=str(dest), bytes=downloaded)
        return dest

    except Exception:
        logger.warning("starter_download_failed", exc_info=True)
        if tmp_dest.exists():
            tmp_dest.unlink(missing_ok=True)
        return None


# ── Convenience: check if starter is needed ─────────────────────────────


def needs_starter(index_doc_count: int) -> bool:
    """Return ``True`` if the local index is empty or nearly empty."""
    return index_doc_count < 10


# ── Sync wrapper ────────────────────────────────────────────────────────


def download_starter_sync(
    data_dir: Path,
    *,
    progress_callback: Any | None = None,
) -> Path | None:
    """Synchronous wrapper around :func:`download_starter_snapshot`."""
    return asyncio.run(
        download_starter_snapshot(
            data_dir,
            progress_callback=progress_callback,
        )
    )


# ── Disk cache helpers ──────────────────────────────────────────────────


def _read_cache(cache_dir: Path) -> StarterAssetInfo | None:
    cache_path = cache_dir / _CACHE_FILE
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text("utf-8"))
        if time.time() - data.get("ts", 0) > _CACHE_TTL:
            return None
        return StarterAssetInfo(
            download_url=data["url"],
            size_bytes=data["size"],
            release_tag=data["tag"],
            created_at=data.get("created_at", ""),
        )
    except Exception:
        return None


def _write_cache(cache_dir: Path, info: StarterAssetInfo) -> None:
    cache_path = cache_dir / _CACHE_FILE
    with contextlib.suppress(Exception):
        cache_path.write_text(
            json.dumps(
                {
                    "ts": time.time(),
                    "url": info.download_url,
                    "size": info.size_bytes,
                    "tag": info.release_tag,
                    "created_at": info.created_at,
                }
            ),
            encoding="utf-8",
        )
