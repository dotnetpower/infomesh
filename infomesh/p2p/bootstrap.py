"""Bootstrap peer discovery — multi-source, resilient bootstrap.

Provides multiple discovery strategies for finding bootstrap nodes:
  1. **Static file** — ``bootstrap/nodes.json`` (bundled with package).
  2. **DNS SRV** — ``_infomesh._tcp.infomesh.io`` SRV records.
  3. **DNS TXT** — ``_infomesh-bootstrap.infomesh.io`` TXT records.
  4. **GitHub** — Fetch latest ``nodes.json`` from the repository.

All strategies are tried in parallel; results are merged and deduplicated.
The bootstrap list is cached locally so subsequent starts are faster
even if external sources are temporarily unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_DNS_DOMAIN = "infomesh.io"
SRV_SERVICE = "_infomesh._tcp"
TXT_PREFIX = "_infomesh-bootstrap"

GITHUB_NODES_URL = (
    "https://raw.githubusercontent.com/dotnetpower/infomesh/main/bootstrap/nodes.json"
)

BOOTSTRAP_CACHE_FILE = "bootstrap_cache.json"
BOOTSTRAP_CACHE_TTL = 3600  # 1 hour cache validity

# Rate limits for bootstrap requests
BOOTSTRAP_RATE_LIMIT_PER_MIN = 10
BOOTSTRAP_MAX_PEERS_SEED = 50  # Max peers to send on initial connect

# Health check constants
HEALTH_CHECK_TIMEOUT = 5.0  # seconds
HEALTH_CHECK_INTERVAL = 60.0  # seconds


# ── Data classes ───────────────────────────────────────────────────────


@dataclass
class BootstrapNode:
    """A discovered bootstrap node."""

    addr: str  # multiaddr string
    source: str  # "static", "dns_srv", "dns_txt", "github", "cache"
    region: str = ""
    last_seen: float = 0.0
    healthy: bool = True
    latency_ms: float = 0.0

    @property
    def host_port(self) -> tuple[str, int]:
        """Extract host and port from multiaddr string.

        Returns:
            Tuple of (host, port).  Falls back to ("", 0) on parse
            failure.
        """
        parts = self.addr.split("/")
        host = ""
        port = 0
        for i, part in enumerate(parts):
            if part in ("ip4", "ip6", "dns4", "dns6") and i + 1 < len(parts):
                host = parts[i + 1]
            elif part == "tcp" and i + 1 < len(parts):
                with contextlib.suppress(ValueError):
                    port = int(parts[i + 1])
        return host, port


@dataclass
class BootstrapResult:
    """Aggregated result from all bootstrap discovery sources."""

    nodes: list[BootstrapNode] = field(default_factory=list)
    sources_tried: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    discovery_ms: float = 0.0

    @property
    def addrs(self) -> list[str]:
        """Return deduplicated multiaddr strings."""
        seen: set[str] = set()
        result: list[str] = []
        for node in self.nodes:
            if node.addr not in seen:
                seen.add(node.addr)
                result.append(node.addr)
        return result


@dataclass
class BootstrapHealth:
    """Health status of a bootstrap node."""

    addr: str
    reachable: bool
    latency_ms: float = 0.0
    peer_count: int = 0
    uptime_seconds: float = 0.0
    last_check: float = 0.0


# ── Discovery strategies ───────────────────────────────────────────────


def discover_from_static(nodes_json: list[dict[str, str]]) -> list[BootstrapNode]:
    """Parse static nodes.json entries.

    Args:
        nodes_json: List of dicts with at least ``addr`` key.

    Returns:
        List of :class:`BootstrapNode` from the static file.
    """
    nodes: list[BootstrapNode] = []
    for entry in nodes_json:
        if isinstance(entry, dict) and "addr" in entry:
            nodes.append(
                BootstrapNode(
                    addr=entry["addr"],
                    source="static",
                    region=entry.get("region", ""),
                    last_seen=time.time(),
                )
            )
    return nodes


async def discover_from_dns_srv(
    domain: str = DEFAULT_DNS_DOMAIN,
) -> list[BootstrapNode]:
    """Query DNS SRV records for bootstrap nodes.

    Looks up ``_infomesh._tcp.<domain>`` SRV records to find bootstrap
    node hosts and ports.

    Args:
        domain: DNS domain to query.

    Returns:
        List of :class:`BootstrapNode` discovered via DNS SRV.
    """
    nodes: list[BootstrapNode] = []
    srv_name = f"{SRV_SERVICE}.{domain}"

    try:
        loop = asyncio.get_running_loop()
        # DNS SRV lookup via socket (stdlib — no dnspython dependency)
        answers = await loop.run_in_executor(None, _resolve_srv, srv_name)
        for host, port in answers:
            addr = f"/dns4/{host}/tcp/{port}"
            nodes.append(
                BootstrapNode(
                    addr=addr,
                    source="dns_srv",
                    last_seen=time.time(),
                )
            )
        if nodes:
            logger.info(
                "bootstrap_dns_srv_discovered",
                domain=domain,
                count=len(nodes),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "bootstrap_dns_srv_failed",
            domain=domain,
            error=str(exc),
        )

    return nodes


async def discover_from_dns_txt(
    domain: str = DEFAULT_DNS_DOMAIN,
) -> list[BootstrapNode]:
    """Query DNS TXT records for bootstrap multiaddrs.

    Looks up ``_infomesh-bootstrap.<domain>`` TXT records.
    Each TXT record should contain a multiaddr string.

    Args:
        domain: DNS domain to query.

    Returns:
        List of :class:`BootstrapNode` discovered via DNS TXT.
    """
    nodes: list[BootstrapNode] = []
    txt_name = f"{TXT_PREFIX}.{domain}"

    try:
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(None, _resolve_txt, txt_name)
        for record in records:
            record = record.strip().strip('"')
            if record.startswith("/"):
                nodes.append(
                    BootstrapNode(
                        addr=record,
                        source="dns_txt",
                        last_seen=time.time(),
                    )
                )
        if nodes:
            logger.info(
                "bootstrap_dns_txt_discovered",
                domain=domain,
                count=len(nodes),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "bootstrap_dns_txt_failed",
            domain=domain,
            error=str(exc),
        )

    return nodes


async def discover_from_github(
    url: str = GITHUB_NODES_URL,
    timeout: float = 10.0,
) -> list[BootstrapNode]:
    """Fetch latest nodes.json from GitHub.

    Args:
        url: Raw GitHub URL to nodes.json.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of :class:`BootstrapNode` from GitHub.
    """
    nodes: list[BootstrapNode] = []

    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            entries = resp.json()

            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and "addr" in entry:
                        nodes.append(
                            BootstrapNode(
                                addr=entry["addr"],
                                source="github",
                                region=entry.get("region", ""),
                                last_seen=time.time(),
                            )
                        )
        if nodes:
            logger.info(
                "bootstrap_github_discovered",
                url=url,
                count=len(nodes),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "bootstrap_github_failed",
            url=url,
            error=str(exc),
        )

    return nodes


# ── DNS resolution helpers (stdlib) ────────────────────────────────────


def _resolve_srv(name: str) -> list[tuple[str, int]]:
    """Resolve DNS SRV records using stdlib socket.

    Falls back to empty list if the system resolver does not
    support SRV lookups (some minimal containers).
    """
    results: list[tuple[str, int]] = []
    try:
        answers = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
        for _family, _kind, _proto, _canonname, sockaddr in answers:
            host = str(sockaddr[0])
            port_val = int(sockaddr[1])
            if port_val > 0:
                results.append((host, port_val))
    except socket.gaierror:
        pass
    return results


def _resolve_txt(name: str) -> list[str]:
    """Resolve DNS TXT records.

    Uses ``socket.getaddrinfo`` as a minimal approach.  For full
    TXT record support, ``dnspython`` would be needed.  This
    implementation returns an empty list when TXT lookups are not
    supported by the system resolver.
    """
    # stdlib socket doesn't natively support TXT records.
    # This is a placeholder that returns empty — production deployments
    # should install dnspython for full DNS support.
    try:
        # Attempt to import dnspython if available
        import dns.resolver

        answers = dns.resolver.resolve(name, "TXT")
        return [
            rdata.strings[0].decode("utf-8", errors="replace")
            for rdata in answers
            if rdata.strings
        ]
    except ImportError:
        logger.debug(
            "dnspython_not_installed",
            hint="Install dnspython for DNS TXT bootstrap discovery",
        )
    except Exception:  # noqa: BLE001
        pass
    return []


# ── Aggregated discovery ───────────────────────────────────────────────


async def discover_bootstrap_nodes(
    static_nodes: list[dict[str, str]] | None = None,
    dns_domain: str = DEFAULT_DNS_DOMAIN,
    github_url: str = GITHUB_NODES_URL,
    cache_dir: Path | None = None,
    use_dns: bool = True,
    use_github: bool = True,
) -> BootstrapResult:
    """Discover bootstrap nodes from all available sources.

    Tries all configured sources in parallel, merges results, and
    deduplicates by multiaddr.  Results are cached locally.

    Args:
        static_nodes: Pre-loaded nodes.json entries (optional).
        dns_domain: Domain for DNS SRV/TXT lookups.
        github_url: URL to fetch latest nodes.json.
        cache_dir: Directory for bootstrap cache file.
        use_dns: Whether to try DNS discovery.
        use_github: Whether to try GitHub discovery.

    Returns:
        :class:`BootstrapResult` with merged, deduplicated nodes.
    """
    start = time.time()
    result = BootstrapResult()
    all_nodes: list[BootstrapNode] = []

    # 1) Static nodes (synchronous)
    if static_nodes:
        result.sources_tried.append("static")
        static = discover_from_static(static_nodes)
        if static:
            all_nodes.extend(static)
            result.sources_succeeded.append("static")

    # 2) Cached nodes (synchronous)
    if cache_dir:
        result.sources_tried.append("cache")
        cached = _load_cache(cache_dir)
        if cached:
            all_nodes.extend(cached)
            result.sources_succeeded.append("cache")

    # 3) Async sources (parallel)
    coro_list: list[asyncio.Task[list[BootstrapNode]]] = []
    names: list[str] = []
    if use_dns:
        names.append("dns_srv")
        coro_list.append(asyncio.ensure_future(discover_from_dns_srv(dns_domain)))
        names.append("dns_txt")
        coro_list.append(asyncio.ensure_future(discover_from_dns_txt(dns_domain)))
    if use_github:
        names.append("github")
        coro_list.append(asyncio.ensure_future(discover_from_github(github_url)))

    if coro_list:
        result.sources_tried.extend(names)

        gathered: list[list[BootstrapNode] | BaseException] = await asyncio.gather(
            *coro_list,
            return_exceptions=True,
        )

        for name, nodes_or_exc in zip(names, gathered, strict=True):
            if isinstance(nodes_or_exc, list):
                all_nodes.extend(nodes_or_exc)
                if nodes_or_exc:
                    result.sources_succeeded.append(name)
            else:
                logger.debug(
                    "bootstrap_source_error",
                    source=name,
                    error=str(nodes_or_exc),
                )

    # Deduplicate by addr
    seen: set[str] = set()
    for node in all_nodes:
        if node.addr not in seen:
            seen.add(node.addr)
            result.nodes.append(node)

    result.discovery_ms = (time.time() - start) * 1000

    # Update cache
    if cache_dir and result.nodes:
        _save_cache(cache_dir, result.nodes)

    logger.info(
        "bootstrap_discovery_complete",
        total_nodes=len(result.nodes),
        sources_succeeded=result.sources_succeeded,
        elapsed_ms=round(result.discovery_ms, 1),
    )

    return result


# ── Health checking ────────────────────────────────────────────────────


async def check_bootstrap_health(
    node: BootstrapNode,
    timeout: float = HEALTH_CHECK_TIMEOUT,
) -> BootstrapHealth:
    """Check if a bootstrap node is reachable via TCP.

    Args:
        node: The bootstrap node to check.
        timeout: Connection timeout in seconds.

    Returns:
        :class:`BootstrapHealth` with reachability status.
    """
    host, port = node.host_port
    if not host or port == 0:
        return BootstrapHealth(
            addr=node.addr,
            reachable=False,
            last_check=time.time(),
        )

    start = time.time()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        latency = (time.time() - start) * 1000
        writer.close()
        await writer.wait_closed()

        return BootstrapHealth(
            addr=node.addr,
            reachable=True,
            latency_ms=latency,
            last_check=time.time(),
        )
    except (OSError, TimeoutError):
        return BootstrapHealth(
            addr=node.addr,
            reachable=False,
            latency_ms=(time.time() - start) * 1000,
            last_check=time.time(),
        )


async def check_all_bootstrap_health(
    nodes: list[BootstrapNode],
    timeout: float = HEALTH_CHECK_TIMEOUT,
) -> list[BootstrapHealth]:
    """Check health of all bootstrap nodes in parallel.

    Args:
        nodes: List of bootstrap nodes to check.
        timeout: Per-node connection timeout.

    Returns:
        List of :class:`BootstrapHealth` results.
    """
    tasks = [check_bootstrap_health(n, timeout) for n in nodes]
    return list(await asyncio.gather(*tasks))


# ── Peer seeding ───────────────────────────────────────────────────────


def select_seed_peers(
    known_peers: list[dict[str, object]],
    max_peers: int = BOOTSTRAP_MAX_PEERS_SEED,
) -> list[dict[str, object]]:
    """Select top-N healthy peers to send to a new node.

    Peers are ranked by uptime and last-seen recency.

    Args:
        known_peers: List of peer info dicts with ``peer_id``,
            ``addr``, ``last_seen``, ``uptime`` keys.
        max_peers: Maximum number of peers to return.

    Returns:
        Sorted subset of peers for seeding.
    """
    now = time.time()

    def score(peer: dict[str, object]) -> float:
        last_seen = peer.get("last_seen", 0)
        uptime = peer.get("uptime", 0)
        ls = float(last_seen) if isinstance(last_seen, (int, float)) else 0.0
        ut = float(uptime) if isinstance(uptime, (int, float)) else 0.0
        recency = max(0.0, 1.0 - (now - ls) / 86400)
        return recency * 0.6 + min(ut / 86400, 1.0) * 0.4

    sorted_peers = sorted(known_peers, key=score, reverse=True)
    return sorted_peers[:max_peers]


# ── Rate limiting ──────────────────────────────────────────────────────


class BootstrapRateLimiter:
    """Simple sliding-window rate limiter for bootstrap requests.

    Prevents abuse of bootstrap nodes by limiting requests per IP.
    """

    def __init__(
        self,
        max_per_minute: int = BOOTSTRAP_RATE_LIMIT_PER_MIN,
        window_seconds: float = 60.0,
    ) -> None:
        self._max = max_per_minute
        self._window = window_seconds
        self._requests: dict[str, list[float]] = {}

    def allow(self, client_id: str) -> bool:
        """Check if a request from *client_id* is allowed.

        Args:
            client_id: Identifier for the client (peer ID or IP).

        Returns:
            ``True`` if the request is within rate limits.
        """
        now = time.time()
        cutoff = now - self._window

        if client_id not in self._requests:
            self._requests[client_id] = [now]
            return True

        # Prune old entries
        self._requests[client_id] = [t for t in self._requests[client_id] if t > cutoff]

        if len(self._requests[client_id]) >= self._max:
            return False

        self._requests[client_id].append(now)
        return True

    def reset(self, client_id: str) -> None:
        """Clear rate limit state for a client."""
        self._requests.pop(client_id, None)

    @property
    def tracked_clients(self) -> int:
        """Number of clients currently being tracked."""
        return len(self._requests)

    def cleanup(self) -> int:
        """Remove expired entries. Returns number of clients removed."""
        now = time.time()
        cutoff = now - self._window
        expired = [k for k, v in self._requests.items() if all(t <= cutoff for t in v)]
        for k in expired:
            del self._requests[k]
        return len(expired)


# ── Cache management ───────────────────────────────────────────────────


def _load_cache(cache_dir: Path) -> list[BootstrapNode]:
    """Load cached bootstrap nodes from disk."""
    cache_file = cache_dir / BOOTSTRAP_CACHE_FILE
    if not cache_file.exists():
        return []

    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []

        cached_at = data.get("cached_at", 0)
        if not isinstance(cached_at, (int, float)):
            cached_at = 0

        # Check TTL
        if time.time() - float(cached_at) > BOOTSTRAP_CACHE_TTL:
            logger.debug(
                "bootstrap_cache_expired",
                age_hours=round((time.time() - float(cached_at)) / 3600, 1),
            )
            return []

        entries = data.get("nodes", [])
        if not isinstance(entries, list):
            return []

        nodes: list[BootstrapNode] = []
        for entry in entries:
            if isinstance(entry, dict) and "addr" in entry:
                addr = entry["addr"]
                if isinstance(addr, str):
                    nodes.append(
                        BootstrapNode(
                            addr=addr,
                            source="cache",
                            region=str(entry.get("region", "")),
                            last_seen=float(
                                entry.get("last_seen", 0)
                                if isinstance(
                                    entry.get("last_seen", 0),
                                    (int, float),
                                )
                                else 0
                            ),
                        )
                    )

        if nodes:
            logger.debug(
                "bootstrap_cache_loaded",
                count=len(nodes),
            )
        return nodes
    except (json.JSONDecodeError, OSError):
        return []


def _save_cache(cache_dir: Path, nodes: list[BootstrapNode]) -> None:
    """Save bootstrap nodes to local cache file."""
    cache_file = cache_dir / BOOTSTRAP_CACHE_FILE
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "cached_at": time.time(),
            "nodes": [
                {
                    "addr": n.addr,
                    "source": n.source,
                    "region": n.region,
                    "last_seen": n.last_seen,
                }
                for n in nodes
            ],
        }
        cache_file.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
        logger.debug("bootstrap_cache_saved", count=len(nodes))
    except OSError as exc:
        logger.debug("bootstrap_cache_save_failed", error=str(exc))
