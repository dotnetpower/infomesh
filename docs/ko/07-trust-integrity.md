# InfoMesh — 신뢰 & 무결성

---

## 1. 개요

분산 시스템에서 **중앙 권위 없이** 데이터의 정확성과 신뢰성을 보장하는 것은 핵심 과제입니다.  
InfoMesh는 4가지 신뢰 문제를 해결해야 합니다:

| 문제 | 핵심 위협 |
|------|----------|
| 중복 문서 | 같은 콘텐츠가 여러 URL/노드에 존재 → 인덱스 품질 저하 |
| LLM 요약 검증 | 요약이 부정확하거나 환각(hallucination) 포함 가능 |
| 문서 조작 | 악성 노드가 크롤링 내용을 변조하여 허위 정보 유포 |
| 크롤러 시작점 | 최초 크롤링 대상이 없으면 인덱스 구축 불가 |

---

## 2. 중복 문서 제거 (Deduplication)

### 2.1 중복 발생 원인

| 유형 | 예시 |
|------|------|
| URL 변형 | `www.example.com` vs `example.com`, trailing `/` |
| 콘텐츠 미러 | 같은 글이 여러 사이트에 게시 (신디케이션) |
| 유사 중복 | 타임스탬프, 광고, 사이드바만 다름 |
| 레이스 컨디션 | DHT 할당 전 여러 노드가 동시 크롤링 |

### 2.2 3계층 중복 제거

```
┌─────────────────────────────────────────────────┐
│          Layer 1: URL 정규화 (즉시)              │
│  www 제거, trailing / 통일, query param 정렬,   │
│  <link rel="canonical"> 태그 추적               │
├─────────────────────────────────────────────────┤
│          Layer 2: 정확 중복 (해시)               │
│  추출된 본문의 SHA-256 → DHT에 content_hash 발행│
│  동일 해시 = 정확 중복 → 원본 포인터만 유지      │
├─────────────────────────────────────────────────┤
│          Layer 3: 유사 중복 (핑거프린트)          │
│  SimHash/MinHash로 문서 핑거프린트 생성          │
│  해밍 거리 ≤ 3 → near-duplicate로 판정           │
│  그룹핑 후 가장 오래된/신뢰 높은 것을 대표 문서로 │
└─────────────────────────────────────────────────┘
```

### 2.3 DHT 크롤링 락

```
크롤링 전:
  1. URL 정규화 → canonical URL 결정
  2. DHT에 hash(canonical_url) = "CRAWLING" + peer_id + timestamp 발행
  3. 다른 노드가 같은 URL 크롤링 시도 → DHT에서 락 확인 → 대기
  4. 크롤링 완료 → "DONE" + content_hash로 업데이트
  5. 락 타임아웃 (5분) → 응답 없으면 다른 노드가 인계
```

### 2.4 인덱스 내 중복 처리

```python
# 중복 판정 로직 (pseudocode)
class DeduplicationPipeline:
    async def process(self, url: str, content: str) -> DeduplicationResult:
        # 1. URL 정규화
        canonical = normalize_url(url)  # www 제거, trailing / 통일 등

        # 2. 정확 중복 체크
        content_hash = sha256(content)
        existing = await self.dht.get(f"content:{content_hash}")
        if existing:
            return DeduplicationResult(is_duplicate=True, original=existing)

        # 3. 유사 중복 체크
        fingerprint = simhash(content)
        similar = await self.find_similar(fingerprint, threshold=3)
        if similar:
            return DeduplicationResult(is_near_duplicate=True, group=similar)

        # 4. 새 문서 등록
        await self.dht.put(f"content:{content_hash}", canonical)
        return DeduplicationResult(is_duplicate=False)
```

---

## 3. LLM 요약 검증 (Summary Verification)

### 3.1 위협 모델

| 위협 | 설명 |
|------|------|
| 환각 (Hallucination) | 원문에 없는 사실을 생성 |
| 누락 (Omission) | 핵심 정보를 빠뜨림 |
| 왜곡 (Distortion) | 원문의 의미를 반대로 요약 |
| 악의적 주입 | 의도적으로 잘못된 요약 삽입 |

### 3.2 3단계 검증 파이프라인

```
┌─────────────────────────────────────────────────┐
│    1단계: 자가 검증 (로컬, 즉시, 저비용)         │
│                                                 │
│    원문 → LLM 요약 → 키팩트 앵커링 검증          │
│                   → 모순 검출 (NLI)              │
│                   → 길이/품질 체크               │
│                                                 │
│    통과율 < 70% → 재생성 (최대 3회)              │
├─────────────────────────────────────────────────┤
│    2단계: 교차 검증 (네트워크, 비동기)            │
│                                                 │
│    같은 문서 복제 노드(N=3) 중 LLM 보유 노드들   │
│    → 독립적으로 요약 생성                        │
│    → 요약 간 코사인 유사도 비교                  │
│                                                 │
│    유사도 > 0.8 → 합의 달성 ✓                    │
│    유사도 < 0.5 → 경고, 원문만 제공 ✗            │
│    중간값 → 다수결 채택                          │
├─────────────────────────────────────────────────┤
│    3단계: 평판 기반 장기 신뢰                     │
│                                                 │
│    교차 검증 통과 이력 → LLM 신뢰 점수            │
│    높은 신뢰 → 1단계만으로 승인                   │
│    낮은 신뢰 → 항상 2단계 교차 검증 필수          │
└─────────────────────────────────────────────────┘
```

### 3.3 키팩트 앵커링 (Key-Fact Anchoring)

```
원문: "Python 3.12는 2023년 10월에 출시되었으며, type 문이 새로 추가됐다."

요약: "Python 3.12는 2024년에 출시되었으며 type 문을 지원한다."
                     ^^^^
         원문과 불일치 → 앵커링 실패 → 재생성

검증 방법:
  1. 요약에서 명사구, 숫자, 날짜, 고유명사 추출
  2. 각 팩트를 원문에서 탐색
  3. 매칭률 계산 = 매칭된 팩트 / 전체 팩트
  4. 매칭률 ≥ 0.7 → 통과
  5. 매칭률 < 0.7 → 재생성 또는 거부
```

### 3.4 저장 구조

```python
@dataclass
class SummarizedDocument:
    url: str                    # 원본 URL
    content_hash: str           # 원문 SHA-256
    summary: str                # LLM 생성 요약
    summary_hash: str           # 요약 SHA-256
    model_id: str               # 사용된 모델 (예: "qwen2.5-7b-q4")
    verification_level: int     # 1=자가, 2=교차, 3=평판
    anchor_score: float         # 키팩트 앵커링 점수 (0.0~1.0)
    cross_validation_score: float | None  # 교차 검증 점수
    summarizer_peer_id: str     # 요약한 노드 ID
    timestamp: datetime         # 요약 생성 시각
```

---

## 4. 문서 조작 방지 (Content Integrity)

### 4.1 위협 모델

| 위협 | 방법 | 동기 |
|------|------|------|
| 콘텐츠 변조 | 크롤링 후 본문 수정 | SEO 스팸, 정치 조작, 피싱 |
| 가짜 크롤링 | 실제로 크롤링하지 않고 가짜 데이터 주입 | 크레딧 사기 |
| 선택적 검열 | 특정 콘텐츠만 의도적으로 누락 | 정보 조작 |
| 요약 조작 | 올바른 원문 + 조작된 요약 | 미묘한 허위 정보 |

### 4.2 콘텐츠 증명 체인 (Content Attestation Chain)

```
크롤링 시:
  ┌────────────────────────────────────────────────┐
  │ 1. HTTP 응답 원본 수신                          │
  │ 2. raw_hash = SHA-256(HTTP response body)       │
  │ 3. trafilatura로 본문 추출                      │
  │ 4. content_hash = SHA-256(extracted text)        │
  │ 5. attestation = sign(                          │
  │       raw_hash + content_hash + url + timestamp,│
  │       peer_private_key                          │
  │    )                                            │
  │ 6. DHT에 발행:                                  │
  │    (url, content_hash, attestation, peer_id)    │
  └────────────────────────────────────────────────┘

검증 시:
  - 검증 노드가 같은 URL 재크롤링
  - 동일 raw_hash / content_hash 확인
  - 불일치 → 조작 의심 → 신뢰 점수 감점
```

### 4.3 랜덤 감사 시스템 (Random Audit)

```
감사 주기: 노드당 평균 1회/시간 (랜덤 간격)

프로세스:
  1. DHT 기반으로 랜덤하게 감사 대상 (노드 + URL) 선정
  2. 감사 노드 3개가 독립적으로 같은 URL 크롤링
  3. content_hash 비교: 원본 노드 vs 감사 노드들
  4. 결과 처리:

  ┌──────────────────┬────────────────────────┐
  │ 감사 결과         │ 조치                   │
  ├──────────────────┼────────────────────────┤
  │ 3/3 일치          │ 통과 ✓ 신뢰 +0.01      │
  │ 2/3 일치          │ 원본 사이트 변경 가능성  │
  │ 1/3 또는 0/3 일치 │ 조작 의심 → 신뢰 -0.2   │
  │ 반복 불일치 (3회+) │ 네트워크 격리           │
  └──────────────────┴────────────────────────┘

  ※ 원본 사이트가 실제로 변경됐는지도 고려
     → timestamp 차이가 크면 자연스러운 변경으로 판단
```

#### 4.3.1 감사 증명 (Proof-of-Audit)

감사자가 실제로 재크롤링하지 않고 "통과"라고 거짓 보고하는 것을 방지합니다:

- **증거 제출**: 각 `AuditResult`에는 감사자가 독립적으로 재크롤링하여 얻은
  `actual_text_hash`와 `actual_raw_hash`가 포함됩니다.
- **교차 검증**: `_cross_validate_auditor_hashes()`가 3명의 감사자 해시를
  비교합니다. 다수결 합의에서 벗어나는 감사자는 `suspicious_auditors`로 플래그됩니다.
- **감사자 서명**: 각 `AuditResult`에는 정규화된 감사 증거에 대한
  `auditor_signature` (Ed25519)가 포함되어 감사자가 해당 결과를 생성했음을 증명합니다.
- **정규화**: `audit_result_canonical(result)`가 감사 데이터의 결정론적
  바이트 표현을 생성하여 서명 검증을 보장합니다.

### 4.4 통합 신뢰 점수

```
Trust(node) = w1 × uptime_score
            + w2 × contribution_score
            + w3 × audit_pass_rate
            + w4 × summary_quality_score

가중치 (기본값):
  w1 = 0.15  (가동 시간)
  w2 = 0.25  (기여량)
  w3 = 0.40  (감사 통과율) ← 가장 중요
  w4 = 0.20  (요약 품질)

신뢰 등급:
  ┌────────┬──────────┬───────────────────────────┐
  │ 등급    │ 점수 범위 │ 네트워크 대우               │
  ├────────┼──────────┼───────────────────────────┤
  │ 신뢰    │ ≥ 0.8    │ 결과 우선 노출, 감사 빈도 ↓ │
  │ 보통    │ 0.5–0.8  │ 일반 대우, 표준 감사         │
  │ 의심    │ 0.3–0.5  │ 결과에 경고 태그, 감사 빈도 ↑│
  │ 불신    │ < 0.3    │ 결과 무시, 격리 후보         │
  └────────┴──────────┴───────────────────────────┘
```

### 4.5 궁극적 방어: 원본 URL

> 어떤 노드의 데이터든 **원본 URL을 재크롤링하면 검증 가능**.  
> InfoMesh는 콘텐츠를 "만드는" 것이 아니라 "가져오는" 것이므로,  
> 원본 사이트가 존재하는 한 조작은 항상 탐지 가능.  
> 이것이 중앙화된 검색 엔진에 없는 InfoMesh의 구조적 장점.

---

## 5. 네트워크 수준 보안

### 5.1 시빌 공격 방어 (Sybil Attack)

시빌 공격은 다수의 가짜 ID를 생성하여 DHT에서 과도한 영향력을 행사하는 것입니다.

| 방어 계층 | 메커니즘 |
|----------|---------|
| PoW 노드 ID | 노드 ID 생성 시 작업 증명 필요 (평균 CPU ~30초), 대량 생성 비용 증가 |
| IP 서브넷 제한 | 하나의 DHT 버킷당 /24 서브넷에서 최대 3개 노드 ID |
| 기여 검증 | 신규 노드는 "보통" (0.5) 신뢰 등급부터 시작; 크레딧 획득 시 실제 HTTP 응답 해시 교차 검증 |
| 점진적 신뢰 | 신뢰 점수가 천천히 증가 — "신뢰" 등급 도달까지 수일/수주 필요 |

```
노드 ID 생성:
  1. 후보 키 쌍 생성
  2. SHA-256(public_key + nonce)의 선행 N개 비트가 0인 nonce 계산
  3. N = 20 (조정 가능) → 평균 CPU ~30초
  4. (public_key, nonce)를 DHT에 발행 → 누구든 검증 가능
```

### 5.2 이클립스 공격 방어 (Eclipse Attack)

이클립스 공격은 대상 노드를 악성 피어로 둘러싸 네트워크 뷰를 조작하는 것입니다.

| 방어 계층 | 메커니즘 |
|----------|---------|
| 다중 부트스트랩 소스 | 시작 시 ≥3개의 독립 부트스트랩 노드에 연결 |
| 라우팅 테이블 다양성 | k-bucket당 /16 서브넷에서 최대 2개 노드 |
| 정기 라우팅 갱신 | 30분마다 각 버킷 범위의 랜덤 ID로 라우팅 테이블 갱신 |
| 이웃 검증 | 독립 경로를 통해 이웃 노드 정기 검증 (다른 경로로 같은 키 질의) |

### 5.3 DHT 인덱스 오염 방어 (Index Poisoning)

악성 노드가 허위 키워드→문서 매핑을 발행하여 검색 결과를 오염시킬 수 있습니다.

| 방어 계층 | 메커니즘 |
|----------|---------|
| 키워드별 발행 제한 | 노드당 키워드 해시당 시간당 최대 10회 발행 |
| 콘텐츠 해시 검증 | 발행된 포인터에 `content_hash` 포함 필수; 질의 노드가 직접 해시 검증 가능 |
| 서명된 발행 | 모든 DHT 발행에 발행자 키 서명 필수; 미서명 항목 거부 |
| 합의 기반 검증 | 인기 키워드의 경우 다중 담당 노드에 질의하여 교집합 채택 |

```
DHT 발행 검증:
  1. 발행 요청 수신: (keyword_hash, peer_id, doc_id, score, content_hash, signature)
  2. peer_id의 공개키로 서명 검증
  3. 속도 제한 확인: peer_id가 이 keyword_hash에 지난 1시간 내 ≤10회 발행했는지
  4. 선택적: peer_id에서 문서 가져와 SHA-256이 content_hash와 일치하는지 검증
  5. 수락 또는 거부
```

### 5.4 크레딧 파밍 방지

악성 노드가 실질적 가치 제공 없이 크레딧을 획득하려 시도할 수 있습니다.

| 공격 벡터 | 방어 |
|----------|------|
| 가짜 크롤링 | 랜덤 감사가 `raw_hash`를 실제 재크롤링과 비교; 불일치 = 신뢰 감점 |
| 자기 참조 루프 | 순환 URL 패턴 감지 (A→B→A); 같은 도메인 크롤링 크레딧 상한 |
| 타임스탬프 조작 | 비수요 시간 LLM 보너스를 IP 지오로케이션 교차 확인 (±2시간 허용) |
| 시빌 크레딧 분할 | 같은 /24 서브넷 내 노드 간 크레딧 이전 검토 대상 플래그 |

추가 조치:
- **신규 노드 수습 기간**: 첫 24시간 → 높은 감사 빈도 (15분당 1회 vs 시간당 1회)
- **통계적 이상 탐지**: 네트워크 평균 대비 크롤링률 >3σ인 노드 플래그
- **원시 HTTP 해시**: `SHA-256(raw_response)` 저장 — 감사자가 독립 재크롤링과 비교

### 5.5 키 관리

| 항목 | 정책 |
|------|------|
| 키 생성 | 첫 실행 시 Ed25519 키 쌍 생성 |
| 저장 위치 | `~/.infomesh/keys/` 디렉토리, 파일 권한 0600 |
| 백업 | 사용자 주도 내보내기: `infomesh keys export` → 암호화된 키 파일 |
| 교체 | `infomesh keys rotate`로 지원; 이전 키가 DHT에서 새 키로의 인수 서명 |
| 폐기 | 키 유출 시: DHT에 서명된 폐기 레코드 발행; ~1시간 내에 네트워크가 이전 키 거부 (가십 전파) |

### 5.6 P2P 메시지 인증

모든 P2P 메시지는 인증 및 재전송 방지를 위해 **SignedEnvelope**로
래핑됩니다 (`infomesh/p2p/message_auth.py`):

```
SignedEnvelope:
  payload:    bytes      # 내부 메시지 바이트
  peer_id:    str        # 발신자 신원
  signature:  bytes      # canonical(peer_id, nonce, timestamp, payload)에 대한 Ed25519
  nonce:      int        # 단조 증가 카운터
  timestamp:  float      # UTC 에포크 — 300초 초과 시 거부

5단계 검증:
  1. 격리 확인  — 네트워크 격리된 피어는 거부 (TrustStore.is_isolated)
  2. 키 조회    — 피어의 공개 키가 알려지지 않으면 거부 (PeerKeyRegistry)
  3. 신선도     — 타임스탬프가 MAX_MESSAGE_AGE_SECONDS(300초)보다 오래되면 거부
  4. 재전송     — 해당 피어의 마지막 nonce 이하이면 거부 (NonceTracker)
  5. 서명       — canonical 바이트에 대해 Ed25519 검증
```

프로토콜 통합:
- `MessageType.SIGNED_ENVELOPE = 100` (`protocol.py`)
- `encode_signed_envelope()` / `decode_signed_envelope()` 와이어 포맷 헬퍼

| 컴포넌트 | 용도 |
|---------|------|
| `NonceCounter` | 아웃바운드 메시지용 스레드 안전 단조 nonce 생성기 |
| `PeerKeyRegistry` | `peer_id → public_key_bytes` 인메모리 매핑 |
| `NonceTracker` | 피어별 최고 nonce 추적으로 재전송 방지 |
| `VerificationError` | 검증 실패 시 사용자 정의 예외 (구체적 사유 포함) |

### 5.7 `crawl_url()` 남용 방지

MCP `crawl_url()` 도구가 네트워크를 과도한 요청으로 압도하는 데 악용될 수 있습니다.

| 제한 | 값 | 근거 |
|------|---|------|
| 노드당 속도 제한 | 60 URL/시간 | 단일 노드에서의 홍수 방지 |
| 도메인당 대기열 제한 | 10개 대기 URL/도메인 | 특정 사이트 과부하 방지 |
| 깊이 제한 | 최대 `depth=3` | 링크 폭발적 증가 방지 |
| 차단 목록 확인 | 크롤링 전 | 차단 목록/robots.txt에 해당하는 URL은 큐잉 전 거부 |

### 5.7 로컬 데이터 보안

| 항목 | 접근 방식 |
|------|----------|
| SQLite 암호화 | 선택적 SQLCipher 통합으로 저장 시 인덱스 암호화 |
| 키 저장 | OS 키체인 통합 (Linux: libsecret, macOS: Keychain) 가능 시 활용 |
| 임시 파일 | 크롤링된 원시 응답은 인덱싱 후 삭제; 미암호화 상태로 디스크에 보존하지 않음 |
| 네트워크 트래픽 | 모든 P2P 연결은 libp2p TLS/Noise 프로토콜로 암호화 |

---

## 6. 크롤러 시작점 (Seed Strategy)

### 6.1 콜드 스타트 문제

새 노드가 처음 시작할 때:
- 인덱스가 비어 있음
- 어떤 URL을 크롤링해야 하는지 모름
- P2P 네트워크에 아직 참여하지 않았을 수 있음

### 6.2 계층적 시드 전략

```
┌──────────────────────────────────────────────────┐
│         시드 우선순위 (위에서 아래로)               │
│                                                  │
│  Layer 1: 큐레이팅된 시드 리스트 (패키지 내장)     │
│           → 검증된 고품질 소스, 즉시 사용 가능      │
│                                                  │
│  Layer 2: Common Crawl URL 리스트                 │
│           → 설치 시 선택한 카테고리의 URL 다운로드   │
│                                                  │
│  Layer 3: DHT 할당 URL                            │
│           → P2P 참여 후 DHT에서 담당 URL 수신       │
│                                                  │
│  Layer 4: 사용자 제출 (crawl_url MCP 도구)         │
│           → LLM이 특정 URL 크롤링 요청              │
│                                                  │
│  Layer 5: 링크 팔로잉                              │
│           → 크롤링 중 발견된 링크 자동 등록           │
└──────────────────────────────────────────────────┘
```

### 6.3 내장 시드 카테고리

| 카테고리 | 시드 소스 | 예시 도메인 | 예상 크기 |
|---------|----------|-----------|----------|
| **기술 문서** | 공식 문서, API 레퍼런스 | docs.python.org, developer.mozilla.org, docs.rs, go.dev | ~2GB |
| **학술** | 오픈 액세스 저널 | arxiv.org, pubmed.ncbi.nlm.nih.gov, semanticscholar.org | ~5GB |
| **백과사전** | 위키 계열 | en.wikipedia.org, wikidata.org | ~8GB |
| **뉴스** | RSS 피드 기반 | 주요 통신사, 기술 뉴스 | ~1GB |
| **정부/공공** | 정부 데이터 | data.gov, data.go.kr | ~2GB |
| **오픈소스** | README, Wiki | github.com, gitlab.com | ~3GB |
| **디렉토리** | 큐레이팅 목록 | curlie.org (DMOZ 후속) | ~1GB |

### 6.4 설치 시 대화형 선택

```
$ uv run infomesh start

🔍 InfoMesh 초기 설정

어떤 도메인에 관심이 있으신가요? (복수 선택 가능)

  [x] 기술 문서 (Python, JS, Rust, Go, ...)     ~2GB
  [ ] 학술 논문 (arXiv, PubMed, ...)            ~5GB
  [x] 백과사전 (Wikipedia)                       ~8GB
  [ ] 뉴스 (RSS 피드)                            ~1GB
  [ ] 정부/공공 데이터                            ~2GB
  [x] 오픈소스 (GitHub README/Wiki)              ~3GB
  [ ] 전체 (Common Crawl 서브셋)                 ~50GB

선택: 기술 문서, 백과사전, 오픈소스 (총 ~13GB)

다운로드 중... ████████████░░░░ 70%
로컬 인덱스 생성 중...
P2P 네트워크 참여 중...

✅ 준비 완료! MCP 서버가 실행 중입니다.
```

### 6.5 크롤링 확산 알고리즘

```
priority_queue = PriorityQueue()

# 1. 시드 URL 투입
for url in seed_urls:
    priority_queue.push(url, priority=SEED_PRIORITY)

# 2. BFS + 우선순위 기반 크롤링
while not priority_queue.empty():
    url = priority_queue.pop()

    # DHT 소유권 확인
    owner = await dht.get_owner(url)
    if owner != self.peer_id:
        await dht.register_url(url)  # 소유자에게 알림
        continue

    # 크롤링
    page = await crawl(url)

    # 링크 추출 + 우선순위 부여
    for link in page.extract_links():
        priority = calculate_priority(link, page)
        priority_queue.push(link, priority)

def calculate_priority(link, source_page):
    score = 0.0

    # 같은 도메인 내부 링크 → 높은 우선순위 (깊이 탐색)
    if same_domain(link, source_page.url):
        score += 3.0

    # 시드 카테고리에 속하는 외부 링크 → 중간
    if matches_seed_category(link):
        score += 2.0

    # 외부 링크 참조 빈도 → 많이 참조될수록 높음
    score += link_reference_count(link) * 0.1

    # robots.txt 차단 or 블랙리스트 → 스킵
    if is_blocked(link):
        return -1  # 크롤링하지 않음

    return score
```

### 6.6 크롤링 시작 vs 검색어

> 주의: InfoMesh의 크롤러는 **검색어로 시작하지 않습니다.**
>
> 기존 검색 엔진은 사용자의 검색어를 기반으로 결과를 보여주지만,
> 크롤러 자체는 검색어와 무관하게 **URL 기반으로 웹을 탐색**합니다.
>
> ```
> 크롤러: URL → 페이지 다운로드 → 텍스트 추출 → 키워드 인덱싱 → 링크 추출 → 반복
> 검색:   사용자 쿼리 → 인덱스에서 매칭 → 결과 반환
> ```
>
> 따라서 "어떤 검색어로 크롤링을 시작하나?"는 올바른 질문이 아닙니다.
> 올바른 질문: **"어떤 URL에서 크롤링을 시작하나?"** → 이것이 시드 전략입니다.

---

## 7. 구현 로드맵 매핑

| 기능 | Phase | 우선순위 |
|------|-------|---------|
| URL 정규화 + 정확 중복 (SHA-256) | 0 (MVP) | 필수 |
| 내장 시드 리스트 + 카테고리 선택 | 0 (MVP) | 필수 |
| 링크 팔로잉 크롤링 | 0 (MVP) | 필수 |
| SimHash 유사 중복 | 1 | 높음 |
| Common Crawl URL 리스트 임포트 | 1 | 높음 |
| 콘텐츠 해시 DHT 발행 | 2 | 필수 |
| DHT 크롤링 락 | 2 | 필수 |
| LLM 자가 검증 (키팩트 앵커링) | 3 | 필수 |
| 콘텐츠 증명 체인 + 서명 | 3 | 필수 |
| LLM 교차 검증 | 3 | 높음 |
| 랜덤 감사 시스템 | 3 | 높음 |
| 통합 신뢰 점수 | 3 | 필수 |
| LLM 평판 기반 신뢰 | 4 | 보통 |
| PoW 노드 ID 생성 (시빌 방어) | 2 | 필수 |
| 라우팅 테이블 다양성 (이클립스 방어) | 2 | 필수 |
| DHT 발행 속도 제한 (오염 방어) | 2 | 높음 |
| 크레딧 파밍 탐지 + 신규 노드 수습 | 3 | 필수 |
| 키 관리 (Ed25519 + 교체) | 2 | 필수 |
| `crawl_url()` 속도 제한 | 0 (MVP) | 필수 |
| SQLCipher 선택적 암호화 | 4 | 보통 |

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [보안 감사](08-security-audit.md) · [콘솔 대시보드](09-console-dashboard.md) · [MCP 연동](10-mcp-integration.md) · [배포](11-publishing.md) · [FAQ](12-faq.md)*
