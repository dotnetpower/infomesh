"""Tests for mDNS local peer discovery."""

from __future__ import annotations

import time

import msgpack

from infomesh.p2p.mdns import (
    MAGIC,
    PEER_TTL,
    DiscoveredPeer,
    MDNSDiscovery,
)


class TestDiscoveredPeer:
    """Tests for the DiscoveredPeer dataclass."""

    def test_fresh_peer_not_stale(self) -> None:
        peer = DiscoveredPeer(peer_id="abc", host="192.168.1.2", port=4001)
        assert not peer.is_stale

    def test_old_peer_is_stale(self) -> None:
        peer = DiscoveredPeer(
            peer_id="abc",
            host="192.168.1.2",
            port=4001,
            last_seen=time.monotonic() - PEER_TTL - 1,
        )
        assert peer.is_stale


class TestMDNSDiscovery:
    """Tests for MDNSDiscovery instance."""

    def test_init(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=5000)
        assert d.peer_count == 0
        assert d.discovered_peers == {}

    def test_build_announce_has_magic(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        packet = d._build_announce()
        assert packet[:8] == MAGIC

    def test_build_announce_parseable(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        packet = d._build_announce()
        payload = msgpack.unpackb(packet[8:], raw=False)
        assert payload["peer_id"] == "test123"
        assert payload["port"] == 4001
        assert "ts" in payload

    def test_parse_own_announce_returns_none(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        packet = d._build_announce()
        result = d._parse_announce(packet, ("192.168.1.1", 5353))
        assert result is None  # should ignore own announcements

    def test_parse_other_peer_announce(self) -> None:
        # Build a packet as if from another peer
        payload = msgpack.packb(
            {"peer_id": "other456", "port": 4002, "ts": time.time()},
            use_bin_type=True,
        )
        packet = MAGIC + payload

        d = MDNSDiscovery(peer_id="test123", port=4001)
        result = d._parse_announce(packet, ("192.168.1.5", 5353))
        assert result is not None
        assert result.peer_id == "other456"
        assert result.host == "192.168.1.5"
        assert result.port == 4002

    def test_parse_invalid_magic_returns_none(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        result = d._parse_announce(b"BADMAGIC" + b"\x00" * 20, ("1.1.1.1", 5353))
        assert result is None

    def test_parse_too_short_returns_none(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        assert d._parse_announce(b"short", ("1.1.1.1", 5353)) is None

    def test_parse_corrupt_payload_returns_none(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        result = d._parse_announce(MAGIC + b"\xff\xfe\xfd", ("1.1.1.1", 5353))
        assert result is None

    def test_parse_missing_peer_id_returns_none(self) -> None:
        payload = msgpack.packb({"port": 4001, "ts": time.time()}, use_bin_type=True)
        packet = MAGIC + payload
        d = MDNSDiscovery(peer_id="test123", port=4001)
        assert d._parse_announce(packet, ("1.1.1.1", 5353)) is None

    def test_parse_missing_port_returns_none(self) -> None:
        payload = msgpack.packb(
            {"peer_id": "other", "ts": time.time()}, use_bin_type=True
        )
        packet = MAGIC + payload
        d = MDNSDiscovery(peer_id="test123", port=4001)
        assert d._parse_announce(packet, ("1.1.1.1", 5353)) is None

    def test_stale_peers_pruned(self) -> None:
        d = MDNSDiscovery(peer_id="test123", port=4001)
        # Inject a stale peer directly
        d._peers["stale_peer"] = DiscoveredPeer(
            peer_id="stale_peer",
            host="10.0.0.1",
            port=4001,
            last_seen=time.monotonic() - PEER_TTL - 10,
        )
        # Should be pruned when we check
        assert d.peer_count == 0

    def test_multiple_peers_tracked(self) -> None:
        d = MDNSDiscovery(peer_id="me", port=4001)
        d._peers["a"] = DiscoveredPeer(peer_id="a", host="10.0.0.1", port=4001)
        d._peers["b"] = DiscoveredPeer(peer_id="b", host="10.0.0.2", port=4002)
        assert d.peer_count == 2
        peers = d.discovered_peers
        assert "a" in peers
        assert "b" in peers

    def test_stop_without_start(self) -> None:
        """stop() should not raise even if never started."""
        d = MDNSDiscovery(peer_id="test", port=4001)
        d.stop()  # should not raise

    def test_start_sets_running(self) -> None:
        """Verify start() cannot be called twice."""
        d = MDNSDiscovery(peer_id="test", port=4001)
        # We can't fully start (needs multicast socket), but test double-start guard
        d._running = True
        d.start()  # should be a no-op
        assert d._running is True
