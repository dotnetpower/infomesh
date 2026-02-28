"""Tests for resource profiles and governor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from infomesh.resources.governor import (
    DegradeLevel,
    ResourceGovernor,
)
from infomesh.resources.profiles import (
    PROFILES,
    ProfileName,
    ResourceProfile,
    build_custom_profile,
    get_profile,
)

# ── Profile tests ───────────────────────────────────────────────────────


class TestProfileName:
    def test_all_profiles_exist(self) -> None:
        for name in ProfileName:
            if name != ProfileName.CUSTOM:
                assert name in PROFILES

    def test_profile_values(self) -> None:
        assert ProfileName.MINIMAL == "minimal"
        assert ProfileName.BALANCED == "balanced"
        assert ProfileName.CONTRIBUTOR == "contributor"
        assert ProfileName.DEDICATED == "dedicated"

    def test_custom_not_in_profiles_dict(self) -> None:
        assert ProfileName.CUSTOM not in PROFILES


class TestGetProfile:
    def test_get_minimal(self) -> None:
        p = get_profile("minimal")
        assert p.name == ProfileName.MINIMAL
        assert p.cpu_cores_limit == 1
        assert p.cpu_nice == 19
        assert p.max_concurrent_crawl == 1
        assert p.llm_enabled is False

    def test_get_balanced(self) -> None:
        p = get_profile("balanced")
        assert p.name == ProfileName.BALANCED
        assert p.cpu_cores_limit == 2
        assert p.max_concurrent_crawl == 3
        assert p.llm_off_peak_only is True

    def test_get_contributor(self) -> None:
        p = get_profile("contributor")
        assert p.name == ProfileName.CONTRIBUTOR
        assert p.cpu_cores_limit == 4
        assert p.llm_enabled is True
        assert p.llm_off_peak_only is False

    def test_get_dedicated(self) -> None:
        p = get_profile("dedicated")
        assert p.name == ProfileName.DEDICATED
        assert p.cpu_cores_limit == 0  # unlimited
        assert p.max_concurrent_crawl == 10
        assert p.upload_limit_mbps == 25.0

    def test_get_custom_returns_balanced_base(self) -> None:
        p = get_profile("custom")
        assert p.name == ProfileName.CUSTOM
        base = get_profile("balanced")
        assert p.cpu_cores_limit == base.cpu_cores_limit
        assert p.max_concurrent_crawl == base.max_concurrent_crawl

    def test_get_profile_by_enum(self) -> None:
        p = get_profile(ProfileName.MINIMAL)
        assert p.name == ProfileName.MINIMAL

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown profile"):
            get_profile("nonexistent")


class TestBuildCustomProfile:
    def test_override_single_field(self) -> None:
        p = build_custom_profile(cpu_cores_limit=8)
        assert p.name == ProfileName.CUSTOM
        assert p.cpu_cores_limit == 8
        # Other fields should be balanced defaults
        base = get_profile("balanced")
        assert p.max_concurrent_crawl == base.max_concurrent_crawl

    def test_override_multiple_fields(self) -> None:
        p = build_custom_profile(
            cpu_cores_limit=16,
            upload_limit_mbps=50.0,
            llm_enabled=False,
        )
        assert p.cpu_cores_limit == 16
        assert p.upload_limit_mbps == 50.0
        assert p.llm_enabled is False

    def test_unknown_field_ignored(self) -> None:
        # Should log warning but not crash
        p = build_custom_profile(nonexistent_field=42)
        assert p.name == ProfileName.CUSTOM


class TestProfileOrdering:
    """Verify that profiles increase in resource allocation."""

    def test_cpu_cores_ascending(self) -> None:
        minimal = get_profile("minimal")
        balanced = get_profile("balanced")
        contributor = get_profile("contributor")
        assert minimal.cpu_cores_limit <= balanced.cpu_cores_limit
        assert balanced.cpu_cores_limit <= contributor.cpu_cores_limit

    def test_concurrent_crawl_ascending(self) -> None:
        minimal = get_profile("minimal")
        balanced = get_profile("balanced")
        contributor = get_profile("contributor")
        dedicated = get_profile("dedicated")
        assert minimal.max_concurrent_crawl <= balanced.max_concurrent_crawl
        assert balanced.max_concurrent_crawl <= contributor.max_concurrent_crawl
        assert contributor.max_concurrent_crawl <= dedicated.max_concurrent_crawl

    def test_network_limits_ascending(self) -> None:
        minimal = get_profile("minimal")
        balanced = get_profile("balanced")
        assert minimal.upload_limit_mbps <= balanced.upload_limit_mbps
        assert minimal.download_limit_mbps <= balanced.download_limit_mbps


# ── Governor tests ──────────────────────────────────────────────────────


@pytest.fixture()
def balanced_profile() -> ResourceProfile:
    return get_profile("balanced")


@pytest.fixture()
def governor(balanced_profile: ResourceProfile) -> ResourceGovernor:
    return ResourceGovernor(balanced_profile)


class TestGovernorInit:
    def test_initial_state(self, governor: ResourceGovernor) -> None:
        assert governor.state.degrade_level == DegradeLevel.NORMAL
        assert governor.state.throttle_factor == 1.0
        assert governor.state.checks_performed == 0

    def test_profile_stored(
        self, governor: ResourceGovernor, balanced_profile: ResourceProfile
    ) -> None:
        assert governor.profile is balanced_profile


class TestGovernorProperties:
    def test_should_throttle_crawl_normal(self, governor: ResourceGovernor) -> None:
        assert governor.should_throttle_crawl is False

    def test_should_pause_crawl_normal(self, governor: ResourceGovernor) -> None:
        assert governor.should_pause_crawl is False

    def test_should_disable_llm_normal(self, governor: ResourceGovernor) -> None:
        assert governor.should_disable_llm is False

    def test_should_disable_remote_search_normal(
        self, governor: ResourceGovernor
    ) -> None:
        assert governor.should_disable_remote_search is False

    def test_is_read_only_normal(self, governor: ResourceGovernor) -> None:
        assert governor.is_read_only is False

    def test_effective_max_concurrent_full(self, governor: ResourceGovernor) -> None:
        assert governor.effective_max_concurrent == 3  # balanced default


class TestGovernorCheckAndAdjust:
    @patch.object(ResourceGovernor, "_sample_cpu", return_value=20.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=40.0)
    def test_normal_load(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        assert state.degrade_level == DegradeLevel.NORMAL
        assert state.throttle_factor == 1.0
        assert state.checks_performed == 1

    @patch.object(ResourceGovernor, "_sample_cpu", return_value=65.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=40.0)
    def test_warning_level(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        assert state.degrade_level == DegradeLevel.WARNING
        assert state.throttle_factor == 0.5
        assert governor.should_pause_crawl is True
        assert governor.should_disable_llm is True

    @patch.object(ResourceGovernor, "_sample_cpu", return_value=85.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=40.0)
    def test_overloaded_level(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        assert state.degrade_level == DegradeLevel.OVERLOADED
        assert state.throttle_factor == 0.25
        assert governor.should_disable_remote_search is True

    @patch.object(ResourceGovernor, "_sample_cpu", return_value=92.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=40.0)
    def test_severe_level(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        assert state.degrade_level == DegradeLevel.SEVERE
        assert state.throttle_factor == 0.0
        assert governor.is_read_only is True

    @patch.object(ResourceGovernor, "_sample_cpu", return_value=97.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=40.0)
    def test_defensive_level(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        assert state.degrade_level == DegradeLevel.DEFENSIVE
        assert state.throttle_factor == 0.0

    @patch.object(ResourceGovernor, "_sample_cpu", return_value=40.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=96.0)
    def test_memory_triggers_defensive(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        assert state.degrade_level == DegradeLevel.DEFENSIVE

    @patch.object(ResourceGovernor, "_sample_cpu", return_value=50.0)
    @patch.object(ResourceGovernor, "_sample_memory", return_value=30.0)
    def test_gradual_throttle(
        self, mock_mem: MagicMock, mock_cpu: MagicMock, governor: ResourceGovernor
    ) -> None:
        state = governor.check_and_adjust()
        # CPU 50 is between LOW (30) and threshold for WARNING (60)
        assert state.degrade_level == DegradeLevel.NORMAL
        # 50 > CPU_LOW_PCT=30, so gradual throttle applies
        assert 0.3 < state.throttle_factor < 1.0

    def test_effective_max_concurrent_throttled(
        self, governor: ResourceGovernor
    ) -> None:
        governor._state.throttle_factor = 0.5
        assert governor.effective_max_concurrent == 1  # int(3 * 0.5) = 1


class TestGovernorOsPriority:
    def test_apply_on_linux(self, governor: ResourceGovernor) -> None:
        # Should not raise on any platform
        with patch("os.nice", return_value=0):
            governor.apply_os_priority()


class TestGovernorNetworkSampling:
    def test_first_call_returns_zero(self, governor: ResourceGovernor) -> None:
        ul, dl = governor.sample_network_mbps()
        # Without psutil or on first call, returns 0.0
        assert ul >= 0.0
        assert dl >= 0.0
