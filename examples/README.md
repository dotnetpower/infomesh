# InfoMesh Examples

Python example scripts demonstrating how to use InfoMesh as a library.

## Prerequisites

```bash
# Clone and install
git clone https://github.com/dotnetpower/infomesh.git
cd infomesh
uv sync
```

## Examples

| Script | Description |
|--------|-------------|
| [basic_search.py](basic_search.py) | Search your local index using FTS5 |
| [crawl_and_search.py](crawl_and_search.py) | Crawl a URL, index it, then search |
| [hybrid_search.py](hybrid_search.py) | Keyword + vector semantic search |
| [fetch_page.py](fetch_page.py) | Fetch full text of a web page |
| [credit_status.py](credit_status.py) | Check credit balance and earnings |
| [mcp_client.py](mcp_client.py) | Connect to InfoMesh MCP server programmatically |

## Running

```bash
# Basic search
uv run python examples/basic_search.py "python asyncio"

# Crawl a page and search it
uv run python examples/crawl_and_search.py https://docs.python.org/3/library/asyncio.html

# Hybrid search (requires: uv add chromadb sentence-transformers)
uv run python examples/hybrid_search.py "how to handle errors in async python"

# Fetch a page
uv run python examples/fetch_page.py https://example.com

# Check credits
uv run python examples/credit_status.py

# MCP client (connects to the MCP server as a subprocess)
uv run python examples/mcp_client.py
```

## Using InfoMesh as a Library

You can import InfoMesh components directly into your own Python projects:

```python
from infomesh.config import load_config
from infomesh.index.local_store import LocalStore
from infomesh.search.query import search_local

config = load_config()
store = LocalStore(db_path=config.index.db_path)
result = search_local(store, "your query", limit=10)
for doc in result.documents:
    print(f"{doc.title} — {doc.url}")
store.close()
```
