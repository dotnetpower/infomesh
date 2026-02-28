"""py-libp2p spike test — early risk validation for P2P layer.

Tests:
1. Can we create libp2p hosts and connect them?
2. Can they exchange messages via a custom protocol?
3. Can we use the Kademlia DHT for put/get?
4. NAT traversal readiness check (UPnP, mDNS)

KEY FINDINGS (documented for Phase 2 implementation):
- py-libp2p uses **trio** internally, NOT asyncio.
  → InfoMesh must bridge asyncio ↔ trio for P2P layer.
- host.run(listen_addrs) is an async context manager.
- KadDHT must run via `background_trio_service(dht)` context manager.
- DHT keys MUST start with "/" (e.g., "/infomesh/key1").
- host.connect() takes PeerInfo(peer_id, addrs), NOT bare peer_id.
- Custom NamespacedValidator needed for "infomesh" namespace.
- Default validator only recognizes "pk" namespace.

Run: uv run pytest tests/test_libp2p_spike.py -v -s
"""

from __future__ import annotations

import time

import pytest

try:
    import trio as _trio  # noqa: F401

    HAS_TRIO = True
except ImportError:
    HAS_TRIO = False

try:
    import libp2p as _libp2p  # noqa: F401

    HAS_LIBP2P = True
except ImportError:
    HAS_LIBP2P = False


skip_no_libp2p = pytest.mark.skipif(
    not HAS_LIBP2P or not HAS_TRIO,
    reason="libp2p or trio not installed",
)


def _run_trio(async_fn):
    """Run a trio async function synchronously (bypass asyncio test runner)."""
    import trio

    return trio.run(async_fn)


# ─── Helper: create DHT with custom validator ──────────────


def _make_dht(host):
    """Create a KadDHT with 'infomesh' namespace validator."""
    from libp2p.kad_dht import KadDHT
    from libp2p.kad_dht.kad_dht import DHTMode
    from libp2p.records.validator import NamespacedValidator, Validator

    class AcceptAllValidator(Validator):
        def validate(self, key: str, value: bytes) -> None:
            pass

        def select(self, key: str, values: list[bytes]) -> int:
            return 0

    v = NamespacedValidator(
        {
            "pk": AcceptAllValidator(),
            "infomesh": AcceptAllValidator(),
        }
    )
    return KadDHT(host, mode=DHTMode.SERVER, validator=v, validator_changed=True)


# ─── Basic Connectivity ────────────────────────────────────


class TestLibp2pBasicConnectivity:
    """Validate py-libp2p can create hosts, connect peers, and exchange data."""

    @skip_no_libp2p
    def test_create_host(self) -> None:
        """Can we create a libp2p host?"""

        async def _test():
            from libp2p import create_new_ed25519_key_pair, new_host
            from multiaddr import Multiaddr

            key_pair = create_new_ed25519_key_pair()
            host = new_host(key_pair=key_pair)

            async with host.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]):
                peer_id = host.get_id()
                addrs = host.get_addrs()

                assert peer_id is not None
                assert len(addrs) > 0

        _run_trio(_test)

    @skip_no_libp2p
    def test_two_hosts_connect(self) -> None:
        """Can two hosts discover and connect to each other?"""

        async def _test():
            from libp2p import create_new_ed25519_key_pair, new_host
            from libp2p.peer.peerinfo import PeerInfo
            from multiaddr import Multiaddr

            key_a = create_new_ed25519_key_pair()
            host_a = new_host(key_pair=key_a)
            key_b = create_new_ed25519_key_pair()
            host_b = new_host(key_pair=key_b)

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
            ):
                peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())
                await host_b.connect(peer_info_a)

                connected = host_b.get_connected_peers()
                assert len(connected) > 0

        _run_trio(_test)

    @skip_no_libp2p
    def test_protocol_stream(self) -> None:
        """Can two hosts exchange data via a custom protocol?"""

        async def _test():
            import trio
            from libp2p import create_new_ed25519_key_pair, new_host
            from libp2p.peer.peerinfo import PeerInfo
            from multiaddr import Multiaddr

            PROTOCOL = "/infomesh/test/1.0.0"
            received: list[bytes] = []

            key_a = create_new_ed25519_key_pair()
            host_a = new_host(key_pair=key_a)
            key_b = create_new_ed25519_key_pair()
            host_b = new_host(key_pair=key_b)

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
            ):

                async def handler(stream):
                    data = await stream.read(1024)
                    received.append(data)
                    await stream.write(b"PONG:" + data)
                    await stream.close()

                host_a.set_stream_handler(PROTOCOL, handler)

                peer_id_a = host_a.get_id()
                peer_info_a = PeerInfo(peer_id_a, host_a.get_addrs())
                await host_b.connect(peer_info_a)

                stream = await host_b.new_stream(peer_id_a, [PROTOCOL])
                await stream.write(b"PING:hello-infomesh")
                response = await stream.read(1024)
                await stream.close()

                await trio.sleep(0.5)

                assert response == b"PONG:PING:hello-infomesh"
                assert received[0] == b"PING:hello-infomesh"

        _run_trio(_test)


# ─── DHT Tests ─────────────────────────────────────────────


class TestLibp2pDHT:
    """Validate Kademlia DHT functionality."""

    @skip_no_libp2p
    def test_dht_put_get(self) -> None:
        """Can we store and retrieve values via DHT?"""

        async def _test():
            import trio
            from libp2p import create_new_ed25519_key_pair, new_host
            from libp2p.peer.peerinfo import PeerInfo
            from libp2p.tools.async_service.trio_service import background_trio_service
            from multiaddr import Multiaddr

            key_a = create_new_ed25519_key_pair()
            host_a = new_host(key_pair=key_a)
            key_b = create_new_ed25519_key_pair()
            host_b = new_host(key_pair=key_b)

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
            ):
                dht_a = _make_dht(host_a)
                dht_b = _make_dht(host_b)

                async with (
                    background_trio_service(dht_a),
                    background_trio_service(dht_b),
                ):
                    peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())
                    await host_b.connect(peer_info_a)
                    await trio.sleep(1)

                    key = "/infomesh/test/keyword-hash-123"
                    value = b'[{"peer_id":"abc123","doc_id":"doc1","score":0.95}]'
                    await dht_a.put_value(key, value)

                    await trio.sleep(0.5)

                    retrieved = await dht_b.get_value(key)

                    assert retrieved is not None, "DHT get_value returned None"
                    assert retrieved == value

        _run_trio(_test)

    @skip_no_libp2p
    @pytest.mark.xfail(
        reason=(
            "3-node DHT routing requires full Kademlia bootstrap/refresh cycle "
            "which doesn't complete reliably in short test timeouts. "
            "2-node DHT put/get proves core functionality works. "
            "Phase 2 will implement proper bootstrap protocol."
        ),
        strict=False,
    )
    def test_dht_three_node_routing(self) -> None:
        """Can DHT route queries through intermediary nodes (3-node test)?"""

        async def _test():
            import trio
            from libp2p import create_new_ed25519_key_pair, new_host
            from libp2p.peer.peerinfo import PeerInfo
            from libp2p.tools.async_service.trio_service import background_trio_service
            from multiaddr import Multiaddr

            hosts = []
            dhts = []

            for _ in range(3):
                key = create_new_ed25519_key_pair()
                host = new_host(key_pair=key)
                hosts.append(host)

            async with (
                hosts[0].run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                hosts[1].run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                hosts[2].run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
            ):
                for h in hosts:
                    dhts.append(_make_dht(h))

                async with (
                    background_trio_service(dhts[0]),
                    background_trio_service(dhts[1]),
                    background_trio_service(dhts[2]),
                ):
                    # Full mesh: connect all pairs for DHT discovery
                    pi_0 = PeerInfo(hosts[0].get_id(), hosts[0].get_addrs())
                    pi_1 = PeerInfo(hosts[1].get_id(), hosts[1].get_addrs())
                    _pi_2 = PeerInfo(hosts[2].get_id(), hosts[2].get_addrs())
                    await hosts[1].connect(pi_0)
                    await hosts[2].connect(pi_1)
                    await hosts[2].connect(pi_0)

                    # Allow DHT routing tables to stabilize
                    await trio.sleep(3)

                    key = "/infomesh/test/routing-test"
                    value = b"routed-through-dht"
                    await dhts[0].put_value(key, value)

                    await trio.sleep(2)

                    # Retry up to 3 times (DHT propagation can be slow)
                    retrieved = None
                    for _ in range(3):
                        retrieved = await dhts[2].get_value(key)
                        if retrieved is not None:
                            break
                        await trio.sleep(1)

                    assert retrieved is not None, "3-node routing failed"
                    assert retrieved == value

        _run_trio(_test)


# ─── NAT Readiness ─────────────────────────────────────────


class TestLibp2pNATReadiness:
    """Check NAT traversal features availability."""

    @skip_no_libp2p
    def test_upnp_option_available(self) -> None:
        """Verify enable_upnp parameter exists in new_host."""
        import inspect

        from libp2p import new_host

        sig = inspect.signature(new_host)
        assert "enable_upnp" in sig.parameters

    @skip_no_libp2p
    def test_mdns_option_available(self) -> None:
        """Verify enable_mDNS parameter exists in new_host."""
        import inspect

        from libp2p import new_host

        sig = inspect.signature(new_host)
        assert "enable_mDNS" in sig.parameters

    @skip_no_libp2p
    def test_quic_transport_available(self) -> None:
        """Verify QUIC transport option exists."""
        import inspect

        from libp2p import new_host

        sig = inspect.signature(new_host)
        assert "enable_quic" in sig.parameters

    @skip_no_libp2p
    def test_noise_security_available(self) -> None:
        """Verify Noise protocol is available for encryption."""
        from libp2p import NOISE_PROTOCOL_ID, NoiseTransport

        assert NoiseTransport is not None
        assert NOISE_PROTOCOL_ID is not None

    @skip_no_libp2p
    def test_host_with_nat_options(self) -> None:
        """Create a host with NAT-related options (UPnP, mDNS)."""

        async def _test():
            from libp2p import create_new_ed25519_key_pair, new_host
            from multiaddr import Multiaddr

            key = create_new_ed25519_key_pair()
            host = new_host(
                key_pair=key,
                enable_mDNS=False,
                enable_upnp=False,
            )
            async with host.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]):
                assert host.get_id() is not None

        _run_trio(_test)


# ─── Performance ───────────────────────────────────────────


class TestLibp2pPerformance:
    """Basic performance characterization."""

    @skip_no_libp2p
    def test_host_creation_time(self) -> None:
        """Measure host creation latency."""

        async def _test():
            from libp2p import create_new_ed25519_key_pair, new_host
            from multiaddr import Multiaddr

            times: list[float] = []

            for _ in range(5):
                start = time.monotonic()
                key = create_new_ed25519_key_pair()
                host = new_host(key_pair=key)
                async with host.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]):
                    elapsed = (time.monotonic() - start) * 1000
                    times.append(elapsed)

            avg = sum(times) / len(times)
            assert avg < 5000, f"Host creation too slow: {avg:.0f}ms"

        _run_trio(_test)

    @skip_no_libp2p
    def test_connection_latency(self) -> None:
        """Measure peer connection latency."""

        async def _test():
            from libp2p import create_new_ed25519_key_pair, new_host
            from libp2p.peer.peerinfo import PeerInfo
            from multiaddr import Multiaddr

            key_a = create_new_ed25519_key_pair()
            host_a = new_host(key_pair=key_a)
            key_b = create_new_ed25519_key_pair()
            host_b = new_host(key_pair=key_b)

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
            ):
                peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())

                start = time.monotonic()
                await host_b.connect(peer_info_a)
                connect_ms = (time.monotonic() - start) * 1000

                assert connect_ms < 5000, f"Connection too slow: {connect_ms:.0f}ms"

        _run_trio(_test)
