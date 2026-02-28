# InfoMesh — Architecture

---

## 1. Overall Network Structure

```
    ┌───────────┐     ┌───────────┐     ┌───────────┐
    │  Peer A   │◄───►│  Peer B   │◄───►│  Peer C   │
    │           │     │           │     │           │
    │ ┌───────┐ │     │ ┌───────┐ │     │ ┌───────┐ │
    │ │Crawler│ │     │ │Crawler│ │     │ │Crawler│ │
    │ │Parser │ │     │ │Parser │ │     │ │Parser │ │
    │ │Index  │ │     │ │Index  │ │     │ │Index  │ │
    │ │Router │ │     │ │Router │ │     │ │Router │ │
    │ │MCP API│ │     │ │MCP API│ │     │ │MCP API│ │
    │ │LLM    │ │     │ │       │ │     │ │LLM    │ │
    │ └───────┘ │     │ └───────┘ │     │ └───────┘ │
    └─────┬─────┘     └─────┬─────┘     └─────┬─────┘
          │                 │                 │
          ◄────────────────►◄────────────────►
               DHT (Kademlia) overlay network
```

> Peer B can fully participate in crawling + indexing + search even without LLM.

---

## 2. Internal Peer Structure

```
┌──────────────────────────────────────────────────┐
│                     Peer                          │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌─────┐│
│  │ P2P      │  │ Crawler  │  │ MCP    │  │ LLM ││
│  │ Network  │  │ Engine   │  │ Server │  │(opt)││
│  │          │  │          │  │        │  │     ││
│  │ • DHT    │  │ • HTTP   │  │ • tool │  │ sum ││
│  │ • Gossip │  │ • Parser │  │  search│  │     ││
│  │   (peer  │  │ • robots │  │ • tool │  │     ││
│  │   disc.) │  │ • dedup  │  │  fetch │  │     ││
│  └────┬─────┘  └────┬─────┘  └───┬────┘  └──┬──┘│
│       │              │            │          │   │
│  ┌────▼──────────────▼────────────▼──────────▼──┐│
│  │              Local Index                      ││
│  │    SQLite FTS5 + Vector (ChromaDB)            ││
│  │    + Summary cache (LLM-generated)            ││
│  └───────────────────────────────────────────────┘│
└──────────────────────────────────────────────────┘
```

---

## 3. DHT-Based Distributed Index

```
Traditional P2P DHT:
  info_hash → peer list

InfoMesh DHT:
  keyword_hash → document pointer list (peer_id, doc_id, score)

Example:
  Peer A crawls "rust async tutorial" page
  → Extract keywords: ["rust", "async", "tutorial", "tokio"]
  → hash("rust") = 0x7A3F... → publish to nodes closest to 0x7A3F in DHT
  → "Peer A has document about 'rust', doc_id=xxx, score=0.85"
```

The DHT is Kademlia-based. Node IDs are 160-bit. Distance = XOR metric.

Two use cases:
1. **Inverted index**: `hash(keyword)` → list of `(peer_id, doc_id, score)` pointers
2. **Crawl coordination**: `hash(URL)` → the node closest to the hash "owns" and crawls that URL

Replication factor: N=3 minimum for all stored data.

> **Note on py-libp2p**: The Python libp2p implementation is less mature than Go/Rust versions.
> Mitigation strategies:
> 1. Validate py-libp2p's DHT + NAT traversal in Phase 0 with integration tests
> 2. Fallback option: Use rust-libp2p via PyO3 bindings if py-libp2p proves insufficient
> 3. Simplified alternative: Custom Kademlia implementation over TCP/QUIC if needed

---

## 4. Crawl Coordination (Without Central Server)

```
URLs managed via DHT:
  hash("https://docs.python.org/3/") = 0xABC...
  → Node closest to 0xABC is the "owner" of this URL
  → Owner is responsible for crawling

Workload distribution:
  - 100 nodes → each handles ~1% of all URLs
  - 1,000 nodes → each handles ~0.1%
  - More nodes → less individual burden (auto-scaling)
```

Crawling rules:
- Always strictly respect `robots.txt`
- Default politeness: ≤1 request/second per domain
- Content extraction: `trafilatura` primary, `BeautifulSoup` fallback
- Storage: raw text + metadata (title, URL, crawl timestamp, language)
- **Crawl lock**: Before crawling, publish `hash(url) = CRAWLING` to DHT to prevent multiple nodes crawling the same URL. Lock timeout: 5 minutes.
- **SPA/JS rendering**: Most content is extractable from static HTML. For JavaScript-heavy pages, a `js_required` DHT tag triggers delegation to nodes with Playwright/headless browser capability. Phase 0 (MVP) focuses on static HTML only.
- **Bandwidth limits**: Default ≤5 Mbps upload / 10 Mbps download for P2P traffic. Configurable via `~/.infomesh/config.toml`. Crawl concurrency: max 5 simultaneous connections per node (adjustable).

---

## 5. Indexing

- **Local keyword search**: SQLite FTS5 with BM25 ranking
- **Local vector search**: ChromaDB for semantic/embedding-based queries
  - Default embedding model: `all-MiniLM-L6-v2` (22M params, runs on CPU)
  - Vector search is **optional** — FTS5 keyword search works standalone without embeddings
  - Nodes without vector search can still participate fully in the network
- **Distributed index**: publish keyword hashes to DHT after crawling
- **Content attestation**: On crawl, compute `SHA-256(raw_response)` + `SHA-256(extracted_text)`, sign with peer private key, publish to DHT

---

## 6. Search Flow

```
User → LLM (Copilot) → MCP → Local peer

[Local peer processing]
1. Parse query: "rust async tutorial" → ["rust", "async", "tutorial"]
2. Search local index first (instant results, <10ms)
3. Route keyword hashes via DHT to responsible nodes (~500ms–2s)
4. Receive remote results
5. Merge local + remote results, rank by BM25 + freshness + trust
6. Fetch full text for top-N documents (~200ms–1s)
7. Return results → MCP → LLM summarizes

Expected total latency:
  - Local-only search: <100ms
  - Network search (mature network, 100+ nodes): ~1–2 seconds
  - Network search (early network, <20 nodes): ~2–5 seconds
  - Comparable to commercial search API response times
```

> All P2P messages are serialized with **msgpack** and compressed with **zstd** for minimal bandwidth usage.

---

## 7. Local LLM Summarization

Nodes can **optionally** run a local LLM to generate summaries of crawled content.

- Summaries are stored alongside full text in the local index and shared via DHT
- Nodes with local LLM can also process summarization requests from other peers
- LLM is **not required** — nodes without LLM can fully participate in crawling + indexing + search

### Recommended Models

| Model | Size | Strength |
|-------|------|----------|
| **Qwen 2.5** | 3B / 7B | Best multilingual quality, Apache 2.0 |
| **Llama 3.x** | 3B / 8B | Strongest general-purpose, large community |
| **Gemma 3** | 4B / 12B | Good size-to-performance ratio |
| **Phi-4** | 3.8B / 14B | Strong reasoning/summarization |

### Runtimes

| Runtime | Features |
|---------|----------|
| **ollama** | Simplest, built-in REST API |
| **llama.cpp** | Lightweight, runs on CPU, GGUF quantization |
| **vLLM** | High throughput with GPU |

Minimum spec: 3B Q4 quantized model on CPU with 8GB RAM.

### Energy-Aware Scheduling

- Nodes can configure their local timezone and off-peak electricity hours (e.g., 23:00–07:00)
- LLM-heavy tasks (batch summarization, processing peer requests) are preferentially scheduled during off-peak windows
- **1.5x credit multiplier** on all LLM-related earnings during off-peak hours
- The network routes batch summarization requests preferentially to nodes currently in their off-peak window

---

## 8. Node Lifecycle

### Join

```
1. Connect to bootstrap node list (hardcoded or DNS)
   ※ Bootstrap ≠ Hub. Any existing peer can be one.
2. Kademlia JOIN → discover neighbor nodes
3. Receive assigned URL range based on node ID
4. Download Common Crawl initial data (optional)
5. Start crawling assigned URLs
6. Sync index data from neighbors
```

### Leave

```
1. Neighbor nodes detect heartbeat failure
2. Departed node's range → automatically inherited by DHT-adjacent nodes
3. No data loss due to replicas (N=3~5)
```

### Malicious Node Defense

Basic defenses at the architecture level:

```
1. Cross-verify same URL across multiple nodes
2. Consensus on search results (majority vote)
3. Trust score: longer participation + more contribution = higher trust
4. Results from low-trust nodes receive lower weight
```

> For the comprehensive trust system (content attestation chain, random audits,
> unified trust score, tamper detection, and network isolation), see
> [Trust & Integrity](07-trust-integrity.md).

---

## 8.1 Data Compression

InfoMesh uses **zstd** for all data compression with level-tunable settings:

| Use Case | zstd Level | Rationale |
|----------|-----------|----------|
| Real-time P2P transfer | 1–3 | Speed priority, minimal latency |
| Local index snapshots | 9–12 | Balanced speed/ratio for export |
| Common Crawl archive | 19–22 | Maximum compression for bulk data |

- **Dictionary mode**: Build per-domain dictionaries for repeated structure (e.g., docs.python.org pages share boilerplate)
- Compression is applied to stored text, index snapshots, and P2P message payloads via `msgpack + zstd`

---

## 8.2 Deduplication

Three-layer deduplication prevents wasted crawling and storage:

| Layer | Method | Purpose |
|-------|--------|--------|
| 1. URL normalization | Canonical URL (lowercase, remove tracking params, trailing slash) | Prevent recrawling same page |
| 2. Exact dedup | SHA-256 content hash published to DHT | Detect identical content across different URLs |
| 3. Near-dedup | SimHash with Hamming distance ≤ 3 | Detect minor variations (ads, timestamps) |

> For full deduplication pipeline details, see [Trust & Integrity](07-trust-integrity.md).

---

## 9. MCP Tool Specification

```python
@mcp.tool()
def search(query: str, limit: int = 10) -> list[SearchResult]:
    """Full network search. Merges local + remote results."""

@mcp.tool()
def search_local(query: str, limit: int = 10) -> list[SearchResult]:
    """Local index only search (works offline)."""

@mcp.tool()
def fetch_page(url: str) -> PageContent:
    """Return full text for a URL (instant from index, or live crawl)."""

@mcp.tool()
def crawl_url(url: str, depth: int = 1) -> CrawlResult:
    """Add a URL to the network and crawl it."""

@mcp.tool()
def network_stats() -> NetworkStats:
    """Network status: peer count, index size, credits, etc."""
```

---

## 9.1 Configuration System

All node settings are managed through `~/.infomesh/config.toml`:

```toml
[node]
data_dir = "~/.infomesh/data"
log_level = "info"                  # debug, info, warning, error

[crawl]
max_concurrent = 5                  # simultaneous HTTP connections
politeness_delay = 1.0              # seconds between requests to same domain
max_depth = 3                       # link-following depth limit

[network]
upload_limit_mbps = 5               # P2P upload bandwidth cap
download_limit_mbps = 10            # P2P download bandwidth cap
bootstrap_nodes = ["default"]       # or list of multiaddrs

[index]
vector_search = true                # enable ChromaDB (requires ~500MB RAM)
embedding_model = "all-MiniLM-L6-v2"

[llm]
enabled = false                     # enable local LLM summarization
runtime = "ollama"                  # ollama | llama_cpp | vllm
model = "qwen2.5:3b"
off_peak_start = "23:00"
off_peak_end = "07:00"
timezone = "auto"                   # auto-detect from system

[storage]
max_index_size_gb = 50
compression_level = 3               # zstd level for local storage
encrypt_at_rest = false             # requires SQLCipher
```

Settings can also be overridden via environment variables: `INFOMESH_CRAWL_MAX_CONCURRENT=10`

---

## 9.2 CLI Commands

```bash
# Core commands
infomesh start                      # Start node (crawl + index + MCP server)
infomesh stop                       # Graceful shutdown
infomesh status                     # Node health and statistics

# Search
infomesh search "query"             # Search from terminal
infomesh search --local "query"     # Local-only search

# Management
infomesh config show                # Display current configuration
infomesh config set crawl.max_depth 5
infomesh keys export                # Export keys for backup
infomesh keys rotate                # Rotate node identity key

# Data
infomesh index stats                # Index size, document count
infomesh index export               # Export index snapshot
infomesh index import <file>        # Import index snapshot
```

### `infomesh status` Output

```
$ infomesh status

InfoMesh Node Status
────────────────────────
Node ID:        abc123...def
Uptime:         3d 14h 22m
Trust Score:    0.72 (Normal)
Credit Balance: 847.5

Network:
  Connected Peers: 42
  DHT Entries:     1,284,301

Index:
  Documents:       156,832
  FTS5 Size:       2.3 GB
  Vector Index:    892 MB

Crawling:
  Pages Crawled:   12,481 (today: 342)
  Queue Size:      1,203
  Rate:            ~10 pages/hr

LLM: disabled
```

---

## 10. Scaling Estimates

### Global Web Scale Reference

| Metric | Value |
|--------|-------|
| Surface Web pages | ~5–10 billion |
| Average text size | ~50KB/page |
| Total text data | ~500TB |

### Coverage by Node Count

| Nodes | Storage/Node | Total Coverage | Time Required |
|-------|-------------|---------------|--------------|
| **1** | 50GB | 1M pages | 3–7 days (MVP) |
| **100** | 50GB | 100M pages | 1–2 weeks |
| **1,000** | 50GB | 1B pages | 1 month |
| **10,000** | 50GB | 5B+ pages | 2–3 months |
| **100,000** | 50GB | **Entire web** | Continuously fresh |

> Per-participant burden: ~50GB disk + minimal bandwidth = negligible

### Data Bootstrap: Common Crawl

```
Common Crawl:
  - Non-profit organization crawls the entire web monthly and publishes it
  - ~3–5 billion pages/month
  - Filter and download only needed domains

Usage:
  1. Select "tech docs pack" during install → download relevant Common Crawl data
  2. Instantly build local index → search immediately
  3. Sync additional data from P2P network afterwards
```

---

## 11. Resource Governance & Graceful Degradation

### Resource Profiles

Nodes can select from 4 predefined resource profiles to match their hardware:

| Profile | CPU Limit | Network | Concurrent Crawl | LLM | Use Case |
|---------|----------|---------|------------------|-----|----------|
| **minimal** | 1 core, nice 19 | ↓1/↑0.5 Mbps | 1 | disabled | Laptop, battery |
| **balanced** | 2 cores, nice 10 | ↓5/↑2 Mbps | 3 | off-peak only | Desktop (**default**) |
| **contributor** | 4 cores, nice 5 | ↓10/↑5 Mbps | 5 | active | Always-on server |
| **dedicated** | unlimited | ↓50/↑25 Mbps | 10 | active + peer requests | Dedicated infra |

### Dynamic Resource Governor

The `ResourceGovernor` monitors system load and dynamically adjusts operations:
- CPU > 80% → throttle crawling by 50%
- CPU < 30% → restore crawling within profile limits
- Network > 90% of limit → reduce P2P traffic by 30%

### Graceful Degradation Levels

Under extreme load, the node degrades services in stages:

| Level | Condition | Behavior |
|-------|-----------|----------|
| 0 (Normal) | All metrics within limits | Full functionality |
| 1 (Warning) | CPU or memory elevated | Disable LLM summarization, pause new crawling |
| 2 (Overload) | Resources heavily strained | Disable remote search, respond local-only |
| 3 (Critical) | Near resource exhaustion | Read-only mode, stop indexing |
| 4 (Defense) | System at risk | Enforced rate limiting, local search only |

---

*Related docs: [Overview](01-overview.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md)*
