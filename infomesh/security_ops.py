"""Security extensions — API key rotation, audit logging, rate limiting.

Features:
- #35: API key rotation support
- #38: Audit log file for API calls
- #36: Rate limiting middleware (activation)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()


# ── #35: API Key Rotation ──────────────────────────────────────────


@dataclass
class APIKeyEntry:
    """An API key with metadata."""

    key_hash: str
    label: str
    created_at: float
    expires_at: float | None = None
    revoked: bool = False


class APIKeyManager:
    """Manage multiple API keys with rotation support.

    Keys are stored as SHA-256 hashes (never plaintext).
    """

    def __init__(self, keys_file: Path | None = None) -> None:
        self._keys: list[APIKeyEntry] = []
        self._file = keys_file

        if keys_file and keys_file.exists():
            self._load()

    def add_key(
        self,
        key: str,
        label: str = "",
        ttl_days: int | None = None,
    ) -> APIKeyEntry:
        """Register a new API key."""
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        expires = time.time() + ttl_days * 86400 if ttl_days else None
        entry = APIKeyEntry(
            key_hash=key_hash,
            label=label or f"key-{len(self._keys) + 1}",
            created_at=time.time(),
            expires_at=expires,
        )
        self._keys.append(entry)
        self._save()
        return entry

    def validate(self, key: str) -> bool:
        """Check if a key is valid (not revoked, not expired)."""
        import hmac

        key_hash = hashlib.sha256(key.encode()).hexdigest()
        now = time.time()
        for entry in self._keys:
            if entry.revoked:
                continue
            if entry.expires_at and now > entry.expires_at:
                continue
            if hmac.compare_digest(entry.key_hash, key_hash):
                return True
        return False

    def revoke(self, label: str) -> bool:
        """Revoke a key by label."""
        for entry in self._keys:
            if entry.label == label:
                entry.revoked = True
                self._save()
                return True
        return False

    def list_keys(self) -> list[dict[str, object]]:
        """List all keys (hashes redacted)."""
        now = time.time()
        return [
            {
                "label": e.label,
                "created": e.created_at,
                "expires": e.expires_at,
                "revoked": e.revoked,
                "active": (
                    not e.revoked and (e.expires_at is None or now < e.expires_at)
                ),
            }
            for e in self._keys
        ]

    def _load(self) -> None:
        if not self._file or not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text("utf-8"))
            for item in data:
                self._keys.append(
                    APIKeyEntry(
                        key_hash=str(item["key_hash"]),
                        label=str(item.get("label", "")),
                        created_at=float(item.get("created_at", 0)),
                        expires_at=item.get("expires_at"),
                        revoked=bool(item.get("revoked", False)),
                    )
                )
        except (json.JSONDecodeError, KeyError, OSError):
            logger.warning("api_keys_load_failed")

    def _save(self) -> None:
        if not self._file:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "key_hash": e.key_hash,
                    "label": e.label,
                    "created_at": e.created_at,
                    "expires_at": e.expires_at,
                    "revoked": e.revoked,
                }
                for e in self._keys
            ]
            self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("api_keys_save_failed")


# ── #38: Audit Log ─────────────────────────────────────────────────


@dataclass
class AuditEntry:
    """A logged API/CLI action."""

    timestamp: float
    action: str
    source: str  # "api", "cli", "mcp"
    client: str  # IP or peer ID
    details: str = ""
    success: bool = True


class AuditLogger:
    """Append-only audit log for API/CLI/MCP actions.

    Writes JSON lines to a log file.
    """

    def __init__(
        self,
        log_path: Path | None = None,
        max_size_mb: int = 50,
    ) -> None:
        self._path = log_path
        self._max_bytes = max_size_mb * 1024 * 1024

    def log(
        self,
        action: str,
        source: str = "api",
        client: str = "localhost",
        details: str = "",
        success: bool = True,
    ) -> None:
        """Append an audit entry."""
        if not self._path:
            return

        entry = {
            "ts": time.time(),
            "action": action,
            "source": source,
            "client": client,
            "details": details[:500],
            "ok": success,
        }

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # Rotate if too large
            if self._path.exists() and self._path.stat().st_size > self._max_bytes:
                rotated = self._path.with_suffix(".log.1")
                if rotated.exists():
                    rotated.unlink()
                self._path.rename(rotated)

            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Audit logging should never crash the app

    def recent(self, limit: int = 50) -> list[AuditEntry]:
        """Read most recent audit entries."""
        if not self._path or not self._path.exists():
            return []

        entries: list[AuditEntry] = []
        try:
            lines = self._path.read_text("utf-8").strip().split("\n")
            for line in lines[-limit:]:
                data = json.loads(line)
                entries.append(
                    AuditEntry(
                        timestamp=float(data.get("ts", 0)),
                        action=str(data.get("action", "")),
                        source=str(data.get("source", "")),
                        client=str(data.get("client", "")),
                        details=str(data.get("details", "")),
                        success=bool(data.get("ok", True)),
                    )
                )
        except (json.JSONDecodeError, OSError):
            pass
        return entries
