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

# Shared action-to-label mapping (used by EarningsBreakdownPanel & TransactionTable)
_ACTION_LABELS: dict[str, str] = {
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

# Short labels for transaction table
_ACTION_SHORT_LABELS: dict[str, str] = {
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
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self._config = config

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        """Load balance from credit ledger."""
        try:
            from infomesh.credits.ledger import CreditLedger

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

            from infomesh.dashboard.utils import tier_label

            tier_str = tier_label(stats.tier)

            def _fmt_credit(v: float) -> str:
                """Format credit: integer if whole, else up to 2 decimals."""
                if v == int(v):
                    return f"{int(v):,}"
                return f"{v:,.2f}".rstrip("0")

            bal_str = _fmt_credit(stats.balance)
            earn_str = _fmt_credit(stats.total_earned)
            spent_str = _fmt_credit(stats.total_spent)
            score_str = f"{int(stats.contribution_score):,}"

            # V = fixed value column width (right-aligned).
            # Keeps right-side labels (Tier/Search cost/Score) aligned
            # regardless of how large the left-side numbers grow.
            V = 15  # noqa: N806
            text = (
                "[bold]Balance[/bold]\n\n"
                f"  Balance:   [bold green]{bal_str:>{V}}[/bold green]"
                f" credits    Tier:        {tier_str}\n"
                f"  Earned:    {earn_str:>{V}}"
                f"            Search cost: {stats.search_cost:.3f}\n"
                f"  Spent:     {spent_str:>{V}}"
                f"            Score:       {score_str}"
            )

            # Network-wide credit info (cross-node sync)
            net_text = self._network_credit_text()
            if net_text:
                text += f"\n\n{net_text}"

            self.update(text)
        except Exception:  # noqa: BLE001
            self.update(
                "[bold]Balance[/bold]\n\n  [dim]Unable to load credit data[/dim]"
            )

    def refresh_data(self) -> None:
        self._update()

    def _network_credit_text(self) -> str:
        """Load network-wide credit info from sync store."""
        try:
            from infomesh.credits.sync import (
                CreditSyncManager,
                CreditSyncStore,
            )

            sync_path = self._config.node.data_dir / "credit_sync.db"
            if not sync_path.exists():
                return ""

            from infomesh.credits.ledger import CreditLedger

            db_path = self._config.node.data_dir / "credits.db"
            if not db_path.exists():
                return ""

            ledger = CreditLedger(db_path)
            sync_store = CreditSyncStore(sync_path)
            try:
                mgr = CreditSyncManager(
                    ledger=ledger,
                    store=sync_store,
                    owner_email="",
                    key_pair=None,
                    local_peer_id="",
                )
                agg = mgr.aggregated_stats()
            finally:
                sync_store.close()
                ledger.close()

            if agg.node_count <= 1:
                return ""

            net_bal = (
                f"{int(agg.balance):,}"
                if agg.balance == int(agg.balance)
                else f"{agg.balance:,.2f}"
            )
            return (
                "[bold]Network (all nodes)[/bold]\n"
                f"  Nodes:     {agg.node_count}\n"
                f"  Balance:   [bold cyan]{net_bal}[/bold cyan]"
                " credits\n"
                f"  Earned:    {agg.total_earned:,.2f}\n"
                f"  Score:     {int(agg.contribution_score):,}"
            )
        except Exception:  # noqa: BLE001
            return ""


class EarningsBreakdownPanel(Widget):
    """Bar chart showing earnings by category."""

    DEFAULT_CSS = """
    EarningsBreakdownPanel {
        border: round $accent;
        padding: 0 1;
        height: auto;
        min-height: 5;
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
                self.query_one("#earnings-chart", BarChart).set_items([])
                return

            ledger = CreditLedger(db_path)
            try:
                rows = ledger.earnings_by_action()
            finally:
                ledger.close()

            if not rows:
                return

            # Map action names to human-readable labels
            colors = ["cyan", "green", "yellow", "blue", "magenta", "red", "white"]

            items = [
                BarItem(
                    label=_ACTION_LABELS.get(row[0], row[0]),
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
                ts = time.strftime("%m-%d %H:%M", time.localtime(entry.timestamp))
                sign = "+" if entry.credits > 0 else ""
                amount = f"{sign}{entry.credits:.3f}"
                note = entry.note or ""
                # Truncate with "..." only if it exceeds available width
                # Other columns: Time(8) + Amount(~8) + Type(~10) + padding(~8) ≈ 34
                try:
                    avail = self.app.size.width - 34
                    avail = max(avail, 20)  # minimum 20 chars
                except Exception:  # noqa: BLE001
                    avail = 60
                if len(note) > avail:
                    note = note[: avail - 3] + "..."

                # Map action to readable label
                action = _ACTION_SHORT_LABELS.get(entry.action, entry.action)

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
        self._prev_earned: float | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield BalancePanel(self._config, id="balance-panel")
            yield EarningsBreakdownPanel(self._config, id="earnings-panel")
            yield TransactionTable(self._config, id="tx-panel")

    _REFRESH_INTERVAL = 5.0

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(self._REFRESH_INTERVAL, self._tick)

    def _tick(self) -> None:
        """Periodic refresh (skip if pane is not visible)."""
        if not self.display:
            return
        try:
            # Check for balance increase before refreshing panels
            self._check_credit_change()
            self.query_one("#balance-panel", BalancePanel).refresh_data()
            self.query_one("#earnings-panel", EarningsBreakdownPanel).refresh_data()
            self.query_one("#tx-panel", TransactionTable).refresh_data()
        except Exception:  # noqa: BLE001
            pass

    def _check_credit_change(self) -> None:
        """Detect new credit earnings and post CreditEarned."""
        try:
            from infomesh.credits.ledger import CreditLedger

            db_path = self._config.node.data_dir / "credits.db"
            if not db_path.exists():
                return

            ledger = CreditLedger(db_path)
            stats = ledger.stats()
            ledger.close()

            current_earned = stats.total_earned
            if self._prev_earned is not None and current_earned > self._prev_earned:
                delta = current_earned - self._prev_earned
                self.post_message(self.CreditEarned(delta))
            self._prev_earned = current_earned
        except Exception:  # noqa: BLE001
            pass

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._tick()
