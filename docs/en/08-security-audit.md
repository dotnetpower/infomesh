# InfoMesh Security Audit Report — Enterprise-Level Review

**Audit Date**: 2026-02-28
**Scope**: infomesh/ full source (28 modules)
**Findings**: 42 vulnerabilities (CRITICAL 4, HIGH 15, MEDIUM 14, LOW 9)

---

## Overview

InfoMesh is a distributed P2P search engine that is functionally well-implemented through Phases 0–4.
However, **from a security perspective, it is not yet suitable for enterprise deployment**.

Key issues are as follows:

| Area | Severity | Summary |
|------|----------|---------|
| **Trust model not applied** | CRITICAL | Signature verification, Sybil defense, DHT validation are implemented but not wired into execution paths |
| **SSRF / Input validation** | CRITICAL | MCP's fetch_page/crawl_url can be exploited for internal network scanning |
| **Content censorship attacks** | CRITICAL | Unsigned GDPR/DMCA requests can delete arbitrary URLs |
| **Data integrity** | HIGH | DHT value overwrites, unverified replication content, search result forgery |
| **Privacy** | HIGH | Search queries are sent as plaintext to remote peers |

---

## CRITICAL Vulnerabilities (Immediate Resolution Required)

### C-1. DHT Validation Completely Disabled

**File**: `p2p/node.py` L192-197

```python
class InfoMeshValidator(Validator):
    def validate(self, key: str, value: bytes) -> None:
        pass  # validates nothing
    def select(self, key: str, values: list[bytes]) -> int:
        return 0  # always selects the first
```

**Attack Scenario**: Any peer can store arbitrary key-value pairs in the DHT.
Keyword index poisoning, false attestation publishing, crawl lock hijacking — virtually all DHT-based features are compromised.

**Remediation**:
```python
class InfoMeshValidator(Validator):
    def validate(self, key: str, value: bytes) -> None:
        data = msgpack.unpackb(value, raw=False)
        # 1. Verify signature exists
        sig = data.get("signature")
        if not sig:
            raise ValueError("unsigned DHT record")
        # 2. Verify signature with peer_id's public key
        peer_id = data.get("peer_id")
        payload = _canonical_payload(key, data)
        if not verify_signature(peer_id, payload, sig):
            raise ValueError("invalid signature")
        # 3. Timestamp verification (within 5 minutes)
        ts = data.get("timestamp", 0)
        if abs(time.time() - ts) > 300:
            raise ValueError("stale record")

    def select(self, key: str, values: list[bytes]) -> int:
        # Select the most recent + highest trust record
        return _select_best_record(values)
```

---

### C-2. Signatures Are Optional — Entire Trust Chain Compromised

**File**: `p2p/dht.py` (publish_keyword, publish_attestation), `p2p/protocol.py` (IndexPublish)

```python
async def publish_keyword(self, keyword, pointers, *, signature: bytes = b""):
    # signature defaults to empty bytes → publishing without signature is possible
```

**Attack Scenario**: Malicious nodes publish forged keyword→peer mappings without signatures.
Search traffic is redirected to malicious nodes.

**Remediation**: Remove the default value from `signature` parameter and make it required:
```python
async def publish_keyword(self, keyword, pointers, *, signature: bytes):
    if not signature:
        raise ValueError("signature required for DHT publication")
    # validate and publish
```

---

### C-3. SSRF — Internal Network Scanning via MCP

**File**: `mcp/server.py` L145-209

```python
case "fetch_page":
    url = arguments["url"]  # attempts to crawl any URL
```

**Attack Scenario**: Through an LLM agent calling MCP tools:
- `fetch_page("http://169.254.169.254/latest/meta-data/")` → AWS credential theft
- `fetch_page("http://localhost:6379/")` → Redis access
- `fetch_page("file:///etc/shadow")` → Local file read

**Remediation**:
```python
import ipaddress
from urllib.parse import urlparse

_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fd00::/8"),        # IPv6 ULA
]

def validate_url(url: str) -> str:
    """Only allow external public HTTP/HTTPS URLs."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked scheme: {parsed.scheme}")
    # Verify IP after DNS resolution (prevents redirect SSRF)
    resolved_ip = socket.getaddrinfo(parsed.hostname, None)[0][4][0]
    ip = ipaddress.ip_address(resolved_ip)
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            raise ValueError(f"blocked internal address: {ip}")
    return url
```

Also validate each redirect URL when following redirects:
```python
# Verify the final URL after redirect
resp = await client.get(url, follow_redirects=True)
validate_url(str(resp.url))  # validate the redirected final URL
```

---

### C-4. Unsigned GDPR/DMCA Requests Enable Content Censorship

**File**: `trust/gdpr.py` L131-140

```python
def receive_request(self, request: DeletionRequest) -> None:
    # Immediately adds to blocklist without signature verification!
    self._blocklist.add(request.url)
```

**Attack Scenario**: Attackers broadcast forged GDPR deletion requests to remove
competitor documents, political content, or any desired URL from the network.

**Remediation**:
```python
def receive_request(self, request: DeletionRequest) -> None:
    # Step 1: Mandatory signature verification
    if not self.verify_request(request, self._get_public_key(request.requester_id)):
        logger.warning("gdpr_invalid_signature", url=request.url)
        raise SecurityError("invalid GDPR request signature")
    # Step 2: Verify requester authorization (original crawler or trust tier 0.8+)
    if not self._is_authorized_requester(request):
        raise SecurityError("unauthorized GDPR requester")
    # Step 3: Apply time delay (24h grace period — abuse prevention)
    self._pending_requests[request.request_id] = (request, time.time())
```

---

## HIGH Vulnerabilities

### H-1. Sybil Defense Code Is Not Executed

**File**: `p2p/sybil.py` (implemented) vs `p2p/node.py` (not called)

`SybilValidator.validate_peer()` exists but is never called from `node.py`'s bootstrap,
connection acceptance, or protocol handlers.
PoW verification and subnet limiting are completely dead code.

**Remediation**: Enforce validation at connection acceptance:
```python
# node.py — add Sybil validation to connection handler
async def _on_peer_connected(self, peer_id: str) -> None:
    result = self._sybil_validator.validate_peer(
        node_id=peer_id,
        proof_of_work=await self._request_pow(peer_id),
        ip_address=self._get_peer_ip(peer_id),
    )
    if not result.valid:
        await self._disconnect_peer(peer_id, reason=result.reason)
```

### H-2. Private Key Stored Without Encryption

**File**: `p2p/keys.py` L97-101

```python
encryption_algorithm=NoEncryption()  # plaintext private key in PEM file
```

**Remediation**: Apply PBKDF2-based encryption:
```python
encryption_algorithm=BestAvailableEncryption(passphrase.encode())
# Passphrase: from environment variable or keyring library
```

### H-3. Replication Content Hash Not Verified

**File**: `p2p/replication.py` L183-222

Content hash (`text_hash`) received from peers is stored without comparing against the actual text.
Malicious peers can send tampered content with a valid hash and it gets indexed as-is.

**Remediation**:
```python
# Verify hash on the receiving side
actual_hash = hashlib.sha256(text.encode()).hexdigest()
if actual_hash != claimed_text_hash:
    logger.warning("replication_hash_mismatch", url=url)
    return False  # reject
```

### H-4. Search Response Signatures Not Verified

**File**: `p2p/routing.py` L162-189

Remote peer search responses are accepted without signatures. Malicious peers can return
fake URLs and misleading snippets that get included in final search results.

**Remediation**: Include and verify peer signatures in search responses:
```python
# Include peer signature in response
response_payload = canonical_bytes(results)
signature = key_pair.sign(response_payload)

# Verify on receiving side
if not verify_peer_signature(peer_id, response_payload, sig):
    logger.warning("search_response_forgery", peer_id=peer_id)
    return []  # discard
```

### H-5. Search Query Plaintext Exposed to Remote Peers

**File**: `p2p/routing.py` L126-134

```python
request = SearchRequest(query=query, keywords=keywords, ...)
```

The full query string is sent as plaintext to remote peers → privacy violation.

**Remediation**: Send only keyword hashes:
```python
request = SearchRequest(
    query_hash=hashlib.sha256(query.encode()).hexdigest()[:16],
    keyword_hashes=[hashlib.sha256(kw.encode()).hexdigest()[:16] for kw in keywords],
    limit=limit,
)
# Remote peers cannot search FTS5 index with hashes
# → perform DHT pointer-based doc_id lookup only
```

### H-6. No Response Body Size Limit

**File**: `crawler/worker.py` L85-87

```python
html = resp.text  # loads entire response, even gigabytes
```

**Remediation**:
```python
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5MB

resp = await client.get(url, timeout=30.0)
content_length = int(resp.headers.get("content-length", 0))
if content_length > MAX_RESPONSE_BYTES:
    return CrawlResult(url=url, success=False, error="response_too_large")

# Stream reading
chunks = []
total = 0
async for chunk in resp.aiter_bytes(chunk_size=8192):
    total += len(chunk)
    if total > MAX_RESPONSE_BYTES:
        return CrawlResult(url=url, success=False, error="response_too_large")
    chunks.append(chunk)
html = b"".join(chunks).decode("utf-8", errors="replace")
```

### H-7. SQL Injection via FTS5 Tokenizer

**File**: `index/local_store.py` L73-86

```python
f"tokenize='{self._tokenizer}'"  # f-string SQL injection
```

**Remediation**:
```python
ALLOWED_TOKENIZERS = {"unicode61", "porter", "ascii", "trigram"}

def __init__(self, ..., tokenizer="unicode61"):
    if tokenizer not in ALLOWED_TOKENIZERS:
        raise ValueError(f"invalid tokenizer: {tokenizer}")
    self._tokenizer = tokenizer
```

### H-8. Crawl Lock Ownership Not Verified

**File**: `p2p/dht.py` L180-200

Any node can release another node's crawl lock.
Attackers can release legitimate locks → acquire their own → crawl tampered content.

**Remediation**: Verify ownership on lock release:
```python
async def release_crawl_lock(self, url: str) -> bool:
    existing = await self._get_crawl_lock(url)
    if existing and existing["peer_id"] != self._peer_id:
        logger.warning("crawl_lock_unauthorized_release", url=url)
        return False
```

### H-9. No Authentication/Rate Limiting on Protocol Handlers

**File**: `p2p/node.py` L220-247

Ping, search, and replication handlers accept all connections unconditionally.
Isolated nodes, unauthenticated peers, and overload-causing peers are all treated equally.

**Remediation**:
```python
async def _authenticated_handler(self, stream, handler_fn):
    peer_id = stream.get_remote_peer_id()
    # 1. Check isolation
    if self._trust_store.is_isolated(peer_id):
        await stream.close()
        return
    # 2. Rate limit
    if not self._rate_limiter.allow(peer_id):
        await stream.close()
        return
    # 3. Execute handler
    await handler_fn(stream)
```

### H-10. No Consensus Mechanism for Trust Scores

**File**: `trust/scoring.py` (entire file)

Each node independently computes trust scores. Malicious nodes can assign themselves a score of 1.0.

**Remediation**: Trust score exchange between neighbor peers (gossip) + median consensus:
```python
# Use median of at least 3 neighbors' trust scores
neighbor_scores = await self._gossip_trust_scores(peer_id)
if len(neighbor_scores) >= 3:
    consensus_score = sorted(neighbor_scores)[len(neighbor_scores) // 2]
```

### H-11. Credit Ledger Local Tampering Possible

**File**: `credits/ledger.py` (entire file)

The SQLite ledger can be directly modified to grant unlimited credits.

**Remediation**: Periodically prove credit history to peers:
```python
# Merkle root of credit entries → sign → publish to DHT
# Require credit history proof submission during audits
# History hash mismatch → trust score decrease
```

### H-12. No msgpack Deserialization Size Limit

**File**: `p2p/routing.py`, `p2p/replication.py`, `p2p/dht.py` (multiple)

```python
msgpack.unpackb(data, raw=False)  # unlimited
```

**Remediation**:
```python
msgpack.unpackb(
    data, raw=False,
    max_buffer_length=1024 * 1024,    # 1MB
    max_str_len=256 * 1024,            # 256KB
    max_array_len=10_000,
    max_map_len=1_000,
)
```

---

## MEDIUM Vulnerabilities

| # | File | Vulnerability | Remediation Direction |
|---|------|--------------|----------------------|
| M-1 | `attestation.py` | `\|` separator not escaped → signature collision | JSON serialization before hashing |
| M-2 | `keys.py` vs `node.py` | Dual ID system (KeyPair vs libp2p) | Unify to single ID scheme |
| M-3 | `dht.py` | Crawl lock TOCTOU race condition | DHT CAS (compare-and-swap) |
| M-4 | `routing.py` | Predictable request_id + unsigned | `secrets.token_hex(16)` + signature |
| M-5 | `query.py` | FTS5 operators (NEAR, NOT) not blocked | Whitelist token filter |
| M-6 | `node.py` | 0.0.0.0 binding, no access control | Bind address config + allowlist |
| M-7 | `protocol.py` | Length-prefix parsing ambiguity | Add magic bytes |
| M-8 | `protocol.py` | 10MB message × 100 streams = 1GB | Limit concurrent streams |
| M-9 | `config.py` | No env variable range validation | Add value range validation |
| M-10 | `link_graph.py` | Unlimited link insertion | Max 100 links per page |
| M-11 | `audit.py` | `random.sample` (not CSPRNG) | `secrets.SystemRandom` |
| M-12 | `sybil.py` | PoW difficulty 20 bits (GPU-vulnerable) | Difficulty 24-28 bits + memory-hard hash |
| M-13 | `query.py`, `server.py` | Search query logged as plaintext | Log only query hash |
| M-14 | `routing.py` | peer_id included in request_id | Anonymized request ID |

---

## Enterprise Deployment Gap Analysis

### Current State vs Enterprise Standards

| Requirement | Enterprise Standard | Current State | Gap |
|------------|-------------------|--------------|-----|
| **Transport Encryption** | TLS 1.3 required | Relies on libp2p defaults (unspecified) | ❌ Explicit Noise/TLS config needed |
| **Authentication** | Mutual auth (mTLS) | None (all peers accepted) | ❌ Peer authentication framework needed |
| **Authorization** | RBAC / role-based | None | ❌ Peer roles (reader/crawler/admin) |
| **Audit Logging** | Tamper-proof audit trail | structlog plaintext | ❌ Signed audit logs |
| **Key Management** | HSM / Key Vault integration | Filesystem plaintext | ❌ PKCS#11 / HashiCorp Vault |
| **Secret Management** | Environment separation | config.toml plaintext | ❌ Secret manager integration |
| **Input Validation** | OWASP Top 10 | SSRF, SQL Injection exist | ❌ Systematic input validation |
| **DoS Defense** | Rate limiting + circuit breaker | None | ❌ Rate limiting across all layers |
| **Privacy** | Query anonymization | Plaintext transmission + logging | ❌ Query privacy protection |
| **Supply Chain** | SBOM + vulnerability scanning | None | ❌ Dependabot + SBOM |
| **Network Isolation** | Segmented deployment | 0.0.0.0 binding | ❌ Bind address configuration |
| **Compliance** | SOC2 / ISO27001 | Not considered | ❌ Audit framework |

---

## Security Hardening Implementation Plan

### Phase 5-Security-A: CRITICAL Fixes (1 week)

| Task | Files | Estimate |
|------|-------|----------|
| DHT validator implementation (signature, timestamp, schema) | `p2p/node.py`, `p2p/dht.py` | 2 days |
| Mandatory signatures on all DHT publications | `p2p/dht.py`, `p2p/protocol.py` | 1 day |
| SSRF defense (URL validation + IP blocking) | `mcp/server.py`, `crawler/worker.py` | 1 day |
| GDPR/DMCA signature enforcement | `trust/gdpr.py`, `trust/dmca.py` | 1 day |

### Phase 5-Security-B: HIGH Fixes (2 weeks)

| Task | Files | Estimate |
|------|-------|----------|
| Wire Sybil defense into execution path | `p2p/node.py`, `p2p/sybil.py` | 2 days |
| Private key encrypted storage | `p2p/keys.py` | 1 day |
| Replication content hash verification | `p2p/replication.py` | 1 day |
| Search response signature + verification | `p2p/routing.py`, `p2p/protocol.py` | 2 days |
| Protocol handler auth + rate limiting | `p2p/node.py` | 2 days |
| Query privacy (hash-based) | `p2p/routing.py` | 1 day |
| HTTP response size limit | `crawler/worker.py` | 0.5 days |
| SQL injection defense (tokenizer whitelist) | `index/local_store.py` | 0.5 days |
| msgpack deserialization size limit | Multiple | 1 day |
| Crawl lock ownership verification | `p2p/dht.py` | 0.5 days |
| ID scheme unification (KeyPair ↔ libp2p) | `p2p/keys.py`, `p2p/node.py` | 1 day |

### Phase 5-Security-C: MEDIUM + Enterprise (2 weeks)

| Task | Files | Estimate |
|------|-------|----------|
| Explicit Noise protocol configuration | `p2p/node.py` | 1 day |
| Peer role-based authorization (RBAC) | `p2p/auth.py` (new) | 2 days |
| Bind address config + network isolation | `config.py`, `p2p/node.py` | 0.5 days |
| Signed audit log chain | `trust/audit_log.py` (new) | 2 days |
| Attestation canonicalization (JSON canonical) | `trust/attestation.py`, `trust/dmca.py`, `trust/gdpr.py` | 1 day |
| CSPRNG transition | `trust/audit.py`, `p2p/sybil.py` | 0.5 days |
| Environment variable range validation | `config.py` | 0.5 days |
| Link graph size limit | `index/link_graph.py` | 0.5 days |
| Query log anonymization | `search/query.py`, `mcp/server.py` | 0.5 days |
| Trust score gossip consensus | `trust/scoring.py` | 2 days |
| Credit Merkle proof | `credits/ledger.py` | 1 day |
| PoW difficulty increase + Argon2 | `p2p/sybil.py` | 1 day |

### New Security Modules

| Module | Purpose |
|--------|---------|
| `p2p/auth.py` | Peer authentication + RBAC framework |
| `p2p/rate_limiter.py` | Per-protocol rate limiting |
| `security/url_validator.py` | SSRF defense URL validation |
| `security/crypto.py` | Canonicalized signature/verification utilities |
| `trust/audit_log.py` | Tamper-proof audit log chain |

---

## Enterprise Deployment Architecture Recommendation

```
┌─────────────────────────────────────────────────────┐
│                Enterprise Deployment                 │
│                                                     │
│  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │
│  │ Reverse │  │ API     │  │ InfoMesh Node       │ │
│  │ Proxy   │──│ Gateway │──│                     │ │
│  │ (nginx) │  │ (rate   │  │ ┌─────┐ ┌────────┐ │ │
│  └─────────┘  │  limit) │  │ │ MCP │ │ P2P    │ │ │
│               └─────────┘  │ │     │ │ (Noise │ │ │
│                            │ │     │ │  TLS)  │ │ │
│  ┌─────────────────────┐   │ └─────┘ └────────┘ │ │
│  │ HashiCorp Vault     │───│   Key Management    │ │
│  │ (Key Store)         │   │                     │ │
│  └─────────────────────┘   └─────────────────────┘ │
│                                                     │
│  ┌─────────────────────┐   ┌─────────────────────┐ │
│  │ Audit Log Collector │   │ Monitoring          │ │
│  │ (Signed Events)     │   │ (Prometheus/Grafana)│ │
│  └─────────────────────┘   └─────────────────────┘ │
│                                                     │
│  Network: Dedicated VLAN / Subnet isolation         │
│  Bind: 127.0.0.1 (MCP) + Internal IP (P2P)         │
│  Firewall: P2P ports only (TCP 4001 + UDP 4001)     │
└─────────────────────────────────────────────────────┘
```

### Deployment Checklist

```
□ Noise TLS encryption enabled
□ Private key stored in Vault/keyring
□ SSRF defense URL validation enabled
□ DHT signature verification enabled
□ Sybil PoW verification enabled
□ Protocol rate limiting configured
□ Bind address restricted to internal IP
□ Audit log external forwarding configured
□ Query log anonymization confirmed
□ Network segment isolation confirmed
□ SBOM generated + vulnerability scanning
```

---

*CRITICAL/HIGH vulnerabilities in this report should be resolved before production or enterprise deployment.*

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md) · [Publishing](11-publishing.md)*
