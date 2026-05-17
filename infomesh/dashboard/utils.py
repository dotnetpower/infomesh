"""Shared dashboard utilities.

Central location for helper functions used by dashboard screens
and text_report. Avoids duplication across modules.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence

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
    """Check if the InfoMesh node process is running.

    Checks both the PID file and recent DB activity as indicators,
    since the crawler subprocess may be active without P2P.
    """
    pid_file = config.node.data_dir / "infomesh.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            pass

    # Fallback: check if index DB was modified in the last 60 seconds
    # (indicates an active crawler even if PID file is stale)
    db_path = config.index.db_path
    if db_path.exists():
        try:
            age = time.time() - db_path.stat().st_mtime
            if age < 60:
                return True
        except OSError:
            pass

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
    """Read p2p_status.json.

    Returns fresh data if within TTL, otherwise returns a minimal
    dict with just peer_id and state='stopped' (stale marker) so
    the dashboard can still display the node identity.
    """
    status_path = config.node.data_dir / "p2p_status.json"
    try:
        if status_path.exists():
            data: dict[str, object] = json.loads(status_path.read_text())
            ts = data.get("timestamp", 0)
            age = time.time() - float(ts if isinstance(ts, (int, float, str)) else 0)
            if age < _P2P_STATUS_TTL_SECONDS:
                return data
            # Stale — return peer_id + stopped state for display
            peer_id = data.get("peer_id", "")
            if peer_id:
                return {"peer_id": peer_id, "state": "stopped", "peers": 0}
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


def format_doc_line(url: str, title: str) -> str:
    """Format a crawled document URL + title for LiveLog display.

    Shared helper used by both OverviewPane and CrawlPane to avoid
    code duplication.
    """
    short_title = title[:40] + "\u2026" if len(title) > 40 else title
    if short_title:
        return f"{url}  ({short_title})"
    return url


def push_new_docs_to_log(
    recent_docs: Sequence[object],
    doc_count: int,
    seen_ids: set[int],
    last_count: int,
    log_widget: object,
) -> tuple[set[int], int]:
    """Detect newly indexed documents and log them to a LiveLog widget.

    Shared helper used by OverviewPane and CrawlPane.

    Args:
        recent_docs: List of RecentDoc objects (must have doc_id,
            url, title, crawled_at attributes).
        doc_count: Current total document count.
        seen_ids: Set of already-seen doc IDs (mutated in place).
        last_count: Previous document count.
        log_widget: LiveLog widget instance (must have ``log_crawl`` method).

    Returns:
        Updated (seen_ids, last_count) tuple.
    """
    if doc_count <= last_count and not recent_docs:
        return seen_ids, last_count

    new_docs = [d for d in recent_docs if d.doc_id not in seen_ids]  # type: ignore[attr-defined]
    if not new_docs:
        return seen_ids, doc_count

    try:
        for doc in sorted(new_docs, key=lambda d: d.crawled_at):  # type: ignore[attr-defined]
            log_widget.log_crawl(  # type: ignore[attr-defined]
                format_doc_line(doc.url, doc.title),  # type: ignore[attr-defined]
                success=True,
            )
            seen_ids.add(doc.doc_id)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    # Prevent unbounded growth — keep only the newest 300 IDs
    if len(seen_ids) > 500:
        sorted_ids = sorted(seen_ids)
        seen_ids = set(sorted_ids[-300:])

    return seen_ids, doc_count
