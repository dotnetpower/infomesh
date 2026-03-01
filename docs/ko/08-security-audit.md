# InfoMesh 보안 감사 보고서 — 엔터프라이즈 수준 검토

**감사 일자**: 2026-02-28
**범위**: infomesh/ 전체 소스 (28개 모듈)
**발견 사항**: 42개 취약점 (CRITICAL 4, HIGH 15, MEDIUM 14, LOW 9)

---

## 개요

InfoMesh는 분산 P2P 검색 엔진으로서 기능적으로 Phase 0~4까지 잘 구현되어 있으나,
**보안 관점에서는 엔터프라이즈 배포에 적합하지 않은 상태**입니다.

핵심 문제는 다음과 같습니다:

| 영역 | 심각도 | 요약 |
|------|--------|------|
| **신뢰 모델 미적용** | CRITICAL | 서명 검증, Sybil 방어, DHT 검증이 구현만 되고 실행 경로에 연결되지 않음 |
| **SSRF / 입력 검증** | CRITICAL | MCP의 fetch_page/crawl_url이 내부 네트워크 스캔에 악용 가능 |
| **콘텐츠 검열 공격** | CRITICAL | 무서명 GDPR/DMCA 요청으로 임의 URL 삭제 가능 |
| **데이터 무결성** | HIGH | DHT 값 덮어쓰기, 복제 콘텐츠 미검증, 검색 결과 위조 |
| **프라이버시** | HIGH | 검색 쿼리가 원문 그대로 원격 피어에 전송됨 |

---

## CRITICAL 취약점 (즉시 해결 필요)

### C-1. DHT 검증이 완전히 비활성화

**파일**: `p2p/node.py` L192-197

```python
class InfoMeshValidator(Validator):
    def validate(self, key: str, value: bytes) -> None:
        pass  # 아무것도 검증하지 않음
    def select(self, key: str, values: list[bytes]) -> int:
        return 0  # 항상 첫 번째 선택
```

**공격 시나리오**: 어떤 피어든 DHT에 임의의 키-값을 저장 가능.
키워드 인덱스 오염, 거짓 attestation 발행, 크롤 락 탈취 등 사실상 모든 DHT 기반 기능이 무력화됨.

**해결**:
```python
class InfoMeshValidator(Validator):
    def validate(self, key: str, value: bytes) -> None:
        data = msgpack.unpackb(value, raw=False)
        # 1. 서명 존재 확인
        sig = data.get("signature")
        if not sig:
            raise ValueError("unsigned DHT record")
        # 2. peer_id의 공개키로 서명 검증
        peer_id = data.get("peer_id")
        payload = _canonical_payload(key, data)
        if not verify_signature(peer_id, payload, sig):
            raise ValueError("invalid signature")
        # 3. 타임스탬프 검증 (5분 이내)
        ts = data.get("timestamp", 0)
        if abs(time.time() - ts) > 300:
            raise ValueError("stale record")

    def select(self, key: str, values: list[bytes]) -> int:
        # 가장 최신 + 가장 높은 신뢰도 레코드 선택
        return _select_best_record(values)
```

---

### C-2. 서명이 선택사항 — 전체 신뢰 체인 무력화

**파일**: `p2p/dht.py` (publish_keyword, publish_attestation), `p2p/protocol.py` (IndexPublish)

```python
async def publish_keyword(self, keyword, pointers, *, signature: bytes = b""):
    # signature 기본값이 빈 바이트 → 서명 없이 발행 가능
```

**공격 시나리오**: 공격 노드가 위조된 키워드→피어 매핑을 서명 없이 DHT에 발행.
검색 트래픽을 악성 노드로 리다이렉트.

**해결**: `signature` 매개변수의 기본값을 제거하고 필수로 변경:
```python
async def publish_keyword(self, keyword, pointers, *, signature: bytes):
    if not signature:
        raise ValueError("signature required for DHT publication")
    # 자동 검증 후 발행
```

---

### C-3. SSRF — MCP에서 내부 네트워크 스캔 가능

**파일**: `mcp/server.py` L145-209

```python
case "fetch_page":
    url = arguments["url"]  # 어떤 URL이든 크롤링 시도
```

**공격 시나리오**: LLM 에이전트를 통해 MCP 도구 호출:
- `fetch_page("http://169.254.169.254/latest/meta-data/")` → AWS 자격증명 탈취
- `fetch_page("http://localhost:6379/")` → Redis 접근
- `fetch_page("file:///etc/shadow")` → 로컬 파일 읽기

**해결**:
```python
import ipaddress
from urllib.parse import urlparse

_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # 링크로컬 / 클라우드 메타데이터
    ipaddress.ip_network("127.0.0.0/8"),     # 루프백
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fd00::/8"),        # IPv6 ULA
]

def validate_url(url: str) -> str:
    """외부 공개 HTTP/HTTPS URL만 허용."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked scheme: {parsed.scheme}")
    # DNS 해석 후 IP 확인 (리다이렉트 SSRF 방지)
    resolved_ip = socket.getaddrinfo(parsed.hostname, None)[0][4][0]
    ip = ipaddress.ip_address(resolved_ip)
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            raise ValueError(f"blocked internal address: {ip}")
    return url
```

또한 httpx 클라이언트에서 리다이렉트 따라가기 시에도 각 리다이렉트 URL을 검증:
```python
# 리다이렉트 후 최종 URL도 검증
resp = await client.get(url, follow_redirects=True)
validate_url(str(resp.url))  # 리다이렉트된 최종 URL 검증
```

---

### C-4. 무서명 GDPR/DMCA 요청으로 콘텐츠 검열

**파일**: `trust/gdpr.py` L131-140

```python
def receive_request(self, request: DeletionRequest) -> None:
    # 서명 검증 없이 즉시 블록리스트에 추가!
    self._blocklist.add(request.url)
```

**공격 시나리오**: 공격자가 위조된 GDPR 삭제 요청을 유포하여
경쟁사의 문서, 정치적 콘텐츠 등 원하는 URL을 네트워크에서 삭제.

**해결**:
```python
def receive_request(self, request: DeletionRequest) -> None:
    # 1단계: 서명 필수 검증
    if not self.verify_request(request, self._get_public_key(request.requester_id)):
        logger.warning("gdpr_invalid_signature", url=request.url)
        raise SecurityError("invalid GDPR request signature")
    # 2단계: 요청자 권한 확인 (원 크롤러 또는 신뢰 티어 0.8+)
    if not self._is_authorized_requester(request):
        raise SecurityError("unauthorized GDPR requester")
    # 3단계: 시간 지연 적용 (24시간 유예 — 악용 방지)
    self._pending_requests[request.request_id] = (request, time.time())
```

---

## HIGH 취약점

### H-1. Sybil 방어 코드가 실행되지 않음

**파일**: `p2p/sybil.py` (구현 완료) vs `p2p/node.py` (호출 없음)

`SybilValidator.validate_peer()`가 존재하지만 `node.py`의 부트스트랩,
연결 수락, 프로토콜 핸들러 어디에서도 호출되지 않음.
PoW 검증과 서브넷 제한이 완전히 죽은 코드.

**해결**: 연결 수락 시점에 강제 검증:
```python
# node.py — 연결 핸들러에 Sybil 검증 추가
async def _on_peer_connected(self, peer_id: str) -> None:
    result = self._sybil_validator.validate_peer(
        node_id=peer_id,
        proof_of_work=await self._request_pow(peer_id),
        ip_address=self._get_peer_ip(peer_id),
    )
    if not result.valid:
        await self._disconnect_peer(peer_id, reason=result.reason)
```

### H-2. 비밀키 암호화 없이 파일 저장

**파일**: `p2p/keys.py` L97-101

```python
encryption_algorithm=NoEncryption()  # PEM 파일에 평문 비밀키
```

**해결**: PBKDF2 기반 암호화 적용:
```python
encryption_algorithm=BestAvailableEncryption(passphrase.encode())
# 패스프레이즈: 환경변수 또는 키링(keyring 라이브러리) 사용
```

### H-3. 복제 콘텐츠 해시 미검증

**파일**: `p2p/replication.py` L183-222

피어로부터 받은 복제 콘텐츠의 `text_hash`를 실제 텍스트와 비교하지 않고 그대로 저장.
악성 피어가 변조된 콘텐츠를 정상 해시와 함께 전송하면 그대로 인덱싱됨.

**해결**:
```python
# 수신 측에서 해시 검증
actual_hash = hashlib.sha256(text.encode()).hexdigest()
if actual_hash != claimed_text_hash:
    logger.warning("replication_hash_mismatch", url=url)
    return False  # 거부
```

### H-4. 검색 응답 서명 미검증

**파일**: `p2p/routing.py` L162-189

원격 피어의 검색 응답을 서명 없이 수락. 악성 피어가 가짜 URL, 오해를 유발하는
스니펫을 반환하면 최종 검색 결과에 그대로 포함됨.

**해결**: 최소한 검색 응답에 피어 서명을 포함하고 검증:
```python
# 응답에 피어 서명 포함
response_payload = canonical_bytes(results)
signature = key_pair.sign(response_payload)

# 수신 측에서 검증
if not verify_peer_signature(peer_id, response_payload, sig):
    logger.warning("search_response_forgery", peer_id=peer_id)
    return []  # 포기
```

### H-5. 검색 쿼리 원문이 원격 피어에 노출

**파일**: `p2p/routing.py` L126-134

```python
request = SearchRequest(query=query, keywords=keywords, ...)
```

전체 쿼리 문자열이 원격 피어에 평문 전송 → 프라이버시 위반.

**해결**: 키워드 해시만 전송:
```python
request = SearchRequest(
    query_hash=hashlib.sha256(query.encode()).hexdigest()[:16],
    keyword_hashes=[hashlib.sha256(kw.encode()).hexdigest()[:16] for kw in keywords],
    limit=limit,
)
# 원격 피어는 해시로 FTS5 인덱스를 검색할 수 없으므로
# → DHT 포인터 기반 doc_id 반환만 수행
```

### H-6. 응답 본문 크기 제한 없음

**파일**: `crawler/worker.py` L85-87

```python
html = resp.text  # 수 GB 응답도 전체 로딩
```

**해결**:
```python
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5MB

resp = await client.get(url, timeout=30.0)
content_length = int(resp.headers.get("content-length", 0))
if content_length > MAX_RESPONSE_BYTES:
    return CrawlResult(url=url, success=False, error="response_too_large")

# 스트리밍으로 읽기
chunks = []
total = 0
async for chunk in resp.aiter_bytes(chunk_size=8192):
    total += len(chunk)
    if total > MAX_RESPONSE_BYTES:
        return CrawlResult(url=url, success=False, error="response_too_large")
    chunks.append(chunk)
html = b"".join(chunks).decode("utf-8", errors="replace")
```

### H-7. SQL Injection via FTS5 토크나이저

**파일**: `index/local_store.py` L73-86

```python
f"tokenize='{self._tokenizer}'"  # f-string SQL 주입
```

**해결**:
```python
ALLOWED_TOKENIZERS = {"unicode61", "porter", "ascii", "trigram"}

def __init__(self, ..., tokenizer="unicode61"):
    if tokenizer not in ALLOWED_TOKENIZERS:
        raise ValueError(f"invalid tokenizer: {tokenizer}")
    self._tokenizer = tokenizer
```

### H-8. 크롤 락 소유권 미검증

**파일**: `p2p/dht.py` L180-200

어떤 노드든 다른 노드의 크롤 락을 해제 가능.
공격자가 정상 노드의 락을 해제 → 자신이 락 획득 → 변조된 콘텐츠 크롤링.

**해결**: 락 해제 시 소유자 검증:
```python
async def release_crawl_lock(self, url: str) -> bool:
    existing = await self._get_crawl_lock(url)
    if existing and existing["peer_id"] != self._peer_id:
        logger.warning("crawl_lock_unauthorized_release", url=url)
        return False
```

### H-9. 프로토콜 핸들러에 인증/속도제한 없음

**파일**: `p2p/node.py` L220-247

ping, search, replication 핸들러가 모든 연결을 무조건 수락.
격리된 노드, 미인증 피어, 과부하 유발 피어 모두 동일 취급.

**해결**:
```python
async def _authenticated_handler(self, stream, handler_fn):
    peer_id = stream.get_remote_peer_id()
    # 1. 격리 확인
    if self._trust_store.is_isolated(peer_id):
        await stream.close()
        return
    # 2. 속도 제한
    if not self._rate_limiter.allow(peer_id):
        await stream.close()
        return
    # 3. 핸들러 실행
    await handler_fn(stream)
```

### H-10. 신뢰 점수에 합의 메커니즘 없음

**파일**: `trust/scoring.py` 전체

각 노드가 독립적으로 신뢰 점수를 계산. 악성 노드는 자신에게 1.0 부여 가능.

**해결**: 이웃 피어 간 신뢰 점수 교환 (gossip) + 중앙값 합의:
```python
# 최소 3개 이웃의 신뢰 점수 중앙값을 사용
neighbor_scores = await self._gossip_trust_scores(peer_id)
if len(neighbor_scores) >= 3:
    consensus_score = sorted(neighbor_scores)[len(neighbor_scores) // 2]
```

### H-11. 크레딧 원장 로컬 변조 가능

**파일**: `credits/ledger.py` 전체

SQLite 원장을 직접 수정하여 무한 크레딧 부여 가능.

**해결**: 크레딧 이력을 주기적으로 피어에게 증명:
```python
# Merkle root of credit entries → 서명 → DHT 발행
# 감사 시 크레딧 이력 증명 제출 요구
# 이력 해시가 불일치하면 신뢰 점수 하락
```

### H-12. msgpack 역직렬화 크기 제한 없음

**파일**: `p2p/routing.py`, `p2p/replication.py`, `p2p/dht.py` 다수

```python
msgpack.unpackb(data, raw=False)  # 무제한
```

**해결**:
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

## MEDIUM 취약점

| # | 파일 | 취약점 | 해결 방향 |
|---|------|--------|----------|
| M-1 | `attestation.py` | `\|` 구분자 미이스케이프 → 서명 충돌 | JSON 직렬화 후 해시 |
| M-2 | `keys.py` vs `node.py` | 이중 ID 시스템 (KeyPair vs libp2p) | 단일 ID 체계로 통합 |
| M-3 | `dht.py` | 크롤 락 TOCTOU 경합 | DHT CAS (compare-and-swap) |
| M-4 | `routing.py` | request_id 예측 가능 + 무서명 | `secrets.token_hex(16)` + 서명 |
| M-5 | `query.py` | FTS5 연산자(NEAR, NOT) 미차단 | 화이트리스트 토큰 필터 |
| M-6 | `node.py` | 0.0.0.0 바인딩, 접근 제어 없음 | 바인드 주소 설정 + allowlist |
| M-7 | `protocol.py` | 길이 접두사 파싱 모호성 | 매직 바이트 추가 |
| M-8 | `protocol.py` | 10MB 메시지 × 100 스트림 = 1GB | 동시 스트림 제한 |
| M-9 | `config.py` | 환경변수 범위 검증 없음 | 값 범위 검증 추가 |
| M-10 | `link_graph.py` | 무제한 링크 삽입 | 페이지당 최대 100 링크 |
| M-11 | `audit.py` | `random.sample` (CSPRNG 아님) | `secrets.SystemRandom` |
| M-12 | `sybil.py` | PoW 난이도 20비트 (GPU에 취약) | 난이도 24~28비트 + 메모리 하드 해시 |
| M-13 | `query.py`, `server.py` | 검색 쿼리 로그 평문 기록 | 쿼리 해시만 로깅 |
| M-14 | `routing.py` | request_id에 peer_id 포함 | 익명화된 요청 ID |

---

## 엔터프라이즈 배포 필수 요구사항 GAP 분석

### 현재 상태 vs 엔터프라이즈 기준

| 요구사항 | 엔터프라이즈 기준 | 현재 상태 | GAP |
|---------|----------------|----------|-----|
| **전송 암호화** | TLS 1.3 필수 | libp2p 기본값 의존 (미명시) | ❌ 명시적 Noise/TLS 설정 필요 |
| **인증** | 상호 인증 (mTLS) | 없음 (모든 피어 수락) | ❌ 피어 인증 프레임워크 필요 |
| **권한 부여** | RBAC / 역할 기반 | 없음 | ❌ 피어 역할 (reader/crawler/admin) |
| **감사 로그** | 변조 불가 감사 추적 | structlog 평문 | ❌ 서명된 감사 로그 |
| **키 관리** | HSM / 키 볼트 연동 | 파일시스템 평문 | ❌ PKCS#11 / HashiCorp Vault |
| **비밀 관리** | 환경 분리 | config.toml 평문 | ❌ 시크릿 매니저 연동 |
| **입력 검증** | OWASP Top 10 | SSRF, SQL Injection 존재 | ❌ 체계적 입력 검증 |
| **DoS 방어** | 속도 제한 + 회로 차단기 | 없음 | ❌ 전 레이어 속도 제한 |
| **프라이버시** | 쿼리 익명화 | 평문 전송+로깅 | ❌ 쿼리 프라이버시 보호 |
| **공급망 보안** | SBOM + 취약점 스캔 | 없음 | ❌ dependabot + SBOM |
| **네트워크 격리** | 세그먼트 배포 | 0.0.0.0 바인딩 | ❌ 바인드 주소 설정 |
| **컴플라이언스** | SOC2 / ISO27001 | 미고려 | ❌ 감사 프레임워크 |

---

## 보안 강화 구현 계획

### Phase 5-Security-A: CRITICAL 수정 (1주)

| 작업 | 파일 | 예상 |
|-----|------|------|
| DHT 검증기 구현 (서명, 타임스탬프, 스키마) | `p2p/node.py`, `p2p/dht.py` | 2일 |
| 모든 DHT 발행에 서명 필수화 | `p2p/dht.py`, `p2p/protocol.py` | 1일 |
| SSRF 방어 (URL 검증 + IP 블록) | `mcp/server.py`, `crawler/worker.py` | 1일 |
| GDPR/DMCA 서명 강제 검증 | `trust/gdpr.py`, `trust/dmca.py` | 1일 |

### Phase 5-Security-B: HIGH 수정 (2주)

| 작업 | 파일 | 예상 |
|-----|------|------|
| Sybil 방어 실행 경로 연결 | `p2p/node.py`, `p2p/sybil.py` | 2일 |
| 비밀키 암호화 저장 | `p2p/keys.py` | 1일 |
| 복제 콘텐츠 해시 검증 | `p2p/replication.py` | 1일 |
| 검색 응답 서명 + 검증 | `p2p/routing.py`, `p2p/protocol.py` | 2일 |
| 프로토콜 핸들러 인증 + 속도 제한 | `p2p/node.py` | 2일 |
| 쿼리 프라이버시 (해시 기반) | `p2p/routing.py` | 1일 |
| HTTP 응답 크기 제한 | `crawler/worker.py` | 0.5일 |
| SQL 주입 방어 (토크나이저 화이트리스트) | `index/local_store.py` | 0.5일 |
| msgpack 역직렬화 크기 제한 | 다수 | 1일 |
| 크롤 락 소유권 검증 | `p2p/dht.py` | 0.5일 |
| ID 체계 통합 (KeyPair ↔ libp2p) | `p2p/keys.py`, `p2p/node.py` | 1일 |

### Phase 5-Security-C: MEDIUM + 엔터프라이즈 (2주)

| 작업 | 파일 | 예상 |
|-----|------|------|
| Noise 프로토콜 명시적 설정 | `p2p/node.py` | 1일 |
| 피어 역할 기반 권한 부여 (RBAC) | `p2p/auth.py` (신규) | 2일 |
| 바인드 주소 설정 + 네트워크 격리 | `config.py`, `p2p/node.py` | 0.5일 |
| 감사 로그 서명 체인 | `trust/audit_log.py` (신규) | 2일 |
| attestation 정규화 (JSON canonical) | `trust/attestation.py`, `trust/dmca.py`, `trust/gdpr.py` | 1일 |
| CSPRNG 전환 | `trust/audit.py`, `p2p/sybil.py` | 0.5일 |
| 환경변수 범위 검증 | `config.py` | 0.5일 |
| 링크 그래프 크기 제한 | `index/link_graph.py` | 0.5일 |
| 쿼리 로그 익명화 | `search/query.py`, `mcp/server.py` | 0.5일 |
| 신뢰 점수 gossip 합의 | `trust/scoring.py` | 2일 |
| 크레딧 Merkle 증명 | `credits/ledger.py` | 1일 |
| PoW 난이도 상향 + Argon2 | `p2p/sybil.py` | 1일 |

### 신규 보안 모듈

| 모듈 | 역할 |
|-----|------|
| `p2p/auth.py` | 피어 인증 + RBAC 프레임워크 |
| `p2p/rate_limiter.py` | 프로토콜별 속도 제한 |
| `security/url_validator.py` | SSRF 방어 URL 검증 |
| `security/crypto.py` | 정규화된 서명/검증 유틸리티 |
| `trust/audit_log.py` | 변조 불가 감사 로그 체인 |

---

## 엔터프라이즈 배포 아키텍처 권장사항

```
┌─────────────────────────────────────────────────────┐
│                  엔터프라이즈 배포                      │
│                                                     │
│  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │
│  │ Reverse │  │ API     │  │ InfoMesh Node       │ │
│  │ Proxy   │──│ Gateway │──│                     │ │
│  │ (nginx) │  │ (rate   │  │ ┌─────┐ ┌────────┐ │ │
│  └─────────┘  │  limit) │  │ │ MCP │ │ P2P    │ │ │
│               └─────────┘  │ │     │ │ (Noise │ │ │
│                            │ │     │ │  TLS)  │ │ │
│  ┌─────────────────────┐   │ └─────┘ └────────┘ │ │
│  │ HashiCorp Vault     │───│   키 관리            │ │
│  │ (키 저장소)          │   │                     │ │
│  └─────────────────────┘   └─────────────────────┘ │
│                                                     │
│  ┌─────────────────────┐   ┌─────────────────────┐ │
│  │ 감사 로그 수집       │   │ 모니터링             │ │
│  │ (서명된 이벤트)      │   │ (Prometheus/Grafana)│ │
│  └─────────────────────┘   └─────────────────────┘ │
│                                                     │
│  네트워크: 전용 VLAN / 서브넷 격리                     │
│  바인드: 127.0.0.1 (MCP) + 내부 IP (P2P)             │
│  방화벽: P2P 포트만 허용 (TCP 4001 + UDP 4001)        │
└─────────────────────────────────────────────────────┘
```

### 배포 체크리스트

```
□ Noise TLS 암호화 활성화 확인
□ 비밀키 Vault/키링 저장 확인
□ SSRF 방어 URL 검증 활성화
□ DHT 서명 검증 활성화
□ Sybil PoW 검증 활성화
□ 프로토콜 속도 제한 설정
□ 바인드 주소 내부 IP로 제한
□ 감사 로그 외부 전송 설정
□ 쿼리 로그 익명화 확인
□ 네트워크 세그먼트 격리 확인
□ SBOM 생성 + 취약점 스캔
```

---

*이 보고서의 CRITICAL/HIGH 취약점이 해결되기 전까지 프로덕션 또는 엔터프라이즈 배포는 권장하지 않습니다.*

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [콘솔 대시보드](09-console-dashboard.md) · [MCP 연동](10-mcp-integration.md) · [배포](11-publishing.md) · [FAQ](12-faq.md)*
