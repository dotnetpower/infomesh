# MCP Connection Guide

How to connect AI assistants to InfoMesh via [MCP (Model Context Protocol)](https://modelcontextprotocol.io/).

---

## What is MCP?

MCP is an open protocol that lets AI assistants (Claude, GitHub Copilot, etc.) call external tools.
InfoMesh exposes **5 tools** via MCP — web_search, fetch_page, crawl_url, fact_check, and status —
so your AI assistant can search the web through your own decentralized index.

> **v0.3.0 consolidation**: The previous 18 tools have been consolidated into 5 focused tools.
> Legacy tool names (`search`, `search_local`, `network_stats`, etc.) are still accepted for backward compatibility.

## Available MCP Tools

### Search & Intelligence

| Tool | Description | Key Parameters |
|------|-------------|---------------|
| `web_search` | Unified web search (P2P + local, RAG, explain, answer extraction) | `query` (required), `top_k`, `recency_days`, `domain_allowlist`, `domain_blocklist`, `language`, `fetch_full_content`, `chunk_size`, `rerank`, `answer_mode`, `local_only`, `explain` |

### Content Access

| Tool | Description | Key Parameters |
|------|-------------|---------------|
| `fetch_page` | Fetch full text of a URL (cached or live, max 100KB) | `url` (required) |
| `crawl_url` | Crawl a URL and add to the index (60/hr rate limit) | `url` (required), `depth`, `force` |

### Verification

| Tool | Description | Key Parameters |
|------|-------------|---------------|
| `fact_check` | Cross-reference claims against indexed sources | `claim` (required), `top_k` |

### Status

| Tool | Description | Key Parameters |
|------|-------------|---------------|
| `status` | Node status: index size, peers, credits, analytics | _(none required)_ |

### web_search Parameters

The `web_search` tool replaces 6 previous tools with optional parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | _(required)_ | Search query text |
| `top_k` | integer | `5` | Number of results to return |
| `recency_days` | integer | — | Only results from the last N days |
| `domain_allowlist` | string[] | — | Only include these domains |
| `domain_blocklist` | string[] | — | Exclude these domains |
| `language` | string | — | ISO 639-1 code (e.g. `"en"`, `"ko"`) |
| `fetch_full_content` | boolean | `false` | Include full article text per result |
| `chunk_size` | integer | — | RAG chunk size (enables chunked output) |
| `rerank` | boolean | `true` | Apply LLM semantic re-ranking |
| `answer_mode` | `"snippets"` \| `"summary"` \| `"structured"` | `"snippets"` | Response format mode |
| `local_only` | boolean | `false` | Search local index only (offline, <10ms) |
| `explain` | boolean | `false` | Include BM25/freshness/trust score breakdown |

### JSON Output

When `format: "json"` is specified, responses include:

```json
{
  "total": 42,
  "elapsed_ms": 12.3,
  "source": "local_fts5",
  "results": [
    {
      "url": "https://example.com/page",
      "title": "Example Page",
      "domain": "example.com",
      "snippet": "...",
      "score": 0.85,
      "scores": { "bm25": 0.7, "freshness": 0.9, "trust": 1.0, "authority": 0.5 }
    }
  ],
  "quota": {
    "credit_balance": 125.5,
    "state": "normal",
    "search_cost": 0.033
  },
  "api_version": "2025.1"
}
```

### Authentication

Set the `INFOMESH_API_KEY` environment variable to require API key authentication.
When set, all tool calls must include the `api_key` parameter.

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"],
      "env": {
        "INFOMESH_API_KEY": "your-secret-key"
      }
    }
  }
}
```

---

## Quick Start

### 1. Install & Run (One Command)

The fastest way — no clone, no setup:

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Run MCP server directly (auto-downloads infomesh from PyPI)
uvx infomesh mcp
```

### 2. Or Install Permanently

```bash
# Install as a tool (available system-wide)
uv tool install infomesh
infomesh mcp

# Or via pip
pip install infomesh
infomesh mcp
```

The MCP server communicates via **stdio** (stdin/stdout) — it doesn't open a network port.
The AI client launches InfoMesh as a subprocess and exchanges JSON-RPC messages through pipes.

---

## IDE & Client Configuration

### VS Code (GitHub Copilot)

Add to your VS Code settings (`.vscode/settings.json` or user settings):

```jsonc
// Recommended: uses uvx (no clone/install needed)
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "uvx",
        "args": ["infomesh", "mcp"]
      }
    }
  }
}
```

Alternative — if installed via `uv tool install` or `pip install`:

```jsonc
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "infomesh",
        "args": ["mcp"]
      }
    }
  }
}
```

After adding the configuration:
1. Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`)
2. Search for **"MCP: List Servers"** to verify InfoMesh appears
3. Use Copilot Chat — it will automatically discover and use InfoMesh tools

### VS Code (MCP `.json` file — alternative)

Create `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

After saving, restart Claude Desktop. You'll see InfoMesh tools in the 🔧 menu.

### Cursor

Cursor supports MCP through its settings. Go to **Cursor Settings → MCP** and add:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### Windsurf

Add to Windsurf's MCP configuration (`~/.windsurf/mcp_config.json`):

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### JetBrains IDEs (IntelliJ, PyCharm, WebStorm, etc.)

JetBrains IDEs with AI Assistant support MCP. Add to your MCP configuration:

1. Open **Settings → Tools → AI Assistant → MCP Servers**
2. Click **Add** (+) and configure:
   - **Name**: `infomesh`
   - **Command**: `uvx`
   - **Arguments**: `infomesh mcp`

Or edit the config file directly (location varies by OS):

```json
{
  "servers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### Zed

Add to your Zed settings (`~/.config/zed/settings.json`):

```json
{
  "context_servers": {
    "infomesh": {
      "command": {
        "path": "uvx",
        "args": ["infomesh", "mcp"]
      }
    }
  }
}
```

### Neovim (with MCP plugin)

If you use an MCP-compatible Neovim plugin (e.g., `mcp.nvim`):

```lua
require("mcp").setup({
  servers = {
    infomesh = {
      command = "uvx",
      args = { "infomesh", "mcp" },
    },
  },
})
```

---

## Programmatic MCP Client (Python)

You can connect to the InfoMesh MCP server from your own Python code:

```python
import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    server = StdioServerParameters(
        command="uv",
        args=["run", "infomesh", "mcp"],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Search
            result = await session.call_tool(
                "web_search", {"query": "python asyncio", "top_k": 5}
            )
            print(result.content[0].text)

            # Crawl a URL
            result = await session.call_tool(
                "crawl_url", {"url": "https://docs.python.org/3/", "depth": 1}
            )
            print(result.content[0].text)

asyncio.run(main())
```

See [`examples/mcp_client.py`](../examples/mcp_client.py) for a complete working example.

### TypeScript / JavaScript

For Node.js applications, see the TypeScript examples in `examples/typescript/`:

```bash
cd examples/typescript
npm install
npx tsx mcp_client.ts     # Full MCP client demo
npx tsx http_client.ts    # Admin API client
```

The TypeScript client demonstrates JSON output, search filters, batch search,
suggestions, sessions, and all available MCP tools.

---

## Docker & Kubernetes Deployment

### Docker Compose (Multi-Node)

```bash
# Start a 3-node local cluster
docker compose up -d

# Nodes are named node1, node2, node3
# Admin APIs: localhost:8080, :8082, :8084
# MCP HTTP:   localhost:8081, :8083, :8085
```

See `docker-compose.yml` for the full configuration.

### Kubernetes

```bash
# Apply all manifests
kubectl apply -f k8s/

# Resources created:
# - Namespace: infomesh
# - ConfigMap: shared config.toml
# - Secret: optional API key
# - StatefulSet: 3 replicas with persistent storage
# - Services: headless + LoadBalancer
```

The StatefulSet includes liveness (`/health`) and readiness (`/readiness`) probes.

---

## HTTP Transport

In addition to stdio, InfoMesh supports HTTP Streamable transport for containers and remote agents:

```bash
# Start MCP server on HTTP
infomesh mcp --http --host 0.0.0.0 --port 8081
```

This is useful for Docker/Kubernetes deployments where stdio isn't available.
Connect your MCP client to `http://<host>:8081/mcp`.

---

## Local HTTP API (Alternative)

If your client doesn't support MCP, InfoMesh also exposes a local REST API
when the node is running (`infomesh start`):

```bash
# Health check
curl http://localhost:8080/health

# Readiness probe (checks DB)
curl http://localhost:8080/readiness

# Node status
curl http://localhost:8080/status

# Index statistics
curl http://localhost:8080/index/stats

# Credit balance
curl http://localhost:8080/credits/balance

# Search analytics
curl http://localhost:8080/analytics
```

API key authentication is supported via the `x-api-key` header when
`INFOMESH_API_KEY` is set.

The API binds to `127.0.0.1` only — it is not exposed to the network.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `INFOMESH_DATA_DIR` | Data directory path | `~/.infomesh` |
| `INFOMESH_CONFIG` | Config file path | `~/.infomesh/config.toml` |
| `INFOMESH_API_KEY` | API key for authentication (optional) | *(none)* |

---

## Troubleshooting

### "Server not found" in VS Code
- Ensure `uv` is in your PATH: `which uv`
- Use absolute path to uv if needed: `/home/user/.cargo/bin/uv`
- Check the Output panel → "MCP" for error logs

### "No results found"
- Your index may be empty. Crawl some pages first: `uvx infomesh crawl https://docs.python.org/3/`
- Or start the node: `uvx infomesh start`

### MCP server exits immediately
- Run `uvx infomesh mcp` manually to see error output
- If using from source, ensure dependencies are installed: `uv sync`

### Permission denied on keys
- InfoMesh stores keys in `~/.infomesh/keys/`. Ensure the directory is writable.
- Key files must be owned by the current user (chmod 600).

---

## MCP Module Architecture

The MCP server code follows the **Single Responsibility Principle (SRP)** — split into four focused modules:

| Module | Responsibility | Approx. Lines |
|--------|---------------|---------------|
| `mcp/server.py` | Thin wiring — creates the `Server` instance, registers tools, dispatches calls to handlers, runs stdio/HTTP servers | ~570 |
| `mcp/tools.py` | Tool schema definitions (`get_all_tools()`), filter extraction (`extract_filters()`), API key check | ~330 |
| `mcp/handlers.py` | All `handle_*` functions — validate arguments, delegate to service layer, format responses | ~1,480 |
| `mcp/session.py` | `SearchSession`, `AnalyticsTracker`, `WebhookRegistry` helper classes | ~230 |

This split enforces that **no business logic lives in `server.py`** — it only dispatches to handlers,
which in turn delegate to `infomesh.services` functions.

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
