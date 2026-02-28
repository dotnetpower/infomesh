"""Malicious node detection and network isolation.

Integrates TrustStore, AuditScheduler, and FarmingDetector to produce
an automated decision about whether a peer should be isolated from the
network.

Detection signals:
- Consecutive audit failures (3× → auto-isolation by TrustStore)
- Credit farming anomalies (blocked by FarmingDetector)
- Trust score below UNTRUSTED threshold (< 0.3)
- Combination of multiple weak signals

This module provides the orchestration layer; the actual isolation
enforcement happens at the P2P routing level (query rejection, DHT
exclusion).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

from infomesh.credits.farming import FarmingDetector, FarmingVerdict
from infomesh.trust.scoring import TrustStore, TrustTier

logger = structlog.get_logger()


# --- Constants -------------------------------------------------------------

# If a peer has multiple weak signals (below thresholds), combine them
WEAK_SIGNAL_AUDIT_FAILURES: int = 2  # < isolation threshold but concerning
WEAK_SIGNAL_ANOMALY_COUNT: int = 1  # At least 1 anomaly
WEAK_SIGNAL_TRUST_THRESHOLD: float = 0.5  # Below NORMAL tier
# How many weak signals trigger isolation
WEAK_SIGNAL_ISOLATION_COUNT: int = 2


class ThreatLevel(StrEnum):
    """Assessed threat level for a peer."""

    NONE = "none"
    LOW = "low"  # 1 weak signal
    MEDIUM = "medium"  # Multiple weak signals
    HIGH = "high"  # Confirmed malicious, should isolate
    ISOLATED = "isolated"  # Already isolated


@dataclass(frozen=True)
class ThreatAssessment:
    """Comprehensive threat assessment for a peer."""

    peer_id: str
    threat_level: ThreatLevel
    trust_score: float
    trust_tier: TrustTier
    farming_verdict: FarmingVerdict
    consecutive_audit_failures: int
    anomaly_count: int
    weak_signals: list[str]
    should_isolate: bool
    detail: str


# --- Detector ---------------------------------------------------------------


class MaliciousNodeDetector:
    """Orchestrates malicious node detection by combining trust, audit,
    and farming signals.

    Args:
        trust_store: TrustStore for trust scores and isolation.
        farming_detector: FarmingDetector for credit gaming detection.
    """

    def __init__(
        self,
        trust_store: TrustStore,
        farming_detector: FarmingDetector,
    ) -> None:
        self._trust = trust_store
        self._farming = farming_detector

    def assess(self, peer_id: str, *, action: str = "crawl") -> ThreatAssessment:
        """Run a comprehensive threat assessment for a peer.

        Args:
            peer_id: Peer to assess.
            action: The action the peer is attempting (for farming check).

        Returns:
            ThreatAssessment with recommendation.
        """
        # Get trust info
        peer_trust = self._trust.get_trust(peer_id)
        trust_score = peer_trust.trust_score if peer_trust else 0.5
        trust_tier_val = peer_trust.tier if peer_trust else TrustTier.NORMAL
        consec_failures = peer_trust.consecutive_audit_failures if peer_trust else 0
        already_isolated = peer_trust.isolated if peer_trust else False

        # Get farming info
        farming_check = self._farming.check(peer_id, action)

        # Already isolated?
        if already_isolated:
            return ThreatAssessment(
                peer_id=peer_id,
                threat_level=ThreatLevel.ISOLATED,
                trust_score=trust_score,
                trust_tier=trust_tier_val,
                farming_verdict=farming_check.verdict,
                consecutive_audit_failures=consec_failures,
                anomaly_count=farming_check.anomaly_count,
                weak_signals=[],
                should_isolate=True,
                detail="already isolated",
            )

        # Collect weak signals
        weak_signals: list[str] = []

        if consec_failures >= WEAK_SIGNAL_AUDIT_FAILURES:
            weak_signals.append(f"audit_failures={consec_failures}")

        if farming_check.anomaly_count >= WEAK_SIGNAL_ANOMALY_COUNT:
            weak_signals.append(f"anomalies={farming_check.anomaly_count}")

        if trust_score < WEAK_SIGNAL_TRUST_THRESHOLD:
            weak_signals.append(f"low_trust={trust_score:.3f}")

        if farming_check.verdict == FarmingVerdict.BLOCKED:
            weak_signals.append("farming_blocked")

        if farming_check.rate_limit_exceeded:
            weak_signals.append("rate_limited")

        # Determine threat level
        should_isolate = False

        if farming_check.verdict == FarmingVerdict.BLOCKED:
            threat_level = ThreatLevel.HIGH
            should_isolate = True
            detail = "blocked for credit farming"
        elif trust_tier_val == TrustTier.UNTRUSTED:
            threat_level = ThreatLevel.HIGH
            should_isolate = True
            detail = f"untrusted peer (score={trust_score:.3f})"
        elif len(weak_signals) >= WEAK_SIGNAL_ISOLATION_COUNT:
            threat_level = ThreatLevel.MEDIUM
            # Multiple weak signals → escalate to isolation
            should_isolate = True
            detail = f"multiple weak signals: {', '.join(weak_signals)}"
        elif len(weak_signals) == 1:
            threat_level = ThreatLevel.LOW
            detail = f"single weak signal: {weak_signals[0]}"
        else:
            threat_level = ThreatLevel.NONE
            detail = "no threats detected"

        assessment = ThreatAssessment(
            peer_id=peer_id,
            threat_level=threat_level,
            trust_score=trust_score,
            trust_tier=trust_tier_val,
            farming_verdict=farming_check.verdict,
            consecutive_audit_failures=consec_failures,
            anomaly_count=farming_check.anomaly_count,
            weak_signals=weak_signals,
            should_isolate=should_isolate,
            detail=detail,
        )

        if should_isolate:
            logger.warning(
                "malicious_node_detected",
                peer_id=peer_id[:12],
                threat_level=threat_level.value,
                detail=detail,
            )

        return assessment

    def assess_and_enforce(
        self, peer_id: str, *, action: str = "crawl"
    ) -> ThreatAssessment:
        """Assess a peer and automatically enforce isolation if needed.

        Args:
            peer_id: Peer to assess.
            action: The action being attempted.

        Returns:
            ThreatAssessment with enforcement applied.
        """
        assessment = self.assess(peer_id, action=action)
        if (
            assessment.should_isolate
            and assessment.threat_level != ThreatLevel.ISOLATED
        ):
            self._trust.isolate_peer(peer_id)
            logger.warning(
                "node_isolated_by_detector",
                peer_id=peer_id[:12],
                reason=assessment.detail,
            )
        return assessment
