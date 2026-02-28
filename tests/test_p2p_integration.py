"""Integration tests for InfoMeshNode — full P2P lifecycle.

Tests node creation, DHT operations, search routing, and replication
using real libp2p hosts with trio.

Requires: pip install libp2p trio
"""

from __future__ import annotations

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
    """Run a trio async function synchronously."""
    import trio

    return trio.run(async_fn)


def _make_host_and_dht():
    """Create a libp2p host and KadDHT for testing."""
    from libp2p import create_new_ed25519_key_pair, new_host
    from libp2p.kad_dht import KadDHT
    from libp2p.kad_dht.kad_dht import DHTMode
    from libp2p.records.validator import NamespacedValidator, Validator

    class AcceptAllValidator(Validator):
        def validate(self, key: str, value: bytes) -> None:
            pass

        def select(self, key: str, values: list[bytes]) -> int:
            return 0

    key_pair = create_new_ed25519_key_pair()
    host = new_host(key_pair=key_pair)
    validator = NamespacedValidator(
        {
            "pk": AcceptAllValidator(),
            "infomesh": AcceptAllValidator(),
        }
    )
    dht = KadDHT(host, mode=DHTMode.SERVER, validator=validator, validator_changed=True)
    return host, dht


class TestNodeDHTIntegration:
    """Test InfoMeshDHT with real libp2p hosts."""

    @skip_no_libp2p
    def test_two_node_keyword_publish_query(self) -> None:
        """Publish keywords on node A, query from node B."""

        async def _test():
            import trio
            from libp2p.peer.peerinfo import PeerInfo
            from libp2p.tools.async_service.trio_service import background_trio_service
            from multiaddr import Multiaddr

            from infomesh.p2p.dht import InfoMeshDHT

            host_a, dht_a = _make_host_and_dht()
            host_b, dht_b = _make_host_and_dht()

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                background_trio_service(dht_a),
                background_trio_service(dht_b),
            ):
                # Connect peers
                peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())
                await host_b.connect(peer_info_a)
                await trio.sleep(1)

                # Create InfoMeshDHT wrappers
                im_dht_a = InfoMeshDHT(dht_a, str(host_a.get_id()))
                im_dht_b = InfoMeshDHT(dht_b, str(host_b.get_id()))

                # Publish on A
                pointers = [
                    {
                        "peer_id": str(host_a.get_id()),
                        "doc_id": 1,
                        "url": "https://example.com/python",
                        "score": 0.95,
                        "title": "Python Docs",
                    }
                ]
                ok = await im_dht_a.publish_keyword("python", pointers)
                assert ok is True

                await trio.sleep(0.5)

                # Query from B
                result = await im_dht_b.query_keyword("python")
                assert len(result) > 0
                assert result[0]["url"] == "https://example.com/python"

        _run_trio(_test)

    @skip_no_libp2p
    def test_crawl_lock_between_nodes(self) -> None:
        """Node A acquires lock, Node B should fail to acquire same lock."""

        async def _test():
            import trio
            from libp2p.peer.peerinfo import PeerInfo
            from libp2p.tools.async_service.trio_service import background_trio_service
            from multiaddr import Multiaddr

            from infomesh.p2p.dht import InfoMeshDHT

            host_a, dht_a = _make_host_and_dht()
            host_b, dht_b = _make_host_and_dht()

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                background_trio_service(dht_a),
                background_trio_service(dht_b),
            ):
                peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())
                await host_b.connect(peer_info_a)
                await trio.sleep(1)

                im_dht_a = InfoMeshDHT(dht_a, str(host_a.get_id()))
                im_dht_b = InfoMeshDHT(dht_b, str(host_b.get_id()))

                url = "https://example.com/crawl-target"

                # A acquires lock
                ok_a = await im_dht_a.acquire_crawl_lock(url)
                assert ok_a is True

                await trio.sleep(0.5)

                # B should fail
                ok_b = await im_dht_b.acquire_crawl_lock(url)
                assert ok_b is False

                # A releases
                await im_dht_a.release_crawl_lock(url)
                await trio.sleep(0.5)

                # Now B can acquire
                ok_b2 = await im_dht_b.acquire_crawl_lock(url)
                assert ok_b2 is True

        _run_trio(_test)

    @skip_no_libp2p
    def test_attestation_publish_retrieve(self) -> None:
        """Publish attestation on A, retrieve on B."""

        async def _test():
            import trio
            from libp2p.peer.peerinfo import PeerInfo
            from libp2p.tools.async_service.trio_service import background_trio_service
            from multiaddr import Multiaddr

            from infomesh.p2p.dht import InfoMeshDHT

            host_a, dht_a = _make_host_and_dht()
            host_b, dht_b = _make_host_and_dht()

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                background_trio_service(dht_a),
                background_trio_service(dht_b),
            ):
                peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())
                await host_b.connect(peer_info_a)
                await trio.sleep(1)

                im_dht_a = InfoMeshDHT(dht_a, str(host_a.get_id()))
                im_dht_b = InfoMeshDHT(dht_b, str(host_b.get_id()))

                # Publish attestation
                ok = await im_dht_a.publish_attestation(
                    url="https://example.com/page",
                    raw_hash="raw_abc123",
                    text_hash="text_def456",
                )
                assert ok is True

                await trio.sleep(0.5)

                # Retrieve on B
                att = await im_dht_b.get_attestation("https://example.com/page")
                assert att is not None
                assert att["raw_hash"] == "raw_abc123"
                assert att["text_hash"] == "text_def456"

        _run_trio(_test)


class TestDistributedIndexIntegration:
    """Test DistributedIndex with real libp2p DHT."""

    @skip_no_libp2p
    def test_publish_and_query_distributed(self) -> None:
        """Publish document on node A, query keywords from node B."""

        async def _test():
            import trio
            from libp2p.peer.peerinfo import PeerInfo
            from libp2p.tools.async_service.trio_service import background_trio_service
            from multiaddr import Multiaddr

            from infomesh.index.distributed import DistributedIndex
            from infomesh.p2p.dht import InfoMeshDHT

            host_a, dht_a = _make_host_and_dht()
            host_b, dht_b = _make_host_and_dht()

            async with (
                host_a.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                host_b.run([Multiaddr("/ip4/127.0.0.1/tcp/0")]),
                background_trio_service(dht_a),
                background_trio_service(dht_b),
            ):
                peer_info_a = PeerInfo(host_a.get_id(), host_a.get_addrs())
                await host_b.connect(peer_info_a)
                await trio.sleep(1)

                im_dht_a = InfoMeshDHT(dht_a, str(host_a.get_id()))
                im_dht_b = InfoMeshDHT(dht_b, str(host_b.get_id()))

                # Create distributed indexes
                dist_a = DistributedIndex(im_dht_a, str(host_a.get_id()))
                dist_b = DistributedIndex(im_dht_b, str(host_b.get_id()))

                # Publish document on A
                count = await dist_a.publish_document(
                    doc_id=42,
                    url="https://docs.python.org/tutorial",
                    title="Python Tutorial",
                    text=(
                        "Python programming language tutorial"
                        " guide comprehensive introduction"
                    ),
                )
                assert count > 0

                await trio.sleep(1)

                # Query from B
                results = await dist_b.query(["python", "tutorial"])
                assert len(results) > 0
                assert results[0].url == "https://docs.python.org/tutorial"
                assert results[0].doc_id == 42

        _run_trio(_test)


class TestNodeInfo:
    """Test NodeInfo and NodeState."""

    def test_node_state_enum(self) -> None:
        from infomesh.p2p.node import NodeState

        assert NodeState.STOPPED == "stopped"
        assert NodeState.RUNNING == "running"
        assert NodeState.ERROR == "error"

    def test_node_info_defaults(self) -> None:
        from infomesh.p2p.node import NodeInfo

        info = NodeInfo()
        assert info.peer_id == ""
        assert info.connected_peers == 0
        assert info.state == "stopped"
