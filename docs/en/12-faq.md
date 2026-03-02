# FAQ — Frequently Asked Questions

## General

### What is InfoMesh?

InfoMesh is a **fully decentralized P2P search engine** built for LLMs.
It crawls, indexes, and searches the web via a peer-to-peer network and exposes
results through MCP (Model Context Protocol). There is no human-facing UI — it is
designed as a tool that AI assistants call directly.

### How is InfoMesh different from commercial search APIs?

| Feature                | Commercial APIs       | InfoMesh                      |
|------------------------|-----------------------|-------------------------------|
| Cost                   | Per-query billing     | Free — earn credits by contributing |
| Privacy                | Query logs stored     | No central query recording    |
| Availability           | Vendor-dependent      | Self-hosted, offline-capable  |
| Protocol               | REST / proprietary    | MCP (native LLM integration)  |
| Architecture           | Centralized SaaS      | Fully decentralized P2P       |

### Do I need to run a server?

No. InfoMesh runs as a local process on your machine. Use `uvx infomesh mcp` for
MCP mode, or `infomesh start` for the full node with P2P, crawling, and the dashboard.

---

## Installation & Prerequisites

### What are the system requirements?

- **Python 3.12+** (required)
- **uv** package manager (recommended) — install via `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **ffplay** or **mpv** — optional, only needed for dashboard background music
- **psutil** — optional, for system resource monitoring in the dashboard
- Internet connection for first install and crawling (local search works offline)

#### System packages for P2P (optional)

The `p2p` extra (`libp2p`) includes C extensions (`fastecdsa`, `coincurve`, `pynacl`) that need native build tools.
These are **not** required for the base install — only for `uv sync --extra p2p` or `pip install infomesh[p2p]`.

**Linux (Debian / Ubuntu):**
```bash
sudo apt-get update && sudo apt-get install -y build-essential python3-dev libgmp-dev
```

**macOS:**
```bash
brew install gmp
```

**Windows:** Use WSL2 (recommended) or install Visual Studio Build Tools + GMP.

### How do I install InfoMesh?

**Quickest (no permanent install):**
```bash
uvx infomesh status
```

**Permanent install:**
```bash
uv tool install infomesh        # or: pip install infomesh
```

**From source:**
```bash
git clone https://github.com/dotnetpower/infomesh.git
cd infomesh
uv sync
uv run infomesh start
```

**Docker:**
```bash
docker build -t infomesh .
docker run -p 4001:4001 -p 8080:8080 infomesh
```

### What optional extras are available?

```bash
pip install infomesh[vector]   # ChromaDB vector search
pip install infomesh[p2p]      # libp2p P2P networking
pip install infomesh[llm]      # Local LLM summarization
pip install infomesh[all]      # Everything
```

---

## MCP Integration

### How do I set up MCP in VS Code (GitHub Copilot)?

Add to `.vscode/mcp.json`:
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

### How do I set up MCP in Claude Desktop / Cursor / Windsurf?

Add to `claude_desktop_config.json` (or Cursor/Windsurf equivalent):
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

### "Server not found" error in VS Code

1. Ensure `uv` is installed and in your PATH: `which uv`
2. Try using the absolute path: `"command": "/home/you/.local/bin/uvx"`
3. Check the VS Code MCP output panel for detailed error messages
4. Run `uvx infomesh mcp` manually in a terminal to test

### "No results found" when searching

Your local index is empty. Crawl some pages first:
```bash
infomesh crawl https://docs.python.org --depth 2
```
Or via MCP, use the `crawl_url` tool.

### MCP server exits immediately

Run manually to see the error:
```bash
uvx infomesh mcp
```
If dependencies are missing, install them: `uv sync` (from source) or
`pip install infomesh[all]`.

---

## Search & Crawling

### How does search work?

1. Your query is parsed into keywords
2. Local index is searched first (< 10 ms)
3. If P2P is enabled, keyword hashes are routed via DHT to remote peers (~ 500 ms)
4. Results are merged and ranked by BM25 + freshness + trust
5. Total target latency: ~ 1 second

### Can I search offline?

Yes. Use `web_search` with `local_only=true` (MCP) or `infomesh search --local <query>`.
This searches only your locally cached index.

### What about robots.txt?

InfoMesh **strictly** respects robots.txt. If a site disallows crawling,
InfoMesh will not crawl it. This is enforced at the crawler level and cannot
be disabled.

### What are the crawling rate limits?

- **60 URLs per hour** per node
- **10 pending URLs per domain** at a time
- **Max depth 3** for recursive crawling
- **1-second politeness delay** between requests to the same domain
- **5 concurrent connections** maximum

### How do I add pages to the index?

```bash
# CLI:
infomesh crawl https://example.com --depth 2

# MCP tool:
# Use the crawl_url tool with url="https://example.com" and depth=2
```

---

## Credit System

### How do credits work?

Credits are earned locally by contributing to the network. There is no
blockchain, no money, and no subscription. Earned credits are spent on
search queries.

| Action                | Credits Earned       |
|-----------------------|----------------------|
| Crawling              | 1.0 / page           |
| Query processing      | 0.5 / query          |
| Document hosting      | 0.1 / hour           |
| Network uptime        | 0.5 / hour           |
| LLM summarization     | 1.5 / page           |
| LLM for peers         | 2.0 / request        |

### What does search cost?

| Contribution Score | Search Cost | Description          |
|-------------------|-------------|----------------------|
| < 100             | 0.100       | New / low contributor|
| 100 – 999         | 0.050       | Moderate contributor |
| ≥ 1000            | 0.033       | High contributor     |

### What happens when credits run out?

Search is **never blocked**. The system uses a "Zero-Dollar Debt" model:

1. **NORMAL**: Balance > 0 — standard search cost
2. **GRACE**: Balance ≤ 0, within 72 hours — no penalty
3. **DEBT**: Balance ≤ 0, past 72 hours — 2× search cost

Earn credits by crawling or keeping your node online to recover.

### Can I earn credits by contributing code?

Yes. Merged pull requests earn bonus credits:

| PR Type                  | Credits     |
|--------------------------|-------------|
| Docs / typo fix          | 1,000       |
| Bug fix (with tests)     | 10,000      |
| New feature              | 50,000      |
| Major / architecture     | 100,000     |

---

## Dashboard & BGM

### How do I launch the dashboard?

```bash
infomesh dashboard
# Or jump to a specific tab:
infomesh dashboard --tab credits
```

### What keyboard shortcuts are available?

| Key    | Action              |
|--------|---------------------|
| `1`–`5`| Switch tabs         |
| `/`    | Focus search input  |
| `m`    | Toggle BGM on/off   |
| `r`    | Force refresh       |
| `q`    | Quit dashboard      |
| `?`    | Show help           |

### BGM audio stutters or is choppy

This is usually caused by CPU contention — the audio player subprocess
competes with crawling and indexing for CPU time. Solutions:

1. **BGM is off by default** since v0.1.2. Enable it manually with the `m`
   key or set `bgm_auto_start = true` in `~/.infomesh/config.toml`
2. Use `mpv` instead of `ffplay` — it tends to be lighter
3. Reduce the dashboard refresh interval:
   ```toml
   [dashboard]
   refresh_interval = 2.0
   ```
4. Use the `minimal` resource profile to reduce background task load:
   ```toml
   [resource]
   profile = "minimal"
   ```

### BGM does not play

Make sure **ffplay** (part of ffmpeg) or **mpv** is installed:
```bash
# Debian/Ubuntu:
sudo apt install ffmpeg
# or:
sudo apt install mpv

# macOS:
brew install ffmpeg
# or:
brew install mpv
```

If neither player is found, BGM is silently disabled (no error).

---

## Configuration

### Where is the config file?

`~/.infomesh/config.toml` — auto-created on first run.

### How do I change settings?

Edit the TOML file directly, or use `infomesh config set`:
```bash
infomesh config set crawl.max_concurrent 10
infomesh config set dashboard.theme dracula
infomesh config show
```

### Can I use environment variables?

Yes. Use the format `INFOMESH_{SECTION}_{KEY}`:
```bash
export INFOMESH_CRAWL_MAX_CONCURRENT=20
export INFOMESH_NODE_LISTEN_PORT=5001
```

### What resource profiles are available?

| Profile       | CPU Cores | Memory  | Concurrent Crawls | Use Case            |
|---------------|-----------|---------|--------------------|--------------------|
| `minimal`     | 1         | 512 MB  | 2                  | Low-power devices  |
| `balanced`    | 2         | 2048 MB | 5                  | Default            |
| `contributor` | 4         | 4096 MB | 10                 | Active contributor |
| `dedicated`   | 8+        | 8192 MB | 20                 | Dedicated server   |

### What themes are available?

`catppuccin-mocha` (default), `textual-dark`, `textual-light`, `dracula`,
`tokyo-night`, `monokai`, `nord`, `gruvbox`, `textual-ansi`, `solarized-light`

---

## Network & P2P

### What port does InfoMesh use?

TCP port **4001** for P2P communication. The local admin API uses port **8080**.

### P2P does not find any peers

1. Check port 4001 is open: `infomesh start` includes an automatic port check
2. If behind NAT, configure port forwarding on your router
3. Check firewall rules: `sudo iptables -L -n | grep 4001`
4. Try connecting to specific bootstrap nodes in `~/.infomesh/config.toml`:
   ```toml
   [network]
   bootstrap_nodes = ["/ip4/x.x.x.x/tcp/4001/p2p/PEER_ID"]
   ```

### I am running on WSL2 — peers cannot connect

WSL2 runs inside a NAT'd virtual network. You need both a Windows Firewall
rule and port proxy forwarding.

**Automatic (recommended):**
InfoMesh detects WSL2 automatically on `infomesh start` and performs
smart configuration checks:

1. **Already configured** — If the firewall rule and port proxy both exist
   with the correct WSL2 IP, startup proceeds silently:
   `✓ Firewall rule + port proxy already configured (→ <WSL_IP>:4001)`
2. **Stale IP** — If the port proxy exists but points to an old WSL2 IP
   (common after reboot), InfoMesh auto-updates it without prompting.
3. **Missing** — If either the firewall rule or port proxy is missing,
   InfoMesh offers to configure them. You need a PowerShell with
   Administrator privileges for this step.

**Manual steps (if auto-setup fails):**
1. Open PowerShell as Administrator on Windows:
   ```powershell
   # Allow port 4001 through Windows Firewall
   New-NetFirewallRule -DisplayName "InfoMesh-P2P-4001" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 4001

   # Get WSL IP address
   wsl hostname -I

   # Set up port forwarding (replace <WSL_IP> with the IP from above)
   netsh interface portproxy add v4tov4 listenport=4001 listenaddress=0.0.0.0 connectport=4001 connectaddress=<WSL_IP>
   ```
2. Note: The WSL IP changes on reboot. InfoMesh detects stale IPs and
   auto-updates the port proxy rule on subsequent starts, so manual
   updates are normally not needed.

### How do I open port 4001 on a cloud VM?

InfoMesh auto-detects AWS, Azure, and GCP environments and can
auto-configure firewall rules. If automatic setup fails, use the
cloud-specific CLI:

**Azure:**
```bash
az network nsg rule create -g <RG> --nsg-name <NSG> -n InfoMeshP2P \
  --priority 1100 --destination-port-ranges 4001 --access Allow \
  --protocol Tcp --direction Inbound
```

**AWS:**
```bash
aws ec2 authorize-security-group-ingress --group-id <SG_ID> \
  --protocol tcp --port 4001 --cidr 0.0.0.0/0
```

**GCP:**
```bash
gcloud compute firewall-rules create infomesh-p2p \
  --allow tcp:4001 --direction INGRESS
```

---

## Security

### How are keys managed?

InfoMesh uses **Ed25519** key pairs stored in `~/.infomesh/keys/`.

```bash
infomesh keys show     # View current key fingerprint
infomesh keys rotate   # Generate new key pair (old key is revoked)
```

### "Permission denied on keys" error

```bash
chmod 600 ~/.infomesh/keys/private.key
chmod 644 ~/.infomesh/keys/public.key
```

### What security measures does InfoMesh use?

- **Sybil defense**: PoW node ID generation + subnet diversity limits
- **DHT validation**: All values size-limited (max 1 MB), signed publications
- **SSRF protection**: URL validation, private IP filtering
- **Message size limits**: Network messages capped with safe deserialization
- **Content attestation**: SHA-256 hashes signed by crawling peer
- **Random audits**: Periodic re-crawl verification by independent nodes
- **Unified trust score**: Based on uptime, contribution, audit pass rate, and summary quality

---

## Troubleshooting

### SSL certificate errors during crawling

InfoMesh uses `httpx` with TLS verification. If you encounter SSL errors
for certain sites, the crawler logs a warning and skips that page.
This is expected behavior for sites with expired or self-signed certificates.

### `uv` command not found

Install `uv`:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Then restart your shell or run `source ~/.bashrc`.

### Build fails with `uv build`

Make sure you are in the project root directory and have `hatchling`
as the build backend:
```bash
uv sync --dev
uv build
```

### Tests fail after pulling new changes

```bash
uv sync --dev
uv run pytest tests/ \
  --ignore=tests/test_vector.py \
  --ignore=tests/test_libp2p_spike.py \
  -x -q --tb=short
```

### How do I report a bug?

Open an issue at [github.com/dotnetpower/infomesh/issues](https://github.com/dotnetpower/infomesh/issues)
with:
1. Your OS and Python version (`python --version`)
2. InfoMesh version (`infomesh --version`)
3. Steps to reproduce
4. Error message or log output

---

## Legal & Compliance

### Does InfoMesh respect robots.txt?

Yes, strictly. If a website disallows crawling via robots.txt, InfoMesh
will not crawl it. This cannot be disabled.

### How does DMCA takedown work?

Signed takedown requests propagate via DHT. All nodes must comply within
24 hours and remove the affected content from their local index.

### What about GDPR?

InfoMesh provides distributed deletion records via signed DHT entries.
Nodes can be configured to exclude pages with personal data.

### Are search queries logged?

No. Search queries are processed locally or routed ephemerally through
the P2P network. There is no central server that records queries.
