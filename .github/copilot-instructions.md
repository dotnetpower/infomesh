# InfoMesh — Copilot Instructions

## Project Overview

InfoMesh is a **fully decentralized P2P search engine** designed exclusively for LLMs.
It crawls, indexes, and searches the web via a peer-to-peer network and exposes results
through MCP (Model Context Protocol) — no human-facing UI required.

**Mission**: InfoMesh does not compete with commercial search providers. These companies serve _human_ search
at massive scale with ads-based monetization. InfoMesh provides _minimal, sufficient_ search
capabilities for LLMs — for free, via MCP — democratizing real-time web access for AI assistants
without per-query billing. It is a community-driven public utility, complementary to existing
search providers.

## Core Principles

| Principle | Description |
|-----------|-------------|
| **Fully Decentralized** | No central server. Every node is both a hub and a participant. |
| **LLM-First** | No browser UI. Pure text API optimized for LLM consumption. |
| **Contribute = Reward** | More crawling contribution → more search quota (cooperative tit-for-tat model). |
| **Offline-Capable** | Local index is searchable without internet. |
| **Privacy** | Search queries are never recorded centrally. |

## Tech Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | **Python 3.12+** | Use modern Python features (type hints, `match`, `type` statement, `StrEnum`, etc.) |
| P2P Network | **libp2p (py-libp2p)** | DHT, Noise encryption built-in. **Uses trio, not asyncio** — see note below |
| DHT | **Kademlia** | Distributed hash table for index & crawl coordination |
| Crawling | **httpx + asyncio** | Async-first HTTP client |
| HTML Parsing | **trafilatura** | Best accuracy for main-content extraction |
| Keyword Index | **SQLite FTS5** | Zero-install, embedded full-text search |
| Vector Index | **ChromaDB** | Semantic search with embeddings |
| MCP Server | **mcp-python-sdk** | VS Code / Claude / Cursor / Windsurf integration |
| Admin API | **FastAPI** | Local status & config endpoints |
| Serialization | **msgpack** | Faster and smaller than JSON |
| Compression | **zstd** | Level-tunable compression; dictionary mode for similar documents |
| Local LLM | **ollama / llama.cpp** | Optional local summarization (Qwen 2.5, Llama 3.x, etc.) |
| Logging | **structlog** | Structured logging for all library code |
| Package Manager | **uv** | Fast Python package/project manager (replaces pip/venv) |
| Build Backend | **hatchling** | PEP 517 build backend for PyPI distribution |

## Project Structure

```
infomesh/
├── pyproject.toml
├── infomesh/
│   ├── __init__.py          # Package root (__version__)
│   ├── __main__.py          # CLI entry point (Click app)
│   ├── config.py            # Configuration management (TOML + env vars)
│   ├── services.py          # Central AppContext + index_document orchestration
│   ├── db.py                # SQLite store base class (WAL, lifecycle)
│   ├── hashing.py           # SHA-256 hashing utilities (content_hash, short_hash)
│   ├── security.py          # SSRF protection (URL validation, IP filtering)
│   ├── types.py             # Shared Protocol types (PEP 544)
│   ├── cli/                 # Click CLI commands
│   │   ├── __init__.py      #   CLI app registration
│   │   ├── config.py        #   `infomesh config show/set` commands
│   │   ├── crawl.py         #   `infomesh crawl`, `mcp`, `dashboard` commands
│   │   ├── index.py         #   `infomesh index stats/export/import` commands
│   │   ├── keys.py          #   `infomesh keys show/rotate` commands
│   │   ├── search.py        #   `infomesh search` command
│   │   └── serve.py         #   `infomesh start/stop/status/serve` commands
│   ├── p2p/                 # P2P network layer
│   │   ├── node.py          #   Peer main process
│   │   ├── dht.py           #   Kademlia DHT
│   │   ├── keys.py          #   Ed25519 key pair management + rotation
│   │   ├── routing.py       #   Query routing (latency-aware)
│   │   ├── replication.py   #   Document/index replication
│   │   ├── protocol.py      #   Message protocol definitions (msgpack)
│   │   ├── peer_profile.py  #   Peer latency tracking & bandwidth classification
│   │   ├── sybil.py         #   Sybil defense (PoW node ID + subnet limiting)
│   │   ├── load_guard.py    #   NodeLoadGuard (QPM + concurrency limiting)
│   │   ├── mdns.py          #   mDNS local peer discovery (multicast UDP)
│   │   ├── throttle.py      #   BandwidthThrottle (token-bucket upload/download)
│   │   └── index_submit.py  #   Enterprise split: DMZ crawler → private indexer
│   ├── crawler/             # Web crawler
│   │   ├── worker.py        #   Async crawl workers
│   │   ├── scheduler.py     #   URL assignment (DHT-based)
│   │   ├── parser.py        #   HTML → text extraction
│   │   ├── robots.py        #   robots.txt compliance + sitemap + crawl-delay
│   │   ├── dedup.py         #   Deduplication pipeline (URL, SHA-256, SimHash)
│   │   ├── simhash.py       #   SimHash near-duplicate detection
│   │   ├── seeds.py         #   Seed URL management & category selection
│   │   ├── recrawl.py       #   Adaptive auto-recrawl scheduler
│   │   └── url_assigner.py  #   DHT-based URL auto-assignment
│   ├── index/               # Search index
│   │   ├── local_store.py   #   SQLite FTS5 local index
│   │   ├── vector_store.py  #   ChromaDB vector index
│   │   ├── distributed.py   #   DHT inverted-index publish/query
│   │   ├── ranking.py       #   BM25 + freshness + trust + authority scoring
│   │   ├── link_graph.py    #   Link graph + domain authority (PageRank-style)
│   │   ├── snapshot.py      #   Index snapshot export/import (zstd-compressed)
│   │   └── commoncrawl.py   #   Common Crawl data importer
│   ├── search/              # Search engine
│   │   ├── query.py         #   Query parsing + distributed orchestration
│   │   ├── merge.py         #   Multi-node result merging
│   │   ├── cache.py         #   Query result LRU cache (TTL + auto-expiry)
│   │   ├── reranker.py      #   LLM re-ranking (prompt-based post-ranking)
│   │   ├── cross_validate.py #  Query result cross-validation
│   │   └── formatter.py     #   Search result text formatting (CLI/MCP/dashboard)
│   ├── mcp/                 # MCP server (Model Context Protocol)
│   │   └── server.py        #   search(), search_local(), fetch_page(), crawl_url(), network_stats()
│   ├── api/                 # Local admin API
│   │   └── local_api.py     #   FastAPI (health, status, config, index stats, credits)
│   ├── credits/             # Incentive system
│   │   ├── ledger.py        #   Local credit ledger (ActionType enum, weights)
│   │   ├── farming.py       #   Credit farming detection (probation, anomaly detection)
│   │   ├── scheduling.py    #   Energy-aware scheduling (off-peak LLM bonus + TZ verification)
│   │   ├── timezone_verify.py #   Off-peak timezone verification (IP cross-check)
│   │   └── verification.py  #   P2P credit verification (signed entries + Merkle proofs)
│   ├── trust/               # Trust & integrity
│   │   ├── attestation.py   #   Content attestation chain (signing, verification, Merkle root)
│   │   ├── audit.py         #   Random audit system + Merkle proof audits
│   │   ├── merkle.py        #   Merkle Tree (index-wide integrity, membership proofs)
│   │   ├── reputation.py    #   LLM reputation-based trust (EMA quality tracking, grade system)
│   │   ├── scoring.py       #   Unified trust score computation
│   │   ├── detector.py      #   Farming + trust unified detector
│   │   ├── dmca.py          #   DMCA takedown propagation
│   │   └── gdpr.py          #   GDPR distributed deletion records
│   ├── summarizer/          # Local LLM summarization
│   │   ├── engine.py        #   LLM backend abstraction (ollama, llama.cpp)
│   │   ├── peer_handler.py  #   Inter-peer summarization request handling
│   │   └── verify.py        #   Summary verification (key-fact anchoring, NLI)
│   ├── dashboard/           # Console app UI (Textual TUI)
│   │   ├── app.py           #   DashboardApp (main Textual Application)
│   │   ├── bgm.py           #   BGM player (background music via mpv/ffplay/aplay)
│   │   ├── text_report.py   #   Rich-based text report (non-interactive fallback)
│   │   ├── data_cache.py    #   Dashboard data caching layer
│   │   ├── dashboard.tcss   #   Textual CSS stylesheet
│   │   ├── screens/         #   Tab panes (overview, crawl, search, network, credits, settings)
│   │   └── widgets/         #   Reusable widgets (sparkline, bar_chart, resource_bar, live_log)
│   ├── resources/           # Resource governance
│   │   ├── profiles.py      #   Predefined resource profiles (minimal/balanced/contributor/dedicated)
│   │   ├── governor.py      #   Dynamic resource throttling (CPU/memory monitoring, degrade levels)
│   │   └── preflight.py     #   Pre-startup disk space + network connectivity checks
│   └── compression/         # Data compression
│       └── zstd.py          #   zstd compression with dictionary support
├── examples/                # Python usage examples
│   ├── README.md            #   Examples index
│   ├── basic_search.py      #   Simple search demo
│   ├── crawl_and_search.py  #   Crawl + search workflow
│   ├── credit_status.py     #   Credit ledger inspection
│   ├── fetch_page.py        #   Single URL fetch
│   ├── hybrid_search.py     #   FTS5 + vector hybrid search
│   └── mcp_client.py        #   MCP client integration demo
├── bootstrap/
│   └── nodes.json           # Bootstrap node list
├── seeds/                   # Bundled seed URL lists
│   ├── tech-docs.txt        #   Technology documentation URLs
│   ├── academic.txt         #   Academic paper source URLs
│   ├── encyclopedia.txt     #   Encyclopedia URLs
│   ├── quickstart.txt       #   Quickstart seed URLs (curated starter set)
│   └── search-strategy.txt  #   Search strategy seeds
├── tests/                   # 52 test files (pytest + pytest-asyncio)
└── docs/                    # Documentation (EN + KO)
```

## Coding Conventions

### General

- **Language**: All source code, comments, docstrings, commit messages, and PR descriptions in **English**.
- **No specific company/product names in comparisons**: README, docs, and marketing text must **never mention specific competing companies or products by name** (e.g., no "Tavily", "Brave Search", "OpenAI charges…"). Use generic categories instead ("API-based search providers", "commercial search APIs", "other MCP servers"). This avoids legal risk, keeps the project neutral, and prevents the text from becoming outdated when products change. Mentioning _MCP-compatible clients_ (VS Code, Claude Desktop, Cursor, etc.) as integration targets is allowed — that is factual compatibility documentation, not competitive comparison.
- **Python version**: 3.12+ — use modern syntax (`match/case`, `type` statement, `StrEnum`, `TypeVar` defaults).
- **Async-first**: All I/O-bound code must use `async/await` with `asyncio`. Never use blocking I/O in the event loop.
- **⚠️ trio exception**: `py-libp2p` uses **trio** (not asyncio). All libp2p/P2P code must run under `trio.run()`. Use a `_run_trio()` wrapper for tests to avoid `asyncio_mode=auto` conflict. Phase 2 will need a trio↔asyncio bridge (e.g., `anyio` or `trio-asyncio`).
- **Type hints**: Required on all public functions and class attributes. Use `from __future__ import annotations` for forward references.

### Single Responsibility Principle (SRP)

Every module, class, and function must have **one clear responsibility**.

- **Modules**: One module = one concern. Don't mix unrelated logic in a single file. If a module exceeds ~300 lines, consider splitting.
- **Classes**: Each class encapsulates one actor or concept. A class should have only one reason to change.
  - ✅ `RobotsChecker` — only checks robots.txt compliance.
  - ✅ `Compressor` — only handles zstd compression/decompression.
  - ❌ A class that both crawls pages AND indexes them.
- **Functions**: Each function does one thing. Avoid functions that handle orchestration AND business logic simultaneously.
  - Prefer composing small functions over writing long procedural blocks.
  - Extract repeated patterns (e.g., "crawl → index → optionally vector-index") into named helper functions.
- **CLI commands**: Thin wrappers that delegate to library code. No business logic in Click handlers — they should only parse arguments, call library functions, and format output.
- **MCP tool handlers**: Same as CLI — dispatch to service-layer functions, don't inline business logic.
- **Dashboard panels**: Read data from caches or public APIs. Never access private attributes (`_conn`, `_db`) of library classes.

When reviewing code, ask: _"If I change X, what else breaks?"_ If the answer includes unrelated concerns, the code violates SRP and should be refactored.

### Style & Formatting

- Formatter: **ruff format** (default settings, line length 88).
- Linter: **ruff check** with `select = ["E", "F", "I", "UP", "B", "SIM"]`.
- Import order: stdlib → third-party → local (enforced by ruff/isort).
- Prefer `pathlib.Path` over `os.path`.

### CI Failure Prevention (Lessons Learned)

The following errors have caused CI failures. **Always check for these before committing:**

| Error Code | Description | Prevention |
|------------|-------------|------------|
| **E501** | Line too long (>88 chars) | Run `ruff format .` before commit. For Click `help=` strings, use multi-line concatenation: `help=("line1 " "line2")`. For long f-strings, break into variables first. |
| **I001** | Import block unsorted | Always group imports: stdlib → third-party → local, alphabetically within each group. Run `ruff check --fix` to auto-sort. Never add `import time` below `from dataclasses import dataclass`. |
| **F541** | f-string without placeholders | Don't write `f"plain string"` — remove the `f` prefix if there are no `{…}` expressions. |
| **F841** | Local variable assigned but never used | Remove unused variables or prefix with `_` if intentionally unused (e.g., `_unused = func()`). |
| **F401** | Module imported but unused | Remove unused imports. If imported for side effects or re-export, add `# noqa: F401`. |
| **F821** | Undefined name used | Ensure all referenced names are imported or defined. Check spelling of variable names. |

**Common pitfalls:**
- Adding a new `import` at the end of an import block instead of in alphabetical order → **I001**.
- Writing Click `help="..."` strings that exceed 88 chars → **E501**. Split into `help=("part1 " "part2")`.
- Copy-pasting code with f-strings but removing the interpolated variables → **F541**.
- Forgetting to remove debug `import subprocess` or `import pdb` → **F401**.

### No Private API Access in Consumers

Library classes (`LocalStore`, `CreditLedger`, etc.) expose public methods for data access. **Never** access private attributes like `store._conn` or `ledger._conn` in CLI, MCP, or dashboard code. If a needed query doesn't have a public API, add one to the library class first.

### Shared Utilities — No Duplication

Utility functions must exist in exactly one place:
- **Dashboard utilities** (`_format_uptime`, `_get_peer_id`, `_is_node_running`, `_read_p2p_status`): use `infomesh/dashboard/utils.py`.
- **Domain-extraction SQL**: use `LocalStore.get_top_domains()` — don't inline raw SQL in dashboard code.
- **Node status assembly** (store stats + P2P status + credit stats): use `services.py` orchestration — don't duplicate across CLI, MCP, and dashboard.

### Pre-commit Checks (Required)

Before every commit, **both lint and format checks must pass**:

```bash
# Lint check — must show 0 errors
uv run ruff check infomesh/ tests/

# Format check — must show "X files already formatted"
uv run ruff format --check .

# Build check — must produce .whl and .tar.gz without errors
uv build

# Test check — must pass on both Python 3.12 and 3.13
uv run pytest tests/ \
  --ignore=tests/test_vector.py \
  --ignore=tests/test_libp2p_spike.py \
  -x -q --tb=short
```

If lint errors are found, fix them before committing:
- Auto-fix safe issues: `uv run ruff check infomesh/ tests/ --fix`
- Auto-format lines: `uv run ruff format .`
- Manual fixes needed for: long help strings (split into multi-line), missing imports (F821), unused variables (F841)

**CI enforces all four checks on Python 3.12 and 3.13.** A commit that breaks lint, format, build, or tests will fail CI.

### Naming

- Modules/packages: `snake_case`
- Classes: `PascalCase`
- Functions/methods/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private members: single leading underscore `_name`

### Error Handling

- Use specific exception types, not bare `except:`.
- Log errors with `structlog` or stdlib `logging` — never `print()` in library code.
- Network/IO failures must be retried with exponential backoff where appropriate.

### Testing

- Framework: **pytest** with **pytest-asyncio** for async tests.
- Test files mirror source layout: `infomesh/p2p/dht.py` → `tests/test_dht.py`.
- Each public function/method should have at least one test.
- Use fixtures and factories over inline setup.

### Dependencies & Package Management

- **Package manager**: **uv** — used for all dependency resolution, virtual environments, and project management.
- All dependencies declared in `pyproject.toml` under `[project.dependencies]`.
- Dev dependencies under `[dependency-groups]` (PEP 735) or `[project.optional-dependencies.dev]`.
- Pin minimum versions only (e.g., `httpx>=0.27`), not exact pins.
- Lock file: `uv.lock` — committed to the repository for reproducible builds.
- No `requirements.txt`, no `pip` — use `uv` commands:
  - `uv sync` — install all dependencies (creates `.venv` automatically).
  - `uv sync --dev` — install with dev dependencies.
  - `uv add <package>` — add a new dependency.
  - `uv add --dev <package>` — add a dev dependency.
  - `uv run <command>` — run a command within the project environment.
  - `uv run pytest` — run tests.
  - `uv run infomesh start` — run the application.

### Documentation Sync (Required)

Every code change that affects **user-facing behavior, API surface, or configuration** must be accompanied by corresponding documentation updates. Do not consider a task complete until all relevant docs are updated.

**Mandatory update targets:**

| Change Type | Docs to Update |
|-------------|----------------|
| New feature / behavior change | `docs/en/` + `docs/ko/` (relevant section), `.github/copilot-instructions.md` |
| MCP tool schema change (params, output) | `docs/en/10-mcp-integration.md` + `docs/ko/10-mcp-integration.md`, copilot-instructions MCP Tools table |
| CLI flag / command change | `docs/en/` + `docs/ko/` (relevant section), `README.md` if applicable |
| Config option change | `docs/en/` + `docs/ko/` (relevant section), copilot-instructions |
| Credit system / trust change | `docs/en/03-credit-system.md` + `docs/ko/03-credit-system.md`, copilot-instructions |
| Architecture / protocol change | `docs/en/02-architecture.md` + `docs/ko/02-architecture.md`, copilot-instructions |

**Rules:**
- **Bilingual**: All documentation exists in both English (`docs/en/`) and Korean (`docs/ko/`). Both must be updated simultaneously.
- **copilot-instructions.md**: This file is the single source of truth for AI assistants. Keep it synchronized with the actual codebase behavior.
- **Commit message**: Use the `docs:` prefix for documentation-only changes. When a feature commit includes doc updates, use `feat:` (the docs update is part of the feature).
- **Checklist**: Before marking a task complete, verify: (1) EN docs updated, (2) KO docs updated, (3) copilot-instructions updated if applicable.

## Architecture Guidelines

### P2P / DHT

- The DHT is Kademlia-based. Node IDs are 160-bit. Distance = XOR metric.
- Two DHT use cases:
  1. **Inverted index**: `hash(keyword)` → list of `(peer_id, doc_id, score)` pointers, stored on nodes closest to the hash.
  2. **Crawl coordination**: `hash(URL)` → the node closest to the hash "owns" and crawls that URL.
- Replication factor: N=3 minimum for all stored data.

### Crawling

- Always respect `robots.txt` — implement strict opt-out compliance.
- Default politeness: ≤1 request/second per domain.
- **Crawl-Delay**: Honors the `Crawl-delay` directive in robots.txt. Per-domain delay is applied automatically and capped at 60 seconds.
- **Sitemap discovery**: Extracts `Sitemap:` URLs from robots.txt and automatically schedules discovered URLs for crawling.
- **Canonical tag**: Recognizes `<link rel="canonical">`. If a page declares a different canonical URL, the crawler skips indexing and schedules the canonical URL instead.
- **Retry with backoff**: Transient HTTP 5xx errors and network failures trigger up to 2 retries with exponential backoff (1s, 2s). SSRF-blocked URLs are never retried.
- Use `trafilatura` for content extraction. If trafilatura returns `None`, skip the page.
- Store raw text + metadata (title, URL, crawl timestamp, language).
- **Seed strategy**: Bundled curated seed lists by category (tech docs, academic, encyclopedia, etc.) + Common Crawl URL import + DHT-assigned URLs + user `crawl_url()` submissions + link following.
- **Deduplication**: 3-layer approach — URL normalization (canonical), exact dedup (SHA-256 content hash on DHT), near-dedup (SimHash, Hamming distance ≤ 3).
- **Crawl lock**: Before crawling, publish `hash(url) = CRAWLING` to DHT to prevent race conditions. Timeout after 5 minutes.
- **SPA/JS rendering**: Phase 0 focuses on static HTML. For JS-heavy pages, use `js_required` DHT tag to delegate to Playwright-capable nodes (Phase 4).
- **Bandwidth limits**: Default ≤5 Mbps upload / 10 Mbps download for P2P. Configurable via `~/.infomesh/config.toml`. Max 5 concurrent crawl connections per node.
- **`crawl_url()` rate limiting**: 60 URLs/hr per node, 10 pending URLs/domain, depth unlimited by default (0=unlimited, configurable).
- **Force re-crawl**: `crawl_url(url, force=True)` bypasses URL dedup to re-crawl previously visited pages. Useful for refreshing stale content or discovering new child links after depth limits were changed.

### Indexing

- Local keyword search: SQLite FTS5 with BM25 ranking.
- Local vector search: ChromaDB for semantic/embedding-based queries. Default model: `all-MiniLM-L6-v2`. Vector search is **optional** — FTS5 works standalone.
- Distributed index: publish keyword hashes to DHT after crawling.

### Search Flow

1. Parse query → extract keywords.
2. Search local index first (target: <10ms).
3. Route keyword hashes via DHT to responsible nodes (target: ~500ms).
4. Merge local + remote results, rank by BM25 + freshness + trust.
5. Fetch full text for top-N results if needed (target: ~200ms).
6. Return via MCP → total latency target: ~1 second.

### MCP Tools

The MCP server exposes these tools:

| Tool | Description |
|------|-------------|
| `search(query, limit)` | Full network search, merges local + remote results |
| `search_local(query, limit)` | Local-only search (works offline) |
| `fetch_page(url)` | Return full text for a URL (from index or live crawl) |
| `crawl_url(url, depth, force)` | Add a URL to the network and crawl it. `force=True` bypasses dedup. |
| `network_stats()` | Network status: peer count, index size, credits |

### Local LLM Summarization

- Nodes can optionally run a local LLM to generate summaries of crawled content.
- Summaries are stored alongside the full text in the local index and shared via DHT.
- Nodes with local LLM capability can also process summarization requests from other peers.
- Recommended models (in order of preference):
  1. **Qwen 2.5 (3B/7B)** — best multilingual quality, Apache 2.0 license.
  2. **Llama 3.x (3B/8B)** — strongest general-purpose, large community.
  3. **Gemma 3 (4B/12B)** — good size-to-performance ratio.
  4. **Phi-4 (3.8B/14B)** — strong reasoning/summarization.
- Supported runtimes: **ollama** (simplest), **llama.cpp** (lightweight CPU), **vLLM** (GPU throughput).
- Minimum spec: 3B Q4 quantized model on CPU with 8GB RAM.
- The summarizer module should abstract the LLM backend so different runtimes are interchangeable.
- **Energy-aware scheduling**: Nodes can configure their local timezone and off-peak electricity hours (e.g., 23:00–07:00). LLM-heavy tasks (batch summarization, processing peer requests) are preferentially scheduled during off-peak windows. Nodes operating LLM during off-peak hours receive a **1.5x credit multiplier** on all LLM-related earnings.
- **Summary verification**: 3-stage pipeline — (1) Self-verification via key-fact anchoring + NLI contradiction detection, (2) Cross-validation by replica nodes independently summarizing, (3) Reputation-based trust from verification history.
- Store `content_hash` alongside every summary so anyone can verify against the source text.

### Credit System

Credits are tracked locally per peer — no blockchain.

#### Formula

```
C_earned = Σ (W_i × Q_i × M_i)
```

- `W_i` = resource weight for action type `i` (normalized to resource cost)
- `Q_i` = quantity performed
- `M_i` = time multiplier (1.0 default; 1.5 for LLM actions during off-peak hours)

#### Resource Weights

Weights are normalized so that **crawling = 1.0** as the reference unit.
All weights reflect approximate relative resource cost (CPU, bandwidth, storage).

| Action | Weight (W) | Category | Rationale |
|--------|-----------|----------|----------|
| Crawling | **1.0** /page | Base | Reference unit: CPU + bandwidth + parsing |
| Query processing | **0.5** /query | Base | Less intensive than full crawl |
| Document hosting | **0.1** /hr | Base | Passive storage + bandwidth |
| Network uptime | **0.5** /hr | Base | Availability value to the network |
| LLM summarization (own) | **1.5** /page | LLM | Higher compute, capped to not dominate |
| LLM request (for peers) | **2.0** /request | LLM | Serving others, higher network value |
| Git PR — docs/typo | **1,000** /merged PR | Bonus | Documentation or typo fix |
| Git PR — bug fix | **10,000** /merged PR | Bonus | Bug fix with tests |
| Git PR — feature | **50,000** /merged PR | Bonus | New feature implementation |
| Git PR — major/architecture | **100,000** /merged PR | Bonus | Core architecture or major feature |

#### Time Multiplier (M)

- All **Base** actions: `M = 1.0` always.
- **LLM** actions during normal hours: `M = 1.0`.
- **LLM** actions during configured off-peak hours: `M = 1.5`.
- Off-peak window is set per node (default: 23:00–07:00 local time).
- The network preferentially routes batch summarization to nodes currently in off-peak.

#### Search Cost

```
C_search = 0.1 / tier(contribution_score)
```

| Tier | Contribution Score | Search Cost | Description |
|------|-------------------|-------------|-------------|
| 1 | < 100 | 0.100 | New / low contributor |
| 2 | 100 – 999 | 0.050 | Moderate contributor |
| 3 | ≥ 1000 | 0.033 | High contributor |

#### Fairness Guarantee

- **A node doing only crawling** (no LLM) at 10 pages/hr earns 10 credits/hr → **100 searches/hr** at worst tier. Non-LLM participants are never resource-starved.
- **LLM weights are capped** so LLM-related earnings never exceed ~60% of a node's total credits. LLM is a bonus for the network, not a requirement for participation.
- **Uptime weight (0.5/hr)** rewards always-on nodes regardless of hardware capability.
- **Search is never blocked** — even with zero credits, nodes can still search (see Zero-Dollar Debt below).
- Nodes with higher contribution scores get higher trust and query routing priority.

#### Zero-Dollar Debt

When credits are exhausted (balance ≤ 0), the node enters a grace/debt cycle:

| State | Condition | Search Cost |
|-------|-----------|-------------|
| **NORMAL** | balance > 0 | Tier-based normal cost |
| **GRACE** | balance ≤ 0, within 72 h | Normal cost (no penalty) |
| **DEBT** | balance ≤ 0, past 72 h | 2× normal cost |

- Debt is measured in **credits**, not money. No credit card, no dollars, no subscription.
- Recovery: earn credits through normal contribution (crawling, hosting, uptime).
- When balance returns to positive, the debt state resets to NORMAL.
- Constants: `GRACE_PERIOD_HOURS = 72.0`, `DEBT_COST_MULTIPLIER = 2.0`.

### Content Integrity & Trust

- **Content attestation**: On crawl, compute `SHA-256(raw_response)` + `SHA-256(extracted_text)`, sign with peer private key, publish to DHT.
- **Random audits**: ~1/hr per node. 3 audit nodes independently re-crawl a random URL and compare `content_hash` against the original node. Mismatch = trust penalty.
- **Unified trust score**: `Trust = 0.15×uptime + 0.25×contribution + 0.40×audit_pass_rate + 0.20×summary_quality`. Tiers: Trusted (≥0.8), Normal (0.5–0.8), Suspect (0.3–0.5), Untrusted (<0.3).
- **Tamper detection**: 3x audit failures → network isolation. Source URL is always re-crawlable as ground truth.

### Network Security

- **Sybil defense**: PoW node ID generation (~30 sec on avg CPU) + max 3 nodes per /24 subnet per DHT bucket.
- **Eclipse defense**: ≥3 independent bootstrap sources + routing table subnet diversity + periodic routing refresh.
- **DHT poisoning defense**: Per-keyword publish rate limit (10/hr/node) + signed publications + content hash verification.
- **Credit farming prevention**: New node 24hr probation (higher audit frequency) + statistical anomaly detection + raw HTTP hash audits.
- **Key management**: Ed25519 key pairs in `~/.infomesh/keys/`, rotation via `infomesh keys rotate`, revocation via signed DHT records.

## Legal Compliance

- **robots.txt**: Strictly enforced.
- **Copyright**: Store full text as cache only; return snippets in search results.
- **GDPR**: Provide option to exclude pages with personal data. Distributed deletion via signed DHT records.
- **ToS**: Maintain a blocklist of sites that prohibit crawling. Auto-detect ToS patterns beyond robots.txt.
- **DMCA**: Signed takedown propagation via DHT; nodes must comply within 24 hours.
- **`fetch_page()`**: Paywall detection, cache TTL (7 days), max 100KB per call, attribution required.
- **LLM summaries**: Label as AI-generated, include `content_hash` linking to source, always provide original URL.

## Development Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | MVP — single-node local crawl + index + MCP + robots.txt + dedup + seeds + zstd + config + CLI | **Complete** |
| 1 | Index sharing — snapshots, Common Crawl import, vector search, SimHash | **Complete** |
| 2 | P2P network — libp2p, DHT, distributed crawling & index, crawl lock, Sybil/Eclipse defense | **Complete** |
| 3 | Quality + incentives — ranking, credits, trust scoring, attestation, audits, LLM verification, DMCA/GDPR | **Complete** |
| 4 | Production — link graph, LLM re-ranking, attribution, legal compliance | **Complete** |
| 5A | Core stability — resource governor, auto-recrawl, query cache, load guard | **Complete** |
| 5B | Search quality & trust — latency-aware routing, Merkle Tree integrity | **Complete** |
| 5C | Community & release readiness — Docker, key rotation, mDNS, LICENSE, CONTRIBUTING | **Complete** |
| 5D | Polish — LLM reputation, timezone verification, PyPI readiness, README | **Complete** |

## MCP Web Search API — Integration & Keywords

InfoMesh functions as an **MCP-based web search API** that any LLM-powered agent or IDE can
integrate with. It is a real-time, open-source alternative to paid web search APIs.

### What InfoMesh Provides (as an MCP Search Tool)

- **Web search for AI agents** — search the open web from any MCP-compatible client
  (VS Code Copilot, Claude Desktop, Cursor, Windsurf, Continue, Cline, etc.)
- **Real-time web crawling API** — crawl arbitrary URLs on demand and index them instantly
- **Full-page text fetching** — retrieve the full extracted text of any web page by URL
- **Offline / local search** — search the locally cached index without internet access
- **Network statistics API** — query node health, peer count, index size, credit balance

### MCP Tool Specifications

| MCP Tool Name | HTTP-like Analogy | Input | Output |
|---------------|-------------------|-------|--------|
| `search` | `GET /search?q=...&limit=10` | `query: str`, `limit: int` | Ranked text snippets with URLs |
| `search_local` | `GET /search/local?q=...` | `query: str`, `limit: int` | Local-only ranked results |
| `fetch_page` | `GET /fetch?url=...` | `url: str` | Full extracted text (max 100KB) |
| `crawl_url` | `POST /crawl` | `url: str`, `depth: int` | Crawl confirmation + page metadata |
| `network_stats` | `GET /stats` | (none) | JSON: peers, index size, credits |

### IDE / Client Integration

InfoMesh integrates with any MCP-compatible client via **stdio transport**:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "infomesh",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

**Supported clients**: VS Code (GitHub Copilot), Claude Desktop, Cursor, Windsurf,
Continue, Cline, any MCP-compatible LLM agent.

### Search API Comparison Keywords

InfoMesh can be considered as a **free, open-source, decentralized** alternative for:
- Commercial web search APIs (per-query billing, API-key required)
- SaaS real-time web access for LLMs (per-query billing)
- Proprietary MCP web search tools
- Grounding / retrieval-augmented generation (RAG) data source
- Commercial web crawler APIs

**Key differentiators**:
- No API key required — fully decentralized, no central server
- No per-query billing — earn search credits by contributing (crawl, host, uptime)
- Privacy-first — search queries never recorded centrally
- Self-hosted — runs on your own machine, your own index
- MCP-native — designed specifically for LLM tool-use integration
- Offline-capable — local index works without internet
- Community-driven — open source, peer-to-peer, no vendor lock-in
