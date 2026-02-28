"""Tests for malicious node detection."""

from __future__ import annotations

import pytest

from infomesh.credits.farming import ANOMALY_FLAG_THRESHOLD, FarmingDetector
from infomesh.trust.detector import (
    WEAK_SIGNAL_AUDIT_FAILURES,
    MaliciousNodeDetector,
    ThreatLevel,
)
from infomesh.trust.scoring import TrustStore


@pytest.fixture()
def trust_store() -> TrustStore:
    return TrustStore()


@pytest.fixture()
def farming_detector() -> FarmingDetector:
    return FarmingDetector()


@pytest.fixture()
def detector(
    trust_store: TrustStore, farming_detector: FarmingDetector
) -> MaliciousNodeDetector:
    return MaliciousNodeDetector(trust_store, farming_detector)


class TestThreatAssessment:
    def test_clean_unknown_peer(self, detector: MaliciousNodeDetector) -> None:
        result = detector.assess("unknown-peer")
        # Unknown peer has default trust 0.5 = NORMAL, no anomalies
        assert result.threat_level == ThreatLevel.NONE
        assert not result.should_isolate

    def test_clean_good_peer(
        self,
        detector: MaliciousNodeDetector,
        trust_store: TrustStore,
    ) -> None:
        trust_store.update_uptime("good-peer", 500)
        trust_store.update_contribution("good-peer", 2000)
        for _ in range(10):
            trust_store.record_audit("good-peer", passed=True)
        result = detector.assess("good-peer")
        assert result.threat_level == ThreatLevel.NONE
        assert not result.should_isolate

    def test_already_isolated(
        self,
        detector: MaliciousNodeDetector,
        trust_store: TrustStore,
    ) -> None:
        trust_store.update_uptime("bad-peer", 10)
        # Force isolation via 3 audit failures
        for _ in range(3):
            trust_store.record_audit("bad-peer", passed=False)
        result = detector.assess("bad-peer")
        assert result.threat_level == ThreatLevel.ISOLATED
        assert result.should_isolate

    def test_farming_blocked_triggers_high(
        self,
        detector: MaliciousNodeDetector,
        farming_detector: FarmingDetector,
    ) -> None:
        farming_detector.register_node("farmer")
        for _ in range(ANOMALY_FLAG_THRESHOLD):
            farming_detector.record_anomaly("farmer", "test")
        result = detector.assess("farmer")
        assert result.threat_level == ThreatLevel.HIGH
        assert result.should_isolate

    def test_untrusted_score_triggers_high(
        self,
        detector: MaliciousNodeDetector,
        trust_store: TrustStore,
    ) -> None:
        # Create a peer with very low trust (all audit failures, no uptime)
        trust_store._ensure_peer("low-trust")
        trust_store._conn.execute(
            "UPDATE peer_trust SET uptime_hours = 0, contribution_raw = 0, "
            "audit_total = 10, audit_passed = 0 WHERE peer_id = ?",
            ("low-trust",),
        )
        trust_store._conn.commit()
        result = detector.assess("low-trust")
        assert result.threat_level == ThreatLevel.HIGH
        assert result.should_isolate

    def test_multiple_weak_signals_medium(
        self,
        detector: MaliciousNodeDetector,
        trust_store: TrustStore,
        farming_detector: FarmingDetector,
    ) -> None:
        # Create a peer with low trust + some anomalies
        trust_store.update_uptime("suspect", 5)
        # Two audit failures = weak signal but not isolation
        for _ in range(WEAK_SIGNAL_AUDIT_FAILURES):
            trust_store.record_audit("suspect", passed=False)
        # One anomaly
        farming_detector.register_node("suspect")
        farming_detector.record_anomaly("suspect", "burst")
        result = detector.assess("suspect")
        assert result.threat_level in (ThreatLevel.MEDIUM, ThreatLevel.HIGH)
        assert result.should_isolate

    def test_single_weak_signal_low(
        self,
        detector: MaliciousNodeDetector,
        farming_detector: FarmingDetector,
    ) -> None:
        farming_detector.register_node("minor")
        farming_detector.record_anomaly("minor", "burst")
        result = detector.assess("minor")
        assert result.threat_level == ThreatLevel.LOW
        assert not result.should_isolate


class TestAssessAndEnforce:
    def test_enforce_isolates(
        self,
        detector: MaliciousNodeDetector,
        trust_store: TrustStore,
        farming_detector: FarmingDetector,
    ) -> None:
        farming_detector.register_node("farmer")
        for _ in range(ANOMALY_FLAG_THRESHOLD):
            farming_detector.record_anomaly("farmer", "test")
        result = detector.assess_and_enforce("farmer")
        assert result.should_isolate
        # Verify actually isolated in trust store
        pt = trust_store.get_trust("farmer")
        assert pt is not None
        assert pt.isolated

    def test_no_enforce_if_clean(
        self,
        detector: MaliciousNodeDetector,
        trust_store: TrustStore,
    ) -> None:
        detector.assess_and_enforce("clean-peer")
        pt = trust_store.get_trust("clean-peer")
        # Clean peer should not be in trust store or not isolated
        assert pt is None or not pt.isolated
