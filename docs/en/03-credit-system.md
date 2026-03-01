# InfoMesh — Credit System

---

## 1. Overview

Credits are tracked locally per peer — no blockchain.

Contribution = Crawling + Index hosting + Query processing + LLM summarization  
Reward = Search API usage quota

```
┌──────────────────────────────────────┐
│  Search only without contributing?   │
│    → Rate limited                    │
│  Contribute crawling?                │
│    → Plenty of searches              │
│  Contribute a lot?                   │
│    → Priority response               │
│  LLM summarization?                  │
│    → Bonus credits                   │
└──────────────────────────────────────┘
```

Credits are exchanged locally between neighbor peers → no global consensus needed.

---

## 2. Generalized Formula

```
C_earned = Σ (W_i × Q_i × M_i)
```

- `W_i` = resource weight for action type `i` (normalized to resource cost)
- `Q_i` = quantity performed
- `M_i` = time multiplier (1.0 default; 1.5 for LLM actions during off-peak hours)

---

## 3. Resource Weights

Weights are normalized so that **crawling = 1.0** as the reference unit.  
All weights reflect approximate relative resource cost (CPU, bandwidth, storage).

| Action | Weight (W) | Category | Rationale |
|--------|-----------|----------|----------|
| Crawling | **1.0** /page | Base | Reference unit: CPU + bandwidth + parsing |
| Query processing | **0.5** /query | Base | Less intensive than full crawl |
| Document hosting | **0.1** /hr | Base | Passive storage + bandwidth |
| Network uptime | **0.5** /hr | Base | Availability value to the network |
| LLM summarization (own) | **1.5** /page | LLM | Higher compute, capped to not dominate |
| LLM request (for peers) | **2.0** /request | LLM | Serving others, higher network value |
| Git PR — docs/typo | **1,000** /merged PR | Bonus | Documentation or typo fix |
| Git PR — bug fix | **10,000** /merged PR | Bonus | Bug fix with tests |
| Git PR — feature | **50,000** /merged PR | Bonus | New feature implementation |
| Git PR — major/architecture | **100,000** /merged PR | Bonus | Core architecture or major feature |

---

## 4. Time Multiplier (M)

| Condition | Multiplier | Applies To |
|-----------|-----------|------------|
| Normal hours, Base actions | `M = 1.0` | Always |
| Normal hours, LLM actions | `M = 1.0` | LLM |
| **Off-peak hours, LLM actions** | **`M = 1.5`** | LLM only |

- Off-peak window is set per node (default: 23:00–07:00 local time)
- Base actions are never affected by time multiplier — LLM bonus never disadvantages non-LLM nodes
- The network routes batch summarization requests preferentially to nodes currently in their off-peak window

### Off-Peak Abuse Prevention

| Risk | Mitigation |
|------|-----------|
| Timezone manipulation | IP geolocation cross-check with ±2 hour tolerance; mismatch → M=1.0 forced |
| VPN timezone spoofing | Reduced max multiplier (1.3x instead of 1.5x) if IP geolocation is inconclusive |
| Clock manipulation | Compare node-reported time against network median; >2hr deviation → flag |

---

## 5. Search Cost

```
C_search = 0.1 / tier(contribution_score)
```

The more you contribute, the cheaper your searches:

| Tier | Contribution Score | Search Cost | Description |
|------|-------------------|-------------|-------------|
| 1 | < 100 | 0.100 | New / low contributor |
| 2 | 100 – 999 | 0.050 | Moderate contributor |
| 3 | ≥ 1000 | 0.033 | High contributor |

---

## 6. Fairness Guarantee

### Non-LLM Node Protection

A **crawling-only node** (no LLM) crawling 10 pages/hr:

```
Credits/hr = (1.0 × 10 × 1.0) + (0.5 × 1 × 1.0) = 10.5 credits/hr
              crawling            uptime

Searches/hr = 10.5 / 0.1 = 105 searches/hr (worst tier)
              10.5 / 0.033 = 318 searches/hr (best tier)
```

→ Even without LLM, **more than enough searches** are available.

### Git Contribution Example

A contributor who merges a bug-fix PR to the InfoMesh repo:

```
Git credits = 10,000 × 1 × 1.0 = 10,000 credits (Bonus, M always 1.0)
Searches = 10,000 / 0.1 = 100,000 searches (worst tier)
         = 10,000 / 0.033 = 303,030 searches (best tier)
```

| PR Type | Credits | Searches (Tier 1) | Searches (Tier 3) |
|---------|---------|-------------------|-------------------|
| docs/typo | 1,000 | 10,000 | 30,303 |
| bug fix | 10,000 | 100,000 | 303,030 |
| feature | 50,000 | 500,000 | 1,515,151 |
| major | 100,000 | 1,000,000 | 3,030,303 |

> Git contribution credits are **one-time bonuses** per merged PR. They are additive with ongoing crawling/uptime credits.
> PR type is determined by the maintainer at merge time via labels.

### Contribution Score Accumulation

`contribution_score` determines your search cost tier. It is the **lifetime total** of all earned credits:

```
contribution_score = Σ (all C_earned since node creation)
```

| Milestone | How to Reach | Time Estimate |
|-----------|-------------|---------------|
| Tier 1 → Tier 2 (100) | Crawl 100 pages | ~10 hours at 10 pages/hr |
| Tier 2 → Tier 3 (1000) | Crawl 1000 pages | ~4 days at 10 pages/hr |
| Fast track | 1 docs PR + 80 pages crawled | 1,000 + 80 = 1,080 → Tier 3 immediately |

### LLM Weight Cap

LLM-related earnings are designed to never exceed ~60% of a node's total credits:

```
10 pages crawled + 10 pages summarized:
  Base credits: 10.0 + 0.5 = 10.5
  LLM credits: 15.0
  LLM ratio: 15.0 / 25.5 = 58.8% ← within cap
```

> LLM is a **bonus** for the network, not a **requirement** for participation.

### Design Principles Summary

| Principle | Implementation |
|-----------|---------------|
| Normalization | Crawling = 1.0 baseline, proportional to resource cost |
| LLM cap | LLM earnings ≤ 60% |
| Non-LLM protection | 100+ searches/hr with crawling only |
| Off-peak bonus | Time multiplier (M) applies to LLM only, no impact on Base |
| Uptime reward | Rewards always-on nodes regardless of hardware |
| Free-rider prevention | Rate-limited when net credit balance is negative |
| Contribution priority | Higher score → higher trust + query routing priority |
| Credit farming defense | New node probation + statistical anomaly detection (see [Trust & Integrity](07-trust-integrity.md#54-credit-farming-prevention)) |
| `crawl_url()` rate limit | 60 URLs/hr per node, 10/domain pending queue, max depth=3 |

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md) · [FAQ](12-faq.md)*
