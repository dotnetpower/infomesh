"""Resource profiles — predefined presets for CPU, network, and crawl limits.

Each profile provides sensible defaults for different deployment scenarios.
Custom profiles allow users to override any individual setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

logger = structlog.get_logger()


class ProfileName(StrEnum):
    """Available resource profile presets."""

    MINIMAL = "minimal"
    BALANCED = "balanced"
    CONTRIBUTOR = "contributor"
    DEDICATED = "dedicated"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ResourceProfile:
    """A complete resource allocation profile.

    Attributes:
        name: Profile identifier.
        cpu_cores_limit: Maximum CPU cores to use.
        cpu_nice: Unix nice value (0–19, higher = lower priority).
        memory_limit_mb: Soft memory limit in MB.
        disk_io_priority: I/O scheduling class.
        upload_limit_mbps: Network upload cap.
        download_limit_mbps: Network download cap.
        max_concurrent_crawl: Simultaneous crawl connections.
        llm_enabled: Whether LLM summarization is active.
        llm_off_peak_only: Restrict LLM to off-peak hours.
    """

    name: ProfileName
    cpu_cores_limit: int
    cpu_nice: int
    memory_limit_mb: int
    disk_io_priority: str  # "low" | "normal" | "high"
    upload_limit_mbps: float
    download_limit_mbps: float
    max_concurrent_crawl: int
    llm_enabled: bool
    llm_off_peak_only: bool


# ── Preset definitions ──────────────────────────────────────────────────

PROFILES: dict[ProfileName, ResourceProfile] = {
    ProfileName.MINIMAL: ResourceProfile(
        name=ProfileName.MINIMAL,
        cpu_cores_limit=1,
        cpu_nice=19,
        memory_limit_mb=512,
        disk_io_priority="low",
        upload_limit_mbps=0.5,
        download_limit_mbps=1.0,
        max_concurrent_crawl=1,
        llm_enabled=False,
        llm_off_peak_only=True,
    ),
    ProfileName.BALANCED: ResourceProfile(
        name=ProfileName.BALANCED,
        cpu_cores_limit=2,
        cpu_nice=10,
        memory_limit_mb=2048,
        disk_io_priority="low",
        upload_limit_mbps=2.0,
        download_limit_mbps=5.0,
        max_concurrent_crawl=3,
        llm_enabled=True,
        llm_off_peak_only=True,
    ),
    ProfileName.CONTRIBUTOR: ResourceProfile(
        name=ProfileName.CONTRIBUTOR,
        cpu_cores_limit=4,
        cpu_nice=5,
        memory_limit_mb=4096,
        disk_io_priority="normal",
        upload_limit_mbps=5.0,
        download_limit_mbps=10.0,
        max_concurrent_crawl=5,
        llm_enabled=True,
        llm_off_peak_only=False,
    ),
    ProfileName.DEDICATED: ResourceProfile(
        name=ProfileName.DEDICATED,
        cpu_cores_limit=0,  # 0 = unlimited
        cpu_nice=0,
        memory_limit_mb=0,  # 0 = unlimited
        disk_io_priority="high",
        upload_limit_mbps=25.0,
        download_limit_mbps=50.0,
        max_concurrent_crawl=10,
        llm_enabled=True,
        llm_off_peak_only=False,
    ),
}


def get_profile(name: str | ProfileName) -> ResourceProfile:
    """Retrieve a named resource profile.

    Args:
        name: Profile name string or enum.

    Returns:
        The matching :class:`ResourceProfile`.

    Raises:
        ValueError: If *name* is not a known profile and is not ``custom``.
    """
    try:
        key = ProfileName(name)
    except ValueError:
        raise ValueError(
            f"Unknown profile '{name}'. "
            f"Valid profiles: {', '.join(p.value for p in ProfileName)}"
        ) from None

    if key == ProfileName.CUSTOM:
        # Return balanced as base; caller should overlay custom values.
        return ResourceProfile(
            name=ProfileName.CUSTOM,
            cpu_cores_limit=PROFILES[ProfileName.BALANCED].cpu_cores_limit,
            cpu_nice=PROFILES[ProfileName.BALANCED].cpu_nice,
            memory_limit_mb=PROFILES[ProfileName.BALANCED].memory_limit_mb,
            disk_io_priority=PROFILES[ProfileName.BALANCED].disk_io_priority,
            upload_limit_mbps=PROFILES[ProfileName.BALANCED].upload_limit_mbps,
            download_limit_mbps=PROFILES[ProfileName.BALANCED].download_limit_mbps,
            max_concurrent_crawl=PROFILES[ProfileName.BALANCED].max_concurrent_crawl,
            llm_enabled=PROFILES[ProfileName.BALANCED].llm_enabled,
            llm_off_peak_only=PROFILES[ProfileName.BALANCED].llm_off_peak_only,
        )

    return PROFILES[key]


def build_custom_profile(**overrides: object) -> ResourceProfile:
    """Build a custom profile by overlaying values on the *balanced* defaults.

    Args:
        **overrides: Field names and their desired values.

    Returns:
        A :class:`ResourceProfile` with ``name=CUSTOM``.
    """
    base = get_profile(ProfileName.BALANCED)
    fields = {f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()}
    fields["name"] = ProfileName.CUSTOM
    for k, v in overrides.items():
        if k in fields and k != "name":
            fields[k] = v
        else:
            logger.warning("profile_unknown_field", field=k)
    return ResourceProfile(**fields)
