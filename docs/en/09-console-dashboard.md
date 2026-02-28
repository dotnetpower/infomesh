# InfoMesh Console Dashboard (Console App UI)

## Overview

A **console app UI (Textual-based TUI)** dashboard for monitoring InfoMesh node status.
Runs directly in the terminal without a separate web server, making it usable via SSH,
mobile terminal apps (Termux, Blink, etc.), and low-spec server environments.

## Technology Choice: Textual

| Item | Choice | Reason |
|------|--------|--------|
| Framework | **Textual** (≥1.0) | Rich-based, responsive CSS layout, mouse/keyboard support |
| Alternatives | curses/blessed/urwid | Textual dominates in CSS layout, widget system, and testability |

## Tab Layout (5 tabs)

### Tab 1: Overview
```
┌─ InfoMesh Dashboard ─────────────────────────── v0.1.0 ─┐
│                                                          │
│  ┌─ Node ──────────────┐  ┌─ Resources ──────────────┐  │
│  │ Peer ID: Qm...3kF   │  │ CPU:  ████░░░░░░  38%    │  │
│  │ State:  🟢 Running   │  │ RAM:  ██████░░░░  62%    │  │
│  │ Uptime: 3d 14h 22m  │  │ Disk: ████████░░  81%    │  │
│  │ Version: 0.1.0      │  │ Net↑: 2.1/5.0 Mbps       │  │
│  │ Data dir: ~/.info... │  │ Net↓: 4.3/10.0 Mbps      │  │
│  └──────────────────────┘  └──────────────────────────┘  │
│                                                          │
│  ┌─ Activity (last 1h) ──────────────────────────────┐  │
│  │ Crawled:    142 pages    ▁▃▅▇▅▃▁▃▅▇███▅▃▁        │  │
│  │ Indexed:    138 docs     ▁▃▅▇▅▃▁▃▅▇███▅▃▁        │  │
│  │ Searches:    23 queries  ▁▁▃▁▁▅▃▁▁▃▁▇▅▁▁        │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Recent Events ───────────────────────────────────┐  │
│  │ 14:23:01  Crawled example.com/page1       +1.0 cr │  │
│  │ 14:22:58  🔍 "python async" (12 results, 8ms)     │  │
│  │ 14:22:45  Peer Qm...xY2 connected                │  │
│  │ 14:22:30  Index snapshot exported (2.3 MB)        │  │
│  │ 14:22:15  Crawled docs.python.org/3/      +1.0 cr │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

> **Implementation Notes**: NodeInfoPanel shows Data dir instead of Peers.
> ResourcePanel displays CPU/RAM when `psutil` is installed, N/A otherwise.
> Resource bar colors auto-switch based on usage (≥90% red, ≥70% yellow).

### Tab 2: Crawl
```
┌─ Crawl ─────────────────────────────────────────────────┐
│                                                          │
│  Workers: 3/5 active    Rate: 42 pages/hr                │
│  Queue:   156 pending   Errors: 2 (1.4%)                 │
│                                                          │
│  ┌─ Top Domains ─────────────────────────────────────┐  │
│  │ docs.python.org      ████████████  234 pages      │  │
│  │ en.wikipedia.org     █████████     178 pages      │  │
│  │ developer.mozilla.org ███████      145 pages      │  │
│  │ stackoverflow.com    █████         98 pages       │  │
│  │ arxiv.org            ███           67 pages       │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Live Feed ───────────────────────────────────────┐  │
│  │ ✓  docs.python.org/3/tutorial/       1.2s  4.2KB │  │
│  │ ✓  en.wikipedia.org/wiki/P2P         0.8s  8.1KB │  │
│  │ ✗  example.com/blocked  robots.txt   —     —     │  │
│  │ ✓  arxiv.org/abs/2401.01234          2.1s  3.7KB │  │
│  │ ⟳  developer.mozilla.org/en-US/...  crawling...  │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Tab 3: Search
```
┌─ Search ────────────────────────────────────────────────┐
│                                                          │
│  🔍 Query: [python async tutorial________________]       │
│                                                          │
│  Found 12 results (8ms, local):                          │
│                                                          │
│  ┌─ Results ─────────────────────────────────────────┐  │
│  │ 1. Python Asyncio Tutorial                        │  │
│  │    https://docs.python.org/3/library/asyncio.html │  │
│  │    BM25=2.341  Fresh=0.95  Trust=0.88  Auth=0.72  │  │
│  │    Score: 1.8234                                   │  │
│  │    This module provides infrastructure for writing │  │
│  │    single-threaded concurrent code using...        │  │
│  │                                                    │  │
│  │ 2. Async IO in Python                             │  │
│  │    https://realpython.com/async-io-python/        │  │
│  │    BM25=2.102  Fresh=0.82  Trust=0.91  Auth=0.68  │  │
│  │    Score: 1.6891                                   │  │
│  │    Async IO is a concurrent programming design... │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Tab 4: Network
```
┌─ Network ───────────────────────────────────────────────┐
│                                                          │
│  ┌─ P2P Status ──────────┐  ┌─ DHT ─────────────────┐  │
│  │ State: 🔴 Offline      │  │ Keys stored:   1,234  │  │
│  │ Peers: 0 connected    │  │ Lookups/hr:      456   │  │
│  │ Bootstrap: 3 nodes    │  │ Publications:    89    │  │
│  │ Port:  4001 TCP       │  │                        │  │
│  │ Replication: 3x       │  │                        │  │
│  └────────────────────────┘  └────────────────────────┘  │
│                                                          │
│  ┌─ Connected Peers ─────────────────────────────────┐  │
│  │ Peer ID          Latency   Trust    State         │  │
│  │ Qm...aB2         23ms     0.92     active        │  │
│  │ Qm...cD4         45ms     0.85     active        │  │
│  │ Qm...eF6        102ms     0.78     idle          │  │
│  │ Qm...gH8         67ms     0.71     active        │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Bandwidth ───────────────────────────────────────┐  │
│  │ Upload:   ▁▃▅▇▅▃▁▃▅▇  2.1/5.0 Mbps              │  │
│  │ Download: ▃▅▇█▇▅▃▅▇█  4.3/10.0 Mbps             │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

> **Implementation Notes**: P2P Status shows Bootstrap node count and Replication factor.
> Peer table columns: Peer ID, Latency, Trust, State (4 columns).
> Bandwidth sparkline shows current/limit format.

### Tab 5: Credits
```
┌─ Credits ───────────────────────────────────────────────┐
│                                                          │
│  ┌─ Balance ─────────────────────────────────────────┐  │
│  │                                                    │  │
│  │  Balance:  1,234.50 credits    Tier: ⭐⭐⭐ (3)     │  │
│  │  Earned:   1,456.75            Search cost: 0.033  │  │
│  │  Spent:      222.25            Score: 1,456.75     │  │
│  │                                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Earnings Breakdown ──────────────────────────────┐  │
│  │ Crawling       ██████████████  702.0  (48.2%)     │  │
│  │ Uptime         ████████        396.0  (27.2%)     │  │
│  │ Query Process  ████            178.5  (12.2%)     │  │
│  │ LLM (own)      ███             135.0   (9.3%)     │  │
│  │ Doc Hosting    ██               45.25  (3.1%)     │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Recent Transactions ─────────────────────────────┐  │
│  │ 14:23:01  +1.000  crawl    example.com/page1      │  │
│  │ 14:22:58  -0.033  search   "python async"         │  │
│  │ 14:22:30  +0.500  uptime   1 hour                 │  │
│  │ 14:22:15  +1.000  crawl    docs.python.org/3/     │  │
│  │ 14:21:00  +1.500  llm_own  summarize page #456    │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

## Mobile Support (40-Column Mode)

Using Textual's responsive CSS, the layout automatically switches to a single-column
layout on narrow screens (under 40 columns).

```
┌─ InfoMesh ──── v0.1.0 ─┐
│                          │
│ Peer: Qm...3kF           │
│ State: 🟢 Running        │
│ Uptime: 3d 14h           │
│ Peers: 12                │
│                          │
│ CPU:  ████░░░░  38%      │
│ RAM:  ██████░░  62%      │
│ Disk: ████████  81%      │
│                          │
│ Crawled : 142 pages/hr   │
│ Indexed : 138 docs/hr    │
│ Searches:  23 queries/hr │
└──────────────────────────┘
```

## Module Structure

```
infomesh/dashboard/            # 16 modules, 2,340 lines
├── __init__.py
├── app.py              # DashboardApp (main Textual Application, 276 lines)
├── bgm.py              # BGMPlayer (background music via mpv/ffplay/aplay, 228 lines)
├── text_report.py      # Rich-based text report (non-interactive fallback, 333 lines)
├── screens/
│   ├── __init__.py
│   ├── overview.py     # OverviewPane — NodeInfoPanel, ResourcePanel, ActivityPanel, LiveLog (284 lines)
│   ├── crawl.py        # CrawlPane — CrawlStatsPanel, TopDomainsPanel, LiveLog (182 lines)
│   ├── search.py       # SearchPane — Input, SearchResultsPanel (151 lines)
│   ├── network.py      # NetworkPane — P2PStatusPanel, DHTPanel, PeerTable, BandwidthPanel (246 lines)
│   └── credits.py      # CreditsPane — BalancePanel, EarningsBreakdownPanel, TransactionTable (289 lines)
├── widgets/
│   ├── __init__.py
│   ├── sparkline.py    # SparklineChart (Unicode block character mini chart, 75 lines)
│   ├── bar_chart.py    # BarChart + BarItem (horizontal bar graph, 90 lines)
│   ├── resource_bar.py # ResourceBar (CPU/RAM/Disk/Net resource bar, 80 lines)
│   └── live_log.py     # LiveLog (real-time event log, RichLog-based, 96 lines)
└── dashboard.tcss      # Textual CSS stylesheet (responsive layout)
```

## CLI Commands

```bash
# Launch dashboard
infomesh dashboard

# Start on a specific tab
infomesh dashboard --tab credits

# Available tabs: overview, crawl, search, network, credits (default: overview)
infomesh dashboard -t network
```

## App Architecture

```
DashboardApp (App[None])
├── Header — Title "InfoMesh Dashboard" + version display
├── TabbedContent (initial=selected tab)
│   ├── TabPane "Overview" → OverviewPane
│   │   ├── Horizontal
│   │   │   ├── NodeInfoPanel (Peer ID, State, Uptime, Version, Data dir)
│   │   │   └── ResourcePanel (CPU, RAM, Disk, Net↑, Net↓)
│   │   ├── ActivityPanel (Crawled/Indexed/Searches + SparklineChart ×3)
│   │   └── LiveLog (event feed)
│   ├── TabPane "Crawl" → CrawlPane
│   │   ├── CrawlStatsPanel (Workers, Queue, Rate, Errors)
│   │   ├── TopDomainsPanel (SQL GROUP BY domain → BarChart)
│   │   └── LiveLog (crawl feed)
│   ├── TabPane "Search" → SearchPane
│   │   ├── Input (search query)
│   │   └── SearchResultsPanel (BM25 scores + snippets)
│   ├── TabPane "Network" → NetworkPane
│   │   ├── Horizontal
│   │   │   ├── P2PStatusPanel (State, Peers, Bootstrap, Port, Replication)
│   │   │   └── DHTPanel (Keys, Lookups/hr, Publications)
│   │   ├── PeerTable (DataTable: Peer ID, Latency, Trust, State)
│   │   └── BandwidthPanel (Upload/Download SparklineChart + current/limit)
│   └── TabPane "Credits" → CreditsPane
│       ├── BalancePanel (Balance, Earned, Spent, Tier, Search cost)
│       ├── EarningsBreakdownPanel (per-action BarChart)
│       └── TransactionTable (DataTable: Time, Amount, Type, Note)
└── Footer — keyboard shortcut display
```

## Keyboard Shortcuts

| Key | Action | Scope |
|-----|--------|-------|
| `1`-`5` | Switch tabs (Overview → Credits) | Global |
| `Tab` | Focus next widget | Global (Textual default) |
| `Shift+Tab` | Focus previous widget | Global (Textual default) |
| `/` | Focus search input | Search tab only |
| `q` | Quit | Global |
| `r` | Refresh (Overview, Crawl, Network, Credits) | Global |
| `?` | Show help notification (5s timeout) | Global |

## Implementation Specs

- **Dependencies**: `textual>=1.0` (in main deps, currently `textual==8.0.0`)
- **Data refresh intervals** (per-tab):
  - Overview: `set_interval(2.0)` — refresh resource/node status every 2s
  - Crawl: `set_interval(3.0)` — refresh domain stats every 3s
  - Network: `set_interval(2.0)` — refresh P2P status every 2s
  - Credits: `set_interval(5.0)` — refresh credit data every 5s
  - Search: no auto-refresh (runs only on user query input)
- **Data sources**:
  - `LocalStore` — document count, domain stats, search (SQLite FTS5)
  - `CreditLedger` — balance, earnings breakdown, transaction records
  - `psutil` (optional) — CPU/RAM usage (shows N/A if not installed)
  - `shutil.disk_usage()` — disk usage
  - PID file (`infomesh.pid`) — node running status check
  - `KeyPair.load()` — Peer ID loading
- **Error handling**: Shows "N/A" or guidance message when data source is unavailable; all `refresh_data()` wrapped with `contextlib.suppress(Exception)`
- **Tests**: 53 pytest unit/integration tests (`tests/test_dashboard.py`)
  - Widget tests: SparklineChart (6), BarChart (4), ResourceBar (4), LiveLog (1)
  - Helper tests: `_format_uptime()`, `_is_node_running()`, `_get_peer_id()` (7)
  - Screen tests: SearchResultsPanel (3), CreditsHelpers (2), NetworkPanels (1), CrawlStatsPanel (2)
  - App/CLI tests: DashboardApp (3), CLI command (1), BarItem (3), Sparkline edge cases (4)
  - BGM/text report tests: BGMPlayer, text_report, etc. (12)

## Widget Implementation Details

### SparklineChart
- Uses Unicode block characters: `" ▁▂▃▄▅▆▇█"` (9 levels)
- `reactive` properties for automatic re-render on data change
- `push_value(max_points=30)` — maintains up to 30 data points
- Automatic value normalization (min-max scaling)

### BarChart
- `BarItem` dataclass: label, value, color, suffix
- `█`/`░` characters for horizontal bar rendering (default width 20 chars)
- Displays both ratio to max value and percentage of total simultaneously

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md)*
