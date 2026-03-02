# InfoMesh — 아키텍처

---

## 1. 전체 네트워크 구조

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
               DHT (Kademlia) 기반 오버레이 네트워크
```

> Peer B처럼 LLM 없이도 크롤링 + 인덱싱 + 검색에 완전히 참여 가능.

---

## 2. 각 피어의 내부 구조

```
┌──────────────────────────────────────────────────┐
│                     Peer                          │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌─────┐│
│  │ P2P      │  │ Crawler  │  │ MCP    │  │ LLM ││
│  │ Network  │  │ Engine   │  │ Server │  │(opt)││
│  │          │  │          │  │        │  │     ││
│  │ • DHT    │  │ • HTTP   │  │ • tool │  │ 요약││
│  │ • Gossip │  │ • Parser │  │  search│  │     ││
│  │   (피어  │  │ • robots │  │ • tool │  │     ││
│  │   탐색)  │  │ • dedup  │  │  fetch │  │     ││
│  └────┬─────┘  └────┬─────┘  └───┬────┘  └──┬──┘│
│       │              │            │          │   │
│  ┌────▼──────────────▼────────────▼──────────▼──┐│
│  │              Local Index                      ││
│  │    SQLite FTS5 + Vector (ChromaDB)            ││
│  │    + 요약 캐시 (LLM 생성)                     ││
│  └───────────────────────────────────────────────┘│
└──────────────────────────────────────────────────┘
```

---

## 3. DHT 기반 분산 인덱스

```
기존 P2P DHT:
  info_hash → 피어 목록

InfoMesh DHT:
  keyword_hash → 문서 포인터 목록 (peer_id, doc_id, score)

예시:
  Peer A가 "rust async tutorial" 페이지를 크롤링
  → 키워드 추출: ["rust", "async", "tutorial", "tokio"]
  → hash("rust") = 0x7A3F... → DHT에서 0x7A3F에 가까운 노드들에 발행
  → "Peer A에 'rust' 관련 문서 있음, doc_id=xxx, score=0.85"
```

DHT는 Kademlia 기반. 노드 ID는 160비트. 거리 = XOR 메트릭.

두 가지 용도:
1. **역인덱스**: `hash(keyword)` → `(peer_id, doc_id, score)` 포인터 목록
2. **크롤링 조율**: `hash(URL)` → 해시에 가장 가까운 노드가 크롤링 담당

복제 팩터: 모든 저장 데이터에 대해 최소 N=3.

> **py-libp2p 참고**: Python libp2p 구현체는 Go/Rust 버전에 비해 성숙도가 낮습니다.
> 완화 전략:
> 1. Phase 0에서 py-libp2p의 DHT + NAT traversal을 통합 테스트로 검증
> 2. 대안: py-libp2p가 부족할 경우 PyO3 바인딩을 통한 rust-libp2p 사용
> 3. 단순화 대안: TCP/QUIC 기반의 커스텀 Kademlia 구현

---

## 4. 크롤링 조율 (중앙 없이)

```
URL도 DHT로 관리:
  hash("https://docs.python.org/3/") = 0xABC...
  → 0xABC에 가장 가까운 노드가 이 URL의 "소유자"
  → 소유자가 크롤링 담당

분담:
  - 노드 100개 → 각자 전체 URL의 ~1% 담당
  - 노드 1,000개 → 각자 ~0.1% 담당
  - 노드가 늘수록 개별 부담 감소 (자동 스케일)
```

크롤링 규칙:
- `robots.txt` 항상 엄격 준수
- 기본 정중함: 도메인당 초당 1회 이하 요청
- **Crawl-Delay**: robots.txt의 `Crawl-delay` 지시자를 준수합니다. 도메인별 지연이 자동 적용되며, 남용 방지를 위해 최대 60초로 제한됩니다.
- **사이트맵 발견**: robots.txt에서 `Sitemap:` URL을 추출하여 발견된 URL을 자동으로 크롤링 큐에 추가합니다.
- **Canonical 태그**: HTML의 `<link rel="canonical">`을 인식합니다. 페이지가 다른 canonical URL을 선언하면, 현재 페이지의 인덱싱을 건너뛰고 canonical URL을 스케줄링하여 인덱스 내 중복 콘텐츠를 방지합니다.
- **지수 백오프 재시도**: 일시적 HTTP 오류(5xx)와 네트워크 장애 시 자동 재시도(최대 2회, 지수 백오프: 1초, 2초). SSRF 차단 URL은 재시도하지 않습니다.
- 컨텐츠 추출: `trafilatura` 사용, 필요시 `BeautifulSoup` 폴백
- 저장: 원문 텍스트 + 메타데이터 (제목, URL, 크롤링 시각, 언어)
- **크롤링 락**: 크롤링 전 `hash(url) = CRAWLING`을 DHT에 발행하여 여러 노드가 같은 URL을 크롤링하는 것을 방지. 락 타임아웃: 5분.
- **SPA/JS 렌더링**: 대부분의 콘텐츠는 정적 HTML에서 추출 가능. JavaScript 중심 페이지는 `js_required` DHT 태그로 Playwright/헤드리스 브라우저 가능 노드에 위임. Phase 0 (MVP)는 정적 HTML에 집중.
- **대역폭 제한**: 기본값 P2P 업로드 ≤5 Mbps / 다운로드 10 Mbps. `~/.infomesh/config.toml`로 설정 가능. 크롤링 동시 연결: 노드당 최대 5개 (조정 가능).
- **강제 재크롤**: `crawl_url(url, force=True)`로 이미 방문한 URL을 다시 크롤링할 수 있습니다. 오래된 콘텐츠 갱신이나 depth 제한 변경 후 새로운 자식 링크 발견에 유용합니다.

---

## 5. 인덱싱

- **로컬 키워드 검색**: SQLite FTS5 + BM25 랭킹
- **로컬 벡터 검색**: ChromaDB로 시맨틱/임베딩 기반 쿼리
  - 기본 임베딩 모델: `all-MiniLM-L6-v2` (22M 파라미터, CPU에서 실행 가능)
  - 벡터 검색은 **선택적** — FTS5 키워드 검색은 임베딩 없이 독립 동작
  - 벡터 검색 없는 노드도 네트워크에 완전히 참여 가능
- **분산 인덱스**: 크롤링 후 키워드 해시를 DHT에 발행
- **콘텐츠 증명**: 크롤링 시 `SHA-256(raw_response)` + `SHA-256(extracted_text)` 계산, 피어 개인키로 서명, DHT에 발행

---

## 6. 검색 흐름

```
사용자 → LLM (Copilot) → MCP → 로컬 피어

[로컬 피어의 처리]
1. 쿼리 분석: "rust async tutorial" → ["rust", "async", "tutorial"]
2. 로컬 인덱스 먼저 검색 (즉시 결과, <10ms)
3. 각 키워드 해시 → DHT 라우팅 → 담당 노드들에 질의 (~500ms–2s)
4. 원격 결과 수신
5. 로컬 + 원격 결과 병합, BM25 + 신선도 + 신뢰도로 랭킹
6. 상위 N개 문서의 전문 텍스트 요청 (~200ms–1s)
7. 결과 반환 → MCP → LLM이 요약

예상 총 지연:
  - 로컬 전용 검색: <100ms
  - 네트워크 검색 (성숙된 네트워크, 100+ 노드): ~1–2초
  - 네트워크 검색 (초기 네트워크, <20 노드): ~2–5초
  - 상용 검색 API 응답 시간과 비슷한 수준
```

> 모든 P2P 메시지는 **msgpack**으로 직렬화하고 **zstd**로 압축하여 대역폭 사용을 최소화합니다.

---

## 7. 로컬 LLM 요약

노드는 **선택적으로** 로컬 LLM을 실행하여 크롤링된 컨텐츠의 요약을 생성할 수 있습니다.

- 요약은 전문 텍스트와 함께 로컬 인덱스에 저장되고 DHT를 통해 공유
- 로컬 LLM이 있는 노드는 다른 피어의 요약 요청도 처리 가능
- LLM은 **필수가 아님** — LLM 없이도 크롤링 + 인덱싱 + 검색에 완전히 참여 가능

### 추천 모델

| 모델 | 크기 | 강점 |
|------|------|------|
| **Qwen 2.5** | 3B / 7B | 다국어 품질 최고, Apache 2.0 |
| **Llama 3.x** | 3B / 8B | 범용 성능 최강, 커뮤니티 |
| **Gemma 3** | 4B / 12B | 크기 대비 성능 우수 |
| **Phi-4** | 3.8B / 14B | 추론/요약에 강점 |

### 실행 환경

| 런타임 | 특징 |
|--------|------|
| **ollama** | 가장 간단, REST API 내장 |
| **llama.cpp** | 경량, CPU에서도 구동, GGUF 양자화 |
| **vLLM** | GPU 있을 때 높은 처리량 |

최소 사양: 8GB RAM CPU에서 3B Q4 양자화 모델.

### 에너지 인식 스케줄링

- 노드는 로컬 시간대와 심야전기 시간대 (예: 23:00–07:00) 설정 가능
- LLM 집중 작업 (일괄 요약, 피어 요청 처리)은 심야 시간대에 우선 스케줄링
- 심야 시간대 LLM 운영 시 모든 LLM 관련 크레딧에 **1.5배 배율** 적용
- 네트워크는 일괄 요약 요청을 현재 심야 시간대인 노드에 우선 라우팅

---

## 8. 노드 생명주기

### 참여 (Join)

```
1. 영속 피어 저장소에서 캐시된 피어 로드 (~/.infomesh/peer_store.db)
2. Bootstrap 노드 목록에 접속 (하드코딩 or DNS)
   ※ Bootstrap ≠ Hub. 기존 피어 중 아무나 가능
3. Bootstrap 실패 시 → 이전 세션의 캐시된 피어에 재연결
4. mDNS: 로컬 네트워크(LAN)에서 피어 자동 발견
5. PEX (Peer Exchange): 연결된 피어에게 알고 있는 피어 목록 요청
6. Kademlia JOIN → 이웃 노드 발견
7. 자기 ID 기준 담당 URL 범위 수신
8. Common Crawl 초기 데이터 다운로드 (선택)
9. 담당 URL 크롤링 시작
10. 인덱스 데이터 이웃에서 동기화
```

### 이탈 (Leave)

```
1. 연결된 피어 목록을 영속 피어 저장소에 저장
2. 이웃 노드가 heartbeat 실패 감지
3. 이탈 노드의 담당 범위 → DHT 인접 노드가 자동 승계
4. 복제본(N=3~5)이 있으므로 데이터 손실 없음
```

### 피어 발견 폴백 체인

부트스트랩 서버를 사용할 수 없을 때, 노드는 여러 대체 메커니즘을
사용하여 재연결합니다:

| 우선순위 | 메커니즘 | 범위 | 설명 |
|----------|----------|------|------|
| 1 | **영속 피어 저장소** | 인터넷 | 이전 세션의 피어에 재연결 (SQLite 캐시) |
| 2 | **PEX (Peer Exchange)** | 인터넷 | 연결된 피어에게 알고 있는 피어 목록 요청 (가십 프로토콜, 5분마다) |
| 3 | **mDNS** | LAN | 같은 로컬 네트워크의 피어 자동 발견 |
| 4 | **수동 설정** | 인터넷 | `config.toml`에 사용자가 직접 지정한 피어 주소 |
| 5 | **DHT 라우팅 테이블** | 메모리 | 인-메모리 라우팅 테이블 (재시작 시 소실) |

### 악성 노드 대응

아키텍처 레벨의 기본 방어:

```
1. 같은 URL을 여러 노드가 크로스 검증
2. 검색 결과에 대한 합의 (다수결)
3. 신뢰 점수: 오래 참여 + 많이 기여 = 높은 신뢰
4. 신뢰 낮은 노드의 결과는 가중치 하락
```

> 종합적인 신뢰 시스템(콘텐츠 증명 체인, 랜덤 감사, 통합 신뢰 점수,
> 조작 감지, 네트워크 격리)에 대한 자세한 내용은
> [신뢰 & 무결성](07-trust-integrity.md)을 참조하세요.

---

## 8.1 데이터 압축

InfoMesh는 레벨 조절 가능한 **zstd**를 모든 데이터 압축에 사용합니다:

| 용도 | zstd 레벨 | 근거 |
|------|----------|------|
| 실시간 P2P 전송 | 1–3 | 속도 우선, 최소 지연 |
| 로컬 인덱스 스냅샷 | 9–12 | 내보내기용 속도/비율 균형 |
| Common Crawl 아카이브 | 19–22 | 대량 데이터에 최대 압축 |

- **딕셔너리 모드**: 도메인별 딕셔너리 구축으로 반복 구조 압축 (예: docs.python.org 페이지들의 공통 보일러플레이트)
- 저장 텍스트, 인덱스 스냅샷, P2P 메시지 페이로드에 `msgpack + zstd`로 압축 적용

---

## 8.2 중복 제거

3계층 중복 제거로 불필요한 크롤링과 저장 방지:

| 계층 | 방법 | 목적 |
|------|------|------|
| 1. URL 정규화 | 정규 URL (소문자, 추적 파라미터 제거, 후행 슬래시) | 같은 페이지 재크롤링 방지 |
| 2. 정확 중복 | SHA-256 콘텐츠 해시를 DHT에 발행 | 다른 URL의 동일 콘텐츠 감지 |
| 3. 유사 중복 | SimHash, 해밍 거리 ≤ 3 | 미세 변형 감지 (광고, 타임스탬프) |

> 중복 제거 파이프라인의 상세 내용은 [신뢰 & 무결성](07-trust-integrity.md)을 참조하세요.

---

## 9. MCP 도구 명세

```python
@mcp.tool()
def web_search(query: str, top_k: int = 5, local_only: bool = False) -> list[SearchResult]:
    """통합 웹 검색. 로컬 + 원격 결과 병합. local_only=True로 오프라인 검색."""

@mcp.tool()
def fetch_page(url: str) -> PageContent:
    """특정 URL의 전문 텍스트 반환 (인덱스에 있으면 즉시, 없으면 크롤링)."""

@mcp.tool()
def crawl_url(url: str, depth: int = 0, force: bool = False) -> CrawlResult:
    """새 URL을 네트워크에 추가하고 크롤링. 같은 도메인만 따라감."""

@mcp.tool()
def fact_check(claim: str, top_k: int = 5) -> FactCheckResult:
    """인덱싱된 소스와 교차 검증."""

@mcp.tool()
def status() -> NodeStatus:
    """네트워크 현황: 피어 수, 인덱스 크기, 내 크레딧 등."""
```

---

## 9.1 설정 시스템

모든 노드 설정은 `~/.infomesh/config.toml`로 관리됩니다:

```toml
[node]
data_dir = "~/.infomesh/data"
log_level = "info"                  # debug, info, warning, error

[crawl]
max_concurrent = 5                  # 동시 HTTP 연결 수
politeness_delay = 1.0              # 같은 도메인 요청 간 초
max_depth = 0                       # 0 = 무제한 (rate limit과 중복 제거로 제어)

[network]
upload_limit_mbps = 5               # P2P 업로드 대역폭 상한
download_limit_mbps = 10            # P2P 다운로드 대역폭 상한
bootstrap_nodes = ["default"]       # 또는 multiaddr 목록

[index]
vector_search = true                # ChromaDB 활성화 (~500MB RAM 필요)
embedding_model = "all-MiniLM-L6-v2"

[llm]
enabled = false                     # 로컬 LLM 요약 활성화
runtime = "ollama"                  # ollama | llama_cpp | vllm
model = "qwen2.5:3b"
off_peak_start = "23:00"
off_peak_end = "07:00"
timezone = "auto"                   # 시스템에서 자동 감지

[storage]
max_index_size_gb = 50
compression_level = 3               # 로컬 저장소 zstd 레벨
encrypt_at_rest = false             # SQLCipher 필요
```

환경 변수로도 오버라이드 가능: `INFOMESH_CRAWL_MAX_CONCURRENT=10`

---

## 9.2 CLI 명령어

```bash
# 핵심 명령
infomesh start                      # 노드 시작 (크롤링 + 인덱싱 + MCP 서버)
infomesh stop                       # 정상 종료
infomesh status                     # 노드 상태 및 통계

# 검색
infomesh search "쿼리"              # 터미널에서 검색
infomesh search --local "쿼리"      # 로컬 전용 검색

# 관리
infomesh config show                # 현재 설정 표시
infomesh config set crawl.max_depth 10  # 깊이 제한 설정 (0=무제한)
infomesh keys export                # 백업용 키 내보내기
infomesh keys rotate                # 노드 ID 키 교체

# 데이터
infomesh index stats                # 인덱스 크기, 문서 수
infomesh index export               # 인덱스 스냅샷 내보내기
infomesh index import <파일>        # 인덱스 스냅샷 가져오기
```

### `infomesh status` 출력 예시

```
$ infomesh status

InfoMesh 노드 상태
────────────────────────
노드 ID:        abc123...def
가동 시간:      3일 14시간 22분
신뢰 점수:      0.72 (보통)
크레딧 잔액:    847.5

네트워크:
  연결된 피어:     42
  DHT 항목:       1,284,301

인덱스:
  문서:           156,832
  FTS5 크기:      2.3 GB
  벡터 인덱스:    892 MB

크롤링:
  크롤링 페이지:  12,481 (오늘: 342)
  대기열 크기:    1,203
  속도:           ~10 페이지/시간

LLM: 비활성
```

---

## 10. 스케일링 추정

### 전 세계 웹 규모

| 지표 | 수치 |
|------|------|
| Surface Web 페이지 | ~50억~100억 |
| 평균 텍스트 크기 | ~50KB/페이지 |
| 총 텍스트 데이터 | ~500TB |

### 참여 노드 수 별 커버리지

| 노드 수 | 노드당 저장 | 전체 커버리지 | 소요 시간 |
|---------|------------|-------------|----------|
| **1** | 50GB | 100만 페이지 | 3~7일 (MVP) |
| **100** | 50GB | 1억 페이지 | 1~2주 |
| **1,000** | 50GB | 10억 페이지 | 1개월 |
| **10,000** | 50GB | 50억+ 페이지 | 2~3개월 |
| **100,000** | 50GB | **전체 웹** | 상시 최신 |

> 각 참여자 부담: ~50GB 디스크 + 약간의 대역폭 = 아무 부담 없음

### 데이터 부트스트랩: Common Crawl

```
Common Crawl:
  - 비영리 단체가 매월 전체 웹을 크롤링해서 공개
  - ~30~50억 페이지/월
  - 필요한 도메인만 필터링해서 다운로드 가능

활용 방식:
  1. 설치 시 "기술 문서 팩" 선택 → 관련 Common Crawl 데이터 다운로드
  2. 즉시 로컬 인덱스 생성 → 바로 검색 가능
  3. 이후 P2P 네트워크에서 추가 데이터 동기화
```

---

## 11. 리소스 거버넌스 & 단계적 서비스 감소

### 리소스 프로파일

노드는 하드웨어에 맞는 4가지 사전 정의된 리소스 프로파일을 선택할 수 있습니다:

| 프로파일 | CPU 제한 | 네트워크 | 동시 크롤링 | LLM | 적합한 환경 |
|---------|---------|---------|-----------|-----|-----------|
| **minimal** | 1 코어, nice 19 | ↓1/↑0.5 Mbps | 1 | 비활성 | 노트북, 배터리 |
| **balanced** | 2 코어, nice 10 | ↓5/↑2 Mbps | 3 | 오프피크만 | 일반 데스크탑 (**기본값**) |
| **contributor** | 4 코어, nice 5 | ↓10/↑5 Mbps | 5 | 활성 | 상시 가동 서버 |
| **dedicated** | 제한 없음 | ↓50/↑25 Mbps | 10 | 활성+피어요청 | 전용 인프라 |

### 동적 리소스 거버너

`ResourceGovernor`가 시스템 부하를 모니터링하고 동적으로 조절합니다:
- CPU > 80% → 크롤링 50% 감속
- CPU < 30% → 프로파일 한도 내에서 크롤링 복원
- 네트워크 > 90% → P2P 트래픽 30% 감소

### 단계적 서비스 감소 (Graceful Degradation)

극한 부하 시 노드는 단계적으로 서비스를 줄입니다:

| 레벨 | 조건 | 동작 |
|------|------|------|
| 0 (정상) | 모든 지표 정상 범위 | 전체 기능 활성 |
| 1 (경고) | CPU 또는 메모리 상승 | LLM 요약 비활성, 새 크롤링 중단 |
| 2 (과부하) | 리소스 과도한 부담 | 원격 검색 비활성, 로컬만 응답 |
| 3 (심각) | 리소스 거의 고갈 | 읽기 전용 모드, 인덱싱 중단 |
| 4 (방어) | 시스템 위험 | 속도 제한 강화, 로컬 검색만 허용 |

---

*관련 문서: [개요](01-overview.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [보안 감사](08-security-audit.md) · [콘솔 대시보드](09-console-dashboard.md) · [MCP 연동](10-mcp-integration.md) · [배포](11-publishing.md) · [FAQ](12-faq.md)*
