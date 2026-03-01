# InfoMesh — Trust & Integrity

---

## 1. Overview

Ensuring data accuracy and reliability **without central authority** is a core challenge in distributed systems.  
InfoMesh must address four trust problems:

| Problem | Core Threat |
|---------|------------|
| Duplicate documents | Same content exists across multiple URLs/nodes → index quality degradation |
| LLM summary verification | Summaries may be inaccurate or contain hallucinations |
| Document tampering | Malicious nodes modify crawled content to spread misinformation |
| Crawler seed strategy | No index can be built without initial crawl targets |

---

## 2. Deduplication

### 2.1 How Duplicates Occur

| Type | Example |
|------|---------|
| URL variations | `www.example.com` vs `example.com`, trailing `/` |
| Content mirrors | Same article published on multiple sites (syndication) |
| Near-duplicates | Only timestamps, ads, or sidebars differ |
| Race conditions | Multiple nodes crawl simultaneously before DHT assignment |

### 2.2 Three-Layer Deduplication

```
┌─────────────────────────────────────────────────┐
│        Layer 1: URL Normalization (instant)      │
│  Remove www, normalize trailing /, sort query    │
│  params, follow <link rel="canonical">           │
├─────────────────────────────────────────────────┤
│        Layer 2: Exact Duplicates (hash)          │
│  SHA-256 of extracted text → publish content_hash│
│  to DHT. Same hash = exact duplicate → keep only │
│  pointer to original                             │
├─────────────────────────────────────────────────┤
│        Layer 3: Near-Duplicates (fingerprint)    │
│  SimHash/MinHash document fingerprinting         │
│  Hamming distance ≤ 3 → near-duplicate           │
│  Group and keep oldest/most-trusted as canonical │
└─────────────────────────────────────────────────┘
```

### 2.3 DHT Crawl Lock

```
Before crawling:
  1. Normalize URL → determine canonical URL
  2. Publish to DHT: hash(canonical_url) = "CRAWLING" + peer_id + timestamp
  3. Other nodes attempting same URL → check DHT for lock → wait
  4. Crawl complete → update to "DONE" + content_hash
  5. Lock timeout (5 min) → if no response, another node takes over
```

### 2.4 Index-Level Dedup

```python
# Deduplication logic (pseudocode)
class DeduplicationPipeline:
    async def process(self, url: str, content: str) -> DeduplicationResult:
        # 1. URL normalization
        canonical = normalize_url(url)  # remove www, trailing / etc.

        # 2. Exact duplicate check
        content_hash = sha256(content)
        existing = await self.dht.get(f"content:{content_hash}")
        if existing:
            return DeduplicationResult(is_duplicate=True, original=existing)

        # 3. Near-duplicate check
        fingerprint = simhash(content)
        similar = await self.find_similar(fingerprint, threshold=3)
        if similar:
            return DeduplicationResult(is_near_duplicate=True, group=similar)

        # 4. Register new document
        await self.dht.put(f"content:{content_hash}", canonical)
        return DeduplicationResult(is_duplicate=False)
```

---

## 3. LLM Summary Verification

### 3.1 Threat Model

| Threat | Description |
|--------|-------------|
| Hallucination | Generates facts not present in source text |
| Omission | Drops critical information |
| Distortion | Reverses the meaning of the original text |
| Malicious injection | Deliberately inserts incorrect summaries |

### 3.2 Three-Stage Verification Pipeline

```
┌─────────────────────────────────────────────────┐
│  Stage 1: Self-Verification (local, instant)    │
│                                                 │
│  Source → LLM summary → Key-fact anchoring      │
│                       → Contradiction detection  │
│                         (NLI)                    │
│                       → Length/quality check     │
│                                                 │
│  Pass rate < 70% → regenerate (max 3 attempts)  │
├─────────────────────────────────────────────────┤
│  Stage 2: Cross-Validation (network, async)     │
│                                                 │
│  Replica nodes (N=3) with LLM independently     │
│  generate summaries                             │
│  → Compare cosine similarity between summaries  │
│                                                 │
│  Similarity > 0.8 → consensus reached ✓         │
│  Similarity < 0.5 → warning, serve raw text ✗   │
│  In between → adopt majority summary            │
├─────────────────────────────────────────────────┤
│  Stage 3: Reputation-Based Long-Term Trust      │
│                                                 │
│  Cross-validation pass history → LLM trust score│
│  High trust → Stage 1 approval only             │
│  Low trust → always require Stage 2             │
└─────────────────────────────────────────────────┘
```

### 3.3 Key-Fact Anchoring

```
Source: "Python 3.12 was released in October 2023 and introduced the type statement."

Summary: "Python 3.12 was released in 2024 and supports the type statement."
                                    ^^^^
          Mismatch with source → anchoring failure → regenerate

Verification method:
  1. Extract noun phrases, numbers, dates, proper nouns from summary
  2. Search for each fact in source text
  3. Calculate match rate = matched facts / total facts
  4. Match rate ≥ 0.7 → pass
  5. Match rate < 0.7 → regenerate or reject
```

### 3.4 Storage Schema

```python
@dataclass
class SummarizedDocument:
    url: str                    # Source URL
    content_hash: str           # Source text SHA-256
    summary: str                # LLM-generated summary
    summary_hash: str           # Summary SHA-256
    model_id: str               # Model used (e.g., "qwen2.5-7b-q4")
    verification_level: int     # 1=self, 2=cross, 3=reputation
    anchor_score: float         # Key-fact anchoring score (0.0–1.0)
    cross_validation_score: float | None  # Cross-validation score
    summarizer_peer_id: str     # Node that generated the summary
    timestamp: datetime         # Summary generation time
```

---

## 4. Content Integrity (Tampering Prevention)

### 4.1 Threat Model

| Threat | Method | Motivation |
|--------|--------|-----------|
| Content tampering | Modify text after crawling | SEO spam, political manipulation, phishing |
| Fake crawling | Inject fake data without actual crawling | Credit fraud |
| Selective censorship | Intentionally omit certain content | Information manipulation |
| Summary tampering | Correct source + manipulated summary | Subtle misinformation |

### 4.2 Content Attestation Chain

```
During crawling:
  ┌────────────────────────────────────────────────┐
  │ 1. Receive raw HTTP response                    │
  │ 2. raw_hash = SHA-256(HTTP response body)       │
  │ 3. Extract text with trafilatura                │
  │ 4. content_hash = SHA-256(extracted text)        │
  │ 5. attestation = sign(                          │
  │       raw_hash + content_hash + url + timestamp,│
  │       peer_private_key                          │
  │    )                                            │
  │ 6. Publish to DHT:                              │
  │    (url, content_hash, attestation, peer_id)    │
  └────────────────────────────────────────────────┘

Verification:
  - Any node re-crawls the same URL
  - Checks for same raw_hash / content_hash
  - Mismatch → suspected tampering → trust score penalty
```

### 4.3 Random Audit System

```
Audit frequency: ~1 per node per hour (random interval)

Process:
  1. Randomly select audit target (node + URL) via DHT
  2. 3 audit nodes independently crawl the same URL
  3. Compare content_hash: original node vs audit nodes
  4. Result handling:

  ┌──────────────────┬────────────────────────┐
  │ Audit Result     │ Action                 │
  ├──────────────────┼────────────────────────┤
  │ 3/3 match        │ Pass ✓ trust +0.01     │
  │ 2/3 match        │ Possible site change   │
  │ 1/3 or 0/3 match │ Suspected tampering    │
  │                  │ → trust -0.2           │
  │ Repeated (3x+)   │ Network isolation      │
  └──────────────────┴────────────────────────┘

  ※ Also considers whether the source site genuinely changed
     → large timestamp difference = natural update
```

### 4.4 Unified Trust Score

```
Trust(node) = w1 × uptime_score
            + w2 × contribution_score
            + w3 × audit_pass_rate
            + w4 × summary_quality_score

Weights (defaults):
  w1 = 0.15  (uptime)
  w2 = 0.25  (contribution volume)
  w3 = 0.40  (audit pass rate) ← most important
  w4 = 0.20  (summary quality)

Trust tiers:
  ┌──────────┬───────────┬──────────────────────────────┐
  │ Tier     │ Score     │ Network Treatment            │
  ├──────────┼───────────┼──────────────────────────────┤
  │ Trusted  │ ≥ 0.8     │ Priority results, fewer audits│
  │ Normal   │ 0.5–0.8   │ Standard treatment           │
  │ Suspect  │ 0.3–0.5   │ Warning tag, more audits     │
  │ Untrusted│ < 0.3     │ Results ignored, isolation   │
  └──────────┴───────────┴──────────────────────────────┘
```

### 4.5 Ultimate Defense: Source URL

> Any node's data can be **verified by re-crawling the source URL**.  
> InfoMesh doesn't "create" content — it "fetches" it.  
> As long as the source site exists, tampering is always detectable.  
> This is a structural advantage InfoMesh has over centralized search engines.

---

## 5. Network-Level Security

### 5.1 Sybil Attack Defense

A Sybil attack creates many fake identities to gain disproportionate DHT influence.

| Defense Layer | Mechanism |
|--------------|-----------|
| PoW Node ID | Generating a node ID requires a proof-of-work computation (~30 sec on avg CPU), making mass creation expensive |
| IP Subnet Limit | Max 3 node IDs per /24 subnet in any single DHT bucket |
| Contribution Verification | New nodes start at Trust tier "Normal" (0.5); credit earnings are cross-verified against actual HTTP response hashes |
| Gradual Trust | Trust score increases slowly — takes days/weeks to reach "Trusted" tier, preventing rapid influence accumulation |

```
Node ID generation:
  1. Generate candidate key pair
  2. Compute: nonce such that SHA-256(public_key + nonce) has N leading zeros
  3. N = 20 (adjustable) → ~30 seconds on average CPU
  4. Publish (public_key, nonce) to DHT → any node can verify
```

### 5.2 Eclipse Attack Defense

An Eclipse attack surrounds a target node with malicious peers to control its view of the network.

| Defense Layer | Mechanism |
|--------------|-----------|
| Multiple Bootstrap Sources | Connect to ≥3 independent bootstrap nodes on startup |
| Routing Table Diversity | Enforce subnet diversity in k-buckets: max 2 nodes per /16 subnet per bucket |
| Periodic Routing Refresh | Refresh routing table every 30 min by querying random IDs in each bucket range |
| Neighbor Verification | Periodically verify neighbors via independent path (query same key through different routes) |

### 5.3 DHT Index Poisoning Defense

Malicious nodes may publish false keyword→document mappings to pollute search results.

| Defense Layer | Mechanism |
|--------------|-----------|
| Per-Keyword Rate Limit | Max 10 publish operations per keyword hash per node per hour |
| Content Hash Verification | Published pointers must include `content_hash`; querying nodes can verify by fetching and hashing |
| Signed Publications | All DHT publish operations are signed with the publisher's key; unsigned entries are rejected |
| Consensus Validation | For popular keywords, query multiple responsible nodes and take intersection of results |

```
DHT publish validation:
  1. Receive publish request: (keyword_hash, peer_id, doc_id, score, content_hash, signature)
  2. Verify signature against peer_id's public key
  3. Check rate limit: peer_id published ≤10 entries for this keyword_hash in the last hour
  4. Optionally: fetch doc from peer_id, verify SHA-256 matches content_hash
  5. Accept or reject
```

### 5.4 Credit Farming Prevention

Malicious nodes may attempt to earn credits without providing genuine value.

| Attack Vector | Defense |
|--------------|---------|
| Fake crawling | Random audits verify `raw_hash` against live re-crawl; mismatch = trust penalty |
| Self-referral loops | Detect cyclic URL patterns (A→B→A); cap credits for same-domain crawling |
| Timestamp manipulation | Off-peak LLM bonus verified via IP geolocation cross-check (±2 hour tolerance) |
| Sybil credit splitting | Credit transfers between nodes in same /24 subnet are flagged for review |

Additional measures:
- **New node probation**: First 24 hours → higher audit frequency (1/15min vs 1/hr)
- **Statistical anomaly detection**: Flag nodes with crawl rates >3σ above network average
- **Raw HTTP hash**: Store `SHA-256(raw_response)` — auditors compare against independent re-crawl

### 5.5 Key Management

| Aspect | Policy |
|--------|--------|
| Key generation | Ed25519 key pair generated on first run |
| Storage | `~/.infomesh/keys/` directory, file permissions 0600 |
| Backup | User-initiated export: `infomesh keys export` → encrypted key file |
| Rotation | Supported via `infomesh keys rotate`; old key signs handover to new key on DHT |
| Revocation | On compromise: publish signed revocation record to DHT; network stops accepting old key within ~1 hour (gossip propagation) |

### 5.6 `crawl_url()` Abuse Prevention

The MCP `crawl_url()` tool could be abused to overwhelm the network with requests.

| Limit | Value | Rationale |
|-------|-------|-----------|
| Per-node rate limit | 60 URLs/hour | Prevents flood from single node |
| Per-domain queue limit | 10 pending URLs/domain | Prevents targeted overload of specific sites |
| Depth limit | max `depth=3` | Prevents exponential link explosion |
| Blocklist check | Before crawling | Reject URLs matching blocklist/robots.txt before queuing |

### 5.7 Local Data Security

| Aspect | Approach |
|--------|----------|
| SQLite encryption | Optional SQLCipher integration for index-at-rest encryption |
| Key storage | OS keychain integration (libsecret on Linux, Keychain on macOS) when available |
| Temp files | Crawled raw responses are deleted after indexing; never persisted to disk unencrypted |
| Network traffic | All P2P connections encrypted via libp2p TLS/Noise protocol |

---

## 6. Crawler Seed Strategy

### 6.1 The Cold Start Problem

When a new node starts:
- Index is empty
- No knowledge of which URLs to crawl
- May not yet be connected to P2P network

### 6.2 Hierarchical Seed Strategy

```
┌──────────────────────────────────────────────────┐
│         Seed Priority (top to bottom)            │
│                                                  │
│  Layer 1: Curated seed lists (bundled)            │
│           → Verified high-quality sources         │
│                                                  │
│  Layer 2: Common Crawl URL lists                  │
│           → Download by selected category         │
│                                                  │
│  Layer 3: DHT-assigned URLs                       │
│           → Receive assigned URLs after P2P join  │
│                                                  │
│  Layer 4: User submissions (crawl_url MCP tool)   │
│           → LLM requests specific URL crawling    │
│                                                  │
│  Layer 5: Link following                          │
│           → Auto-register links found in crawled  │
│             pages                                 │
└──────────────────────────────────────────────────┘
```

### 6.3 Bundled Seed Categories

| Category | Seed Source | Example Domains | Est. Size |
|----------|-----------|----------------|----------|
| **Tech docs** | Official docs, API refs | docs.python.org, developer.mozilla.org, docs.rs, go.dev | ~2GB |
| **Academic** | Open-access journals | arxiv.org, pubmed.ncbi.nlm.nih.gov, semanticscholar.org | ~5GB |
| **Encyclopedia** | Wiki family | en.wikipedia.org, wikidata.org | ~8GB |
| **News** | RSS-based | Major wire services, tech news | ~1GB |
| **Government** | Public data | data.gov, data.go.kr | ~2GB |
| **Open source** | README, Wiki | github.com, gitlab.com | ~3GB |
| **Directory** | Curated listings | curlie.org (DMOZ successor) | ~1GB |

### 6.4 Interactive Setup on Install

```
$ uv run infomesh start

🔍 InfoMesh Initial Setup

What domains are you interested in? (multi-select)

  [x] Tech docs (Python, JS, Rust, Go, ...)      ~2GB
  [ ] Academic papers (arXiv, PubMed, ...)        ~5GB
  [x] Encyclopedia (Wikipedia)                     ~8GB
  [ ] News (RSS feeds)                             ~1GB
  [ ] Government/Public data                       ~2GB
  [x] Open source (GitHub README/Wiki)             ~3GB
  [ ] Everything (Common Crawl subset)             ~50GB

Selected: Tech docs, Encyclopedia, Open source (~13GB)

Downloading... ████████████░░░░ 70%
Building local index...
Joining P2P network...

✅ Ready! MCP server is running.
```

### 6.5 Crawl Expansion Algorithm

```
priority_queue = PriorityQueue()

# 1. Inject seed URLs
for url in seed_urls:
    priority_queue.push(url, priority=SEED_PRIORITY)

# 2. BFS + priority-based crawling
while not priority_queue.empty():
    url = priority_queue.pop()

    # Check DHT ownership
    owner = await dht.get_owner(url)
    if owner != self.peer_id:
        await dht.register_url(url)  # notify owner
        continue

    # Crawl
    page = await crawl(url)

    # Extract links + assign priorities
    for link in page.extract_links():
        priority = calculate_priority(link, page)
        priority_queue.push(link, priority)

def calculate_priority(link, source_page):
    score = 0.0

    # Same-domain internal link → high priority (depth exploration)
    if same_domain(link, source_page.url):
        score += 3.0

    # External link matching seed category → medium
    if matches_seed_category(link):
        score += 2.0

    # External link reference frequency → more references = higher
    score += link_reference_count(link) * 0.1

    # Blocked by robots.txt or blocklist → skip
    if is_blocked(link):
        return -1  # do not crawl

    return score
```

### 6.6 Crawling Starts from URLs, Not Search Terms

> Important: InfoMesh's crawler **does not start from search terms**.
>
> Traditional search engines show results based on user queries,
> but the crawler itself explores the web **based on URLs** regardless of queries.
>
> ```
> Crawler: URL → download page → extract text → index keywords → extract links → repeat
> Search:  user query → match against index → return results
> ```
>
> Therefore "what search terms does the crawler start with?" is the wrong question.
> The right question: **"What URLs does the crawler start from?"** → That's the seed strategy.

---

## 7. Implementation Roadmap Mapping

| Feature | Phase | Priority |
|---------|-------|----------|
| URL normalization + exact dedup (SHA-256) | 0 (MVP) | Required |
| Bundled seed lists + category selection | 0 (MVP) | Required |
| Link-following crawling | 0 (MVP) | Required |
| SimHash near-duplicate detection | 1 | High |
| Common Crawl URL list import | 1 | High |
| Content hash DHT publishing | 2 | Required |
| DHT crawl lock | 2 | Required |
| LLM self-verification (key-fact anchoring) | 3 | Required |
| Content attestation chain + signing | 3 | Required |
| LLM cross-validation | 3 | High |
| Random audit system | 3 | High |
| Unified trust score | 3 | Required |
| LLM reputation-based trust | 4 | Medium |
| PoW node ID generation (Sybil defense) | 2 | Required |
| Routing table diversity (Eclipse defense) | 2 | Required |
| DHT publish rate limiting (poisoning defense) | 2 | High |
| Credit farming detection + new node probation | 3 | Required |
| Key management (Ed25519 + rotation) | 2 | Required |
| `crawl_url()` rate limiting | 0 (MVP) | Required |
| SQLCipher optional encryption | 4 | Medium |

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
