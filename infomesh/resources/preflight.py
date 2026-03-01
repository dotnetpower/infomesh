"""Preflight checks — disk space and network connectivity validation.

Run before starting the node to ensure the environment is viable.

Usage::

    from infomesh.resources.preflight import (
        check_disk_space,
        check_outbound_connectivity,
        run_preflight_checks,
    )

    issues = run_preflight_checks(config)
    for issue in issues:
        print(issue.severity, issue.message)
"""

from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

# Minimum free disk space required to start (MB)
MIN_DISK_SPACE_MB = 500
# Disk space threshold to pause crawling (MB)
LOW_DISK_SPACE_MB = 200
# Hosts/ports to probe for outbound connectivity
CONNECTIVITY_TARGETS: list[tuple[str, int]] = [
    ("docs.python.org", 443),
    ("developer.mozilla.org", 443),
    ("1.1.1.1", 443),
]
# Socket connect timeout (seconds)
CONNECT_TIMEOUT = 3.0


class IssueSeverity(StrEnum):
    """Severity level of a preflight issue."""

    ERROR = "error"  # Cannot proceed
    WARNING = "warning"  # Can proceed but degraded


@dataclass(frozen=True)
class PreflightIssue:
    """A single preflight check result."""

    severity: IssueSeverity
    check: str
    message: str


# ── Disk space ──────────────────────────────────────────────────────────


def get_disk_free_mb(path: Path) -> float:
    """Return free disk space at *path* in megabytes."""
    usage = shutil.disk_usage(path)
    return usage.free / (1024 * 1024)


def check_disk_space(data_dir: Path) -> list[PreflightIssue]:
    """Check whether there is sufficient disk space at *data_dir*.

    Returns:
        List of issues (empty if OK).
    """
    issues: list[PreflightIssue] = []
    try:
        free_mb = get_disk_free_mb(data_dir)
        logger.debug("disk_space_check", free_mb=round(free_mb, 1), path=str(data_dir))

        if free_mb < MIN_DISK_SPACE_MB:
            issues.append(
                PreflightIssue(
                    severity=IssueSeverity.ERROR,
                    check="disk_space",
                    message=(
                        f"Insufficient disk space: {free_mb:.0f} MB free "
                        f"(minimum {MIN_DISK_SPACE_MB} MB required). "
                        f"Free up space at {data_dir} before starting."
                    ),
                ),
            )
        elif free_mb < MIN_DISK_SPACE_MB * 2:
            issues.append(
                PreflightIssue(
                    severity=IssueSeverity.WARNING,
                    check="disk_space",
                    message=(
                        f"Low disk space: {free_mb:.0f} MB free. "
                        f"Consider freeing space at {data_dir}."
                    ),
                ),
            )
    except OSError as exc:
        issues.append(
            PreflightIssue(
                severity=IssueSeverity.ERROR,
                check="disk_space",
                message=f"Cannot check disk space: {exc}",
            ),
        )
    return issues


def is_disk_critically_low(data_dir: Path) -> bool:
    """Return ``True`` if disk space is below the crawl-pause threshold.

    Suitable for periodic checks during the crawl loop.
    """
    try:
        free_mb = get_disk_free_mb(data_dir)
        return free_mb < LOW_DISK_SPACE_MB
    except OSError:
        return False


# ── Network connectivity ────────────────────────────────────────────────


def check_outbound_connectivity() -> list[PreflightIssue]:
    """Probe outbound HTTPS connectivity to well-known hosts.

    Returns:
        List of issues (empty if at least one target is reachable).
    """
    reachable = 0
    unreachable: list[str] = []

    for host, port in CONNECTIVITY_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
                reachable += 1
                logger.debug("connectivity_ok", host=host, port=port)
        except (OSError, TimeoutError):
            unreachable.append(f"{host}:{port}")
            logger.debug("connectivity_failed", host=host, port=port)

    issues: list[PreflightIssue] = []

    if reachable == 0:
        issues.append(
            PreflightIssue(
                severity=IssueSeverity.ERROR,
                check="network",
                message=(
                    "No outbound connectivity — cannot reach any external host. "
                    f"Tried: {', '.join(unreachable)}. "
                    "Check your firewall or proxy settings."
                ),
            ),
        )
    elif unreachable:
        issues.append(
            PreflightIssue(
                severity=IssueSeverity.WARNING,
                check="network",
                message=(
                    f"Some hosts unreachable: {', '.join(unreachable)}. "
                    "Crawling may be limited."
                ),
            ),
        )

    return issues


# ── Combined runner ─────────────────────────────────────────────────────


def run_preflight_checks(data_dir: Path) -> list[PreflightIssue]:
    """Run all preflight checks and return a list of issues.

    Args:
        data_dir: The node's data directory (for disk space check).

    Returns:
        List of :class:`PreflightIssue` items. Empty means all clear.
    """
    issues: list[PreflightIssue] = []
    issues.extend(check_disk_space(data_dir))
    issues.extend(check_outbound_connectivity())
    return issues
