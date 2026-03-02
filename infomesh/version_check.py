"""Version check — detect available updates from PyPI and P2P peers.

Provides two complementary update-detection mechanisms:

1. **PyPI check**: Query the PyPI JSON API for the latest release.
   Results are cached on disk for 24 hours to avoid excessive requests.
2. **P2P version gossip**: Track peer versions seen during PING/PONG
   exchanges and report if any connected peer runs a newer version.

Usage::

    from infomesh.version_check import check_for_update, UpdateInfo

    info = check_for_update()
    if info is not None:
        click.echo(f"Update available: {info.current} → {info.latest}")
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from infomesh import __version__

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

_PYPI_URL = "https://pypi.org/pypi/infomesh/json"
_CACHE_TTL_SECONDS = 86400  # 24 hours
_CACHE_FILE_NAME = "version_cache.json"
_REQUEST_TIMEOUT = 5.0  # seconds


@dataclass(frozen=True)
class UpdateInfo:
    """Describes an available update."""

    current: str
    latest: str
    source: str  # "pypi" or "peer"


# ── Version comparison ──────────────────────────────────────────────────


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a PEP 440 version string into a comparable tuple.

    Only handles numeric segments (e.g., ``0.1.10`` → ``(0, 1, 10)``).
    Pre-release suffixes are stripped for comparison simplicity.
    """
    parts: list[int] = []
    for segment in v.split("."):
        # Strip non-numeric suffixes (e.g., "1a2" → "1")
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def is_newer(candidate: str, current: str | None = None) -> bool:
    """Return ``True`` if *candidate* is a newer version than *current*.

    Args:
        candidate: The version string to check.
        current: The baseline version. Defaults to ``__version__``.
    """
    base = current or __version__
    return _parse_version(candidate) > _parse_version(base)


# ── PyPI check ──────────────────────────────────────────────────────────


def _cache_path(data_dir: Path) -> Path:
    return data_dir / _CACHE_FILE_NAME


def _read_cache(data_dir: Path) -> str | None:
    """Read cached latest version if still fresh."""
    path = _cache_path(data_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        ts = float(raw.get("ts", 0))
        if time.time() - ts > _CACHE_TTL_SECONDS:
            return None
        ver = raw.get("version", "")
        return ver if isinstance(ver, str) and ver else None
    except Exception:  # noqa: BLE001
        return None


def _write_cache(data_dir: Path, version: str) -> None:
    """Persist latest version to disk cache."""
    path = _cache_path(data_dir)
    with contextlib.suppress(OSError):
        path.write_text(
            json.dumps({"version": version, "ts": time.time()}),
            encoding="utf-8",
        )


def _fetch_latest_from_pypi() -> str | None:
    """Query PyPI for the latest infomesh release version.

    Returns ``None`` on any network or parse error.
    """
    try:
        import httpx

        resp = httpx.get(_PYPI_URL, timeout=_REQUEST_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
        ver = data.get("info", {}).get("version", "")
        return ver if isinstance(ver, str) and ver else None
    except Exception:  # noqa: BLE001
        return None


def check_pypi_update(data_dir: Path) -> UpdateInfo | None:
    """Check PyPI for a newer version of infomesh.

    Uses a 24-hour disk cache to avoid redundant network requests.

    Args:
        data_dir: Node data directory (e.g., ``~/.infomesh``).

    Returns:
        :class:`UpdateInfo` if a newer version exists, else ``None``.
    """
    # Try cache first
    cached = _read_cache(data_dir)
    if cached is not None:
        if is_newer(cached):
            return UpdateInfo(
                current=__version__,
                latest=cached,
                source="pypi",
            )
        return None

    # Fetch from PyPI
    latest = _fetch_latest_from_pypi()
    if latest is None:
        return None

    _write_cache(data_dir, latest)

    if is_newer(latest):
        return UpdateInfo(current=__version__, latest=latest, source="pypi")
    return None


# ── P2P peer version tracking ──────────────────────────────────────────


class PeerVersionTracker:
    """Track versions reported by connected P2P peers.

    Thread-safe: only appended to, never mutated in-place.
    """

    def __init__(self) -> None:
        self._peer_versions: dict[str, str] = {}

    def record(self, peer_id: str, version: str) -> None:
        """Record the version reported by a peer."""
        if version and isinstance(version, str):
            self._peer_versions[peer_id] = version

    def get_newest_peer_version(self) -> str | None:
        """Return the highest version seen across all peers."""
        if not self._peer_versions:
            return None
        return max(self._peer_versions.values(), key=_parse_version)

    def check_peer_update(self) -> UpdateInfo | None:
        """Check if any connected peer runs a newer version.

        Returns:
            :class:`UpdateInfo` if a peer has a newer version, else ``None``.
        """
        newest = self.get_newest_peer_version()
        if newest is not None and is_newer(newest):
            return UpdateInfo(
                current=__version__,
                latest=newest,
                source="peer",
            )
        return None

    @property
    def peer_versions(self) -> dict[str, str]:
        """Return a copy of known peer versions."""
        return dict(self._peer_versions)


# ── Convenience ─────────────────────────────────────────────────────────


def check_for_update(
    data_dir: Path | None = None,
    peer_tracker: PeerVersionTracker | None = None,
) -> UpdateInfo | None:
    """Check for updates from all available sources.

    Checks PyPI first (cached), then peer versions.

    Args:
        data_dir: Node data directory for PyPI cache.
        peer_tracker: Optional peer version tracker.

    Returns:
        :class:`UpdateInfo` for the highest available version, or ``None``.
    """
    best: UpdateInfo | None = None

    if data_dir is not None:
        try:
            pypi = check_pypi_update(data_dir)
            if pypi is not None:
                best = pypi
        except Exception:  # noqa: BLE001
            logger.debug("pypi_update_check_failed")

    if peer_tracker is not None:
        peer = peer_tracker.check_peer_update()
        if peer is not None and (best is None or is_newer(peer.latest, best.latest)):
            best = peer

    return best


def format_update_banner(info: UpdateInfo) -> str:
    """Format a user-facing update notification string."""
    source_label = "PyPI" if info.source == "pypi" else "P2P peer"
    return (
        f"\n  ⬆ Update available ({source_label}): "
        f"v{info.current} → v{info.latest}\n"
        f"    Run: infomesh update\n"
    )
