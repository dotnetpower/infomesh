# InfoMesh — Project Overview

> **A fully decentralized P2P network that crawls, indexes, and searches the web,
> delivering real-time information to LLMs via MCP (Model Context Protocol) — an open-source project**

---

## 1. Background & Motivation

### Problem

| Current Solution | Limitation |
|-----------------|------------|
| Paid search APIs | Per-request billing, wrapping someone else's search engine |
| Commercial search APIs | Paid, API limits, optimized for human UI |
| Self-crawling | Server cost, single point of failure, maintenance burden |

### Mission: Democratizing Search for LLMs

InfoMesh does **not** aim to compete with existing commercial search providers.
These companies have built world-class search infrastructure over decades — web-scale crawling,
sophisticated ranking, knowledge graphs, ads platforms, and rich UIs for billions of users.
InfoMesh has no intention of replicating or disrupting any of that.

Instead, InfoMesh pursues a fundamentally different goal:

> **Provide minimal, sufficient search capabilities for LLMs — for free, via MCP —
> so that anyone can give their AI assistant real-time web access without paying per query.**

| Aspect | Commercial Search Engines | InfoMesh |
|--------|--------------|----------|
| Target user | Humans (browsers) | LLMs (MCP protocol) |
| Business model | Ads, API billing | **None — free, open-source** |
| Search quality | World-class | **Sufficient for LLM context** |
| Scale | Billions of pages | **Modest — community-contributed** |
| Relationship | N/A | **Complementary, not competitive** |

Commercial search engines solve _human_ search at massive scale and monetize through advertising.
InfoMesh solves _LLM_ search at minimal scale and monetizes through nothing — it is a
community-driven public utility. The two serve entirely different audiences with
entirely different economics.

### Insight

```
The search paradigm has shifted in the LLM era:

  Before: Human → Search Engine → 10 blue links + images + ads + UI → Manual judgment
  Now:    LLM → Text API → Pure text results → LLM understands/synthesizes instantly

70% of what major search engines built over 20 years is "for humans" — UI/UX/ads
LLMs only need the remaining 30% — crawling + indexing + text delivery
→ The barrier to entry drops dramatically
```

### Differentiation: Existing P2P Search vs InfoMesh

| | Y*** (2004) | InfoMesh |
|--|-------------|----------|
| Consumer | Humans (browser) | **LLMs (MCP)** |
| UI | Web search page required | **Not needed** |
| Ranking quality | Critical weakness | **LLM compensates** |
| Installation | Java + complex setup | **`uv run infomesh start` one-liner** |
| Participation motive | Ideological (privacy) | **Practical (free search MCP)** |
| Data bootstrap | Crawl from scratch | **Common Crawl** |
| Content integrity | None | **Crypto attestation + random audits** |

---

## 2. Vision

```
uv run infomesh start
```

One command:
1. Join the P2P network
2. Automatically start web crawling (contribute)
3. Search local + network index
4. MCP server auto-starts → instant use from Copilot/Claude

---

## 3. Core Principles

| Principle | Description |
|-----------|-------------|
| **Fully Decentralized** | No central server. Every node = Hub + Node |
| **LLM-First** | No human UI. Focus on pure text API |
| **Contribute = Reward** | More crawling → more search quota (cooperative tit-for-tat model) |
| **Offline-Capable** | Local index searchable without internet |
| **Privacy** | Search queries are never recorded centrally |

---

## 4. Competitive Comparison

| | SaaS Search API A | SaaS Search API B | Commercial Search API | **InfoMesh** |
|--|--------|---------------|------------|-------------|
| Cost | Per-request billing | Per-request billing | Per-request billing | **Free** |
| Data | Wrapping others' APIs | Own + third-party | Own | **Own P2P** |
| Privacy | Queries sent externally | Queries sent externally | Queries sent externally | **Local processing** |
| SPOF | Yes | Yes | Yes | **None** |
| Offline | No | No | No | **Yes** |
| Customization | Limited | Limited | Limited | **Full freedom** |
| Scalability | Dependent | Dependent | Dependent | **Scales with participants** |
| Trust/Integrity | Opaque | Opaque | Opaque | **Crypto-attested, auditable** |
| Compression | N/A | N/A | N/A | **zstd (level-tunable)** |

---

## 5. VS Code MCP Setup (for users)

After installation, add to `.vscode/settings.json`:

```json
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "infomesh",
        "args": ["mcp"],
        "env": {}
      }
    }
  }
}
```

Use directly in Copilot Chat:

```
"Search for the latest async patterns in Rust"
→ InfoMesh MCP searches the P2P network in real-time
→ Copilot summarizes the results
```

---

## 6. Known Challenges & Mitigations

| Challenge | Risk Level | Mitigation Strategy |
|-----------|-----------|---------------------|
| **Chicken-and-egg problem** | High | Common Crawl bootstrap + bundled seed packs → useful from day one even with 1 node |
| **py-libp2p maturity** | Medium | Phase 0 spike test; fallback to rust-libp2p via PyO3 or custom Kademlia |
| **Sybil attacks** | High | PoW node ID + IP subnet limits + gradual trust (see [Trust & Integrity](07-trust-integrity.md)) |
| **DHT poisoning** | Medium | Signed publications + rate limiting + content hash verification |
| **Credit farming** | Medium | New node probation + statistical anomaly detection + raw hash audits |
| **SPA/JS-heavy sites** | Low | Static HTML focus in MVP; Playwright delegation in Phase 4 |
| **Legal compliance** | Medium | Strict robots.txt + ToS auto-detection + DMCA propagation (see [Legal](06-legal.md)) |
| **Search quality with few nodes** | High | Local index provides instant value; quality improves with network growth |

> InfoMesh is designed so that **a single node is already useful** (local crawl + search via MCP).
> Network participation amplifies value but is not required for basic functionality.

---

## 7. Development Status

All core development phases are **complete**:

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | MVP — single-node local crawl + index + MCP + CLI | ✅ Complete |
| 1 | Index sharing — snapshots, Common Crawl, vector search, SimHash | ✅ Complete |
| 2 | P2P network — libp2p, DHT, distributed crawl & index, Sybil/Eclipse defense | ✅ Complete |
| 3 | Quality + incentives — ranking, credits, trust, attestation, audits, LLM | ✅ Complete |
| 4 | Production — link graph, LLM re-ranking, attribution, legal compliance | ✅ Complete |
| 5A | Core stability — resource governor, auto-recrawl, query cache, load guard | ✅ Complete |
| 5B | Search quality — latency-aware routing, Merkle Tree integrity | ✅ Complete |
| 5C | Release readiness — Docker, key rotation, mDNS, LICENSE, CONTRIBUTING | ✅ Complete |
| 5D | Polish — LLM reputation, timezone verification, dashboard, PyPI readiness | ✅ Complete |
| 6 | Search intelligence, RAG, security, observability, SDK, integrations, DX | ✅ Complete |

### v0.2.0 Highlights

The latest release adds **100+ features** organized into:

- **Search Intelligence** — NLP query processing (9 languages), spelling correction, facets, clustering, snippet highlighting
- **RAG & Answer Extraction** — Chunked RAG output, direct answer extraction, fact checking, entity extraction
- **Crawler Enhancements** — PDF extraction, RSS/Atom feeds, structured data (JSON-LD, OpenGraph), language detection, content diffing
- **Security & API** — API key management, role-based access, audit logging, webhook signatures, Prometheus metrics
- **Developer Experience** — Python SDK, plugin system, LangChain/LlamaIndex/Haystack integrations
- **Deployment** — Helm chart, Docker Compose, systemd service, Terraform modules
- **5 consolidated MCP tools** — `web_search`, `fetch_page`, `crawl_url`, `fact_check`, `status` (legacy names still supported)

See the full [CHANGELOG](../../CHANGELOG.md) for details.

### Future Work

These items require external infrastructure or significant dependencies and are planned for future releases:

- [ ] **Public bootstrap nodes** — volunteer-run seed nodes for easy onboarding
- [ ] **SPA/JS rendering** — Playwright delegation to capable nodes for JavaScript-heavy sites
- [ ] **Multi-language stemming** — language-specific tokenization and stemming
- [ ] **Web dashboard** — optional browser UI alongside the TUI
- [ ] **Semantic search fusion** — BM25 + vector hybrid ranking with RRF

---

*Related docs: [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
