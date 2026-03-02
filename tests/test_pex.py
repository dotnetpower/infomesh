"""Tests for the PEX (Peer Exchange) protocol module."""

from __future__ import annotations

import time

import pytest

from infomesh.p2p.pex import (
    PEX_MAX_PEERS,
    PEX_MIN_INTERVAL,
    PeerExchange,
    PEXPeerInfo,
    _is_valid_multiaddr,
)

# ── Multiaddr validation ──────────────────────────────────────────


class TestIsValidMultiaddr:
    def test_valid_ipv4(self) -> None:
        addr = "/ip4/192.168.1.1/tcp/4001/p2p/12D3KooWTest"
        assert _is_valid_multiaddr(addr) is True

    def test_valid_ipv6(self) -> None:
        addr = "/ip6/::1/tcp/4001/p2p/12D3KooWTest"
        assert _is_valid_multiaddr(addr) is True

    def test_missing_p2p(self) -> None:
        addr = "/ip4/192.168.1.1/tcp/4001"
        assert _is_valid_multiaddr(addr) is False

    def test_missing_ip_prefix(self) -> None:
        addr = "/dns4/example.com/tcp/4001/p2p/12D3KooWTest"
        assert _is_valid_multiaddr(addr) is False

    def test_empty_string(self) -> None:
        assert _is_valid_multiaddr("") is False

    def test_p2p_only(self) -> None:
        assert _is_valid_multiaddr("/p2p/12D3KooWTest") is False


# ── Rate limiting ──────────────────────────────────────────────────


class TestRateLimiting:
    def test_first_request_allowed(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        assert pex.check_rate_limit("requester-1") is True

    def test_rapid_request_blocked(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        pex.check_rate_limit("requester-1")
        assert pex.check_rate_limit("requester-1") is False

    def test_different_peers_independent(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        assert pex.check_rate_limit("requester-1") is True
        assert pex.check_rate_limit("requester-2") is True

    def test_allowed_after_interval(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        pex.check_rate_limit("requester-1")
        # Simulate time passing
        pex._last_request["requester-1"] = time.time() - PEX_MIN_INTERVAL - 1
        assert pex.check_rate_limit("requester-1") is True

    def test_cleanup_stale_entries(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        pex._last_request["old-peer"] = time.time() - 99999
        pex._last_request["new-peer"] = time.time()
        pex.cleanup_rate_limits()
        assert "old-peer" not in pex._last_request
        assert "new-peer" in pex._last_request


# ── Build response ─────────────────────────────────────────────────


class TestBuildResponse:
    def test_basic_response(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        connected = [
            ("peer-A", "/ip4/1.2.3.4/tcp/4001/p2p/peer-A"),
            ("peer-B", "/ip4/5.6.7.8/tcp/4001/p2p/peer-B"),
        ]
        result = pex.build_response(connected)
        assert len(result) == 2
        assert result[0]["peer_id"] == "peer-A"
        assert result[1]["peer_id"] == "peer-B"

    def test_excludes_self(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        connected = [
            ("self-node", "/ip4/127.0.0.1/tcp/4001/p2p/self-node"),
            ("peer-A", "/ip4/1.2.3.4/tcp/4001/p2p/peer-A"),
        ]
        result = pex.build_response(connected)
        assert len(result) == 1
        assert result[0]["peer_id"] == "peer-A"

    def test_max_peers_limit(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        connected = [
            (f"peer-{i}", f"/ip4/10.0.0.{i}/tcp/4001/p2p/peer-{i}") for i in range(20)
        ]
        result = pex.build_response(connected, max_peers=3)
        assert len(result) == 3

    def test_skips_invalid_multiaddr(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        connected = [
            ("peer-A", "/dns4/example.com/tcp/4001/p2p/peer-A"),
            ("peer-B", "/ip4/5.6.7.8/tcp/4001/p2p/peer-B"),
        ]
        result = pex.build_response(connected)
        assert len(result) == 1
        assert result[0]["peer_id"] == "peer-B"

    def test_empty_connected(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        result = pex.build_response([])
        assert result == []


# ── Process response ───────────────────────────────────────────────


class TestProcessResponse:
    def test_basic_processing(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {
                "peer_id": "new-peer",
                "multiaddr": "/ip4/10.0.0.1/tcp/4001/p2p/new-peer",
            },
        ]
        result = pex.process_response("sender-1", peers_data)
        assert len(result) == 1
        assert result[0].peer_id == "new-peer"

    def test_excludes_self(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {
                "peer_id": "self-node",
                "multiaddr": "/ip4/10.0.0.1/tcp/4001/p2p/self-node",
            },
        ]
        result = pex.process_response("sender-1", peers_data)
        assert len(result) == 0

    def test_excludes_sender(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {
                "peer_id": "sender-1",
                "multiaddr": "/ip4/10.0.0.1/tcp/4001/p2p/sender-1",
            },
        ]
        result = pex.process_response("sender-1", peers_data)
        assert len(result) == 0

    def test_excludes_known_peers(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {
                "peer_id": "known",
                "multiaddr": "/ip4/10.0.0.1/tcp/4001/p2p/known",
            },
            {
                "peer_id": "new",
                "multiaddr": "/ip4/10.0.0.2/tcp/4001/p2p/new",
            },
        ]
        result = pex.process_response(
            "sender-1",
            peers_data,
            known_peers={"known"},
        )
        assert len(result) == 1
        assert result[0].peer_id == "new"

    def test_skips_invalid_multiaddr(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {"peer_id": "bad", "multiaddr": "/p2p/bad"},
            {
                "peer_id": "good",
                "multiaddr": "/ip4/10.0.0.1/tcp/4001/p2p/good",
            },
        ]
        result = pex.process_response("sender-1", peers_data)
        assert len(result) == 1
        assert result[0].peer_id == "good"

    def test_skips_empty_fields(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {"peer_id": "", "multiaddr": "/ip4/10.0.0.1/tcp/4001/p2p/x"},
            {"peer_id": "ok", "multiaddr": ""},
            {
                "peer_id": "good",
                "multiaddr": "/ip4/10.0.0.2/tcp/4001/p2p/good",
            },
        ]
        result = pex.process_response("sender-1", peers_data)
        assert len(result) == 1
        assert result[0].peer_id == "good"

    def test_caps_at_max_peers(self) -> None:
        pex = PeerExchange(peer_id="self-node")
        peers_data: list[dict[str, object]] = [
            {
                "peer_id": f"p-{i}",
                "multiaddr": f"/ip4/10.0.0.{i}/tcp/4001/p2p/p-{i}",
            }
            for i in range(50)
        ]
        result = pex.process_response("sender-1", peers_data)
        assert len(result) <= PEX_MAX_PEERS


# ── PEXPeerInfo ────────────────────────────────────────────────────


class TestPEXPeerInfo:
    def test_frozen(self) -> None:
        info = PEXPeerInfo(
            peer_id="peer-1",
            multiaddr="/ip4/1.2.3.4/tcp/4001/p2p/peer-1",
        )
        with pytest.raises(AttributeError):
            info.peer_id = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = PEXPeerInfo("p", "/ip4/1.2.3.4/tcp/4001/p2p/p")
        b = PEXPeerInfo("p", "/ip4/1.2.3.4/tcp/4001/p2p/p")
        assert a == b
