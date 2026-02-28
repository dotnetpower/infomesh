"""Energy-aware LLM scheduling.

Preferentially routes batch LLM summarization tasks to nodes currently
in their off-peak electricity window.  Nodes earn a 1.5× credit multiplier
for LLM actions performed during off-peak hours.

Scheduling flow:
1. Each node advertises its off-peak window and current timezone.
2. The scheduler verifies timezone plausibility via IP cross-check.
3. The scheduler examines available nodes and selects those currently
   in off-peak when assigning LLM work.
4. If no off-peak nodes are available, work is assigned to any capable
   node (no multiplier).

This module provides the scheduling logic; actual task dispatch uses the
P2P protocol layer.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import structlog

from infomesh.credits.timezone_verify import (
    TimezoneConsistencyTracker,
    verify_timezone,
)

logger = structlog.get_logger()


# --- Constants -------------------------------------------------------------

# Default off-peak window
DEFAULT_OFF_PEAK_START: int = 23  # hour
DEFAULT_OFF_PEAK_END: int = 7  # hour

# Credit multiplier for off-peak LLM work
OFF_PEAK_MULTIPLIER: float = 1.5

# Prefer off-peak nodes unless fewer than this count are available
MIN_OFF_PEAK_NODES: int = 1


@dataclass(frozen=True)
class NodeScheduleInfo:
    """Scheduling metadata for a node."""

    peer_id: str
    off_peak_start: int  # 0-23
    off_peak_end: int  # 0-23
    timezone: str  # IANA timezone (e.g. "Asia/Seoul")
    has_llm: bool  # Whether the node has LLM capability
    trust_score: float  # Current trust score
    ip_address: str = ""  # Peer IP for timezone verification


@dataclass(frozen=True)
class ScheduleDecision:
    """Result of scheduling an LLM task."""

    target_peer_id: str
    is_off_peak: bool
    credit_multiplier: float
    reason: str


# --- Off-peak calculation --------------------------------------------------


def is_off_peak_at(
    *,
    hour: int,
    start: int = DEFAULT_OFF_PEAK_START,
    end: int = DEFAULT_OFF_PEAK_END,
) -> bool:
    """Check if a given hour falls in the off-peak window.

    Handles midnight wrap-around (e.g. 23:00 → 07:00).

    Args:
        hour: Hour to check (0-23).
        start: Off-peak start hour (inclusive).
        end: Off-peak end hour (exclusive).

    Returns:
        True if the hour is within the off-peak window.
    """
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def current_hour_in_timezone(timezone: str) -> int:
    """Get the current hour in a given timezone.

    Args:
        timezone: IANA timezone string (e.g. "America/New_York").

    Returns:
        Current hour (0-23) in that timezone.
    """
    try:
        tz = ZoneInfo(timezone)
        return datetime.datetime.now(tz=tz).hour
    except (KeyError, ValueError):
        # Unknown timezone, fall back to UTC
        return datetime.datetime.now(tz=datetime.UTC).hour


def node_is_off_peak(
    node: NodeScheduleInfo, *, now_override_hour: int | None = None
) -> bool:
    """Check if a node is currently in its off-peak window.

    Args:
        node: Node Schedule info with timezone and off-peak window.
        now_override_hour: Override the current hour (for testing).

    Returns:
        True if the node is in off-peak.
    """
    if now_override_hour is not None:
        hour = now_override_hour
    else:
        hour = current_hour_in_timezone(node.timezone)

    return is_off_peak_at(hour=hour, start=node.off_peak_start, end=node.off_peak_end)


# --- Scheduler --------------------------------------------------------------


class EnergyAwareScheduler:
    """Selects the best node for LLM tasks based on off-peak status
    and trust scores.

    Integrates ``TimezoneConsistencyTracker`` to verify timezone claims
    and deny off-peak multipliers to suspicious peers.
    """

    def __init__(self) -> None:
        self._tz_tracker = TimezoneConsistencyTracker()

    def _verify_off_peak(
        self, node: NodeScheduleInfo, *, now_override_hour: int | None = None
    ) -> bool:
        """Check if a node is legitimately in off-peak.

        Returns False if timezone claim is implausible or the peer
        has too many timezone changes (suspicious behavior).
        """
        if not node_is_off_peak(node, now_override_hour=now_override_hour):
            return False

        # Verify timezone plausibility via IP if available
        if node.ip_address:
            tz_check = verify_timezone(node.peer_id, node.timezone, node.ip_address)
            self._tz_tracker.record_claim(node.peer_id, node.timezone)

            if not tz_check.plausible:
                logger.warning(
                    "off_peak_tz_implausible",
                    peer_id=node.peer_id,
                    claimed=node.timezone,
                    ip=node.ip_address,
                )
                return False

            if self._tz_tracker.is_suspicious(node.peer_id):
                logger.warning(
                    "off_peak_tz_suspicious",
                    peer_id=node.peer_id,
                    reason="frequent_tz_changes",
                )
                return False

        return True

    def schedule_llm_task(
        self,
        nodes: list[NodeScheduleInfo],
        *,
        now_override_hour: int | None = None,
    ) -> ScheduleDecision | None:
        """Select the best node for an LLM summarization task.

        Priority:
        1. Off-peak nodes with LLM, sorted by trust_score desc.
        2. Any nodes with LLM, sorted by trust_score desc.

        Args:
            nodes: Available nodes with schedule info.
            now_override_hour: Override hour for all nodes (testing).

        Returns:
            ScheduleDecision or None if no LLM-capable node is available.
        """
        llm_nodes = [n for n in nodes if n.has_llm]
        if not llm_nodes:
            logger.debug("schedule_no_llm_nodes")
            return None

        # Partition into off-peak and on-peak
        off_peak = [
            n
            for n in llm_nodes
            if self._verify_off_peak(n, now_override_hour=now_override_hour)
        ]
        on_peak = [
            n
            for n in llm_nodes
            if not self._verify_off_peak(n, now_override_hour=now_override_hour)
        ]

        if off_peak:
            # Sort by trust score descending — prefer more trusted nodes
            off_peak.sort(key=lambda n: n.trust_score, reverse=True)
            best = off_peak[0]
            return ScheduleDecision(
                target_peer_id=best.peer_id,
                is_off_peak=True,
                credit_multiplier=OFF_PEAK_MULTIPLIER,
                reason=f"off-peak in {best.timezone} (trust={best.trust_score:.3f})",
            )

        # No off-peak nodes — use best on-peak node
        on_peak.sort(key=lambda n: n.trust_score, reverse=True)
        best = on_peak[0]
        return ScheduleDecision(
            target_peer_id=best.peer_id,
            is_off_peak=False,
            credit_multiplier=1.0,
            reason=f"on-peak, no off-peak available (trust={best.trust_score:.3f})",
        )

    def schedule_batch(
        self,
        nodes: list[NodeScheduleInfo],
        task_count: int,
        *,
        now_override_hour: int | None = None,
    ) -> list[ScheduleDecision]:
        """Schedule multiple LLM tasks across available nodes.

        Distributes tasks round-robin among off-peak nodes first, then
        overflows to on-peak nodes.

        Args:
            nodes: Available nodes.
            task_count: Number of tasks to schedule.
            now_override_hour: Override hour for testing.

        Returns:
            List of ScheduleDecision (one per task, may be shorter if
            insufficient nodes).
        """
        llm_nodes = [n for n in nodes if n.has_llm]
        if not llm_nodes:
            return []

        off_peak = sorted(
            [
                n
                for n in llm_nodes
                if self._verify_off_peak(n, now_override_hour=now_override_hour)
            ],
            key=lambda n: n.trust_score,
            reverse=True,
        )
        on_peak = sorted(
            [
                n
                for n in llm_nodes
                if not self._verify_off_peak(n, now_override_hour=now_override_hour)
            ],
            key=lambda n: n.trust_score,
            reverse=True,
        )

        decisions: list[ScheduleDecision] = []

        # Assign to off-peak first (round-robin)
        for i in range(task_count):
            if off_peak:
                node = off_peak[i % len(off_peak)]
                decisions.append(
                    ScheduleDecision(
                        target_peer_id=node.peer_id,
                        is_off_peak=True,
                        credit_multiplier=OFF_PEAK_MULTIPLIER,
                        reason=f"batch off-peak ({node.timezone})",
                    )
                )
            elif on_peak:
                node = on_peak[i % len(on_peak)]
                decisions.append(
                    ScheduleDecision(
                        target_peer_id=node.peer_id,
                        is_off_peak=False,
                        credit_multiplier=1.0,
                        reason=f"batch on-peak ({node.timezone})",
                    )
                )
            else:
                break

        return decisions
