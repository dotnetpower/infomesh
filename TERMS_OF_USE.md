# InfoMesh — Terms of Use

*Last updated: February 2026*

## 1. Acceptance

By using InfoMesh software ("the Software"), you agree to these terms.

## 2. What InfoMesh Is

InfoMesh is a **decentralized, peer-to-peer search engine** designed for LLMs.
Each node independently crawls, indexes, and searches the web. There is no
central server — your node is both client and server.

## 3. Content and Crawling

### 3.1 Crawled Content
- InfoMesh caches web content **temporarily** for search indexing purposes.
- All crawling respects `robots.txt` directives and site terms of service.
- Cached content is stored locally with a default TTL of 7 days.
- Search results return **snippets only** — not full page content.

### 3.2 Content Responsibility
- You are responsible for content your node crawls and serves.
- Do not configure your node to intentionally crawl illegal content.
- InfoMesh includes a domain blocklist; do not disable it.

### 3.3 Takedown Compliance
- DMCA takedown requests propagated via the DHT must be honored within 24 hours.
- GDPR deletion records in the DHT must be honored immediately.
- Failure to comply may result in trust score reduction and network isolation.

## 4. Network Participation

### 4.1 Fairness
- The credit system rewards contribution and penalizes free-riding.
- Nodes with negative credit balance may be rate-limited.
- Intentional credit farming (e.g., fabricated crawl results) is prohibited.

### 4.2 Trust
- Node trust is computed from four signals: uptime, contribution, audit pass
  rate, and summary quality.
- Nodes failing 3 consecutive random audits are isolated from the network.
- Tampered content (modified hashes) violates network integrity.

### 4.3 Resource Usage
- Configure resource profiles to match your hardware and network capacity.
- Do not exceed reasonable crawl rates for the domains you visit.
- Default limits (60 URLs/hr, 5 Mbps upload) exist for a reason.

## 5. Privacy

- **No central telemetry.** InfoMesh never phones home.
- Search queries stay local or are routed via the DHT with no central logging.
- Your node's crawl history is stored locally in `~/.infomesh/`.
- P2P traffic is encrypted via libp2p noise protocol.

## 6. LLM Summarization

- AI-generated summaries are labeled as such.
- Every summary includes a `content_hash` linking to the original source.
- Original URLs are always provided alongside AI summaries.
- Summaries are not a substitute for the original content.

## 7. No Warranty

InfoMesh is provided "as is" without warranty of any kind. See the
[MIT License](LICENSE) for full terms.

## 8. Changes

These terms may be updated. Significant changes will be announced via the
project repository.

---

*Questions? Open an issue at https://github.com/dotnetpower/infomesh*
