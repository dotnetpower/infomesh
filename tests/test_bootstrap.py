"""Tests for bootstrap peer discovery module.

Covers:
  - Static node parsing
  - DNS SRV/TXT discovery
  - GitHub discovery
  - Aggregated multi-source discovery
  - Bootstrap cache
  - Health checking
  - Peer seeding
  - Rate limiting
  - BootstrapNode properties
  - NetworkConfig bootstrap fields
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infomesh.p2p.bootstrap import (
    BOOTSTRAP_CACHE_FILE,
    BOOTSTRAP_CACHE_TTL,
    BOOTSTRAP_MAX_PEERS_SEED,
    BootstrapHealth,
    BootstrapNode,
    BootstrapRateLimiter,
    BootstrapResult,
    _load_cache,
    _save_cache,
    check_bootstrap_health,
    discover_bootstrap_nodes,
    discover_from_dns_srv,
    discover_from_dns_txt,
    discover_from_github,
    discover_from_static,
    select_seed_peers,
)

# ── BootstrapNode tests ───────────────────────────────────────────────


class TestBootstrapNode:
    def test_basic_fields(self) -> None:
        node = BootstrapNode(
            addr="/ip4/1.2.3.4/tcp/4001/p2p/12D3KooW...",
            source="static",
            region="eastus",
        )
        assert node.addr == "/ip4/1.2.3.4/tcp/4001/p2p/12D3KooW..."
        assert node.source == "static"
        assert node.region == "eastus"
        assert node.healthy is True

    def test_host_port_ipv4(self) -> None:
        node = BootstrapNode(
            addr="/ip4/10.0.0.1/tcp/4001/p2p/12D3KooW...",
            source="static",
        )
        host, port = node.host_port
        assert host == "10.0.0.1"
        assert port == 4001

    def test_host_port_dns4(self) -> None:
        node = BootstrapNode(
            addr="/dns4/bootstrap.infomesh.io/tcp/4001",
            source="dns_srv",
        )
        host, port = node.host_port
        assert host == "bootstrap.infomesh.io"
        assert port == 4001

    def test_host_port_empty(self) -> None:
        node = BootstrapNode(addr="/invalid", source="test")
        host, port = node.host_port
        assert host == ""
        assert port == 0

    def test_host_port_bad_port(self) -> None:
        node = BootstrapNode(
            addr="/ip4/1.2.3.4/tcp/notaport",
            source="test",
        )
        host, port = node.host_port
        assert host == "1.2.3.4"
        assert port == 0  # Parse failure → 0


class TestBootstrapResult:
    def test_addrs_dedup(self) -> None:
        result = BootstrapResult(
            nodes=[
                BootstrapNode(addr="/ip4/1.1.1.1/tcp/4001", source="a"),
                BootstrapNode(addr="/ip4/2.2.2.2/tcp/4001", source="b"),
                BootstrapNode(addr="/ip4/1.1.1.1/tcp/4001", source="c"),
            ],
        )
        addrs = result.addrs
        assert len(addrs) == 2
        assert "/ip4/1.1.1.1/tcp/4001" in addrs


# ── Static discovery tests ────────────────────────────────────────────


class TestStaticDiscovery:
    def test_parse_nodes_json(self) -> None:
        entries = [
            {"addr": "/ip4/1.2.3.4/tcp/4001/p2p/XYZ", "region": "eastus"},
            {"addr": "/ip4/5.6.7.8/tcp/4001/p2p/ABC"},
        ]
        nodes = discover_from_static(entries)
        assert len(nodes) == 2
        assert nodes[0].source == "static"
        assert nodes[0].region == "eastus"
        assert nodes[1].region == ""

    def test_skip_invalid_entries(self) -> None:
        entries = [
            {"addr": "/ip4/1.2.3.4/tcp/4001"},
            "not-a-dict",  # type: ignore[list-item]
            {"no_addr_key": True},
        ]
        nodes = discover_from_static(entries)
        assert len(nodes) == 1

    def test_empty_list(self) -> None:
        assert discover_from_static([]) == []


# ── DNS discovery tests ───────────────────────────────────────────────


class TestDNSSrvDiscovery:
    @pytest.mark.asyncio
    async def test_srv_success(self) -> None:
        with patch(
            "infomesh.p2p.bootstrap._resolve_srv",
            return_value=[("boot1.infomesh.io", 4001)],
        ):
            nodes = await discover_from_dns_srv("infomesh.io")
            assert len(nodes) == 1
            assert nodes[0].source == "dns_srv"
            assert "boot1.infomesh.io" in nodes[0].addr

    @pytest.mark.asyncio
    async def test_srv_failure(self) -> None:
        with patch(
            "infomesh.p2p.bootstrap._resolve_srv",
            side_effect=Exception("DNS error"),
        ):
            nodes = await discover_from_dns_srv("infomesh.io")
            assert nodes == []

    @pytest.mark.asyncio
    async def test_srv_empty(self) -> None:
        with patch(
            "infomesh.p2p.bootstrap._resolve_srv",
            return_value=[],
        ):
            nodes = await discover_from_dns_srv("infomesh.io")
            assert nodes == []


class TestDNSTxtDiscovery:
    @pytest.mark.asyncio
    async def test_txt_success(self) -> None:
        with patch(
            "infomesh.p2p.bootstrap._resolve_txt",
            return_value=["/ip4/10.0.0.1/tcp/4001/p2p/12D3KooW..."],
        ):
            nodes = await discover_from_dns_txt("infomesh.io")
            assert len(nodes) == 1
            assert nodes[0].source == "dns_txt"

    @pytest.mark.asyncio
    async def test_txt_non_multiaddr_skipped(self) -> None:
        with patch(
            "infomesh.p2p.bootstrap._resolve_txt",
            return_value=["not-a-multiaddr", "also-not"],
        ):
            nodes = await discover_from_dns_txt("infomesh.io")
            assert nodes == []

    @pytest.mark.asyncio
    async def test_txt_failure(self) -> None:
        with patch(
            "infomesh.p2p.bootstrap._resolve_txt",
            side_effect=Exception("DNS error"),
        ):
            nodes = await discover_from_dns_txt("infomesh.io")
            assert nodes == []


# ── GitHub discovery tests ─────────────────────────────────────────────


class TestGitHubDiscovery:
    @pytest.mark.asyncio
    async def test_github_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"addr": "/ip4/20.42.12.161/tcp/4001/p2p/XYZ", "region": "eastus"}
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            nodes = await discover_from_github()
            assert len(nodes) == 1
            assert nodes[0].source == "github"
            assert nodes[0].region == "eastus"

    @pytest.mark.asyncio
    async def test_github_failure(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            nodes = await discover_from_github()
            assert nodes == []


# ── Aggregated discovery tests ─────────────────────────────────────────


class TestAggregatedDiscovery:
    @pytest.mark.asyncio
    async def test_static_only(self) -> None:
        static = [{"addr": "/ip4/1.2.3.4/tcp/4001"}]
        result = await discover_bootstrap_nodes(
            static_nodes=static,
            use_dns=False,
            use_github=False,
        )
        assert len(result.nodes) == 1
        assert "static" in result.sources_succeeded

    @pytest.mark.asyncio
    async def test_dedup_across_sources(self) -> None:
        static = [{"addr": "/ip4/1.2.3.4/tcp/4001"}]
        with (
            patch(
                "infomesh.p2p.bootstrap.discover_from_dns_srv",
                return_value=[
                    BootstrapNode(addr="/ip4/1.2.3.4/tcp/4001", source="dns_srv"),
                    BootstrapNode(addr="/ip4/5.6.7.8/tcp/4001", source="dns_srv"),
                ],
            ),
            patch(
                "infomesh.p2p.bootstrap.discover_from_dns_txt",
                return_value=[],
            ),
        ):
            result = await discover_bootstrap_nodes(
                static_nodes=static,
                use_dns=True,
                use_github=False,
            )
            assert len(result.nodes) == 2  # deduped

    @pytest.mark.asyncio
    async def test_cache_dir(self, tmp_path: Path) -> None:
        static = [{"addr": "/ip4/1.2.3.4/tcp/4001"}]
        result = await discover_bootstrap_nodes(
            static_nodes=static,
            cache_dir=tmp_path,
            use_dns=False,
            use_github=False,
        )
        assert len(result.nodes) == 1
        # Cache file should be written
        cache_file = tmp_path / BOOTSTRAP_CACHE_FILE
        assert cache_file.exists()

    @pytest.mark.asyncio
    async def test_no_sources(self) -> None:
        result = await discover_bootstrap_nodes(
            use_dns=False,
            use_github=False,
        )
        assert len(result.nodes) == 0


# ── Cache tests ────────────────────────────────────────────────────────


class TestBootstrapCache:
    def test_save_and_load(self, tmp_path: Path) -> None:
        nodes = [
            BootstrapNode(
                addr="/ip4/1.2.3.4/tcp/4001",
                source="static",
                region="eastus",
            )
        ]
        _save_cache(tmp_path, nodes)
        loaded = _load_cache(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].addr == "/ip4/1.2.3.4/tcp/4001"
        assert loaded[0].source == "cache"

    def test_load_expired_cache(self, tmp_path: Path) -> None:
        cache_file = tmp_path / BOOTSTRAP_CACHE_FILE
        data = {
            "cached_at": time.time() - BOOTSTRAP_CACHE_TTL - 100,
            "nodes": [{"addr": "/ip4/1.2.3.4/tcp/4001"}],
        }
        cache_file.write_text(json.dumps(data), encoding="utf-8")
        loaded = _load_cache(tmp_path)
        assert loaded == []

    def test_load_missing_cache(self, tmp_path: Path) -> None:
        loaded = _load_cache(tmp_path)
        assert loaded == []

    def test_load_corrupt_cache(self, tmp_path: Path) -> None:
        cache_file = tmp_path / BOOTSTRAP_CACHE_FILE
        cache_file.write_text("not json", encoding="utf-8")
        loaded = _load_cache(tmp_path)
        assert loaded == []

    def test_load_invalid_structure(self, tmp_path: Path) -> None:
        cache_file = tmp_path / BOOTSTRAP_CACHE_FILE
        cache_file.write_text('"just a string"', encoding="utf-8")
        loaded = _load_cache(tmp_path)
        assert loaded == []


# ── Health check tests ─────────────────────────────────────────────────


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_unreachable_node(self) -> None:
        node = BootstrapNode(
            addr="/ip4/192.0.2.1/tcp/4001",  # TEST-NET, unreachable
            source="test",
        )
        health = await check_bootstrap_health(node, timeout=0.5)
        assert isinstance(health, BootstrapHealth)
        assert health.reachable is False

    @pytest.mark.asyncio
    async def test_invalid_addr(self) -> None:
        node = BootstrapNode(addr="/invalid", source="test")
        health = await check_bootstrap_health(node, timeout=0.5)
        assert health.reachable is False

    def test_health_fields(self) -> None:
        health = BootstrapHealth(
            addr="/ip4/1.2.3.4/tcp/4001",
            reachable=True,
            latency_ms=42.0,
            peer_count=10,
            uptime_seconds=3600.0,
            last_check=time.time(),
        )
        assert health.reachable is True
        assert health.latency_ms == 42.0


# ── Peer seeding tests ─────────────────────────────────────────────────


class TestPeerSeeding:
    def test_select_empty(self) -> None:
        assert select_seed_peers([]) == []

    def test_select_max_limit(self) -> None:
        peers = [
            {"peer_id": f"peer{i}", "last_seen": time.time(), "uptime": 1000}
            for i in range(100)
        ]
        selected = select_seed_peers(peers, max_peers=10)
        assert len(selected) == 10

    def test_select_default_max(self) -> None:
        peers = [
            {"peer_id": f"peer{i}", "last_seen": time.time(), "uptime": 1000}
            for i in range(100)
        ]
        selected = select_seed_peers(peers)
        assert len(selected) == BOOTSTRAP_MAX_PEERS_SEED

    def test_select_ranking(self) -> None:
        now = time.time()
        peers = [
            {"peer_id": "stale", "last_seen": now - 100000, "uptime": 100},
            {"peer_id": "fresh", "last_seen": now, "uptime": 86400},
            {"peer_id": "medium", "last_seen": now - 3600, "uptime": 43200},
        ]
        selected = select_seed_peers(peers, max_peers=2)
        # Fresh + high uptime should rank first
        ids = [p["peer_id"] for p in selected]
        assert ids[0] == "fresh"


# ── Rate limiter tests ─────────────────────────────────────────────────


class TestRateLimiter:
    def test_allow_first_request(self) -> None:
        limiter = BootstrapRateLimiter(max_per_minute=5)
        assert limiter.allow("peer1") is True

    def test_block_after_limit(self) -> None:
        limiter = BootstrapRateLimiter(max_per_minute=3)
        assert limiter.allow("peer1") is True
        assert limiter.allow("peer1") is True
        assert limiter.allow("peer1") is True
        assert limiter.allow("peer1") is False

    def test_different_clients_independent(self) -> None:
        limiter = BootstrapRateLimiter(max_per_minute=1)
        assert limiter.allow("peer1") is True
        assert limiter.allow("peer2") is True
        assert limiter.allow("peer1") is False

    def test_reset_client(self) -> None:
        limiter = BootstrapRateLimiter(max_per_minute=1)
        assert limiter.allow("peer1") is True
        assert limiter.allow("peer1") is False
        limiter.reset("peer1")
        assert limiter.allow("peer1") is True

    def test_tracked_clients(self) -> None:
        limiter = BootstrapRateLimiter()
        limiter.allow("a")
        limiter.allow("b")
        assert limiter.tracked_clients == 2

    def test_cleanup_expired(self) -> None:
        limiter = BootstrapRateLimiter(max_per_minute=10, window_seconds=0.01)
        limiter.allow("peer1")
        import time as _time

        _time.sleep(0.02)
        removed = limiter.cleanup()
        assert removed == 1
        assert limiter.tracked_clients == 0


# ── NetworkConfig bootstrap fields ─────────────────────────────────────


class TestNetworkConfigBootstrapFields:
    def test_defaults(self) -> None:
        from infomesh.config import NetworkConfig

        cfg = NetworkConfig()
        assert cfg.bootstrap_dns is True
        assert cfg.bootstrap_github is True
        assert cfg.bootstrap_dns_domain == "infomesh.io"

    def test_custom_values(self) -> None:
        from infomesh.config import NetworkConfig

        cfg = NetworkConfig(
            bootstrap_dns=False,
            bootstrap_github=False,
            bootstrap_dns_domain="custom.io",
        )
        assert cfg.bootstrap_dns is False
        assert cfg.bootstrap_github is False
        assert cfg.bootstrap_dns_domain == "custom.io"
