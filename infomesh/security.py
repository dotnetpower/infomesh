"""URL validation — SSRF protection for all HTTP-fetching code paths.

Prevents crawling / fetching of internal network resources by validating
URL scheme, hostname, and resolved IP addresses against allow/deny lists.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger()

# Maximum URL length to prevent abuse
MAX_URL_LENGTH = 4096

# Allowed schemes
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Private / reserved IP networks to block
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),  # multicast
]

# Hostname patterns to block
_BLOCKED_HOSTNAME_RE = re.compile(
    r"^(localhost|.*\.local|.*\.internal|.*\.intranet|metadata\.google\.internal)$",
    re.IGNORECASE,
)

# Cloud metadata endpoints
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "[fd00:ec2::254]",
    }
)


class SSRFError(Exception):
    """Raised when a URL fails SSRF validation."""


def validate_url(url: str, *, resolve_dns: bool = False) -> str:
    """Validate a URL for safe external fetching.

    Checks:
    1. URL length limit
    2. Scheme is http or https
    3. Hostname is not empty and not a private/reserved name
    4. If ``resolve_dns=True``, resolves hostname and checks IP

    Args:
        url: URL string to validate.
        resolve_dns: Whether to resolve DNS and check the IP address.

    Returns:
        The validated URL (unchanged).

    Raises:
        SSRFError: If the URL fails any validation check.
    """
    if not url or not isinstance(url, str):
        raise SSRFError("Empty or invalid URL")

    if len(url) > MAX_URL_LENGTH:
        raise SSRFError(f"URL exceeds maximum length of {MAX_URL_LENGTH}")

    parsed = urlparse(url)

    # Check scheme
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(
            f"Scheme '{parsed.scheme}' not allowed; "
            f"must be one of {sorted(_ALLOWED_SCHEMES)}"
        )

    # Check hostname
    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL has no hostname")

    # Block known dangerous hostnames
    if hostname in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"Hostname '{hostname}' is blocked (metadata endpoint)")

    if _BLOCKED_HOSTNAME_RE.match(hostname):
        raise SSRFError(f"Hostname '{hostname}' matches blocked pattern")

    # Check if hostname is an IP literal
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_blocked_ip(ip):
            raise SSRFError(f"IP address {ip} is in a private/reserved range")
    except ValueError:
        # Not an IP literal — hostname is a domain name
        pass

    # Optional DNS resolution check
    if resolve_dns:
        _check_resolved_ip(hostname)

    return url


def validate_url_post_redirect(final_url: str) -> str:
    """Validate a URL after HTTP redirect resolution.

    Should be called on the final URL after following redirects to ensure
    the redirect didn't land on an internal resource.

    Args:
        final_url: The URL after redirect resolution.

    Returns:
        The validated URL.

    Raises:
        SSRFError: If the redirected URL targets a private resource.
    """
    return validate_url(final_url, resolve_dns=False)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP is in any blocked network."""
    return any(ip in network for network in _BLOCKED_NETWORKS)


def _check_resolved_ip(hostname: str) -> None:
    """Resolve hostname and verify all IPs are public."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            if _is_blocked_ip(ip):
                raise SSRFError(f"Hostname '{hostname}' resolves to private IP {ip}")
        except ValueError:
            continue
