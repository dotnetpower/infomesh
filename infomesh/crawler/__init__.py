"""Web crawler."""

from __future__ import annotations

import ssl
from pathlib import Path

# Maximum response body size (10 MB) — shared across crawl modules
MAX_RESPONSE_BYTES: int = 10 * 1024 * 1024

# Well-known system CA bundle paths (Linux / macOS)
_SYSTEM_CA_PATHS: list[str] = [
    "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL/CentOS
    "/etc/ssl/cert.pem",                    # Alpine/macOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora
]


def create_ssl_context() -> ssl.SSLContext | bool:
    """Create an SSL context that tries system CA bundles first.

    Returns an ``ssl.SSLContext`` loaded from the first available system
    CA bundle, or ``True`` (httpx default verify) if none found.
    """
    for ca_path in _SYSTEM_CA_PATHS:
        if Path(ca_path).is_file():
            ctx = ssl.create_default_context(cafile=ca_path)
            return ctx
    # Fallback: let httpx use its bundled certifi
    return True
