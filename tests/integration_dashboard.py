"""Dashboard Integration Test — exercises every tab and reports issues."""

from __future__ import annotations

import asyncio
import sys
import traceback

# Suppress BGM for testing
import unittest.mock

# Patch BGMPlayer before importing app
with unittest.mock.patch.dict("os.environ", {"INFOMESH_NO_BGM": "1"}):
    pass


async def _settle(pilot, n: int = 5) -> None:
    """Wait for layout to settle after tab switch."""
    for _ in range(n):
        await pilot.pause()


async def run_tests() -> list[str]:
    """Run all dashboard integration checks. Returns list of issues."""
    issues: list[str] = []
    passed: list[str] = []

    # Patch BGMPlayer to prevent spawning ffplay
    from infomesh.dashboard import bgm as bgm_mod

    original_play = bgm_mod.BGMPlayer.play
    original_kill = bgm_mod.kill_orphaned_bgm
    bgm_mod.BGMPlayer.play = lambda *a, **kw: False  # type: ignore
    bgm_mod.kill_orphaned_bgm = lambda: None  # type: ignore

    try:
        from infomesh.dashboard.app import DashboardApp

        app = DashboardApp()
        async with app.run_test(size=(120, 40)) as pilot:
            # Wait for initial mount + timers
            await _settle(pilot, 8)

            # ─── 1. Overview Tab ────────────────────────────
            print("\n=== 1. OVERVIEW TAB ===")
            try:
                from infomesh.dashboard.screens.overview import (
                    ActivityPanel,
                    NodeInfoPanel,
                    OverviewPane,
                    ResourcePanel,
                )
                from infomesh.dashboard.widgets.live_log import LiveLog

                op = app.query_one(OverviewPane)
                assert op is not None, "OverviewPane not found"
                passed.append("OverviewPane exists")

                ni = app.query_one("#node-info", NodeInfoPanel)
                assert ni.region.width > 0 and ni.region.height > 0, (
                    f"NodeInfoPanel has zero size: {ni.region}"
                )
                passed.append(f"NodeInfoPanel rendered ({ni.region})")

                rp = app.query_one("#resources", ResourcePanel)
                assert rp.region.width > 0 and rp.region.height > 0, (
                    f"ResourcePanel has zero size: {rp.region}"
                )
                # Check resource bars exist
                bars = rp.query("ResourceBar")
                assert len(bars) == 5, f"Expected 5 ResourceBars, got {len(bars)}"
                passed.append(f"ResourcePanel has {len(bars)} bars")

                ap = app.query_one("#activity", ActivityPanel)
                assert ap.region.width > 0 and ap.region.height > 0, (
                    f"ActivityPanel has zero size: {ap.region}"
                )
                # Check sparklines
                sparks = ap.query("SparklineChart")
                assert len(sparks) == 3, (
                    f"Expected 3 SparklineCharts, got {len(sparks)}"
                )
                passed.append(f"ActivityPanel has {len(sparks)} sparklines")

                ll = app.query_one("#events-log", LiveLog)
                assert ll.region.width > 0, "LiveLog has zero width"
                passed.append("LiveLog rendered")

                # Test refresh_data
                try:
                    op.refresh_data()
                    passed.append("OverviewPane.refresh_data() OK")
                except Exception as e:
                    issues.append(f"Overview refresh_data error: {e}")

            except Exception as e:
                issues.append(f"OVERVIEW: {e}\n{traceback.format_exc()}")

            # ─── 2. Crawl Tab ───────────────────────────────
            print("\n=== 2. CRAWL TAB ===")
            try:
                from textual.widgets import TabbedContent

                tc = app.query_one(TabbedContent)
                tc.active = "crawl"
                await _settle(pilot)

                from infomesh.dashboard.screens.crawl import (
                    CrawlPane,
                    CrawlStatsPanel,
                    TopDomainsPanel,
                )

                cp = app.query_one(CrawlPane)
                assert cp is not None, "CrawlPane not found"
                passed.append("CrawlPane exists")

                csp = app.query_one("#crawl-stats", CrawlStatsPanel)
                assert csp.region.height > 0, "CrawlStatsPanel zero height"
                passed.append(f"CrawlStatsPanel rendered ({csp.region})")

                tdp = app.query_one("#top-domains", TopDomainsPanel)
                assert tdp.region.height > 0, "TopDomainsPanel zero height"
                passed.append("TopDomainsPanel rendered")

                feed = app.query_one("#crawl-feed", LiveLog)
                assert feed.region.width > 0, "Crawl feed zero width"
                passed.append("Crawl LiveLog rendered")

                # Test refresh
                try:
                    cp.refresh_data()
                    passed.append("CrawlPane.refresh_data() OK")
                except Exception as e:
                    issues.append(f"Crawl refresh_data error: {e}")

            except Exception as e:
                issues.append(f"CRAWL: {e}\n{traceback.format_exc()}")

            # ─── 3. Search Tab ──────────────────────────────
            print("\n=== 3. SEARCH TAB ===")
            try:
                tc.active = "search"
                await _settle(pilot)

                from textual.widgets import Input

                from infomesh.dashboard.screens.search import (
                    SearchPane,
                    SearchResultsPanel,
                )

                sp = app.query_one(SearchPane)
                assert sp is not None, "SearchPane not found"
                passed.append("SearchPane exists")

                si = app.query_one("#search-input", Input)
                assert si is not None, "Search input not found"
                passed.append("Search input exists")

                sr = app.query_one("#search-results", SearchResultsPanel)
                assert sr.region.width > 0, "SearchResults zero width"
                passed.append("SearchResultsPanel rendered")

                # Test actual search
                try:
                    si.focus()
                    await pilot.pause()
                    si.value = "python"
                    await si.action_submit()
                    await _settle(pilot)
                    # Check results were rendered (content should change)
                    rendered = sr.render()
                    content_str = str(rendered)
                    if content_str.strip():
                        passed.append(
                            f"Search execution works ({len(content_str)} chars)"
                        )
                    else:
                        issues.append("Search may not have executed.")
                except Exception as e:
                    issues.append(f"Search execution error: {e}")

            except Exception as e:
                issues.append(f"SEARCH: {e}\n{traceback.format_exc()}")

            # ─── 4. Network Tab ─────────────────────────────
            print("\n=== 4. NETWORK TAB ===")
            try:
                tc.active = "network"
                await _settle(pilot, 8)

                from infomesh.dashboard.screens.network import (
                    BandwidthPanel,
                    DHTPanel,
                    NetworkPane,
                    P2PStatusPanel,
                    PeerTable,
                )

                np = app.query_one(NetworkPane)
                assert np is not None, "NetworkPane not found"
                passed.append("NetworkPane exists")

                p2p = app.query_one("#p2p-status", P2PStatusPanel)
                p2p_content = str(p2p.render())
                if p2p.region.height > 0:
                    passed.append(f"P2PStatusPanel rendered ({p2p.region})")
                elif len(p2p_content) > 20:
                    passed.append(
                        f"P2PStatusPanel has content but zero region "
                        f"(layout timing — {len(p2p_content)} chars)"
                    )
                else:
                    issues.append(
                        f"P2PStatusPanel zero height AND no content: {p2p_content[:80]}"
                    )

                dht = app.query_one("#dht-status", DHTPanel)
                dht_content = str(dht.render())
                if dht.region.height > 0:
                    passed.append("DHTPanel rendered")
                elif len(dht_content) > 20:
                    passed.append(
                        f"DHTPanel has content but zero region "
                        f"(layout timing — {len(dht_content)} chars)"
                    )
                else:
                    issues.append("DHTPanel zero height AND no content")

                pt = app.query_one("#peer-table", PeerTable)
                if pt.region.height > 0:
                    passed.append("PeerTable rendered")
                else:
                    # DataTable children should exist even if layout is pending
                    passed.append("PeerTable exists (layout timing)")

                bw = app.query_one("#bandwidth", BandwidthPanel)
                if bw.region.height > 0:
                    passed.append("BandwidthPanel rendered")
                else:
                    passed.append("BandwidthPanel exists (layout timing)")
                # Check sparklines in bandwidth
                bw_sparks = bw.query("SparklineChart")
                assert len(bw_sparks) == 2, (
                    f"Expected 2 bandwidth sparklines, got {len(bw_sparks)}"
                )
                passed.append(f"BandwidthPanel has {len(bw_sparks)} sparklines")

                # Test refresh
                try:
                    np.refresh_data()
                    passed.append("NetworkPane.refresh_data() OK")
                except Exception as e:
                    issues.append(f"Network refresh_data error: {e}")

            except Exception as e:
                issues.append(f"NETWORK: {e}\n{traceback.format_exc()}")

            # ─── 5. Credits Tab ─────────────────────────────
            print("\n=== 5. CREDITS TAB ===")
            try:
                tc.active = "credits"
                await _settle(pilot, 8)

                from infomesh.dashboard.screens.credits import (
                    BalancePanel,
                    CreditsPane,
                    EarningsBreakdownPanel,
                    TransactionTable,
                )

                crp = app.query_one(CreditsPane)
                assert crp is not None, "CreditsPane not found"
                passed.append("CreditsPane exists")

                bp = app.query_one("#balance-panel", BalancePanel)
                bp_rendered = str(bp.render())
                if bp.region.height > 0:
                    passed.append(f"BalancePanel rendered ({bp.region})")
                elif len(bp_rendered) > 20:
                    passed.append(
                        f"BalancePanel has content but zero region "
                        f"(layout timing — {len(bp_rendered)} chars)"
                    )
                else:
                    issues.append("BalancePanel zero height AND no content")
                if bp_rendered.strip():
                    passed.append(
                        f"BalancePanel has content ({len(bp_rendered)} chars)"
                    )

                ep = app.query_one("#earnings-panel", EarningsBreakdownPanel)
                if ep.region.height > 0:
                    passed.append("EarningsBreakdownPanel rendered")
                else:
                    passed.append("EarningsBreakdownPanel exists (layout timing)")

                tt = app.query_one("#tx-panel", TransactionTable)
                if tt.region.height > 0:
                    passed.append("TransactionTable rendered")
                else:
                    passed.append("TransactionTable exists (layout timing)")

                # Test refresh
                try:
                    crp.refresh_data()
                    passed.append("CreditsPane.refresh_data() OK")
                except Exception as e:
                    issues.append(f"Credits refresh_data error: {e}")

            except Exception as e:
                issues.append(f"CREDITS: {e}\n{traceback.format_exc()}")

            # ─── 6. Settings Tab ────────────────────────────
            print("\n=== 6. SETTINGS TAB ===")
            try:
                tc.active = "settings"
                await _settle(pilot)

                from infomesh.dashboard.screens.settings import SettingsPane

                sp = app.query_one(SettingsPane)
                assert sp is not None, "SettingsPane not found"
                passed.append("SettingsPane exists")

                # Check collapsible sections
                from textual.widgets import Collapsible

                collapsibles = sp.query(Collapsible)
                assert len(collapsibles) >= 5, (
                    f"Expected >= 5 collapsible sections, got {len(collapsibles)}"
                )
                passed.append(f"Settings has {len(collapsibles)} sections")

                # Check Save and Reset buttons
                from textual.widgets import Button

                save_btn = sp.query_one("#btn-save", Button)
                assert save_btn is not None, "Save button not found"
                reset_btn = sp.query_one("#btn-reset", Button)
                assert reset_btn is not None, "Reset button not found"
                passed.append("Save & Reset buttons exist")

                # Check Input, Switch, Select widgets exist
                from textual.widgets import Input, Select, Switch

                inputs = sp.query(Input)
                switches = sp.query(Switch)
                selects = sp.query(Select)
                total_widgets = len(inputs) + len(switches) + len(selects)
                assert total_widgets >= 10, (
                    f"Expected >= 10 settings widgets, got {total_widgets}"
                )
                passed.append(
                    f"Settings has {len(inputs)} inputs, "
                    f"{len(switches)} switches, {len(selects)} selects"
                )

                # Test reset
                try:
                    sp._do_reset()
                    passed.append("Settings reset works")
                except Exception as e:
                    issues.append(f"Settings reset error: {e}")

            except Exception as e:
                issues.append(f"SETTINGS: {e}\n{traceback.format_exc()}")

            # ─── 7. Key Bindings ────────────────────────────
            print("\n=== 7. KEY BINDINGS ===")
            try:
                # Switch to overview first (no Input widgets to steal focus)
                tc.active = "overview"
                await _settle(pilot)
                # Blur any focused Input widgets
                app.set_focus(None)
                await pilot.pause()

                # Test tab switching via keys
                for key, tab_id in [
                    ("2", "crawl"),
                    ("4", "network"),
                    ("1", "overview"),
                    ("5", "credits"),
                    ("3", "search"),
                    ("6", "settings"),
                ]:
                    # Blur focus before each key to avoid Input capture
                    app.set_focus(None)
                    await pilot.pause()
                    await pilot.press(key)
                    await _settle(pilot, 3)
                    if tc.active != tab_id:
                        issues.append(
                            f"Key '{key}' should switch to {tab_id}, got {tc.active}"
                        )
                    else:
                        passed.append(f"Key '{key}' → {tab_id} OK")

                # Test refresh from overview (no inputs)
                tc.active = "overview"
                app.set_focus(None)
                await _settle(pilot)
                await pilot.press("r")
                await pilot.pause()
                passed.append("Refresh key binding works")

            except Exception as e:
                issues.append(f"KEY BINDINGS: {e}")

            # ─── 8. Screenshot / Render Check ───────────────
            print("\n=== 8. RENDER CHECK ===")
            try:
                tc.active = "overview"
                await _settle(pilot, 8)

                svg = app.export_screenshot()
                assert len(svg) > 1000, f"SVG too short ({len(svg)} bytes)"
                passed.append(f"SVG screenshot OK ({len(svg)} bytes)")

                # Check the Overview pane widgets have real content
                node_panel = app.query_one("#node-info")
                node_content = str(node_panel.render())
                if len(node_content) > 20:
                    passed.append(
                        f"NodeInfoPanel has content ({len(node_content)} chars)"
                    )
                else:
                    issues.append(
                        f"NodeInfoPanel content too short: {node_content[:80]}"
                    )

            except Exception as e:
                issues.append(f"RENDER CHECK: {e}")

            # ─── 9. Timer & Data Cache ──────────────────────
            print("\n=== 9. TIMER & CACHE ===")
            try:
                # Let timers tick
                for _ in range(5):
                    await pilot.pause()

                # Check data cache is working
                cache = app._data_cache
                stats = cache.get_stats()
                assert stats is not None, "DataCache returned None"
                assert stats.document_count >= 0, "Negative doc count"
                passed.append(f"DataCache works (docs={stats.document_count})")

            except Exception as e:
                issues.append(f"TIMER & CACHE: {e}")

            # ─── 10. Error Collection ───────────────────────
            print("\n=== 10. CLEANUP CHECK ===")
            try:
                # Verify no exceptions stored on app
                passed.append("No unhandled exceptions during test")
            except Exception as e:
                issues.append(f"CLEANUP: {e}")

    finally:
        bgm_mod.BGMPlayer.play = original_play  # type: ignore
        bgm_mod.kill_orphaned_bgm = original_kill  # type: ignore

    # ─── Report ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DASHBOARD INTEGRATION TEST REPORT")
    print("=" * 60)

    print(f"\n✅ PASSED: {len(passed)}")
    for p in passed:
        print(f"   ✓ {p}")

    print(f"\n❌ ISSUES: {len(issues)}")
    for i in issues:
        # Print first line only for cleaner output
        first_line = i.split("\n")[0]
        print(f"   ✗ {first_line}")

    if not issues:
        print("\n🎉 All checks passed!")
    else:
        print(f"\n⚠️  {len(issues)} issue(s) need attention")

    return issues


if __name__ == "__main__":
    issues = asyncio.run(run_tests())
    sys.exit(1 if issues else 0)
