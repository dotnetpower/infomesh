"""Cryptographic hashing utilities for InfoMesh.

Centralizes SHA-256 hashing patterns used across crawling, attestation,
caching, and trust modules.  All callers should import from here instead
of inlining ``hashlib.sha256(...)`` directly.
"""

from __future__ import annotations

import hashlib


def content_hash(data: bytes | str) -> str:
    """Compute the full SHA-256 hex digest of *data*.

    Args:
        data: Raw bytes or text string (encoded as UTF-8).

    Returns:
        Lowercase 64-character hex SHA-256 digest.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def short_hash(data: bytes | str, *, length: int = 16) -> str:
    """Compute a truncated SHA-256 hex digest.

    Useful for log-friendly identifiers and cache keys where the full
    64-character digest is unnecessarily long.

    Args:
        data: Raw bytes or text string.
        length: Number of hex characters to keep (default 16).

    Returns:
        Truncated hex digest.
    """
    return content_hash(data)[:length]
