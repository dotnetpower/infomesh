"""Local credit ledger for the InfoMesh incentive system.

Credits are tracked locally per peer — no blockchain.

Formula:
    C_earned = Σ (W_i × Q_i × M_i)

    W_i = resource weight for action type i
    Q_i = quantity performed
    M_i = time multiplier (1.0 default; 1.5 for LLM during off-peak)

Search cost depends on the contributor tier:
    Tier 1 (<100 score):   0.100
    Tier 2 (100–999):      0.050
    Tier 3 (≥1000):        0.033

Grace Period + Debt:
    When credits are exhausted (balance ≤ 0), a 72-hour grace period
    begins where search continues at normal cost.  After the grace
    window expires, the node enters *debt mode* — search still works
    but at 2× cost.  Debt is measured in credits, not money.  To
    recover, simply run the node and earn credits through normal
    contribution (crawling, hosting, uptime).  Once balance is
    positive again, the debt state resets.

    No credit card.  No dollars.  No subscription.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from infomesh.credits.scheduling import OFF_PEAK_MULTIPLIER
from infomesh.db import SQLiteStore
from infomesh.hashing import content_hash
from infomesh.types import KeyPairLike

logger = structlog.get_logger()


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


# --- Ledger ---------------------------------------------------------------


class CreditLedger(SQLiteStore):
    """SQLite-backed local credit ledger.

    Thread-safe for single-writer / multi-reader use (WAL mode).
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS credit_entries (
            entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            action      TEXT    NOT NULL,
            quantity    REAL    NOT NULL,
            weight      REAL    NOT NULL,
            multiplier  REAL    NOT NULL DEFAULT 1.0,
            credits     REAL    NOT NULL,
            timestamp   REAL    NOT NULL,
            note        TEXT    NOT NULL DEFAULT '',
            entry_hash  TEXT    NOT NULL DEFAULT '',
            signature   TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS credit_spending (
            spend_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            amount      REAL    NOT NULL,
            reason      TEXT    NOT NULL DEFAULT 'search',
            timestamp   REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credit_grace (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            grace_start REAL
        );
        INSERT OR IGNORE INTO credit_grace (id, grace_start) VALUES (1, NULL);

        CREATE INDEX IF NOT EXISTS idx_entries_action
            ON credit_entries(action);
        CREATE INDEX IF NOT EXISTS idx_entries_ts
            ON credit_entries(timestamp);
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        super().__init__(db_path, extra_pragmas=["PRAGMA foreign_keys=ON"])
        self._migrate()

    # --- Migration ---------------------------------------------------------

    def _migrate(self) -> None:
        """Add entry_hash / signature columns.

        Also adds credit_grace table for existing databases.
        """
        cursor = self._conn.execute("PRAGMA table_info(credit_entries)")
        columns = {row[1] for row in cursor.fetchall()}
        if "entry_hash" not in columns:
            self._conn.execute(
                "ALTER TABLE credit_entries"
                " ADD COLUMN entry_hash TEXT"
                " NOT NULL DEFAULT ''"
            )
        if "signature" not in columns:
            self._conn.execute(
                "ALTER TABLE credit_entries"
                " ADD COLUMN signature TEXT"
                " NOT NULL DEFAULT ''"
            )

        # Ensure credit_grace table exists (for databases created before debt system)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS credit_grace "
            "(id INTEGER PRIMARY KEY CHECK (id = 1),"
            " grace_start REAL)"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO credit_grace (id, grace_start) VALUES (1, NULL)"
        )
        self._conn.commit()

    # --- Earning -----------------------------------------------------------

    def record_action(
        self,
        action: ActionType,
        quantity: float = 1.0,
        *,
        off_peak: bool = False,
        note: str = "",
        key_pair: KeyPairLike | None = None,
    ) -> float:
        """Record a credited action and return the credits earned.

        Args:
            action: Type of contribution.
            quantity: Number of units (pages, queries, hours, …). Must be > 0.
            off_peak: ``True`` if the action occurred during off-peak hours.
            note: Free-text annotation.
            key_pair: Optional key pair for signing the entry (enables P2P
                verification).  When provided, the entry's canonical data is
                hashed and signed with Ed25519.

        Returns:
            Credits earned (after weight × quantity × multiplier).

        Raises:
            ValueError: If ``quantity`` is not positive.
        """
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")

        weight = ACTION_WEIGHTS[action]
        multiplier = (
            OFF_PEAK_MULTIPLIER if (off_peak and action in _LLM_ACTIONS) else 1.0
        )
        earned = weight * quantity * multiplier

        now = time.time()

        # Compute entry hash and optional signature
        canonical = _entry_canonical(
            action.value,
            quantity,
            weight,
            multiplier,
            earned,
            now,
            note,
        )
        entry_hash = content_hash(canonical)
        sig_hex = ""
        if key_pair is not None:
            sig_hex = key_pair.sign(canonical).hex()

        self._conn.execute(
            """INSERT INTO credit_entries
               (action, quantity, weight, multiplier, credits, timestamp, note,
                entry_hash, signature)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action.value,
                quantity,
                weight,
                multiplier,
                earned,
                now,
                note,
                entry_hash,
                sig_hex,
            ),
        )
        self._conn.commit()

        # Check if earning credits restored a positive balance → clear debt
        if self.balance() > 0:
            self._clear_grace()

        logger.debug(
            "credit_earned",
            action=action.value,
            quantity=quantity,
            multiplier=multiplier,
            credits=round(earned, 4),
        )
        return earned

    # --- Spending ----------------------------------------------------------

    def spend(self, amount: float, *, reason: str = "search") -> bool:
        """Deduct credits.  **Always succeeds** — debt is allowed.

        When the balance goes negative, a 72-hour grace period starts.
        After the grace window, the node enters debt mode (search cost
        doubles).  Debt is recovered by earning credits through normal
        contribution.

        Uses a single transaction to prevent TOCTOU race conditions.

        Args:
            amount: Credits to deduct. Must be > 0.
            reason: Why the deduction happened.

        Returns:
            Always ``True`` — search is never blocked.

        Raises:
            ValueError: If ``amount`` is not positive.
        """
        if amount <= 0:
            raise ValueError(f"amount must be positive, got {amount}")

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            now = time.time()

            self._conn.execute(
                "INSERT INTO credit_spending"
                " (amount, reason, timestamp)"
                " VALUES (?, ?, ?)",
                (amount, reason, now),
            )

            # Check if balance just went negative → start grace period
            new_balance = self.balance()
            if new_balance <= 0:
                row = self._conn.execute(
                    "SELECT grace_start FROM credit_grace WHERE id = 1"
                ).fetchone()
                if row is not None and row[0] is None:
                    self._conn.execute(
                        "UPDATE credit_grace SET grace_start = ? WHERE id = 1",
                        (now,),
                    )
                    logger.info(
                        "grace_period_started",
                        balance=round(new_balance, 4),
                    )

            self._conn.execute("COMMIT")
            logger.debug(
                "credit_spent",
                amount=round(amount, 4),
                reason=reason,
                balance=round(new_balance, 4),
            )
            return True
        except Exception:
            with contextlib.suppress(Exception):
                self._conn.execute("ROLLBACK")
            raise

    # --- Queries -----------------------------------------------------------

    def total_earned(self) -> float:
        """Sum of all credits earned."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(credits), 0) FROM credit_entries"
        ).fetchone()
        return float(row[0])

    def total_spent(self) -> float:
        """Sum of all credits spent."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM credit_spending"
        ).fetchone()
        return float(row[0])

    def balance(self) -> float:
        """Current credit balance (earned − spent).  Can be negative (debt)."""
        return self.total_earned() - self.total_spent()

    def debt_amount(self) -> float:
        """Amount of credit debt (always ≥ 0).  Zero when balance is positive."""
        bal = self.balance()
        return max(0.0, -bal)

    def credit_state(self) -> CreditState:
        """Current credit state: NORMAL, GRACE, or DEBT."""
        if self.balance() > 0:
            # Balance is positive → clear any lingering grace start
            self._clear_grace()
            return CreditState.NORMAL

        grace_start = self._grace_start()
        if grace_start is None:
            # Balance is zero but no grace started yet (edge case: exactly 0)
            return CreditState.NORMAL

        elapsed_hours = (time.time() - grace_start) / 3600.0
        if elapsed_hours <= GRACE_PERIOD_HOURS:
            return CreditState.GRACE
        return CreditState.DEBT

    def grace_remaining_hours(self) -> float | None:
        """Hours remaining in the grace period, or ``None`` if not in grace."""
        if self.credit_state() != CreditState.GRACE:
            return None
        grace_start = self._grace_start()
        if grace_start is None:
            return None
        elapsed = (time.time() - grace_start) / 3600.0
        return max(0.0, GRACE_PERIOD_HOURS - elapsed)

    def search_allowance(self) -> SearchAllowance:
        """Compute the current search cost considering grace/debt state.

        Search is **never blocked** — only the cost changes.

        Returns:
            SearchAllowance with state, effective cost, and debt info.
        """
        state = self.credit_state()
        base_cost = self.search_cost()

        if state == CreditState.DEBT:
            effective_cost = base_cost * DEBT_COST_MULTIPLIER
        else:
            effective_cost = base_cost

        return SearchAllowance(
            state=state,
            search_cost=effective_cost,
            grace_remaining_hours=self.grace_remaining_hours(),
            debt_amount=self.debt_amount(),
        )

    def _grace_start(self) -> float | None:
        """Return the grace-period start timestamp, or ``None``."""
        row = self._conn.execute(
            "SELECT grace_start FROM credit_grace WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return row[0]

    def _clear_grace(self) -> None:
        """Reset grace period when balance returns to positive."""
        row = self._conn.execute(
            "SELECT grace_start FROM credit_grace WHERE id = 1"
        ).fetchone()
        if row is not None and row[0] is not None:
            self._conn.execute(
                "UPDATE credit_grace SET grace_start = NULL WHERE id = 1"
            )
            self._conn.commit()
            logger.info("grace_period_resolved", msg="balance restored to positive")

    def contribution_score(self) -> float:
        """Contribution score = total_earned (only non-capped LLM portion).

        LLM credits are capped at 60 % of total to prevent LLM-only farming.
        """
        row = self._conn.execute(
            "SELECT COALESCE(SUM(credits), 0)"
            " FROM credit_entries"
            " WHERE action IN (?, ?)",
            (
                ActionType.LLM_SUMMARIZE_OWN.value,
                ActionType.LLM_SUMMARIZE_PEER.value,
            ),
        ).fetchone()
        llm_raw = float(row[0])

        row2 = self._conn.execute(
            "SELECT COALESCE(SUM(credits), 0)"
            " FROM credit_entries"
            " WHERE action NOT IN (?, ?)",
            (
                ActionType.LLM_SUMMARIZE_OWN.value,
                ActionType.LLM_SUMMARIZE_PEER.value,
            ),
        ).fetchone()
        non_llm = float(row2[0])

        # Cap LLM credits at 60 % of total contribution
        total_uncapped = non_llm + llm_raw
        if total_uncapped > 0 and llm_raw / total_uncapped > LLM_CREDIT_CAP_RATIO:
            llm_capped = non_llm * (LLM_CREDIT_CAP_RATIO / (1 - LLM_CREDIT_CAP_RATIO))
        else:
            llm_capped = llm_raw

        return non_llm + llm_capped

    def tier(self) -> ContributionTier:
        """Current contribution tier based on score."""
        score = self.contribution_score()
        return _score_to_tier(score)

    def search_cost(self) -> float:
        """Search cost for this node based on its tier."""
        return _tier_search_cost(self.tier())

    def stats(self) -> LedgerStats:
        """Full ledger summary including grace/debt state."""
        row_llm = self._conn.execute(
            "SELECT COALESCE(SUM(credits), 0)"
            " FROM credit_entries"
            " WHERE action IN (?, ?)",
            (
                ActionType.LLM_SUMMARIZE_OWN.value,
                ActionType.LLM_SUMMARIZE_PEER.value,
            ),
        ).fetchone()
        row_non = self._conn.execute(
            "SELECT COALESCE(SUM(credits), 0)"
            " FROM credit_entries"
            " WHERE action NOT IN (?, ?)",
            (
                ActionType.LLM_SUMMARIZE_OWN.value,
                ActionType.LLM_SUMMARIZE_PEER.value,
            ),
        ).fetchone()

        earned = self.total_earned()
        spent = self.total_spent()
        score = self.contribution_score()
        t = _score_to_tier(score)
        allowance = self.search_allowance()

        return LedgerStats(
            total_earned=round(earned, 4),
            total_spent=round(spent, 4),
            balance=round(earned - spent, 4),
            contribution_score=round(score, 4),
            tier=t,
            search_cost=allowance.search_cost,
            llm_credits=round(float(row_llm[0]), 4),
            non_llm_credits=round(float(row_non[0]), 4),
            credit_state=allowance.state,
            grace_remaining_hours=allowance.grace_remaining_hours,
            debt_amount=allowance.debt_amount,
        )

    def recent_entries(self, *, limit: int = 50) -> list[CreditEntry]:
        """Return the most recent ledger entries."""
        rows = self._conn.execute(
            """SELECT entry_id, action, quantity, weight, multiplier, credits,
                      timestamp, note, entry_hash, signature
               FROM credit_entries ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            CreditEntry(
                entry_id=r[0],
                action=r[1],
                quantity=r[2],
                weight=r[3],
                multiplier=r[4],
                credits=r[5],
                timestamp=r[6],
                note=r[7],
                entry_hash=r[8],
                signature=r[9],
            )
            for r in rows
        ]

    def signed_entries(self) -> list[CreditEntry]:
        """Return all entries with non-empty signatures (chronological).

        Used by the credit verification module to build Merkle proofs.
        Entries without signatures (recorded before signing was enabled)
        are excluded.
        """
        rows = self._conn.execute(
            """SELECT entry_id, action, quantity, weight, multiplier, credits,
                      timestamp, note, entry_hash, signature
               FROM credit_entries
               WHERE entry_hash != '' AND signature != ''
               ORDER BY timestamp ASC""",
        ).fetchall()
        return [
            CreditEntry(
                entry_id=r[0],
                action=r[1],
                quantity=r[2],
                weight=r[3],
                multiplier=r[4],
                credits=r[5],
                timestamp=r[6],
                note=r[7],
                entry_hash=r[8],
                signature=r[9],
            )
            for r in rows
        ]

    # close() inherited from SQLiteStore


# --- Helpers ---------------------------------------------------------------


def _entry_canonical(
    action: str,
    quantity: float,
    weight: float,
    multiplier: float,
    credits: float,
    timestamp: float,
    note: str,
) -> bytes:
    """Build canonical byte representation of a credit entry for hashing/signing.

    The format is deterministic:
    ``action|quantity|weight|multiplier|credits|timestamp|note``.
    This is the data that gets hashed (SHA-256) and signed (Ed25519).
    """
    return (
        f"{action}|{quantity}|{weight}|{multiplier}|{credits}|{timestamp}|{note}"
    ).encode()


def _score_to_tier(score: float) -> ContributionTier:
    for threshold, tier, _ in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return ContributionTier.TIER_1


def _tier_search_cost(tier: ContributionTier) -> float:
    for _, t, cost in TIER_THRESHOLDS:
        if t == tier:
            return cost
    return 0.100


# Re-export from scheduling for backward compatibility
def is_off_peak(
    *,
    hour: int | None = None,
    start: int | None = None,
    end: int | None = None,
) -> bool:
    """Check whether the given hour falls in the off-peak window.

    Delegates to :func:`infomesh.credits.scheduling.is_off_peak_at`.
    """
    import datetime

    from infomesh.credits.scheduling import (
        DEFAULT_OFF_PEAK_END,
        DEFAULT_OFF_PEAK_START,
        is_off_peak_at,
    )

    if hour is None:
        hour = datetime.datetime.now().hour  # noqa: DTZ005
    return is_off_peak_at(
        hour=hour,
        start=start if start is not None else DEFAULT_OFF_PEAK_START,
        end=end if end is not None else DEFAULT_OFF_PEAK_END,
    )
