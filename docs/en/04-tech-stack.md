# InfoMesh — Tech Stack & Coding Conventions

---

## 1. Tech Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | **Python 3.12+** | Use modern Python features (type hints, `match`, `type` statement, `StrEnum`, `TypeVar` defaults, etc.) |
| P2P Network | **libp2p (py-libp2p)** | DHT, NAT traversal, encryption built-in |
| DHT | **Kademlia** | Proven distributed hash table for index & crawl coordination |
| Crawling | **httpx + asyncio** | Async-first high-performance HTTP client |
| HTML Parsing | **trafilatura** | Best accuracy for main-content extraction |
| Keyword Index | **SQLite FTS5** | Zero-install, embedded full-text search |
| Vector Index | **ChromaDB** | Semantic search with embeddings |
| MCP Server | **mcp-python-sdk** | VS Code / Claude integration |
| Admin API | **FastAPI** | Local status & config endpoints |
| Serialization | **msgpack** | Faster and smaller than JSON |
| Compression | **zstd** | Level-tunable compression; dictionary mode for similar documents |
| Local LLM | **ollama / llama.cpp** | Optional local summarization (Qwen 2.5, Llama 3.x, etc.) |
| Logging | **structlog** | Structured logging for all library code |
| Package Manager | **uv** | Fast Python package/project manager (replaces pip/venv) |

### Optional / Fallback Dependencies

| Package | When Used |
|---------|----------|
| **BeautifulSoup4** | HTML parsing fallback when trafilatura fails |
| **vLLM** | High-throughput GPU inference (alternative to ollama/llama.cpp) |
| **sentence-transformers** | Embedding generation for ChromaDB vector index |
| **Playwright** | Headless Chromium for JS-rendered pages (`pip install 'infomesh[browser]'`) |
| **jieba** | Chinese word segmentation for CJK search (`pip install 'infomesh[cjk]'`) |

---

## 2. Project Structure

```
infomesh/
├── pyproject.toml
├── infomesh/
│   ├── __init__.py          # Package root
│   ├── __main__.py          # CLI entry point
│   ├── config.py            # Configuration management
│   ├── services.py          # Central AppContext + index_document orchestration
│   ├── p2p/                 # P2P network layer
│   │   ├── node.py          #   Peer main process
│   │   ├── dht.py           #   Kademlia DHT
│   │   ├── routing.py       #   Query routing
│   │   ├── replication.py   #   Document/index replication
│   │   └── protocol.py      #   Message protocol definitions
│   ├── crawler/             # Web crawler
│   │   ├── worker.py        #   Async crawl workers
│   │   ├── scheduler.py     #   URL assignment (DHT-based)
│   │   ├── parser.py        #   HTML → text extraction
│   │   ├── robots.py        #   robots.txt compliance
│   │   ├── dedup.py         #   Deduplication pipeline (URL, SHA-256, SimHash)
│   │   ├── seeds.py         #   Seed URL management & category selection
│   │   └── crawl_loop.py    #   Continuous seed-and-crawl loop (extracted from services.py)
│   ├── index/               # Search index
│   │   ├── local_store.py   #   SQLite FTS5 local index
│   │   ├── vector_store.py  #   ChromaDB vector index
│   │   ├── distributed.py   #   DHT inverted-index publish/query
│   │   └── ranking.py       #   BM25 + freshness + trust scoring
│   ├── search/              # Search engine
│   │   ├── query.py         #   Query parsing + distributed orchestration
│   │   └── merge.py         #   Multi-node result merging
│   ├── mcp/                 # MCP server (SRP: split into 4 modules)
│   │   ├── server.py        #   Thin wiring: Server creation, tool dispatch, runners
│   │   ├── tools.py         #   Tool schema definitions + filter extraction
│   │   ├── handlers.py      #   Tool handler implementations (handle_search, etc.)
│   │   └── session.py       #   SearchSession, AnalyticsTracker, WebhookRegistry
│   ├── api/                 # Local admin API
│   │   └── local_api.py     #   FastAPI (status, config)
│   ├── credits/             # Incentive system
│   │   ├── types.py         #   ActionType, CreditState, dataclasses (extracted from ledger.py)
│   │   └── ledger.py        #   SQLite-backed credit ledger (imports types from types.py)
│   ├── trust/               # Trust & integrity
│   │   ├── attestation.py   #   Content attestation chain (signing, verification)
│   │   ├── audit.py         #   Random audit system
│   │   └── scoring.py       #   Unified trust score computation
│   ├── summarizer/          # Local LLM summarization
│   │   ├── engine.py        #   LLM backend abstraction (ollama, llama.cpp)
│   │   ├── summarize.py     #   Content summarization pipeline
│   │   └── verify.py        #   Summary verification (key-fact anchoring, NLI)
│   └── compression/         # Data compression
│       └── zstd.py          #   zstd compression with dictionary support
├── bootstrap/
│   └── nodes.json           # Bootstrap node list
├── seeds/                   # Bundled seed URL lists
│   ├── tech-docs.txt        #   Technology documentation URLs
│   ├── academic.txt         #   Academic paper source URLs
│   └── encyclopedia.txt     #   Encyclopedia URLs
├── tests/
│   ├── conftest.py          # Shared fixtures
│   ├── test_dht.py
│   ├── test_crawler.py
│   ├── test_index.py
│   ├── test_search.py
│   ├── test_credits.py
│   ├── test_trust.py
│   ├── test_summarizer.py
│   ├── test_mcp.py
│   ├── test_services.py     # Services layer tests
│   └── test_mcp_handlers.py # MCP handler tests
└── docs/
```

---

## 3. Coding Conventions

### 3.1 General

- **Language**: All source code, comments, docstrings, commit messages, and PR descriptions in **English**.
- **Python version**: 3.12+ — use modern syntax (`match/case`, `type` statement, `StrEnum`, `TypeVar` defaults).
- **Async-first**: All I/O-bound code must use `async/await` with `asyncio`. Never use blocking I/O in the event loop.
- **Type hints**: Required on all public functions and class attributes. Use `from __future__ import annotations` for forward references.

### 3.2 Style & Formatting

- Formatter: **ruff format** (default settings, line length 88).
- Linter: **ruff** with `select = ["E", "F", "I", "UP", "B", "SIM"]`.
- Import order: stdlib → third-party → local (enforced by ruff/isort).
- Prefer `pathlib.Path` over `os.path`.

### 3.3 Naming

| Target | Convention | Example |
|--------|-----------|---------|
| Modules/packages | `snake_case` | `local_store.py` |
| Classes | `PascalCase` | `SearchResult` |
| Functions/methods/variables | `snake_case` | `parse_query()` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Private members | Single underscore | `_internal_state` |

### 3.4 Error Handling

- Use specific exception types, not bare `except:`.
- Log errors with `structlog` or stdlib `logging` — never `print()` in library code.
- Network/IO failures must be retried with exponential backoff where appropriate.

### 3.5 Testing

- Framework: **pytest** with **pytest-asyncio** for async tests.
- Test files mirror source layout: `infomesh/p2p/dht.py` → `tests/test_dht.py`.
- Each public function/method should have at least one test.
- Use fixtures and factories over inline setup.

---

## 4. Dependencies & Package Management

### Using uv

`uv` is used for all dependency resolution, virtual environments, and project management.

- All dependencies declared in `pyproject.toml` under `[project.dependencies]`.
- Dev dependencies under `[dependency-groups]` (PEP 735) or `[project.optional-dependencies.dev]`.
- Pin minimum versions only (e.g., `httpx>=0.27`), not exact pins.
- Lock file: `uv.lock` — committed to the repository for reproducible builds.
- No `requirements.txt`, no `pip` — use `uv` commands only.

### Key Commands

```bash
uv sync --locked     # Install all dependencies from uv.lock
uv sync --dev --locked  # Install dev dependencies from uv.lock
uv add <package>     # Add a new dependency
uv add --dev <pkg>   # Add a dev dependency
uv run <command>     # Run a command within the project environment
uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short
uv run infomesh start  # Run the application
```

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
