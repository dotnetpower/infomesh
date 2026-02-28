"""Tests for infomesh.credits.ledger — credit system."""

from __future__ import annotations

import pytest

from infomesh.credits.ledger import (
    ACTION_WEIGHTS,
    DEBT_COST_MULTIPLIER,
    GRACE_PERIOD_HOURS,
    ActionType,
    ContributionTier,
    CreditLedger,
    CreditState,
    is_off_peak,
)
from infomesh.credits.scheduling import OFF_PEAK_MULTIPLIER


@pytest.fixture
def ledger():
    """In-memory credit ledger."""
    lg = CreditLedger()
    yield lg
    lg.close()


# --- Basic earning ---------------------------------------------------------


class TestEarning:
    def test_record_crawl(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.CRAWL, 10.0)
        assert earned == pytest.approx(10.0)  # 1.0 * 10
        assert ledger.total_earned() == pytest.approx(10.0)

    def test_record_query(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.QUERY_PROCESS, 5.0)
        assert earned == pytest.approx(2.5)  # 0.5 * 5

    def test_record_uptime(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.NETWORK_UPTIME, 2.0)
        assert earned == pytest.approx(1.0)  # 0.5 * 2

    def test_off_peak_llm_bonus(self, ledger: CreditLedger):
        normal = ledger.record_action(ActionType.LLM_SUMMARIZE_OWN, 1.0, off_peak=False)
        off_peak = ledger.record_action(
            ActionType.LLM_SUMMARIZE_OWN, 1.0, off_peak=True
        )
        assert normal == pytest.approx(1.5)  # 1.5 * 1
        assert off_peak == pytest.approx(1.5 * OFF_PEAK_MULTIPLIER)

    def test_off_peak_no_effect_on_base_actions(self, ledger: CreditLedger):
        """Off-peak multiplier only applies to LLM actions."""
        normal = ledger.record_action(ActionType.CRAWL, 1.0, off_peak=False)
        off_peak = ledger.record_action(ActionType.CRAWL, 1.0, off_peak=True)
        assert normal == off_peak  # Both 1.0

    def test_action_weights_match(self):
        """Verify all ActionType members have weights."""
        for action in ActionType:
            assert action in ACTION_WEIGHTS


# --- Spending --------------------------------------------------------------


class TestSpending:
    def test_spend_succeeds(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 10.0)
        assert ledger.spend(5.0) is True
        assert ledger.balance() == pytest.approx(5.0)

    def test_spend_allows_debt(self, ledger: CreditLedger):
        """Spend always succeeds — debt is allowed."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        assert ledger.spend(10.0) is True
        assert ledger.balance() == pytest.approx(-9.0)

    def test_balance_zero_initial(self, ledger: CreditLedger):
        assert ledger.balance() == 0.0


# --- Tiers -----------------------------------------------------------------


class TestTiers:
    def test_new_node_is_tier1(self, ledger: CreditLedger):
        assert ledger.tier() == ContributionTier.TIER_1

    def test_tier2_at_100(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 100.0)
        assert ledger.tier() == ContributionTier.TIER_2

    def test_tier3_at_1000(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 1000.0)
        assert ledger.tier() == ContributionTier.TIER_3

    def test_search_cost_decreases_with_tier(self, ledger: CreditLedger):
        assert ledger.search_cost() == 0.100
        ledger.record_action(ActionType.CRAWL, 100.0)
        assert ledger.search_cost() == 0.050
        ledger.record_action(ActionType.CRAWL, 900.0)
        assert ledger.search_cost() == 0.033


# --- LLM credit cap -------------------------------------------------------


class TestLLMCap:
    def test_llm_credits_capped_at_60_percent(self, ledger: CreditLedger):
        """LLM-only credits should be capped so they don't exceed 60%."""
        # All LLM credits, no base credits
        ledger.record_action(ActionType.LLM_SUMMARIZE_OWN, 100.0)
        score = ledger.contribution_score()
        # With 0 non-LLM, capped LLM = 0 * (0.6/0.4) = 0
        assert score == pytest.approx(0.0)

    def test_mixed_credits_not_capped(self, ledger: CreditLedger):
        """When LLM < 60% of total, no cap applied."""
        ledger.record_action(ActionType.CRAWL, 100.0)  # 100 non-LLM
        ledger.record_action(ActionType.LLM_SUMMARIZE_OWN, 10.0)  # 15 LLM
        stats = ledger.stats()
        # 15/115 = 13% < 60%, so no cap
        assert stats.contribution_score == pytest.approx(115.0)

    def test_fairness_crawl_only_node(self, ledger: CreditLedger):
        """A crawl-only node at 10 pages/hr gets 100 searches/hr at worst tier."""
        ledger.record_action(ActionType.CRAWL, 10.0)  # 10 credits/hr
        cost = ledger.search_cost()
        # At Tier 1: 10 / 0.1 = 100 searches/hr
        assert 10.0 / cost >= 100


# --- Tiered Git PR credits -------------------------------------------------


class TestGitPRTiers:
    def test_git_docs_weight(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.GIT_DOCS, 1.0)
        assert earned == pytest.approx(1_000.0)

    def test_git_fix_weight(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.GIT_FIX, 1.0)
        assert earned == pytest.approx(10_000.0)

    def test_git_feature_weight(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.GIT_FEATURE, 1.0)
        assert earned == pytest.approx(50_000.0)

    def test_git_major_weight(self, ledger: CreditLedger):
        earned = ledger.record_action(ActionType.GIT_MAJOR, 1.0)
        assert earned == pytest.approx(100_000.0)

    def test_git_docs_reaches_tier3(self, ledger: CreditLedger):
        """A single docs PR (1,000 credits) should reach Tier 3."""
        ledger.record_action(ActionType.GIT_DOCS, 1.0)
        assert ledger.tier() == ContributionTier.TIER_3

    def test_git_off_peak_no_bonus(self, ledger: CreditLedger):
        """Git contributions should not receive off-peak multiplier."""
        normal = ledger.record_action(ActionType.GIT_FIX, 1.0, off_peak=False)
        ledger2 = CreditLedger()
        off_peak = ledger2.record_action(ActionType.GIT_FIX, 1.0, off_peak=True)
        ledger2.close()
        assert normal == off_peak  # Both 10_000.0


# --- Stats -----------------------------------------------------------------


class TestStats:
    def test_stats_summary(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 50.0)
        ledger.record_action(ActionType.LLM_SUMMARIZE_PEER, 5.0)
        ledger.spend(3.0)
        stats = ledger.stats()
        assert stats.total_earned == pytest.approx(60.0)  # 50 + 2*5
        assert stats.total_spent == pytest.approx(3.0)
        assert stats.balance == pytest.approx(57.0)
        assert stats.non_llm_credits == pytest.approx(50.0)
        assert stats.llm_credits == pytest.approx(10.0)

    def test_recent_entries(self, ledger: CreditLedger):
        for i in range(5):
            ledger.record_action(ActionType.CRAWL, 1.0, note=f"batch-{i}")
        entries = ledger.recent_entries(limit=3)
        assert len(entries) == 3
        assert entries[0].note == "batch-4"  # Most recent first


# --- is_off_peak -----------------------------------------------------------


class TestIsOffPeak:
    def test_midnight_is_off_peak(self):
        assert is_off_peak(hour=0, start=23, end=7) is True

    def test_noon_is_not_off_peak(self):
        assert is_off_peak(hour=12, start=23, end=7) is False

    def test_start_hour_inclusive(self):
        assert is_off_peak(hour=23, start=23, end=7) is True

    def test_end_hour_exclusive(self):
        assert is_off_peak(hour=7, start=23, end=7) is False

    def test_non_wrapping_range(self):
        assert is_off_peak(hour=10, start=9, end=17) is True
        assert is_off_peak(hour=8, start=9, end=17) is False


# --- Grace period & debt --------------------------------------------------


class TestGracePeriod:
    """Tests for the 72-hour grace period when credits are exhausted."""

    def test_normal_state_with_positive_balance(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 10.0)
        assert ledger.credit_state() == CreditState.NORMAL

    def test_normal_state_initial(self, ledger: CreditLedger):
        """New ledger with zero balance is NORMAL (no grace yet)."""
        assert ledger.credit_state() == CreditState.NORMAL

    def test_grace_starts_on_negative_balance(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)  # balance = -4.0
        assert ledger.balance() == pytest.approx(-4.0)
        assert ledger.credit_state() == CreditState.GRACE

    def test_grace_remaining_hours(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        remaining = ledger.grace_remaining_hours()
        assert remaining is not None
        assert remaining > 71.9  # Should be ~72h since we just started

    def test_grace_remaining_none_when_normal(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 10.0)
        assert ledger.grace_remaining_hours() is None

    def test_debt_amount(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 5.0)
        ledger.spend(8.0)
        assert ledger.debt_amount() == pytest.approx(3.0)

    def test_debt_amount_zero_when_positive(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 10.0)
        assert ledger.debt_amount() == 0.0

    def test_debt_state_after_grace_expires(self, ledger: CreditLedger):
        """Simulate expired grace period by directly setting grace_start."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        # Manually backdate grace_start to 73 hours ago
        import time as _time

        old_start = _time.time() - (GRACE_PERIOD_HOURS + 1) * 3600
        ledger._conn.execute(
            "UPDATE credit_grace SET grace_start = ? WHERE id = 1",
            (old_start,),
        )
        ledger._conn.commit()
        assert ledger.credit_state() == CreditState.DEBT

    def test_recovery_clears_grace(self, ledger: CreditLedger):
        """Earning enough credits to go positive resets grace state."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        assert ledger.credit_state() == CreditState.GRACE
        # Earn enough to recover
        ledger.record_action(ActionType.CRAWL, 10.0)
        assert ledger.balance() == pytest.approx(6.0)
        assert ledger.credit_state() == CreditState.NORMAL
        assert ledger.grace_remaining_hours() is None

    def test_multiple_spends_accumulate_debt(self, ledger: CreditLedger):
        """Multiple spends keep going deeper in debt."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(2.0)
        ledger.spend(3.0)
        ledger.spend(4.0)
        assert ledger.balance() == pytest.approx(-8.0)
        assert ledger.debt_amount() == pytest.approx(8.0)


class TestSearchAllowance:
    """Tests for the debt-aware search cost system."""

    def test_normal_allowance(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 10.0)
        allowance = ledger.search_allowance()
        assert allowance.state == CreditState.NORMAL
        assert allowance.search_cost == 0.100  # Tier 1 base cost
        assert allowance.debt_amount == 0.0
        assert allowance.grace_remaining_hours is None

    def test_grace_allowance_normal_cost(self, ledger: CreditLedger):
        """During grace period, search cost stays normal."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        allowance = ledger.search_allowance()
        assert allowance.state == CreditState.GRACE
        assert allowance.search_cost == 0.100  # Normal cost
        assert allowance.grace_remaining_hours is not None
        assert allowance.debt_amount == pytest.approx(4.0)

    def test_debt_allowance_doubled_cost(self, ledger: CreditLedger):
        """After grace expires, search cost doubles."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        # Backdate grace to simulate expiry
        import time as _time

        old_start = _time.time() - (GRACE_PERIOD_HOURS + 1) * 3600
        ledger._conn.execute(
            "UPDATE credit_grace SET grace_start = ? WHERE id = 1",
            (old_start,),
        )
        ledger._conn.commit()
        allowance = ledger.search_allowance()
        assert allowance.state == CreditState.DEBT
        assert allowance.search_cost == pytest.approx(0.100 * DEBT_COST_MULTIPLIER)
        assert allowance.grace_remaining_hours is None
        assert allowance.debt_amount == pytest.approx(4.0)

    def test_debt_recovery_resets_to_normal(self, ledger: CreditLedger):
        """Earning credits to go positive resets state and cost."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        # Backdate to debt
        import time as _time

        old_start = _time.time() - (GRACE_PERIOD_HOURS + 1) * 3600
        ledger._conn.execute(
            "UPDATE credit_grace SET grace_start = ? WHERE id = 1",
            (old_start,),
        )
        ledger._conn.commit()
        assert ledger.credit_state() == CreditState.DEBT
        # Recover
        ledger.record_action(ActionType.CRAWL, 100.0)
        allowance = ledger.search_allowance()
        assert allowance.state == CreditState.NORMAL
        assert allowance.search_cost == 0.050  # Now Tier 2!
        assert allowance.debt_amount == 0.0

    def test_search_never_blocked(self, ledger: CreditLedger):
        """Search spend always returns True, even with massive debt."""
        # Spend with zero balance — creates debt
        for _ in range(100):
            assert ledger.spend(1.0) is True
        assert ledger.balance() == pytest.approx(-100.0)
        assert ledger.debt_amount() == pytest.approx(100.0)

    def test_stats_includes_grace_debt_info(self, ledger: CreditLedger):
        """LedgerStats reflects grace/debt state."""
        ledger.record_action(ActionType.CRAWL, 1.0)
        ledger.spend(5.0)
        stats = ledger.stats()
        assert stats.credit_state == CreditState.GRACE
        assert stats.debt_amount == pytest.approx(4.0)
        assert stats.grace_remaining_hours is not None

    def test_stats_normal_state(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, 50.0)
        stats = ledger.stats()
        assert stats.credit_state == CreditState.NORMAL
        assert stats.debt_amount == 0.0
        assert stats.grace_remaining_hours is None
