# Getting Started — First Run Guide

This guide walks you through installing InfoMesh, running it for the first time,
connecting to the P2P network, and crawling your first pages — all the way to
multi-node peering.

---

## 1. Install InfoMesh

### Option A: Quick run with `uvx` (no install needed)

```bash
uvx infomesh mcp
```

This runs the MCP server directly. Perfect for trying InfoMesh with your IDE.

### Option B: Install with `uv`

```bash
uv tool install infomesh
```

### Option C: Install with `pip`

```bash
pip install infomesh
```

### Option D: Install with P2P support

```bash
pip install "infomesh[p2p]"
```

> **Note**: The `p2p` extra requires native build tools (`build-essential`, `libgmp-dev`
> on Linux; Xcode CLI on macOS). See the [FAQ](12-faq.md) for details.

After installation, verify:

```bash
infomesh --version
# InfoMesh v0.1.x
```

---

## 2. First Start

Run the full node for the first time:

```bash
infomesh start
```

InfoMesh runs a series of first-start checks in order:

```
InfoMesh v0.1.6 starting...
  Peer ID: 12D3KooWAbCdEfGh...
  Data dir: /home/user/.infomesh
```

### Step 1: Git Check

InfoMesh checks whether `git` is installed on your system. Git is used to
auto-detect your GitHub email for cross-node credit aggregation.

**If git is installed:**

```
  ✔ GitHub detected: alice@example.com (from git config)
```

Your email is automatically read from `git config --global user.email`.
No further action needed.

**If git is NOT installed:**

```
  ⚠ git not found — install git to auto-detect your GitHub identity.
    Install: https://git-scm.com/downloads
    Or set manually: infomesh config set node.github_email "you@email.com"
  Enter GitHub email (or press Enter to skip):
```

You can:
- Enter your GitHub email directly, or
- Press Enter to skip (credits will be local-only), or
- Install git later and restart.

### Step 2: GitHub Account Linking

If git is installed but no email is configured (or you haven't set one in
`config.toml`), you will see an interactive prompt:

```
  ⚠ GitHub not linked — credits will be local-only.
  Link your GitHub account now? (enables cross-node credits) [Y/n]: y
  GitHub email: alice@example.com
  ✔ GitHub linked: alice@example.com
    Credits will aggregate across all nodes using this email.
```

**Why link your GitHub?**

- Credits earned on any node are aggregated under your GitHub email.
- If you run InfoMesh on multiple machines, all credits accumulate together.
- Higher contribution scores unlock cheaper search costs.
- You can always set or change it later:

```bash
infomesh config set node.github_email "alice@example.com"
```

### Step 3: Preflight Checks

After identity setup, InfoMesh runs preflight checks:

```
  ⏳ Running preflight checks... ✔
```

This verifies disk space, network connectivity, and port availability.

### Step 4: Key Generation

On first run, Ed25519 key pairs are generated automatically:

```
  Peer ID: 12D3KooWAbCdEfGh...
  Data dir: /home/user/.infomesh
```

Your keys are stored in `~/.infomesh/keys/`. These identify your node on
the P2P network. Keep them safe — they are tied to your node's reputation
and credits.

---

## 3. Bootstrap & Network Connection

After startup, InfoMesh connects to bootstrap nodes to join the P2P network:

```
  Connecting to P2P network...
  ✔ Connected to 3 peers
```

### How bootstrapping works

1. InfoMesh ships with a bundled list of bootstrap nodes (`bootstrap/nodes.json`).
2. Your node contacts these known peers via TCP on port 4001.
3. Through Kademlia DHT, your node discovers additional peers.
4. Once connected, your node participates in the distributed hash table.
5. All successfully connected peers are saved to `~/.infomesh/peer_store.db`.

### What if bootstrap nodes are down?

InfoMesh uses a multi-layer fallback for peer discovery:

1. **Persistent Peer Store** — On restart, previously connected peers are
   loaded from `peer_store.db` and contacted directly. No bootstrap needed.
2. **PEX (Peer Exchange)** — Every 5 minutes, your node asks connected
   peers for their known peers. Discovered peers are saved to the peer store.
3. **mDNS** — Peers on the same LAN are discovered automatically via
   multicast UDP (no internet required).
4. **Manual config** — You can add any known peer's address to `config.toml`.

This means once your node has connected to the network at least once,
it can rejoin even if all bootstrap servers are offline.

### Bootstrap node list

The default bootstrap nodes are loaded automatically. You can also configure
custom bootstrap nodes:

```bash
infomesh config set network.bootstrap_nodes '["/ip4/1.2.3.4/tcp/4001/p2p/12D3KooW..."]'
```

### Firewall

For full P2P participation, open TCP port **4001** (inbound + outbound).
If behind NAT, InfoMesh will still work in client mode but with reduced
connectivity.

---

## 4. Initial Crawling

Once connected, InfoMesh begins crawling based on your configured seeds:

```bash
# Manually crawl specific URLs
infomesh crawl https://docs.python.org

# Or let the auto-crawl loop handle seed URLs
infomesh start   # seeds are crawled automatically
```

### What happens during crawling

1. **URL assignment**: The DHT assigns URL ownership based on `hash(URL)`.
2. **robots.txt check**: Every domain's robots.txt is fetched and respected.
3. **Content extraction**: `trafilatura` extracts the main text content.
4. **Deduplication**: 3-layer dedup (URL normalization → SHA-256 → SimHash).
5. **Indexing**: Content is stored in SQLite FTS5 for keyword search.
6. **DHT publish**: Keyword hashes are published to the DHT so other nodes
   can find your indexed content.

### Seed categories

InfoMesh ships with curated seed lists:

| Category | File | Description |
|----------|------|-------------|
| Tech Docs | `seeds/tech-docs.txt` | Python, MDN, Rust, Go documentation |
| Academic | `seeds/academic.txt` | ArXiv, PubMed, academic sources |
| Encyclopedia | `seeds/encyclopedia.txt` | Wikipedia, encyclopedia sites |
| Quickstart | `seeds/quickstart.txt` | Curated starter set |

Select a category at startup or let it use the default:

```bash
infomesh crawl --category tech-docs
```

### Monitor progress

Use the dashboard to watch crawling in real-time:

```bash
infomesh dashboard
```

Or check stats via CLI:

```bash
infomesh index stats
```

---

## 5. Second Node — Peering

When another user installs InfoMesh on a different machine, the two nodes
automatically discover each other and collaborate.

### Node B installation

On a second machine:

```bash
pip install "infomesh[p2p]"
infomesh start
```

Node B goes through the same first-start flow:

1. Git check + GitHub linking prompt
2. Key generation (unique Peer ID)
3. Bootstrap connection (same bootstrap nodes)
4. DHT discovery — Node B finds Node A (and vice versa)

### What happens when nodes peer

```
Node A                          Node B
  │                               │
  ├── Connected to bootstrap ─────┤
  │                               │
  ├── DHT: discovers Node B ──────┤
  │                               │
  ├── Shares keyword index ───────┤
  │   (hash(keyword) → doc_id)    │
  │                               │
  ├── Receives Node B's index ────┤
  │                               │
  ├── Distributed search works ───┤
  │   (queries routed via DHT)    │
  │                               │
  └── Crawl coordination ─────────┘
      (URLs assigned by hash)
```

### Distributed search

Once peered, searches span both nodes:

```bash
infomesh search "python asyncio tutorial"
```

1. Query keywords are hashed.
2. DHT routes the query to the node(s) closest to each keyword hash.
3. Results from all participating nodes are merged and ranked.
4. Response is returned — typically within ~1 second.

### Crawl collaboration

The DHT prevents duplicate crawling:

- `hash("https://docs.python.org")` maps to a specific node.
- That node "owns" and crawls the URL.
- Other nodes requesting that URL get results from the owner.
- Crawl locks prevent race conditions (5-minute timeout).

---

## 6. Using with MCP (IDE Integration)

InfoMesh is designed to be used as an MCP tool from your IDE:

### VS Code (GitHub Copilot)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "infomesh": {
      "command": "infomesh",
      "args": ["mcp"]
    }
  }
}
```

### Claude Desktop

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "infomesh",
      "args": ["mcp"]
    }
  }
}
```

Once configured, your AI assistant can call tools like `search`, `fetch_page`,
`crawl_url`, and more. See [MCP Integration](10-mcp-integration.md) for the
full tool reference.

---

## 7. Configuration Reference

InfoMesh stores configuration in `~/.infomesh/config.toml`:

```toml
[node]
data_dir = "~/.infomesh"
github_email = "alice@example.com"
role = "full"

[crawl]
max_depth = 3
politeness_delay = 1.0

[network]
listen_port = 4001
bootstrap_nodes = [
  "/ip4/20.42.12.161/tcp/4001/p2p/12D3KooWEXwYVk9amWHKkNAPHsZEpmZ6H811RKrN6aBY3ayEEdty"
]
```

View the current configuration:

```bash
infomesh config show
```

Set individual values:

```bash
infomesh config set node.github_email "alice@example.com"
infomesh config set crawl.max_depth 5
```

---

## Summary — First Run Timeline

```
Install (pip/uv)
    │
    ▼
infomesh start
    │
    ├─ 1. Git check ──── auto-detect GitHub email
    │                     or prompt for manual entry
    │
    ├─ 2. GitHub link ── link account for cross-node credits
    │                     (skip OK, set later via config)
    │
    ├─ 3. Preflight ──── disk, network, port checks
    │
    ├─ 4. Key gen ────── Ed25519 key pair created
    │                     unique Peer ID assigned
    │
    ├─ 5. Bootstrap ──── connect to known nodes
    │                     join Kademlia DHT
    │
    ├─ 6. Crawl ──────── fetch seed URLs
    │                     index content (FTS5)
    │                     publish to DHT
    │
    └─ 7. Ready ──────── searchable locally + via network
                          MCP tools available
                          dashboard at `infomesh dashboard`
```

> **Tip**: Link your GitHub email during first start to earn and accumulate
> credits across all your nodes. Credits never expire and higher contribution
> scores unlock cheaper search costs.
