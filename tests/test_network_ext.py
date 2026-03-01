"""Tests for infomesh.p2p.network_ext — network extensions."""

from __future__ import annotations

from infomesh.p2p.network_ext import (
    GeoLocation,
    PartitionDetector,
    estimate_geo_distance,
    select_relay,
    sort_peers_by_proximity,
)


class TestGeoDistance:
    def test_same_location(self) -> None:
        dist = estimate_geo_distance(
            GeoLocation(latitude=37.5665, longitude=126.9780),
            GeoLocation(latitude=37.5665, longitude=126.9780),
        )
        assert dist == 0.0

    def test_known_distance(self) -> None:
        seoul = GeoLocation(latitude=37.5665, longitude=126.9780)
        tokyo = GeoLocation(latitude=35.6762, longitude=139.6503)
        dist = estimate_geo_distance(seoul, tokyo)
        # Seoul-Tokyo is ~1160 km
        assert 1000 < dist < 1500

    def test_sort_by_proximity(self) -> None:
        origin = GeoLocation(latitude=37.5665, longitude=126.9780)
        peers = [
            ("peer_ny", GeoLocation(latitude=40.7128, longitude=-74.0060)),
            ("peer_tokyo", GeoLocation(latitude=35.6762, longitude=139.6503)),
            ("peer_london", GeoLocation(latitude=51.5074, longitude=-0.1278)),
        ]
        sorted_peers = sort_peers_by_proximity(peers, origin)
        # Tokyo should be closest to Seoul
        assert sorted_peers[0][0] == "peer_tokyo"


class TestPartitionDetector:
    def test_initial_state(self) -> None:
        pd = PartitionDetector(threshold=0.5)
        state = pd.check(reachable=5, total=10)
        assert state.is_partitioned is False

    def test_partition_detected(self) -> None:
        pd = PartitionDetector(threshold=0.5)
        state = pd.check(reachable=0, total=10)
        assert state.is_partitioned is True
        actions = pd.get_recovery_actions()
        assert len(actions) > 0


class TestRelaySelection:
    def test_select_relay(self) -> None:
        candidates = [
            ("relay1", 50.0),
            ("relay2", 100.0),
        ]
        best = select_relay(candidates)
        assert best is not None
        # Should prefer lower latency
        assert best == "relay1"

    def test_no_candidates(self) -> None:
        assert select_relay([]) is None
