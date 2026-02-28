"""Tests for off-peak timezone verification."""

from __future__ import annotations

import pytest

from infomesh.credits.timezone_verify import (
    MAX_OFFSET_DIFF_HOURS,
    MAX_TZ_CHANGES_PER_DAY,
    TimezoneConsistencyTracker,
    estimate_offset_from_ip,
    get_timezone_offset,
    verify_timezone,
)


class TestGetTimezoneOffset:
    """Tests for get_timezone_offset."""

    def test_utc(self) -> None:
        assert get_timezone_offset("UTC") == 0.0

    def test_known_timezone(self) -> None:
        offset = get_timezone_offset("Asia/Seoul")
        assert offset == 9.0

    def test_invalid_timezone(self) -> None:
        assert get_timezone_offset("Invalid/Nowhere") == 0.0


class TestEstimateOffsetFromIp:
    """Tests for estimate_offset_from_ip."""

    def test_korean_ip(self) -> None:
        # 210.x.x.x → Korea → +9
        result = estimate_offset_from_ip("210.1.2.3")
        assert result is not None
        assert result == 9.0

    def test_north_american_ip(self) -> None:
        # 8.x.x.x → North America → -5
        result = estimate_offset_from_ip("8.8.8.8")
        assert result is not None
        assert result == -5.0

    def test_european_ip(self) -> None:
        # 5.x.x.x → Europe → +1
        result = estimate_offset_from_ip("5.1.2.3")
        assert result is not None
        assert result == 1.0

    def test_unknown_ip_returns_none(self) -> None:
        # 250.x.x.x is not in our heuristic ranges
        assert estimate_offset_from_ip("250.0.0.1") is None

    def test_invalid_ip(self) -> None:
        assert estimate_offset_from_ip("not-an-ip") is None

    def test_empty_ip(self) -> None:
        assert estimate_offset_from_ip("") is None


class TestVerifyTimezone:
    """Tests for verify_timezone."""

    def test_plausible_korean(self) -> None:
        result = verify_timezone("peer1", "Asia/Seoul", "210.1.2.3")
        assert result.plausible is True
        assert result.claimed_tz == "Asia/Seoul"

    def test_implausible_mismatch(self) -> None:
        # Claiming Asia/Seoul from a North American IP
        result = verify_timezone("peer2", "Asia/Seoul", "8.8.8.8")
        assert result.plausible is False
        assert result.offset_diff_hours is not None
        assert result.offset_diff_hours > MAX_OFFSET_DIFF_HOURS

    def test_unknown_ip_is_plausible(self) -> None:
        result = verify_timezone("peer3", "Europe/London", "250.0.0.1")
        assert result.plausible is True
        assert result.estimated_offset_hours is None

    def test_matching_european(self) -> None:
        result = verify_timezone("peer4", "Europe/Paris", "5.1.2.3")
        assert result.plausible is True

    def test_close_offset_passes(self) -> None:
        # UTC+1 claimed from IP suggesting UTC+1 → pass
        result = verify_timezone("peer5", "Europe/Berlin", "83.0.0.1")
        assert result.plausible is True


class TestTimezoneConsistencyTracker:
    """Tests for consistency tracking."""

    @pytest.fixture
    def tracker(self) -> TimezoneConsistencyTracker:
        return TimezoneConsistencyTracker()

    def test_single_claim_not_suspicious(
        self, tracker: TimezoneConsistencyTracker
    ) -> None:
        record = tracker.record_claim("peer1", "Asia/Seoul")
        assert not record.suspicious
        assert record.claim_count == 1
        assert record.unique_timezones == 1

    def test_same_timezone_not_suspicious(
        self, tracker: TimezoneConsistencyTracker
    ) -> None:
        for _ in range(10):
            record = tracker.record_claim("peer1", "Asia/Seoul")
        assert not record.suspicious
        assert record.unique_timezones == 1

    def test_frequent_changes_suspicious(
        self, tracker: TimezoneConsistencyTracker
    ) -> None:
        timezones = [
            "Asia/Seoul",
            "America/New_York",
            "Europe/London",
            "Asia/Tokyo",
            "America/Los_Angeles",
        ]
        record = None
        for tz in timezones:
            record = tracker.record_claim("peer1", tz)
        assert record is not None
        assert record.suspicious is True
        assert record.changes_in_24h >= MAX_TZ_CHANGES_PER_DAY

    def test_two_changes_not_suspicious(
        self, tracker: TimezoneConsistencyTracker
    ) -> None:
        tracker.record_claim("peer1", "Asia/Seoul")
        tracker.record_claim("peer1", "Asia/Tokyo")
        record = tracker.record_claim("peer1", "Asia/Seoul")
        assert record.changes_in_24h == 2
        assert not record.suspicious

    def test_is_suspicious_method(self, tracker: TimezoneConsistencyTracker) -> None:
        # Not suspicious initially
        tracker.record_claim("peer1", "UTC")
        assert not tracker.is_suspicious("peer1")

    def test_unknown_peer_not_suspicious(
        self, tracker: TimezoneConsistencyTracker
    ) -> None:
        assert not tracker.is_suspicious("unknown_peer")

    def test_multiple_peers_independent(
        self, tracker: TimezoneConsistencyTracker
    ) -> None:
        # Peer1 changes a lot
        for tz in ["A", "B", "C", "D", "E"]:
            tracker.record_claim("peer1", tz)
        # Peer2 is stable
        tracker.record_claim("peer2", "Asia/Seoul")
        assert not tracker.is_suspicious("peer2")
