# InfoMesh — Legal Considerations

---

## 1. Overview

As a distributed crawling + indexing system, InfoMesh requires clear rules to minimize  
legal risk while providing a useful search service.

---

## 2. robots.txt Compliance

| Item | Policy |
|------|--------|
| Compliance | **Strictly enforced** — respect opt-out |
| User-Agent | Use dedicated `InfoMesh` UA |
| Crawl-delay | Honor if specified in robots.txt; default 1 sec/domain otherwise |
| Blocked paths | Never crawl `Disallow` paths |
| Sitemap | Use Sitemap directive from robots.txt when available |

### Implementation Principles

- Always check `robots.txt` before crawling any domain
- Cache validity: max 24 hours, re-check after
- On parse failure, act **conservatively** (do not crawl)

---

## 3. Copyright Protection

| Item | Policy |
|------|--------|
| Full text storage | **Cache purpose only** — for search index generation |
| Search result response | Return **snippets only** (with source URL) |
| Cache deletion | Remove cache on robots.txt change or owner request |
| LLM summaries | Treated as **factual information extraction**, not derivative works |

### Implementation Principles

- MCP `search()` results: title + snippet (~200 chars) + URL
- MCP `fetch_page()`: always attribute original source when returning cached text
- Need a takedown process for copyright owner requests

---

## 4. GDPR (Personal Data Protection)

| Item | Policy |
|------|--------|
| Personal data crawling | **Exclusion option** provided |
| Search query logging | **No central logging** — P2P architecture prevents central query collection |
| Data deletion | Remove from local index on request for specific URLs/domains |
| User data | No personal data collection beyond node operator IP |

### Implementation Principles

- Apply personal data detection heuristics during crawling (email, phone patterns, etc.)
- Option to mask personal data in detected pages during indexing
- Nodes in GDPR jurisdictions can enable enhanced filtering in settings

---

## 5. Terms of Service (ToS) Compliance

| Item | Policy |
|------|--------|
| Sites prohibiting crawling | Maintain **blocklist** |
| Blocklist updates | Distributed blocklist sync via DHT |
| Site requests | Add to blocklist on crawl exclusion request |
| Default blocklist | Ship with known prohibited sites |
| ToS auto-detection | Heuristic scan for common ToS patterns beyond robots.txt |

### Implementation Principles

- Integrate blocklist checking in `infomesh/crawler/robots.py`
- Dual management: local + DHT-synced blocklist
- Community reporting system for blocklist updates
- **Default blocklist includes** sites with known crawling prohibitions in ToS (e.g., LinkedIn, Facebook, Instagram)
- **ToS heuristic detection**: Scan page footer / `/terms` / `/tos` URLs during first-time domain crawl for keywords like "automated access prohibited", "scraping forbidden" → flag for human review before continued crawling

---

## 6. LLM Summary Attribution

| Item | Policy |
|------|--------|
| Summary source | Always include `content_hash` linking summary to original content |
| Summary label | Mark LLM-generated summaries as "AI-generated summary" |
| Original access | Always provide link to original source URL alongside summaries |
| Summary verification | Verified summaries (see [Trust & Integrity](07-trust-integrity.md)) are labeled as such |

### Implementation Principles

- Store `content_hash` (SHA-256 of source text) alongside every summary
- MCP search results containing summaries must include `source_url` and `is_ai_summary: true`
- Summaries failing verification (NLI contradiction detected) are discarded, not served

---

## 7. DMCA Takedown Propagation

| Item | Policy |
|------|--------|
| Takedown request | Node operators can submit DMCA-style removal requests |
| Propagation | Signed deletion records published to DHT → all nodes holding the content must comply |
| Verification | Deletion records are signed by the requester; nodes verify before acting |
| Audit compliance | Random audits check that deleted content is no longer served |
| Response time | Nodes must process deletion records within 24 hours of receipt |

### Implementation Principles

- DMCA records use the same attestation chain as content records (Ed25519 signature)
- Deletion record format: `(url, content_hash, reason, requester_signature, timestamp)`
- Nodes that fail to comply after 3 audit checks receive trust score penalties
- Maintain a public `DMCA_LOG` (hashes only, no content) for transparency
- **Persistence**: All takedown notices, acknowledgments, and propagation records are persisted in SQLite (`_TakedownStore`). A node cannot evade DMCA obligations by restarting — records survive across restarts and are loaded on startup.

---

## 8. `fetch_page()` Content Limitations

The MCP `fetch_page()` tool returns full text of a URL from cache or live crawl. This requires careful limitations:

| Item | Policy |
|------|--------|
| Paywall detection | Heuristic detection of login walls / paywalls → return error, not partial content |
| Cache TTL | Cached content expires after 7 days; after expiry, re-crawl or return "stale" warning |
| Original site check | If original site is available, prefer live crawl over stale cache |
| Content size limit | Return max 100KB of text per `fetch_page()` call |
| Attribution | Always include `source_url`, `crawl_timestamp`, and `is_cached: true/false` |

### Implementation Principles

- Paywall indicators: HTTP 402/403, meta tags (`<meta name="robots" content="noarchive">`), login form detection
- If content is from cache and >24h old, include `stale_warning: true` in response
- Never serve `fetch_page()` results for URLs on the blocklist

---

## 9. GDPR Distributed Deletion

In a P2P network, deletion requests must propagate to all nodes holding the data:

| Item | Policy |
|------|--------|
| Deletion request | Signed DHT deletion record for specific URL/content_hash |
| Propagation | Gossip protocol ensures all replica nodes receive the request |
| Verification | Random audits confirm deleted content is purged (same mechanism as DMCA) |
| Scope | Applies to: local index, cached text, DHT pointers, summaries |
| Right to be forgotten | URL-based deletion removes all associated data including summaries |

### Implementation Principles

- Deletion records stored permanently on DHT to prevent re-indexing
- Nodes joining later receive accumulated deletion records during sync
- `infomesh delete --url <URL>` CLI command for local operators
- Log deletion requests (hash only) for audit trail
- **Persistence**: All deletion requests, confirmations, propagation records, and the URL blocklist are persisted in SQLite (`_GDPRStore`). Obligations survive node restarts — blocked URLs remain blocked even after reboot.

---

## 10. Common Crawl Data Usage

| Item | Policy |
|------|--------|
| URL lists | Freely usable under Common Crawl Terms of Use |
| Full content | Subject to original site's copyright — use only for indexing, not redistribution |
| Attribution | Credit Common Crawl as data source in documentation and `status()` |
| Filtering | Apply blocklist and personal data filters to Common Crawl imports |

---

## 11. InfoMesh Project Licensing

| Item | Policy |
|------|--------|
| Source code | Licensed under a permissive open-source license (MIT or Apache 2.0) |
| Terms of use | `TERMS_OF_USE.md` in repository root — covers node operator responsibilities |
| Contributor agreement | Contributors agree to license their contributions under the same license |
| Data responsibility | Each node operator is responsible for compliance with local laws |

### `TERMS_OF_USE.md` should cover:

- Node operators are responsible for respecting robots.txt and blocklists
- No guarantee of search result accuracy or availability
- No liability for content crawled from the web
- Node operators in GDPR jurisdictions must enable enhanced filtering
- Abuse of the network (Sybil attacks, credit farming) may result in network isolation

---

## 12. Summary

```
┌─────────────────────────────────────────────────┐
│            Legal Compliance Checklist            │
│                                                 │
│  ✓ robots.txt strictly enforced                 │
│  ✓ Copyright: cache only, return snippets       │
│  ✓ GDPR: exclude personal data, no central logs │
│  ✓ GDPR: distributed deletion propagation       │
│  ✓ ToS: blocklist + auto-detection heuristics   │
│  ✓ Crawl rate: ≤1 request/second per domain     │
│  ✓ Attribution: always provide source URL       │
│  ✓ LLM summaries: labeled + verified + sourced  │
│  ✓ DMCA: signed takedown propagation via DHT    │
│  ✓ fetch_page(): paywall detection + cache TTL  │
│  ✓ Common Crawl: proper attribution + filtering │
│  ✓ Project: TERMS_OF_USE.md + open-source license│
└─────────────────────────────────────────────────┘
```

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
