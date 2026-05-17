# Contributing to InfoMesh

Thank you for your interest in InfoMesh! This guide explains how to set up
your development environment, run tests, and submit changes.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/dotnetpower/infomesh.git
cd infomesh

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies (creates .venv automatically)
uv sync --dev --locked

# Run the supported test suite
uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short

# Start the node
uv run infomesh start
```

## Development Setup

### Requirements

- **Python 3.12+** — we use modern features (`match/case`, `StrEnum`, type hints)
- **uv** — fast Python package manager (replaces pip/venv)
- No other system dependencies required for basic development

### Optional Dependencies

```bash
# Vector search (ChromaDB + sentence-transformers)
uv sync --extra vector

# Local LLM summarization
uv sync --extra llm
```

## Code Style

### Formatting & Linting

```bash
# Format code
uv run ruff format .

# Lint (auto-fix)
uv run ruff check --fix .

# Type check
uv run mypy infomesh/ --ignore-missing-imports
```

- **Formatter**: ruff (line length 88, Black-compatible)
- **Linter**: ruff with `select = ["E", "F", "I", "UP", "B", "SIM"]`
- **Type hints**: Required on all public functions

### Naming Conventions

| Element | Style | Example |
|---------|-------|---------|
| Modules/packages | `snake_case` | `local_store.py` |
| Classes | `PascalCase` | `CrawlWorker` |
| Functions/variables | `snake_case` | `crawl_url()` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_DEPTH` |
| Private members | `_leading_underscore` | `_client` |

### Key Principles

- **Single Responsibility**: One class = one concern, one function = one task
- **Async-first**: All I/O uses `async/await` with `asyncio`
- **No `print()`**: Use `structlog` for all logging
- **`pathlib.Path`** over `os.path`

## Testing

```bash
# Run the supported test suite
uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short

# Run specific test file
uv run pytest tests/test_crawler.py

# Run with coverage
uv run pytest tests/ --ignore=tests/test_vector.py --cov=infomesh

# Run a single test
uv run pytest tests/test_index.py::TestLocalStore::test_search
```

### Writing Tests

- Test files go in `tests/` mirroring source layout
- Use `pytest` with `pytest-asyncio` for async tests
- Use fixtures over inline setup
- Aim for at least one test per public function

```python
# Example test
import pytest
from infomesh.crawler.dedup import DeduplicatorDB

class TestDeduplicator:
    def test_url_seen(self, tmp_path):
        db = DeduplicatorDB(tmp_path / "dedup.db")
        db.mark_url_seen("https://example.com")
        assert db.is_url_seen("https://example.com")
        assert not db.is_url_seen("https://other.com")
```

## Project Structure

```
infomesh/
├── infomesh/          # Main package
│   ├── __main__.py    #   CLI entry point (Click)
│   ├── config.py      #   Configuration management
│   ├── services.py    #   Service layer (AppContext)
│   ├── errors.py      #   Structured error hierarchy
│   ├── scalability.py #   Batch ingest & horizontal scaling
│   ├── data_quality.py#   Quality scoring & validation
│   ├── security_ext.py#   API keys, RBAC, audit logging
│   ├── dx.py          #   Developer experience (plugin system)
│   ├── p2p/           #   P2P network (libp2p, DHT, mDNS)
│   ├── crawler/       #   Web crawler (PDF, RSS, structured data)
│   ├── index/         #   Search index (FTS5, ChromaDB, link graph)
│   ├── search/        #   Query processing (NLP, facets, RAG)
│   ├── mcp/           #   MCP server for LLMs (15 tools)
│   ├── credits/       #   Incentive system
│   ├── trust/         #   Content integrity & attestation
│   ├── summarizer/    #   Local LLM summarization
│   ├── dashboard/     #   Console TUI (Textual)
│   ├── resources/     #   Resource governance
│   ├── compression/   #   zstd compression
│   ├── api/           #   FastAPI admin API + extensions
│   ├── sdk/           #   Python SDK client
│   ├── integrations/  #   LangChain, LlamaIndex, Haystack
│   ├── persistence/   #   Persistent key-value store
│   └── observability/ #   Prometheus metrics
├── tests/             # Supported test suite (1,844 tests)
├── docs/              # Documentation (en + ko)
├── seeds/             # Seed URL lists
├── examples/          # Usage examples
└── bootstrap/         # Bootstrap node config
```

## Good First Issues

Looking for a place to start? Here are beginner-friendly tasks:

| Issue | Difficulty | Area |
|-------|-----------|------|
| Add seed URLs for a new language/topic | 🟢 Easy | Seeds |
| Fix typos in documentation | 🟢 Easy | Docs |
| Translate docs to a new language | 🟢 Easy | Docs |
| Add missing docstrings | 🟢 Easy | Code Quality |
| Write tests for untested edge cases | 🟡 Medium | Testing |
| Add a new NLP stop-word language | 🟡 Medium | Search |
| Improve CLI help text and examples | 🟡 Medium | CLI |
| Add new structured data extractors | 🟡 Medium | Crawler |
| Create a usage example in `examples/` | 🟡 Medium | DX |
| Add a new dashboard widget | 🟠 Moderate | Dashboard |
| Implement a new search result format | 🟠 Moderate | Search |
| Add new credit action types | 🟠 Moderate | Credits |
| Integration with a new LLM framework | 🔴 Advanced | Integrations |
| Playwright-based JS rendering | 🔴 Advanced | Crawler |
| Multi-language stemming support | 🔴 Advanced | Search |

## Submitting Changes

### Pull Request Process

1. **Fork** the repository and create a feature branch
2. **Write tests** for your changes
3. **Run the supported test suite**: `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short`
4. **Lint and format your code**: `uv run ruff check infomesh/ tests/` and `uv run ruff format --check .`
5. **Open a PR** with a clear description

### Commit Messages

- Use English for all commits
- Keep the first line under 72 characters
- Use imperative mood: "Add feature" not "Added feature"

```
feat: add mDNS local peer discovery
fix: resolve seed rediscovery on restart
docs: update Phase 5C roadmap
test: add preflight check tests
refactor: extract link discovery from crawl loop
```

### What We Accept

- Bug fixes with tests
- New features aligned with the [project overview](docs/en/01-overview.md)
- Documentation improvements
- Performance optimizations with benchmarks
- Seed URL contributions (add URLs to `seeds/`)

### Credit

All contributors earn credits per merged PR in the InfoMesh credit
system, based on the type of contribution:

| PR Type | Credits |
|---------|--------|
| docs/typo | **1,000** |
| bug fix | **10,000** |
| feature | **50,000** |
| major/architecture | **100,000** |

Your contributions directly improve global search for LLMs!

## Seed URL Contributions

One of the easiest ways to contribute is adding quality seed URLs:

```bash
# Add URLs to the appropriate category file
echo "https://docs.example.com/" >> seeds/tech-docs.txt
```

Requirements for seed URLs:
- Publicly accessible (no login required)
- Respects robots.txt
- High-quality content (documentation, academic, reference)
- No spam, ads-heavy, or low-value sites

## Communication

- **Issues**: Bug reports, feature requests
- **Pull Requests**: Code contributions
- **Discussions**: Architecture questions, proposals

All communication should be in **English**.

---

## Key Systems for Contributors

### Plugin System (`infomesh/plugins.py`)

Register custom extensions via hook points:

```python
from infomesh.plugins import get_registry, HookPoint

registry = get_registry()

@registry.hook(HookPoint.PRE_SEARCH)
def boost_local_results(data):
    # Custom scoring logic
    return data
```

Available hooks: `PRE_CRAWL`, `POST_CRAWL`, `PRE_INDEX`, `POST_INDEX`,
`PRE_SEARCH`, `POST_SEARCH`, `PRE_RANK`, `POST_RANK`,
`CUSTOM_TOKENIZER`, `CUSTOM_SCORER`.

### New Module Checklist

When adding a new module:

1. Create the module under the appropriate package
2. Add tests in `tests/test_<module>.py`
3. Register in `.github/copilot-instructions.md` project structure
4. Update docs (both `docs/en/` and `docs/ko/`)
5. Run: `uv run ruff check infomesh/ tests/ && uv run ruff format --check . && uv run mypy infomesh/ --ignore-missing-imports && uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short`

---

*See [LICENSE](LICENSE) for license terms and [TERMS_OF_USE.md](TERMS_OF_USE.md) for usage terms.*
