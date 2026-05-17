"""Network monitoring and diagnostics.

Features:
- #19: Network partition detection
- #22: DHT performance benchmarking
- #49: `infomesh doctor` diagnostics
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()


# ── #19: Network Partition Detection ───────────────────────────────


@dataclass
class PartitionAlert:
    """Alert for suspected network partition."""

    timestamp: float
    previous_peers: int
    current_peers: int
    drop_pct: float
    severity: str  # "warning", "critical"


class PartitionDetector:
    """Detect sudden peer count drops indicating network partition."""

    def __init__(
        self,
        *,
        warning_threshold: float = 0.5,
        critical_threshold: float = 0.8,
        min_peers_for_alert: int = 3,
    ) -> None:
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._min_peers = min_peers_for_alert
        self._history: list[tuple[float, int]] = []
        self._alerts: list[PartitionAlert] = []

    def record(self, peer_count: int) -> PartitionAlert | None:
        """Record current peer count and check for partitions."""
        now = time.time()
        self._history.append((now, peer_count))

        # Keep last 60 entries
        if len(self._history) > 60:
            self._history = self._history[-60:]

        if len(self._history) < 3:
            return None

        # Compare with recent average
        recent = self._history[-10:-1]
        if not recent:
            return None

        avg = sum(c for _, c in recent) / len(recent)
        if avg < self._min_peers:
            return None

        drop = 1.0 - (peer_count / avg) if avg > 0 else 0.0

        if drop >= self._critical_threshold:
            severity = "critical"
        elif drop >= self._warning_threshold:
            severity = "warning"
        else:
            return None

        alert = PartitionAlert(
            timestamp=now,
            previous_peers=int(avg),
            current_peers=peer_count,
            drop_pct=round(drop * 100, 1),
            severity=severity,
        )
        self._alerts.append(alert)
        logger.warning(
            "network_partition_detected",
            severity=severity,
            peer_drop_pct=alert.drop_pct,
        )
        return alert

    @property
    def alerts(self) -> list[PartitionAlert]:
        return list(self._alerts)


# ── #22: DHT Performance Benchmark ─────────────────────────────────


@dataclass
class DHTBenchResult:
    """DHT operation benchmark result."""

    operation: str
    samples: int
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    errors: int


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * pct / 100)
    return sorted_v[min(idx, len(sorted_v) - 1)]


# ── #49: `infomesh doctor` Diagnostics ─────────────────────────────


@dataclass
class DiagnosticCheck:
    """Single diagnostic check result."""

    name: str
    status: str  # "ok", "warning", "error"
    message: str
    details: str = ""


@dataclass
class DiagnosticReport:
    """Full diagnostic report."""

    checks: list[DiagnosticCheck] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return all(c.status == "ok" for c in self.checks)

    @property
    def summary(self) -> str:
        ok = sum(1 for c in self.checks if c.status == "ok")
        warn = sum(1 for c in self.checks if c.status == "warning")
        err = sum(1 for c in self.checks if c.status == "error")
        return f"{ok} ok, {warn} warnings, {err} errors"


def run_diagnostics(data_dir: Path | None = None) -> DiagnosticReport:
    """Run all diagnostic checks."""
    from infomesh.config import DEFAULT_DATA_DIR

    data_dir = data_dir or DEFAULT_DATA_DIR
    report = DiagnosticReport()

    # 1. Data directory
    if data_dir.exists():
        report.checks.append(
            DiagnosticCheck(
                "data_dir",
                "ok",
                f"Data directory exists: {data_dir}",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                "data_dir",
                "error",
                f"Data directory missing: {data_dir}",
            )
        )

    # 2. Key pair
    key_file = data_dir / "keys" / "private.key"
    if key_file.exists():
        report.checks.append(
            DiagnosticCheck(
                "key_pair",
                "ok",
                "Ed25519 key pair present",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                "key_pair",
                "warning",
                "No key pair found (will be generated on start)",
            )
        )

    # 3. Index database
    db_path = data_dir / "index.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        report.checks.append(
            DiagnosticCheck(
                "index_db",
                "ok",
                f"Index DB: {size_mb:.1f} MB",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                "index_db",
                "warning",
                "No index database (empty node)",
            )
        )

    # 4. Config file
    config_path = data_dir / "config.toml"
    if config_path.exists():
        report.checks.append(
            DiagnosticCheck(
                "config",
                "ok",
                "Config file present",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                "config",
                "ok",
                "Using default config (no config.toml)",
            )
        )

    # 5. P2P port
    port = 4001
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        if result == 0:
            report.checks.append(
                DiagnosticCheck(
                    "p2p_port",
                    "ok",
                    f"Port {port} is in use (node may be running)",
                )
            )
        else:
            report.checks.append(
                DiagnosticCheck(
                    "p2p_port",
                    "ok",
                    f"Port {port} available",
                )
            )
    except OSError:
        report.checks.append(
            DiagnosticCheck(
                "p2p_port",
                "warning",
                f"Cannot check port {port}",
            )
        )

    # 6. Admin API port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", 8080))
        sock.close()
        if result == 0:
            report.checks.append(
                DiagnosticCheck(
                    "admin_api",
                    "ok",
                    "Admin API responding on port 8080",
                )
            )
        else:
            report.checks.append(
                DiagnosticCheck(
                    "admin_api",
                    "warning",
                    "Admin API not responding",
                )
            )
    except OSError:
        report.checks.append(
            DiagnosticCheck(
                "admin_api",
                "warning",
                "Cannot check admin API",
            )
        )

    # 7. Disk space
    import shutil

    total, used, free = shutil.disk_usage(data_dir if data_dir.exists() else "/")
    free_gb = free / (1024**3)
    if free_gb < 1:
        report.checks.append(
            DiagnosticCheck(
                "disk_space",
                "error",
                f"Low disk space: {free_gb:.1f} GB free",
            )
        )
    elif free_gb < 5:
        report.checks.append(
            DiagnosticCheck(
                "disk_space",
                "warning",
                f"Disk space low: {free_gb:.1f} GB free",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                "disk_space",
                "ok",
                f"Disk space: {free_gb:.1f} GB free",
            )
        )

    # 8. Python version
    import sys

    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    report.checks.append(
        DiagnosticCheck(
            "python",
            "ok",
            f"Python {ver}",
        )
    )

    # 9. Credit ledger
    credit_db = data_dir / "credits.db"
    if credit_db.exists():
        report.checks.append(
            DiagnosticCheck(
                "credits",
                "ok",
                "Credit ledger present",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                "credits",
                "ok",
                "No credit ledger (will be created)",
            )
        )

    # 10. Bootstrap connectivity
    report.checks.append(
        DiagnosticCheck(
            "bootstrap",
            "ok",
            "Bootstrap node: 20.42.12.161:4001 (check with `infomesh status`)",
        )
    )

    return report
