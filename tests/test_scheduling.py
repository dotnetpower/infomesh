"""Tests for energy-aware scheduling."""

from __future__ import annotations

import pytest

from infomesh.credits.scheduling import (
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_START,
    OFF_PEAK_MULTIPLIER,
    EnergyAwareScheduler,
    NodeScheduleInfo,
    is_off_peak_at,
    node_is_off_peak,
)


def _node(
    peer_id: str,
    timezone: str = "UTC",
    has_llm: bool = True,
    trust: float = 0.8,
    start: int = DEFAULT_OFF_PEAK_START,
    end: int = DEFAULT_OFF_PEAK_END,
) -> NodeScheduleInfo:
    return NodeScheduleInfo(
        peer_id=peer_id,
        off_peak_start=start,
        off_peak_end=end,
        timezone=timezone,
        has_llm=has_llm,
        trust_score=trust,
    )


class TestIsOffPeak:
    def test_midnight_wrap_inside(self) -> None:
        # 23:00 → 07:00, check at 1:00
        assert is_off_peak_at(hour=1, start=23, end=7)

    def test_midnight_wrap_at_start(self) -> None:
        assert is_off_peak_at(hour=23, start=23, end=7)

    def test_midnight_wrap_outside(self) -> None:
        assert not is_off_peak_at(hour=12, start=23, end=7)

    def test_no_wrap_inside(self) -> None:
        assert is_off_peak_at(hour=3, start=2, end=6)

    def test_no_wrap_outside(self) -> None:
        assert not is_off_peak_at(hour=8, start=2, end=6)


class TestNodeIsOffPeak:
    def test_node_off_peak_override(self) -> None:
        node = _node("peer-1", start=23, end=7)
        assert node_is_off_peak(node, now_override_hour=2)

    def test_node_on_peak_override(self) -> None:
        node = _node("peer-1", start=23, end=7)
        assert not node_is_off_peak(node, now_override_hour=14)


class TestScheduler:
    @pytest.fixture()
    def scheduler(self) -> EnergyAwareScheduler:
        return EnergyAwareScheduler()

    def test_no_llm_nodes(self, scheduler: EnergyAwareScheduler) -> None:
        nodes = [_node("p1", has_llm=False), _node("p2", has_llm=False)]
        assert scheduler.schedule_llm_task(nodes, now_override_hour=2) is None

    def test_prefer_off_peak(self, scheduler: EnergyAwareScheduler) -> None:
        nodes = [
            _node("off-peak", start=22, end=6, trust=0.8),
            _node("on-peak", start=8, end=12, trust=0.9),
        ]
        # At hour 2, off-peak node (22-6) is in off-peak
        result = scheduler.schedule_llm_task(nodes, now_override_hour=2)
        assert result is not None
        assert result.target_peer_id == "off-peak"
        assert result.is_off_peak
        assert result.credit_multiplier == OFF_PEAK_MULTIPLIER

    def test_fallback_to_on_peak(self, scheduler: EnergyAwareScheduler) -> None:
        nodes = [
            _node("p1", start=22, end=6, trust=0.9),
            _node("p2", start=22, end=6, trust=0.7),
        ]
        # At hour 14 — all nodes are on-peak
        result = scheduler.schedule_llm_task(nodes, now_override_hour=14)
        assert result is not None
        assert not result.is_off_peak
        assert result.credit_multiplier == 1.0
        # Higher trust node should be selected
        assert result.target_peer_id == "p1"

    def test_higher_trust_preferred(self, scheduler: EnergyAwareScheduler) -> None:
        nodes = [
            _node("low", start=22, end=6, trust=0.5),
            _node("high", start=22, end=6, trust=0.95),
        ]
        result = scheduler.schedule_llm_task(nodes, now_override_hour=2)
        assert result is not None
        assert result.target_peer_id == "high"


class TestBatchScheduling:
    @pytest.fixture()
    def scheduler(self) -> EnergyAwareScheduler:
        return EnergyAwareScheduler()

    def test_batch_empty(self, scheduler: EnergyAwareScheduler) -> None:
        assert scheduler.schedule_batch([], 5) == []

    def test_batch_distributes_to_off_peak(
        self, scheduler: EnergyAwareScheduler
    ) -> None:
        nodes = [
            _node("p1", start=22, end=6, trust=0.8),
            _node("p2", start=22, end=6, trust=0.9),
        ]
        decisions = scheduler.schedule_batch(nodes, 4, now_override_hour=2)
        assert len(decisions) == 4
        assert all(d.is_off_peak for d in decisions)
        # Round-robin: p2, p1, p2, p1 (sorted by trust desc)
        assert decisions[0].target_peer_id == "p2"
        assert decisions[1].target_peer_id == "p1"

    def test_batch_overflow_to_on_peak(self, scheduler: EnergyAwareScheduler) -> None:
        nodes = [
            _node("off", start=22, end=6, trust=0.8),
            _node("on", start=8, end=12, trust=0.7),
        ]
        # At hour 14, "off" is on-peak (14 not in 22-6),
        # "on" is also on-peak (14 not in 8-12)
        decisions = scheduler.schedule_batch(nodes, 3, now_override_hour=14)
        assert len(decisions) == 3
        assert all(not d.is_off_peak for d in decisions)

    def test_batch_no_llm_nodes(self, scheduler: EnergyAwareScheduler) -> None:
        nodes = [_node("p1", has_llm=False)]
        assert scheduler.schedule_batch(nodes, 5) == []
