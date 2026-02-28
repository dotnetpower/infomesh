"""Credits pane — balance, earnings breakdown, transaction history."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Static

from infomesh.config import Config
from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem


class BalancePanel(Static):
    """Credit balance summary with tier info."""

    DEFAULT_CSS = """
    BalancePanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 7;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        """Load balance from credit ledger."""
        try:
            from infomesh.credits.ledger import ContributionTier, CreditLedger

            db_path = self._config.node.data_dir / "credits.db"
            if not db_path.exists():
                self.update(
                    "[bold]Balance[/bold]\n\n"
                    "  No credit history yet.\n"
                    "  Start crawling to earn credits!"
                )
                return

            ledger = CreditLedger(db_path)
            stats = ledger.stats()
            ledger.close()

            # Tier stars
            tier_map = {
                ContributionTier.TIER_1: "⭐ Tier 1",
                ContributionTier.TIER_2: "⭐⭐ Tier 2",
                ContributionTier.TIER_3: "⭐⭐⭐ Tier 3",
            }
            tier_str = tier_map.get(stats.tier, "Unknown")

            text = (
                f"[bold]Balance[/bold]\n\n"
                f"  Balance:     [bold green]{stats.balance:,.2f}"
                f"[/bold green] credits    "
                f"Tier: {tier_str}\n"
                f"  Earned:      {stats.total_earned:,.2f}            "
                f"Search cost: {stats.search_cost:.3f}\n"
                f"  Spent:       {stats.total_spent:,.2f}            "
                f"Score: {stats.contribution_score:,.2f}"
            )
            self.update(text)
        except Exception:  # noqa: BLE001
            self.update(
                "[bold]Balance[/bold]\n\n  [dim]Unable to load credit data[/dim]"
            )

    def refresh_data(self) -> None:
        self._update()


class EarningsBreakdownPanel(Widget):
    """Bar chart showing earnings by category."""

    DEFAULT_CSS = """
    EarningsBreakdownPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 8;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]Earnings Breakdown[/bold]", classes="panel-title")
        yield BarChart(id="earnings-chart")

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        """Load earnings by category."""
        try:
            from infomesh.credits.ledger import CreditLedger

            db_path = self._config.node.data_dir / "credits.db"
            if not db_path.exists():
                return

            ledger = CreditLedger(db_path)

            # Query earnings grouped by action type
            rows = ledger._conn.execute(
                """SELECT action, COALESCE(SUM(credits), 0)
                   FROM credit_entries
                   GROUP BY action
                   ORDER BY SUM(credits) DESC"""
            ).fetchall()
            ledger.close()

            if not rows:
                return

            # Map action names to human-readable labels
            label_map = {
                "crawl": "Crawling",
                "query_process": "Query Processing",
                "doc_hosting": "Document Hosting",
                "network_uptime": "Network Uptime",
                "llm_own": "LLM (own)",
                "llm_peer": "LLM (for peers)",
                "git_contrib": "Git Contribution",
                "git_docs": "Git PR (docs)",
                "git_fix": "Git PR (fix)",
                "git_feature": "Git PR (feature)",
                "git_major": "Git PR (major)",
            }
            colors = ["cyan", "green", "yellow", "blue", "magenta", "red", "white"]

            items = [
                BarItem(
                    label=label_map.get(row[0], row[0]),
                    value=float(row[1]),
                    color=colors[i % len(colors)],
                )
                for i, row in enumerate(rows)
                if float(row[1]) > 0
            ]

            self.query_one("#earnings-chart", BarChart).set_items(items)
        except Exception:  # noqa: BLE001
            pass

    def refresh_data(self) -> None:
        self._update()


class TransactionTable(Widget):
    """Table of recent credit transactions."""

    DEFAULT_CSS = """
    TransactionTable {
        border: round $accent;
        height: auto;
        min-height: 8;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]Recent Transactions[/bold]", classes="panel-title")
        table: DataTable[str] = DataTable(id="tx-table")
        table.add_columns("Time", "Amount", "Type", "Note")
        yield table

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        """Load recent transactions from ledger."""
        try:
            from infomesh.credits.ledger import CreditLedger

            db_path = self._config.node.data_dir / "credits.db"
            if not db_path.exists():
                return

            ledger = CreditLedger(db_path)
            entries = ledger.recent_entries(limit=30)
            ledger.close()

            table = self.query_one("#tx-table", DataTable)
            table.clear()

            for entry in entries:
                ts = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
                sign = "+" if entry.credits > 0 else ""
                amount = f"{sign}{entry.credits:.3f}"
                note = entry.note[:40] if entry.note else ""

                # Map action to readable label
                label_map = {
                    "crawl": "crawl",
                    "query_process": "query",
                    "doc_hosting": "hosting",
                    "network_uptime": "uptime",
                    "llm_own": "llm",
                    "llm_peer": "llm(peer)",
                    "git_contrib": "git",
                    "git_docs": "git(docs)",
                    "git_fix": "git(fix)",
                    "git_feature": "git(feat)",
                    "git_major": "git(major)",
                }
                action = label_map.get(entry.action, entry.action)

                table.add_row(ts, amount, action, note)

        except Exception:  # noqa: BLE001
            pass

    def refresh_data(self) -> None:
        self._update()


class CreditsPane(Widget):
    """Main credits monitoring pane."""

    class CreditEarned(Message):
        """Posted when the credit balance increases."""

        def __init__(self, delta: float) -> None:
            self.delta = delta
            super().__init__()

    DEFAULT_CSS = """
    CreditsPane {
        height: 1fr;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config
        self._refresh_timer: Timer | None = None
        self._prev_balance: float | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield BalancePanel(self._config, id="balance-panel")
            yield EarningsBreakdownPanel(self._config, id="earnings-panel")
            yield TransactionTable(self._config, id="tx-panel")

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(5.0, self._tick)

    def _tick(self) -> None:
        """Periodic refresh."""
        try:
            # Check for balance increase before refreshing panels
            self._check_credit_change()
            self.query_one("#balance-panel", BalancePanel).refresh_data()
            self.query_one("#earnings-panel", EarningsBreakdownPanel).refresh_data()
            self.query_one("#tx-panel", TransactionTable).refresh_data()
        except Exception:  # noqa: BLE001
            pass

    def _check_credit_change(self) -> None:
        """Detect credit balance increase and post CreditEarned."""
        try:
            from infomesh.credits.ledger import CreditLedger

            db_path = self._config.node.data_dir / "credits.db"
            if not db_path.exists():
                return

            ledger = CreditLedger(db_path)
            stats = ledger.stats()
            ledger.close()

            current = stats.balance
            if self._prev_balance is not None and current > self._prev_balance:
                delta = current - self._prev_balance
                self.post_message(self.CreditEarned(delta))
            self._prev_balance = current
        except Exception:  # noqa: BLE001
            pass

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._tick()
