"""Extended security features.

Features:
- #36: TLS configuration helper
- #37: JWT/OAuth2 token verification
- #38: Role-based access control (RBAC)
- #39: IP allowlist/blocklist
- #40: Webhook HMAC signing
- #41: API access audit log
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# ── #36: TLS configuration ────────────────────────────────────────


@dataclass(frozen=True)
class TLSConfig:
    """TLS/HTTPS configuration for HTTP transport."""

    enabled: bool = False
    cert_file: str = ""
    key_file: str = ""
    ca_file: str = ""

    def validate(self) -> list[str]:
        """Validate TLS configuration. Returns list of errors."""
        errors: list[str] = []
        if not self.enabled:
            return errors
        if not self.cert_file:
            errors.append("TLS cert_file is required")
        elif not Path(self.cert_file).exists():
            errors.append(f"TLS cert_file not found: {self.cert_file}")
        if not self.key_file:
            errors.append("TLS key_file is required")
        elif not Path(self.key_file).exists():
            errors.append(f"TLS key_file not found: {self.key_file}")
        return errors

    def ssl_context(self) -> Any:
        """Create SSL context for uvicorn. Returns None if disabled."""
        if not self.enabled:
            return None
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_file, self.key_file)
        if self.ca_file:
            ctx.load_verify_locations(self.ca_file)
        return ctx


# ── #37: JWT token verification ───────────────────────────────────


def verify_jwt_token(
    token: str,
    secret: str,
    *,
    algorithms: list[str] | None = None,
) -> dict[str, object] | None:
    """Verify a JWT token and return payload.

    Uses PyJWT if available, falls back to manual HMAC-SHA256.

    Args:
        token: JWT token string.
        secret: Secret key for verification.
        algorithms: Allowed algorithms.

    Returns:
        Decoded payload dict or None if invalid.
    """
    try:
        import jwt

        payload: dict[str, object] = jwt.decode(
            token,
            secret,
            algorithms=algorithms or ["HS256"],
        )
        return payload
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None

    # Manual HMAC-SHA256 fallback
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        import base64

        payload_b = parts[1] + "=" * (4 - len(parts[1]) % 4)
        sig_b = parts[2] + "=" * (4 - len(parts[2]) % 4)

        # Verify signature
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        expected_sig = hmac.new(
            secret.encode(),
            signing_input,
            hashlib.sha256,
        ).digest()
        actual_sig = base64.urlsafe_b64decode(sig_b)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_json = base64.urlsafe_b64decode(payload_b)
        result: dict[str, object] = json.loads(payload_json)

        # Check expiration
        exp = result.get("exp")
        if exp is not None:
            exp_val = float(exp) if isinstance(exp, (int, float)) else 0
            if exp_val < time.time():
                return None

        return result
    except Exception:  # noqa: BLE001
        return None


# ── #38: Role-based access control ────────────────────────────────


class Role(StrEnum):
    """User roles for access control."""

    ADMIN = "admin"
    READER = "reader"
    CRAWLER = "crawler"


# Tool-to-role mapping
_TOOL_ROLES: dict[str, set[Role]] = {
    "search": {Role.ADMIN, Role.READER, Role.CRAWLER},
    "search_local": {Role.ADMIN, Role.READER, Role.CRAWLER},
    "fetch_page": {Role.ADMIN, Role.READER},
    "crawl_url": {Role.ADMIN, Role.CRAWLER},
    "network_stats": {Role.ADMIN, Role.READER},
    "batch_search": {Role.ADMIN, Role.READER},
    "suggest": {Role.ADMIN, Role.READER, Role.CRAWLER},
    "register_webhook": {Role.ADMIN},
    "analytics": {Role.ADMIN},
}


def check_role(
    tool_name: str,
    user_role: str | None,
) -> bool:
    """Check if a role has access to a tool.

    Args:
        tool_name: MCP tool name.
        user_role: User's role string.

    Returns:
        True if access is allowed.
    """
    if user_role is None:
        return True  # No RBAC configured
    allowed = _TOOL_ROLES.get(tool_name)
    if allowed is None:
        return True  # Unknown tool — allow by default
    try:
        role = Role(user_role)
    except ValueError:
        return False
    return role in allowed


# ── #39: IP allowlist/blocklist ───────────────────────────────────


@dataclass
class IPFilter:
    """IP-based access control filter."""

    allowlist: set[str] = field(default_factory=set)
    blocklist: set[str] = field(default_factory=set)

    def is_allowed(self, ip: str) -> bool:
        """Check if an IP address is allowed.

        If allowlist is non-empty, only listed IPs are allowed.
        Blocklist always takes priority.
        """
        if ip in self.blocklist:
            return False
        return not (self.allowlist and ip not in self.allowlist)

    def add_allow(self, ip: str) -> None:
        self.allowlist.add(ip)

    def add_block(self, ip: str) -> None:
        self.blocklist.add(ip)

    def remove_allow(self, ip: str) -> None:
        self.allowlist.discard(ip)

    def remove_block(self, ip: str) -> None:
        self.blocklist.discard(ip)


# ── #40: Webhook HMAC signing ─────────────────────────────────────


def sign_webhook_payload(
    payload: dict[str, object],
    secret: str,
) -> str:
    """Sign a webhook payload with HMAC-SHA256.

    Args:
        payload: Webhook event payload.
        secret: Signing secret.

    Returns:
        Hex-encoded HMAC signature.
    """
    body = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    sig = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={sig}"


def verify_webhook_signature(
    payload: dict[str, object],
    signature: str,
    secret: str,
) -> bool:
    """Verify a webhook payload signature.

    Args:
        payload: Webhook event payload.
        signature: Expected signature string.
        secret: Signing secret.

    Returns:
        True if signature is valid.
    """
    expected = sign_webhook_payload(payload, secret)
    return hmac.compare_digest(expected, signature)


# ── #41: API access audit log ─────────────────────────────────────


class AuditLog:
    """SQLite-based API access audit log."""

    def __init__(
        self,
        db_path: Path | str | None = None,
    ) -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                tool_name TEXT NOT NULL,
                api_key_hash TEXT,
                client_ip TEXT,
                arguments_json TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                latency_ms REAL DEFAULT 0
            )
        """)
        self._conn.commit()

    def log(
        self,
        tool_name: str,
        *,
        api_key: str | None = None,
        client_ip: str | None = None,
        arguments: dict[str, Any] | None = None,
        success: bool = True,
        latency_ms: float = 0,
    ) -> None:
        """Record an API access event."""
        key_hash = None
        if api_key:
            key_hash = hashlib.sha256(
                api_key.encode(),
            ).hexdigest()[:16]

        args_json = None
        if arguments:
            # Redact sensitive fields
            safe = {
                k: v
                for k, v in arguments.items()
                if k not in ("api_key", "password", "secret")
            }
            args_json = json.dumps(safe)[:1000]

        self._conn.execute(
            "INSERT INTO audit_log "
            "(timestamp, tool_name, api_key_hash, "
            "client_ip, arguments_json, success, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                tool_name,
                key_hash,
                client_ip,
                args_json,
                1 if success else 0,
                latency_ms,
            ),
        )
        self._conn.commit()

    def query(
        self,
        *,
        limit: int = 100,
        tool_name: str | None = None,
        since: float | None = None,
    ) -> list[dict[str, object]]:
        """Query audit log entries."""
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list[object] = []

        if tool_name:
            sql += " AND tool_name = ?"
            params.append(tool_name)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
