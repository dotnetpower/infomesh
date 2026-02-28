#!/usr/bin/env python3
"""Example: Check credit balance and earning breakdown.

Demonstrates how to read the local credit ledger
programmatically.

Usage:
    uv run python examples/credit_status.py
"""

from __future__ import annotations

from infomesh.config import load_config
from infomesh.credits.ledger import CreditLedger


def main() -> None:
    config = load_config()
    ledger = CreditLedger(config.node.data_dir / "credits.db")

    # Balance and search allowance
    balance = ledger.balance()
    allowance = ledger.search_allowance()

    print("InfoMesh Credit Status")
    print("=" * 40)
    print(f"Balance:      {balance:>10.2f} credits")
    print(f"Tier:         {allowance.tier}")
    print(f"Search cost:  {allowance.search_cost:.3f} / query")
    print(f"State:        {allowance.state.value}")

    if allowance.state.value == "grace":
        print(f"Grace left:   {allowance.grace_remaining_hours:.1f} hours")
    elif allowance.state.value == "debt":
        print(f"Debt amount:  {allowance.debt_amount:.2f}")

    # Earnings breakdown
    print("\nEarnings Breakdown")
    print("-" * 40)
    breakdown = ledger.earnings_breakdown()
    for action, amount in sorted(breakdown.items(), key=lambda x: -x[1]):
        print(f"  {action:<20s} {amount:>10.2f}")

    # Recent transactions
    print("\nRecent Transactions (last 10)")
    print("-" * 40)
    for tx in ledger.recent_transactions(limit=10):
        sign = "+" if tx.amount > 0 else ""
        print(f"  {sign}{tx.amount:>8.3f}  {tx.action_type:<12s}  {tx.reason}")

    ledger.close()


if __name__ == "__main__":
    main()
