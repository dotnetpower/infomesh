"""Off-peak timezone verification — cross-check claimed timezone.

When a peer claims to be in a certain timezone (e.g., "Asia/Seoul"),
we can perform a lightweight plausibility check by comparing the
claimed timezone with the UTC offset derived from the peer's IP
address (via a GeoIP lookup or a simple heuristic).

This prevents peers from falsely claiming off-peak status to earn
the 1.5× credit multiplier on LLM tasks.

Verification levels:
    1. **UTC offset plausibility** — check if the claimed timezone's
       UTC offset is within ±2 hours of the IP-derived offset.
    2. **Consistency tracking** — log timezone claims over time; flag
       peers whose timezone changes frequently.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger()

# ── Constants ─────────────────────────────────────────────────

# Maximum UTC offset difference (hours) before flagging a mismatch
MAX_OFFSET_DIFF_HOURS = 2

# How many timezone changes in 24 hours before flagging
MAX_TZ_CHANGES_PER_DAY = 3

# IP-to-offset mapping for common regions (approximate, no external deps)
# For production use, a MaxMind GeoLite2 database would be more accurate.
# Format: first octet ranges → typical UTC offset
_IP_REGION_OFFSETS: list[tuple[range, float, str]] = [
    # Asia
    (range(1, 2), 8.0, "Asia (CN/JP/KR)"),
    (range(14, 15), 9.0, "Asia-Pacific"),
    (range(27, 28), 5.5, "South Asia"),
    (range(36, 37), 9.0, "Japan"),
    (range(49, 50), 9.0, "Japan"),
    (range(58, 59), 8.0, "East Asia"),
    (range(61, 62), 5.5, "India"),
    (range(101, 126), 8.0, "East Asia"),
    (range(175, 176), 9.0, "Asia-Pacific"),
    (range(210, 212), 9.0, "Korea"),
    (range(218, 222), 9.0, "Korea"),
    # Europe
    (range(2, 3), 1.0, "Europe"),
    (range(5, 6), 1.0, "Europe"),
    (range(31, 32), 1.0, "Europe"),
    (range(37, 38), 1.0, "France"),
    (range(46, 47), 3.0, "Russia"),
    (range(62, 63), 1.0, "Europe"),
    (range(77, 80), 1.0, "Europe"),
    (range(80, 82), 1.0, "Europe"),
    (range(83, 88), 1.0, "Europe"),
    (range(88, 96), 1.0, "Europe"),
    (range(145, 150), 1.0, "Europe"),
    (range(176, 178), 1.0, "Europe"),
    (range(185, 195), 1.0, "Europe"),
    (range(193, 196), 1.0, "Europe"),
    # Americas
    (range(3, 5), -5.0, "North America"),
    (range(6, 9), -5.0, "North America"),
    (range(12, 14), -5.0, "North America"),
    (range(15, 20), -5.0, "North America"),
    (range(23, 27), -5.0, "North America"),
    (range(32, 36), -5.0, "North America"),
    (range(38, 45), -5.0, "North America"),
    (range(47, 49), -5.0, "North America"),
    (range(50, 55), -5.0, "North America"),
    (range(63, 77), -5.0, "North America"),
    (range(96, 101), -5.0, "North America"),
    (range(128, 145), -5.0, "North America"),
    (range(198, 210), -5.0, "North America"),
    # Oceania
    (range(150, 154), 10.0, "Oceania"),
    (range(202, 204), 10.0, "Oceania"),
]


@dataclass(frozen=True)
class TimezoneCheck:
    """Result of timezone plausibility verification."""

    peer_id: str
    claimed_tz: str
    claimed_offset_hours: float
    estimated_offset_hours: float | None
    offset_diff_hours: float | None
    plausible: bool
    reason: str


@dataclass(frozen=True)
class ConsistencyRecord:
    """Tracks timezone consistency for a peer."""

    peer_id: str
    claim_count: int
    unique_timezones: int
    changes_in_24h: int
    suspicious: bool


def get_timezone_offset(tz_name: str) -> float:
    """Get the current UTC offset for a timezone in hours.

    Args:
        tz_name: IANA timezone (e.g. "Asia/Seoul").

    Returns:
        UTC offset in hours (e.g. 9.0 for KST).
    """
    try:
        tz = ZoneInfo(tz_name)
        dt = datetime.datetime.now(tz=tz)
        offset = dt.utcoffset()
        if offset is None:
            return 0.0
        return offset.total_seconds() / 3600
    except (KeyError, ValueError):
        return 0.0


def estimate_offset_from_ip(ip_address: str) -> float | None:
    """Estimate UTC offset from an IP address using heuristic ranges.

    This is a rough approximation without external GeoIP databases.
    Returns ``None`` if the IP cannot be mapped.

    Args:
        ip_address: IPv4 address string.

    Returns:
        Estimated UTC offset in hours, or ``None``.
    """
    try:
        first_octet = int(ip_address.split(".")[0])
    except (ValueError, IndexError):
        return None

    for ip_range, offset, _region in _IP_REGION_OFFSETS:
        if first_octet in ip_range:
            return offset

    return None


def verify_timezone(
    peer_id: str,
    claimed_tz: str,
    ip_address: str,
) -> TimezoneCheck:
    """Verify that a peer's claimed timezone is plausible given its IP.

    Args:
        peer_id: The peer's ID.
        claimed_tz: Claimed IANA timezone string.
        ip_address: Peer's IP address.

    Returns:
        ``TimezoneCheck`` with plausibility result.
    """
    claimed_offset = get_timezone_offset(claimed_tz)
    estimated_offset = estimate_offset_from_ip(ip_address)

    if estimated_offset is None:
        # Cannot determine — give benefit of the doubt
        return TimezoneCheck(
            peer_id=peer_id,
            claimed_tz=claimed_tz,
            claimed_offset_hours=claimed_offset,
            estimated_offset_hours=None,
            offset_diff_hours=None,
            plausible=True,
            reason="IP region unknown, cannot verify",
        )

    diff = abs(claimed_offset - estimated_offset)
    # Handle wrap-around (e.g. UTC+12 vs UTC-12)
    if diff > 12:
        diff = 24 - diff

    plausible = diff <= MAX_OFFSET_DIFF_HOURS

    if plausible:
        reason = f"offset diff {diff:.1f}h within ±{MAX_OFFSET_DIFF_HOURS}h tolerance"
    else:
        reason = (
            f"offset diff {diff:.1f}h exceeds ±{MAX_OFFSET_DIFF_HOURS}h "
            f"(claimed {claimed_tz}={claimed_offset:+.1f}, "
            f"IP suggests {estimated_offset:+.1f})"
        )
        logger.warning(
            "timezone_mismatch",
            peer_id=peer_id[:16],
            claimed=claimed_tz,
            diff_hours=diff,
        )

    return TimezoneCheck(
        peer_id=peer_id,
        claimed_tz=claimed_tz,
        claimed_offset_hours=claimed_offset,
        estimated_offset_hours=estimated_offset,
        offset_diff_hours=diff,
        plausible=plausible,
        reason=reason,
    )


class TimezoneConsistencyTracker:
    """Tracks timezone claims over time to detect suspicious changes.

    A peer that changes its claimed timezone more than
    ``MAX_TZ_CHANGES_PER_DAY`` times in 24 hours is flagged.
    """

    def __init__(self) -> None:
        # peer_id → list of (timestamp, timezone) claims
        self._claims: dict[str, list[tuple[float, str]]] = {}

    def record_claim(self, peer_id: str, timezone: str) -> ConsistencyRecord:
        """Record a timezone claim and return consistency status.

        Args:
            peer_id: The peer.
            timezone: Claimed IANA timezone.

        Returns:
            ``ConsistencyRecord`` with suspicion flag.
        """
        now = time.time()

        if peer_id not in self._claims:
            self._claims[peer_id] = []

        self._claims[peer_id].append((now, timezone))

        # Prune claims older than 48 hours
        cutoff = now - 48 * 3600
        self._claims[peer_id] = [
            (ts, tz) for ts, tz in self._claims[peer_id] if ts >= cutoff
        ]

        claims = self._claims[peer_id]

        # Count unique timezones
        all_tzs = {tz for _, tz in claims}

        # Count timezone changes in last 24 hours
        recent_cutoff = now - 24 * 3600
        recent = [(ts, tz) for ts, tz in claims if ts >= recent_cutoff]
        changes_24h = 0
        for i in range(1, len(recent)):
            if recent[i][1] != recent[i - 1][1]:
                changes_24h += 1

        suspicious = changes_24h >= MAX_TZ_CHANGES_PER_DAY

        if suspicious:
            logger.warning(
                "timezone_suspicious_changes",
                peer_id=peer_id[:16],
                changes=changes_24h,
            )

        return ConsistencyRecord(
            peer_id=peer_id,
            claim_count=len(claims),
            unique_timezones=len(all_tzs),
            changes_in_24h=changes_24h,
            suspicious=suspicious,
        )

    def is_suspicious(self, peer_id: str) -> bool:
        """Check if a peer has been flagged for suspicious timezone changes."""
        if peer_id not in self._claims:
            return False
        record = self.record_claim(peer_id, self._claims[peer_id][-1][1])
        return record.suspicious
