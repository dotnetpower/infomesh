"""Tests for credit farming detection."""

from __future__ import annotations

import pytest

from infomesh.credits.farming import (
    ANOMALY_FLAG_THRESHOLD,
    BURST_THRESHOLD,
    BURST_WINDOW_MINUTES,
    MAX_CRAWLS_PER_HOUR,
    PROBATION_HOURS,
    FarmingDetector,
    FarmingVerdict,
)


@pytest.fixture()
def detector() -> FarmingDetector:
    return FarmingDetector()


# --- Probation tests -------------------------------------------------------


class TestProbation:
    def test_new_node_is_on_probation(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        assert detector.is_on_probation("peer-1", now=now)

    def test_unregistered_node_on_probation(self, detector: FarmingDetector) -> None:
        assert detector.is_on_probation("unknown")

    def test_node_leaves_probation_after_24h(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        later = now + PROBATION_HOURS * 3600 + 1
        assert not detector.is_on_probation("peer-1", now=later)

    def test_probation_remaining(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        half = now + PROBATION_HOURS * 3600 / 2
        remaining = detector.probation_remaining("peer-1", now=half)
        assert 11.5 < remaining < 12.5  # ~12h remaining

    def test_probation_remaining_zero_after(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        later = now + PROBATION_HOURS * 3600 + 100
        assert detector.probation_remaining("peer-1", now=later) == 0.0


# --- Rate limiting tests ---------------------------------------------------


class TestRateLimiting:
    def test_no_actions_not_limited(self, detector: FarmingDetector) -> None:
        detector.register_node("peer-1")
        assert not detector.is_rate_limited("peer-1", "crawl")

    def test_within_limit(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        for i in range(MAX_CRAWLS_PER_HOUR - 1):
            detector.log_action("peer-1", "crawl", now=now + i)
        assert not detector.is_rate_limited(
            "peer-1", "crawl", now=now + MAX_CRAWLS_PER_HOUR
        )

    def test_exceeds_limit(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        for i in range(MAX_CRAWLS_PER_HOUR):
            detector.log_action("peer-1", "crawl", now=now + i)
        assert detector.is_rate_limited(
            "peer-1", "crawl", now=now + MAX_CRAWLS_PER_HOUR
        )

    def test_old_actions_expire(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now)
        for i in range(MAX_CRAWLS_PER_HOUR):
            detector.log_action("peer-1", "crawl", now=now + i)
        # After 1 hour, the old actions should not count
        later = now + 3601
        assert not detector.is_rate_limited("peer-1", "crawl", now=later)

    def test_actions_in_last_hour(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        for i in range(5):
            detector.log_action("peer-1", "crawl", now=now + i * 10)
        assert detector.actions_in_last_hour("peer-1", "crawl", now=now + 50) == 5


# --- Burst detection tests -------------------------------------------------


class TestBurstDetection:
    def test_no_burst_normal(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        for i in range(10):
            detector.log_action("peer-1", "crawl", now=now + i * 30)
        assert not detector.detect_burst("peer-1", "crawl", now=now + 300)

    def test_burst_detected(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        # 30 actions in 5 minutes = burst
        for i in range(BURST_THRESHOLD):
            detector.log_action("peer-1", "crawl", now=now + i)
        # Check at the window boundary
        check_time = now + BURST_WINDOW_MINUTES * 60 - 1
        assert detector.detect_burst("peer-1", "crawl", now=check_time)


# --- Interval regularity tests ---------------------------------------------


class TestRegularIntervals:
    def test_no_data_not_suspicious(self, detector: FarmingDetector) -> None:
        assert not detector.detect_regular_intervals("peer-1", "crawl")

    def test_regular_bot_like_intervals(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        # Perfectly regular 30s intervals
        for i in range(20):
            detector.log_action("peer-1", "crawl", now=now + i * 30.0)
        assert detector.detect_regular_intervals(
            "peer-1", "crawl", window_hours=1.0, now=now + 600
        )

    def test_human_like_intervals_ok(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        import random

        rng = random.Random(42)
        # Human-like: variable intervals from 20-60s
        ts = now
        for _ in range(20):
            ts += rng.uniform(20, 60)
            detector.log_action("peer-1", "crawl", now=ts)
        assert not detector.detect_regular_intervals(
            "peer-1", "crawl", window_hours=1.0, now=ts + 1
        )


# --- Anomaly recording and blocking ----------------------------------------


class TestAnomalyRecording:
    def test_record_anomaly_increments(self, detector: FarmingDetector) -> None:
        detector.register_node("peer-1")
        count = detector.record_anomaly("peer-1", "test_anomaly", "detail")
        assert count == 1

    def test_auto_block_after_threshold(self, detector: FarmingDetector) -> None:
        detector.register_node("peer-1")
        for _i in range(ANOMALY_FLAG_THRESHOLD):
            detector.record_anomaly("peer-1", "test_anomaly")
        assert detector.is_blocked("peer-1")

    def test_unblock(self, detector: FarmingDetector) -> None:
        detector.register_node("peer-1")
        for _i in range(ANOMALY_FLAG_THRESHOLD):
            detector.record_anomaly("peer-1", "test_anomaly")
        assert detector.is_blocked("peer-1")
        detector.unblock("peer-1")
        assert not detector.is_blocked("peer-1")

    def test_anomaly_history(self, detector: FarmingDetector) -> None:
        detector.register_node("peer-1")
        detector.record_anomaly("peer-1", "burst", "5 min")
        detector.record_anomaly("peer-1", "regular", "cv=0.05")
        history = detector.get_anomaly_history("peer-1")
        assert len(history) == 2
        assert history[0].anomaly_type == "regular"  # Most recent first


# --- Comprehensive check tests ----------------------------------------------


class TestComprehensiveCheck:
    def test_clean_check(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now - PROBATION_HOURS * 3600 - 1)
        result = detector.check("peer-1", "crawl", now=now)
        assert result.verdict == FarmingVerdict.CLEAN

    def test_probation_check(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        result = detector.check("peer-new", "crawl", now=now)
        assert result.verdict == FarmingVerdict.PROBATION
        assert result.probation_remaining_hours > 0

    def test_rate_limited_check(self, detector: FarmingDetector) -> None:
        import random as _rnd

        now = 1_000_000.0
        detector.register_node("peer-1", now=now - PROBATION_HOURS * 3600 - 1)
        # Spread actions across the full hour with random jitter to avoid
        # triggering regular-interval or burst anomaly detection.
        rng = _rnd.Random(42)
        for i in range(MAX_CRAWLS_PER_HOUR):
            ts = now + i * 30 + rng.uniform(0, 15)  # ~30s spacing, jittered
            detector.log_action("peer-1", "crawl", now=ts)
        result = detector.check("peer-1", "crawl", now=now + 3600)
        assert result.verdict == FarmingVerdict.RATE_LIMITED
        assert result.rate_limit_exceeded

    def test_blocked_check(self, detector: FarmingDetector) -> None:
        now = 1_000_000.0
        detector.register_node("peer-1", now=now - PROBATION_HOURS * 3600 - 1)
        for _i in range(ANOMALY_FLAG_THRESHOLD):
            detector.record_anomaly("peer-1", "manual_block")
        result = detector.check("peer-1", "crawl", now=now)
        assert result.verdict == FarmingVerdict.BLOCKED
