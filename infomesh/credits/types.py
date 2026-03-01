"""Credit system types, enums, constants, and dataclasses.

Shared definitions for the InfoMesh incentive/credit subsystem.
Extracted from ``ledger.py`` to satisfy the Single Responsibility
Principle — the ledger module focuses on persistence & mutation,
while this module owns the *data model*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# --- Action types & weights -----------------------------------------------


class ActionType(StrEnum):
    """Creditable actions with normalized resource weights."""

    CRAWL = "crawl"  # 1.0 /page   — reference unit
    QUERY_PROCESS = "query_process"  # 0.5 /query
    DOC_HOSTING = "doc_hosting"  # 0.1 /hour
    NETWORK_UPTIME = "network_uptime"  # 0.5 /hour
    LLM_SUMMARIZE_OWN = "llm_own"  # 1.5 /page
    LLM_SUMMARIZE_PEER = "llm_peer"  # 2.0 /request
    GIT_DOCS = "git_docs"  # 1_000 /merged PR (docs/typo)
    GIT_FIX = "git_fix"  # 10_000 /merged PR (bug fix)
    GIT_FEATURE = "git_feature"  # 50_000 /merged PR (new feature)
    GIT_MAJOR = "git_major"  # 100_000 /merged PR (core/architecture)


ACTION_WEIGHTS: dict[ActionType, float] = {
    ActionType.CRAWL: 1.0,
    ActionType.QUERY_PROCESS: 0.5,
    ActionType.DOC_HOSTING: 0.1,
    ActionType.NETWORK_UPTIME: 0.5,
    ActionType.LLM_SUMMARIZE_OWN: 1.5,
    ActionType.LLM_SUMMARIZE_PEER: 2.0,
    ActionType.GIT_DOCS: 1_000.0,
    ActionType.GIT_FIX: 10_000.0,
    ActionType.GIT_FEATURE: 50_000.0,
    ActionType.GIT_MAJOR: 100_000.0,
}

# Legacy alias for backward compatibility with existing ledger entries
GIT_CONTRIBUTION_LEGACY = "git_contrib"

_GIT_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.GIT_DOCS,
        ActionType.GIT_FIX,
        ActionType.GIT_FEATURE,
        ActionType.GIT_MAJOR,
    }
)

# Actions eligible for off-peak multiplier
_LLM_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.LLM_SUMMARIZE_OWN,
        ActionType.LLM_SUMMARIZE_PEER,
    }
)


# --- Tier definitions -----------------------------------------------------


class ContributionTier(StrEnum):
    """Contributor tiers — higher tier = cheaper search."""

    TIER_1 = "tier_1"  # < 100 score
    TIER_2 = "tier_2"  # 100 – 999
    TIER_3 = "tier_3"  # ≥ 1000


TIER_THRESHOLDS: list[tuple[float, ContributionTier, float]] = [
    (1000.0, ContributionTier.TIER_3, 0.033),
    (100.0, ContributionTier.TIER_2, 0.050),
    (0.0, ContributionTier.TIER_1, 0.100),
]

# Maximum share of credits from LLM actions (cap at 60 %)
LLM_CREDIT_CAP_RATIO: float = 0.60

# Grace period & debt constants
GRACE_PERIOD_HOURS: float = 72.0
DEBT_COST_MULTIPLIER: float = 2.0


# --- Credit state ---------------------------------------------------------


class CreditState(StrEnum):
    """Node credit state — determines search cost behaviour."""

    NORMAL = "normal"  # balance > 0
    GRACE = "grace"  # balance ≤ 0, within 72 h grace window
    DEBT = "debt"  # balance ≤ 0, grace window expired


# --- Data classes ---------------------------------------------------------


@dataclass(frozen=True)
class CreditEntry:
    """Single credit ledger entry."""

    entry_id: int
    action: str
    quantity: float
    weight: float
    multiplier: float
    credits: float
    timestamp: float
    note: str
    entry_hash: str = ""
    signature: str = ""


@dataclass(frozen=True)
class SearchAllowance:
    """Result of a search-cost check.

    Search is **never blocked** — only the cost changes.
    """

    state: CreditState
    search_cost: float
    grace_remaining_hours: float | None  # None when NORMAL/DEBT
    debt_amount: float  # 0.0 when NORMAL


@dataclass(frozen=True)
class LedgerStats:
    """Summary statistics for a peer's credit ledger."""

    total_earned: float
    total_spent: float
    balance: float
    contribution_score: float
    tier: ContributionTier
    search_cost: float
    llm_credits: float
    non_llm_credits: float
    credit_state: CreditState = CreditState.NORMAL
    grace_remaining_hours: float | None = None
    debt_amount: float = 0.0
    owner_email: str = ""
