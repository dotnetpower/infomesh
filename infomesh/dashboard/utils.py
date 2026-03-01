"""Shared dashboard utilities.

Central location for helper functions used by dashboard screens
and text_report. Avoids duplication across modules.
"""

from __future__ import annotations

import json
import os
import time

from infomesh.config import Config


def format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime string."""
    if seconds <= 0:
        return "—"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def get_peer_id(config: Config) -> str:
    """Load peer ID from key file if available."""
    keys_dir = config.node.data_dir / "keys"
    if (keys_dir / "private.pem").exists():
        try:
            from infomesh.p2p.keys import KeyPair

            pair = KeyPair.load(keys_dir)
            return pair.peer_id
        except Exception:  # noqa: BLE001
            pass
    return "(not generated)"


def is_node_running(config: Config) -> bool:
    """Check if the InfoMesh node process is running."""
    pid_file = config.node.data_dir / "infomesh.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def is_node_running_with_uptime(
    config: Config,
) -> tuple[bool, float]:
    """Check if node is running and return (running, uptime_seconds)."""
    pid_file = config.node.data_dir / "infomesh.pid"
    if not pid_file.exists():
        return False, 0.0
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        uptime = time.time() - pid_file.stat().st_mtime
        return True, uptime
    except (ValueError, ProcessLookupError, PermissionError):
        return False, 0.0


def read_p2p_status(config: Config) -> dict[str, object]:
    """Read p2p_status.json, returning empty dict on stale/missing data."""
    status_path = config.node.data_dir / "p2p_status.json"
    try:
        if status_path.exists():
            data: dict[str, object] = json.loads(status_path.read_text())
            ts = data.get("timestamp", 0)
            age = time.time() - float(ts if isinstance(ts, (int, float, str)) else 0)
            if age < _P2P_STATUS_TTL_SECONDS:
                return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


_P2P_STATUS_TTL_SECONDS = 30


def tier_label(tier: object) -> str:
    """Convert a ContributionTier enum value to a display label.

    Uses name-based matching so the caller does not need to import
    the enum — works with any object whose ``.name`` matches.
    """
    name = getattr(tier, "name", "")
    mapping = {
        "TIER_1": "⭐ Tier 1",
        "TIER_2": "⭐⭐ Tier 2",
        "TIER_3": "⭐⭐⭐ Tier 3",
    }
    return mapping.get(name, "Unknown")


def format_bytes(n: int | float) -> str:
    """Format byte count to human readable."""
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
