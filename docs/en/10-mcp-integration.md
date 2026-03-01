# MCP Connection Guide

How to connect AI assistants to InfoMesh via [MCP (Model Context Protocol)](https://modelcontextprotocol.io/).

---

## What is MCP?

MCP is an open protocol that lets AI assistants (Claude, GitHub Copilot, etc.) call external tools.
InfoMesh exposes 5 tools via MCP — search, search_local, fetch_page, crawl_url, and network_stats —
so your AI assistant can search the web through your own decentralized index.

## Available MCP Tools

| Tool | Description | Parameters |
|------|-------------|-----------|
| `search` | Search the P2P network (local + distributed) | `query` (string), `limit` (int, default 10) |
| `search_local` | Search local index only (works offline) | `query` (string), `limit` (int, default 10) |
| `fetch_page` | Fetch full text of a URL (cached or live) | `url` (string) |
| `crawl_url` | Crawl a URL and add to the index | `url` (string), `depth` (int, default 0, max 3) |
| `network_stats` | Node status: index size, peers, credits | *(none)* |

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
                "search", {"query": "python asyncio", "limit": 5}
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

---

## Local HTTP API (Alternative)

If your client doesn't support MCP, InfoMesh also exposes a local REST API
when the node is running (`infomesh start`):

```bash
# Health check
curl http://localhost:8080/health

# Node status
curl http://localhost:8080/status

# Index statistics
curl http://localhost:8080/index/stats

# Credit balance
curl http://localhost:8080/credits/balance
```

The API binds to `127.0.0.1` only — it is not exposed to the network.

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `INFOMESH_DATA_DIR` | Data directory path | `~/.infomesh` |
| `INFOMESH_CONFIG` | Config file path | `~/.infomesh/config.toml` |

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

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
