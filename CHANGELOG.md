# Changelog

All notable changes to InfoMesh will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-03-01

### Added — Search Intelligence

- **NLP Query Processing** — stop-word removal (9 languages), synonym expansion,
  natural language query parsing (`"python tutorials in korean"` →
  filters by language automatically)
- **Did-you-mean Suggestions** — edit-distance spelling correction when no results found
- **Related Search Tracking** — suggests related queries based on search history
- **Search Facets** — domain, language, and date-range facet counts per query
- **Result Clustering** — groups search results by domain for organized browsing
- **Snippet Highlighting** — query terms wrapped in `**bold**` within result snippets
- **Result Deduplication** — Jaccard similarity-based dedup removes near-identical results
- **Search Explain** — transparent score breakdowns (BM25, freshness, trust, authority)
  for every result via `explain` MCP tool
- **Query Pipeline Trace** — shows each step of query processing with timing

### Added — RAG & Answer Extraction

- **RAG Output Formatter** — `search_rag` MCP tool returns chunked, source-attributed
  context windows optimized for LLM consumption
- **Answer Extraction** — `extract_answer` MCP tool pulls direct answers from search
  results with confidence scores and source attribution
- **Fact Checking** — `fact_check` MCP tool cross-references claims against indexed
  sources with supporting/contradicting evidence
- **Entity Extraction** — identifies persons, organizations, URLs, emails in text
- **Toxicity Filtering** — optional content safety scoring for search results
- **Chain-of-Thought Re-ranking** — prompt templates for LLM-based result re-ranking
- **Summary Prompt Builder** — generates structured summarization prompts from results

### Added — Crawler Enhancements

- **PDF Text Extraction** — extracts text from crawled PDF documents
- **Structured Data Extraction** — parses JSON-LD, OpenGraph, and meta tags from HTML
- **Language Detection** — script-based + word-frequency language identification
  (supports en, ko, ja, zh, de, fr, es, pt, ru)
- **RSS/Atom Feed Discovery** — auto-discovers and parses RSS/Atom feeds from pages
- **Content Change Detection** — computes diffs between crawl versions with change ratios
- **WARC Export** — exports crawled content in WARC (Web ARChive) format
- **Code Block Extraction** — extracts `<pre><code>` blocks with language detection
- **Table Extraction** — extracts HTML tables into structured data (CSV/dict export)

### Added — Data Quality & Trust

- **Freshness Indicators** — every result tagged with age label and freshness grade
  (Fresh/Recent/Aging/Stale/Archival)
- **Trust Grades** — unified A+/A/B/C/D/F grading for peer trust scores
- **Citation Extraction** — detects DOI, ISBN, arXiv, RFC, and URL citations in text
- **Cross-Reference Fact Check** — validates claims against multiple indexed sources

### Added — MCP Tools (6 new)

| Tool | Description |
|------|-------------|
| `explain` | Score breakdown for search results (BM25, freshness, trust components) |
| `search_history` | View/clear past search queries with latency stats |
| `search_rag` | RAG-optimized output with chunked, source-attributed context |
| `extract_answer` | Direct answer extraction with confidence and source |
| `fact_check` | Cross-reference claims against indexed sources |
| Total MCP tools | **15** (was 9) |

### Added — API & Security

- **OpenAPI 3.1 Spec** — auto-generated API specification at `/openapi-spec`
- **Prometheus Metrics** — `/metrics` endpoint with counters, gauges, histograms
- **Rate Limiting** — configurable per-client QPM with token bucket algorithm
- **API Key Management** — create, validate, revoke, rotate API keys programmatically
- **Role-Based Access** — Admin/Reader/Crawler roles with per-tool permission matrix
- **IP Allow/Block Lists** — configurable IP filtering for API access
- **Webhook Signatures** — HMAC-SHA256 signed webhook payloads for verification
- **Audit Logging** — SQLite-backed audit trail for all MCP tool invocations
- **TLS Configuration** — optional TLS with certificate validation for HTTP transport
- **JWT Token Verification** — bearer token authentication support

### Added — Observability

- **Metrics Collector** — in-process counters, gauges, and histograms
- **Prometheus Export** — standard `/metrics` endpoint format
- **Query Tracing** — distributed trace spans with per-peer latency tracking
- **Grafana Dashboard** — auto-generated Grafana dashboard JSON
- **Alert Rules** — pre-configured alert rule templates for monitoring
- **Benchmarking** — built-in search latency and throughput benchmarks

### Added — Persistence & SDK

- **Persistent Store** — SQLite-backed storage for analytics, webhooks, sessions,
  search history, and user presets
- **Python SDK** — `InfoMeshClient` class with sync/async search, crawl, suggest
- **Session Management** — save/restore search sessions with automatic expiry

### Added — Network Extensions

- **NAT Type Detection** — automatic STUN-based NAT classification
- **DNS Peer Discovery** — resolve peers via DNS SRV/TXT records
- **Geo-Distance Sorting** — Haversine-based peer proximity ranking
- **Partition Detection** — automatic network partition detection with recovery actions
- **Relay Node Selection** — latency-based relay node selection for NAT traversal

### Added — Scalability

- **Connection Pooling** — SQLite connection pool with configurable max connections
- **Batch Ingestion** — bulk document indexing with error tracking
- **Bloom Filter** — probabilistic membership testing for URL deduplication
- **Incremental Index Rebuild** — rebuild indexes without full re-crawl

### Added — Developer Experience

- **Plugin System** — register custom plugins with setup/teardown lifecycle
- **Custom Tokenizer Hook** — pluggable tokenizer for search indexing
- **MCP Tool Guide Generator** — auto-generated tool reference documentation
- **Changelog Generator** — structured changelog from version entries
- **Shell Completions** — bash and zsh completion scripts for CLI
- **Structured Error Catalog** — 20 pre-defined error codes with categories,
  resolutions, and HTTP status mappings

### Added — Integrations

- **LangChain Retriever** — `InfoMeshRetriever` for LangChain pipelines
- **LlamaIndex Reader** — `InfoMeshReader` for LlamaIndex data loading
- **Haystack Document Store** — `InfoMeshDocumentStore` for Haystack pipelines

### Added — Deployment

- **Helm Chart** — Kubernetes deployment with configurable replicas and resources
- **Docker Compose** — multi-container setup with volume persistence
- **systemd Service** — production systemd unit file for Linux servers
- **Terraform Module** — infrastructure-as-code for cloud deployment

### Changed

- MCP API version updated to `2025.1`
- Search formatter now includes freshness labels in both text and JSON output
- Crawler parser enhanced with NLP-based language detection fallback
- Local API extended with `/metrics` and `/openapi-spec` endpoints

### Stats

| Metric | v0.1.3 | v0.2.0 |
|--------|--------|--------|
| Source modules | 96 | 130+ |
| Test files | 50 | 67 |
| Tests passing | 1,161 | 1,307 |
| MCP tools | 9 | 15 |
| Source lines | ~19,500 | ~27,000 |

---

## [0.1.3] — 2026-02-15

### Added

- Phase 5D complete — LLM reputation, timezone verification, PyPI readiness
- Dashboard settings tab with live config editing
- P2P credit verification with Merkle proofs
- GitHub email resolution for cross-node credits
- README with full project documentation

### Changed

- Improved dashboard data caching
- Enhanced trust scoring with summary quality factor

---

## [0.1.2] — 2026-02-01

### Added

- Phase 5B/5C — Merkle Tree integrity, mDNS discovery, key rotation
- Docker support with production Dockerfile
- CONTRIBUTING.md and TERMS_OF_USE.md
- Latency-aware query routing

---

## [0.1.1] — 2026-01-15

### Added

- Phase 5A — Resource governor, auto-recrawl, query cache, load guard
- Pre-flight checks for disk and network
- Bandwidth throttling with token bucket

---

## [0.1.0] — 2026-01-01

### Added

- Initial release — Phases 0–4 complete
- Single-node and P2P crawling, indexing, search
- MCP server with 9 tools
- Credit system with farming detection
- Trust scoring and content attestation
- Console dashboard with 6 tabs
- robots.txt compliance, DMCA/GDPR support
- SQLite FTS5 + optional ChromaDB vector search
- zstd compression for index snapshots
- Common Crawl data import

[0.2.0]: https://github.com/dotnetpower/infomesh/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/dotnetpower/infomesh/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/dotnetpower/infomesh/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/dotnetpower/infomesh/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dotnetpower/infomesh/releases/tag/v0.1.0
