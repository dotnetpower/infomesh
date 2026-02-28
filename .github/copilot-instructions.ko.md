# InfoMesh — Copilot 지침

## 프로젝트 개요

InfoMesh는 **LLM 전용으로 설계된 완전 탈중앙화 P2P 검색 엔진**입니다.
P2P 네트워크를 통해 웹을 크롤링·인덱싱·검색하고,
MCP(Model Context Protocol)를 통해 결과를 제공합니다 — 사람용 UI는 없습니다.

**미션**: InfoMesh는 상용 검색 사업자와 경쟁하지 않습니다. 이 기업들은 _사람_ 검색을
대규모로 서비스하며 광고로 수익을 창출합니다. InfoMesh는 _최소한의 충분한_ 검색 기능을
LLM에게 MCP를 통해 무료로 제공하여, 쿼리당 과금 없이 AI 어시스턴트의 실시간 웹 접근을
민주화합니다. 기존 검색 사업자와 경쟁이 아닌 보완 관계의 커뮤니티 공공 유틸리티입니다.

## 핵심 원칙

| 원칙 | 설명 |
|------|------|
| **완전 탈중앙화** | 중앙 서버 없음. 모든 노드가 허브이자 참여자 |
| **LLM 퍼스트** | 브라우저 UI 없음. LLM 소비에 최적화된 순수 텍스트 API |
| **기여 = 보상** | 크롤링 기여가 많을수록 검색 쿼터 증가 (협력적 상호호혜 모델) |
| **오프라인 가능** | 인터넷 없이도 로컬 인덱스 검색 가능 |
| **프라이버시** | 검색 쿼리가 중앙에 기록되지 않음 |

## 기술 스택

| 레이어 | 기술 | 비고 |
|--------|------|------|
| 언어 | **Python 3.12+** | 최신 Python 기능 사용 (타입 힌트, `match`, `type` 문, `StrEnum` 등) |
| P2P 네트워크 | **libp2p (py-libp2p)** | DHT, Noise 암호화 내장. **trio 사용 (asyncio 아님)** — 아래 참고 |
| DHT | **Kademlia** | 인덱스 및 크롤링 조율용 분산 해시 테이블 |
| 크롤링 | **httpx + asyncio** | 비동기 우선 HTTP 클라이언트 |
| HTML 파싱 | **trafilatura** | 본문 추출 정확도 최고 |
| 키워드 인덱스 | **SQLite FTS5** | 설치 불필요, 내장 전문 검색 |
| 벡터 인덱스 | **ChromaDB** | 임베딩 기반 시맨틱 검색 |
| MCP 서버 | **mcp-python-sdk** | VS Code / Claude / Cursor / Windsurf 연동 |
| 관리 API | **FastAPI** | 로컬 상태 조회 및 설정 엔드포인트 |
| 직렬화 | **msgpack** | JSON보다 빠르고 작음 |
| 압축 | **zstd** | 레벨 조절 가능 압축; 유사 문서에 딕셔너리 모드 지원 |
| 로컬 LLM | **ollama / llama.cpp** | 선택적 로컬 요약 (Qwen 2.5, Llama 3.x 등) |
| 로깅 | **structlog** | 모든 라이브러리 코드의 구조화된 로깅 |
| 패키지 매니저 | **uv** | 빠른 Python 패키지/프로젝트 매니저 (pip/venv 대체) |
| 빌드 백엔드 | **hatchling** | PyPI 배포용 PEP 517 빌드 백엔드 |

## 프로젝트 구조

```
infomesh/
├── pyproject.toml
├── infomesh/
│   ├── __init__.py          # 패키지 루트 (__version__)
│   ├── __main__.py          # CLI 진입점 (Click 앱)
│   ├── config.py            # 설정 관리 (TOML + 환경 변수)
│   ├── services.py          # 중앙 AppContext + index_document 오케스트레이션
│   ├── db.py                # SQLite 스토어 베이스 클래스 (WAL, 라이프사이클)
│   ├── hashing.py           # SHA-256 해싱 유틸리티 (content_hash, short_hash)
│   ├── security.py          # SSRF 보호 (URL 검증, IP 필터링)
│   ├── types.py             # 공유 Protocol 타입 (PEP 544)
│   ├── cli/                 # Click CLI 명령어
│   │   ├── __init__.py      #   CLI 앱 등록
│   │   ├── config.py        #   `infomesh config show/set` 명령어
│   │   ├── crawl.py         #   `infomesh crawl`, `mcp`, `dashboard` 명령어
│   │   ├── index.py         #   `infomesh index stats/export/import` 명령어
│   │   ├── keys.py          #   `infomesh keys show/rotate` 명령어
│   │   ├── search.py        #   `infomesh search` 명령어
│   │   └── serve.py         #   `infomesh start/stop/status/serve` 명령어
│   ├── p2p/                 # P2P 네트워크 레이어
│   │   ├── node.py          #   피어 메인 프로세스
│   │   ├── dht.py           #   Kademlia DHT
│   │   ├── keys.py          #   Ed25519 키 쌍 관리 + 로테이션
│   │   ├── routing.py       #   쿼리 라우팅 (지연시간 인식)
│   │   ├── replication.py   #   문서/인덱스 복제
│   │   ├── protocol.py      #   메시지 프로토콜 정의 (msgpack)
│   │   ├── peer_profile.py  #   피어 지연시간 추적 & 대역폭 분류
│   │   ├── sybil.py         #   시빌 방어 (PoW 노드 ID + 서브넷 제한)
│   │   ├── load_guard.py    #   NodeLoadGuard (QPM + 동시성 제한)
│   │   ├── mdns.py          #   mDNS 로컬 피어 탐색 (멀티캐스트 UDP)
│   │   ├── throttle.py      #   BandwidthThrottle (토큰 버킷 업/다운로드)
│   │   └── index_submit.py  #   엔터프라이즈 분리: DMZ 크롤러 → 프라이빗 인덱서
│   ├── crawler/             # 웹 크롤러
│   │   ├── worker.py        #   비동기 크롤링 워커
│   │   ├── scheduler.py     #   URL 할당 (DHT 기반)
│   │   ├── parser.py        #   HTML → 텍스트 추출
│   │   ├── robots.py        #   robots.txt 준수
│   │   ├── dedup.py         #   중복 제거 파이프라인 (URL, SHA-256, SimHash)
│   │   ├── simhash.py       #   SimHash 근접 중복 감지
│   │   ├── seeds.py         #   시드 URL 관리 및 카테고리 선택
│   │   ├── recrawl.py       #   적응형 자동 재크롤링 스케줄러
│   │   └── url_assigner.py  #   DHT 기반 URL 자동 할당
│   ├── index/               # 검색 인덱스
│   │   ├── local_store.py   #   SQLite FTS5 로컬 인덱스
│   │   ├── vector_store.py  #   ChromaDB 벡터 인덱스
│   │   ├── distributed.py   #   DHT 역인덱스 발행/질의
│   │   ├── ranking.py       #   BM25 + 신선도 + 신뢰도 + 권위 점수 랭킹
│   │   ├── link_graph.py    #   링크 그래프 + 도메인 권위 (PageRank 스타일)
│   │   ├── snapshot.py      #   인덱스 스냅샷 내보내기/가져오기 (zstd 압축)
│   │   └── commoncrawl.py   #   Common Crawl 데이터 가져오기
│   ├── search/              # 검색 엔진
│   │   ├── query.py         #   쿼리 파싱 + 분산 검색 오케스트레이션
│   │   ├── merge.py         #   다중 노드 결과 병합
│   │   ├── cache.py         #   쿼리 결과 LRU 캐시 (TTL + 자동 만료)
│   │   ├── reranker.py      #   LLM 재랭킹 (프롬프트 기반 후처리 랭킹)
│   │   ├── cross_validate.py #  쿼리 결과 교차 검증
│   │   └── formatter.py     #   검색 결과 텍스트 포맷팅 (CLI/MCP/대시보드)
│   ├── mcp/                 # MCP 서버 (Model Context Protocol)
│   │   └── server.py        #   search(), search_local(), fetch_page(), crawl_url(), network_stats()
│   ├── api/                 # 로컬 관리 API
│   │   └── local_api.py     #   FastAPI (헬스, 상태, 설정, 인덱스 통계, 크레딧)
│   ├── credits/             # 인센티브 시스템
│   │   ├── ledger.py        #   로컬 크레딧 원장 (ActionType enum, 가중치)
│   │   ├── farming.py       #   크레딧 파밍 감지 (수습 기간, 이상 탐지)
│   │   ├── scheduling.py    #   에너지 인식 스케줄링 (심야 LLM 보너스 + TZ 검증)
│   │   ├── timezone_verify.py #   심야 시간대 검증 (IP 교차 확인)
│   │   └── verification.py  #   P2P 크레딧 검증 (서명된 항목 + 머클 증명)
│   ├── trust/               # 신뢰 & 무결성
│   │   ├── attestation.py   #   콘텐츠 증명 체인 (서명, 검증, 머클 루트)
│   │   ├── audit.py         #   랜덤 감사 시스템 + 머클 증명 감사
│   │   ├── merkle.py        #   머클 트리 (인덱스 전체 무결성, 멤버십 증명)
│   │   ├── reputation.py    #   LLM 평판 기반 신뢰 (EMA 품질 추적, 등급 시스템)
│   │   ├── scoring.py       #   통합 신뢰 점수 계산
│   │   ├── detector.py      #   파밍 + 신뢰 통합 감지기
│   │   ├── dmca.py          #   DMCA 삭제 요청 전파
│   │   └── gdpr.py          #   GDPR 분산 삭제 레코드
│   ├── summarizer/          # 로컬 LLM 요약
│   │   ├── engine.py        #   LLM 백엔드 추상화 (ollama, llama.cpp)
│   │   ├── peer_handler.py  #   피어 간 요약 요청 처리
│   │   └── verify.py        #   요약 검증 (키팩트 앵커링, NLI)
│   ├── dashboard/           # 콘솔 앱 UI (Textual TUI)
│   │   ├── app.py           #   DashboardApp (메인 Textual 애플리케이션)
│   │   ├── bgm.py           #   BGM 플레이어 (mpv/ffplay/aplay)
│   │   ├── text_report.py   #   Rich 기반 텍스트 리포트 (비대화형 폴백)
│   │   ├── data_cache.py    #   대시보드 데이터 캐싱 레이어
│   │   ├── dashboard.tcss   #   Textual CSS 스타일시트
│   │   ├── screens/         #   탭 페인 (개요, 크롤링, 검색, 네트워크, 크레딧, 설정)
│   │   └── widgets/         #   재사용 위젯 (sparkline, bar_chart, resource_bar, live_log)
│   ├── resources/           # 자원 거버넌스
│   │   ├── profiles.py      #   사전 정의 자원 프로필 (minimal/balanced/contributor/dedicated)
│   │   ├── governor.py      #   동적 자원 스로틀링 (CPU/메모리 모니터링, 성능 저하 단계)
│   │   └── preflight.py     #   시작 전 디스크 공간 + 네트워크 연결 점검
│   └── compression/         # 데이터 압축
│       └── zstd.py          #   zstd 압축 + 딕셔너리 지원
├── examples/                # Python 사용 예제
│   ├── README.md            #   예제 목차
│   ├── basic_search.py      #   간단한 검색 데모
│   ├── crawl_and_search.py  #   크롤링 + 검색 워크플로우
│   ├── credit_status.py     #   크레딧 원장 조회
│   ├── fetch_page.py        #   단일 URL 가져오기
│   ├── hybrid_search.py     #   FTS5 + 벡터 하이브리드 검색
│   └── mcp_client.py        #   MCP 클라이언트 연동 데모
├── bootstrap/
│   └── nodes.json           # 부트스트랩 노드 목록
├── seeds/                   # 내장 시드 URL 목록
│   ├── tech-docs.txt        #   기술 문서 URL
│   ├── academic.txt         #   학술 논문 소스 URL
│   ├── encyclopedia.txt     #   백과사전 URL
│   ├── quickstart.txt       #   퀵스타트 시드 URL (큐레이팅된 스타터 셋)
│   └── search-strategy.txt  #   검색 전략 시드
├── tests/                   # 52개 테스트 파일 (pytest + pytest-asyncio)
└── docs/                    # 문서 (EN + KO)
```

## 코딩 컨벤션

### 일반 규칙

- **언어**: 모든 소스 코드, 주석, 독스트링, 커밋 메시지, PR 설명은 **영어**로 작성.
- **Python 버전**: 3.12+ — 최신 문법 사용 (`match/case`, `type` 문, `StrEnum`, `TypeVar` 디폴트).
- **비동기 우선**: 모든 I/O 바운드 코드는 `async/await` + `asyncio` 사용. 이벤트 루프에서 블로킹 I/O 금지.
- **⚠️ trio 예외**: `py-libp2p`는 **trio** 사용 (asyncio 아님). 모든 libp2p/P2P 코드는 `trio.run()`에서 실행. 테스트에서 `asyncio_mode=auto` 충돌 방지를 위해 `_run_trio()` 래퍼 사용.
- **타입 힌트**: 모든 공개 함수와 클래스 속성에 필수. 전방 참조 시 `from __future__ import annotations` 사용.

### 단일 책임 원칙 (SRP)

모든 모듈, 클래스, 함수는 **하나의 명확한 책임**을 가져야 합니다.

- **모듈**: 모듈 하나 = 관심사 하나. 관련 없는 로직을 하나의 파일에 혼합하지 않음. 모듈이 ~300줄을 넘으면 분할 고려.
- **클래스**: 각 클래스는 하나의 액터 또는 개념을 캡슐화. 변경 이유가 하나만 있어야 함.
  - ✅ `RobotsChecker` — robots.txt 준수만 확인.
  - ✅ `Compressor` — zstd 압축/해제만 처리.
  - ❌ 페이지 크롤링과 인덱싱을 동시에 하는 클래스.
- **함수**: 각 함수는 하나만 수행. 오케스트레이션과 비즈니스 로직을 동시에 처리하는 함수 금지.
- **CLI 명령어**: 라이브러리 코드에 위임하는 얇은 래퍼. Click 핸들러에 비즈니스 로직 금지.
- **MCP 도구 핸들러**: CLI와 동일 — 서비스 레이어 함수에 디스패치, 비즈니스 로직 인라인 금지.

### 스타일 & 포맷팅

- 포맷터: **ruff format** (기본 설정, 라인 길이 88).
- 린터: **ruff check** (`select = ["E", "F", "I", "UP", "B", "SIM"]`).
- 임포트 순서: 표준 라이브러리 → 서드파티 → 로컬 (ruff/isort로 강제).
- `os.path` 대신 `pathlib.Path` 사용 권장.

### 네이밍

- 모듈/패키지: `snake_case`
- 클래스: `PascalCase`
- 함수/메서드/변수: `snake_case`
- 상수: `UPPER_SNAKE_CASE`
- 비공개 멤버: 단일 밑줄 접두사 `_name`

### 에러 처리

- 구체적인 예외 타입 사용, 맨 `except:` 금지.
- 에러 로깅은 `structlog` 또는 표준 `logging` — 라이브러리 코드에서 `print()` 금지.
- 네트워크/I/O 실패는 적절한 경우 지수 백오프로 재시도.

### 테스트

- 프레임워크: **pytest** + 비동기 테스트용 **pytest-asyncio**.
- 테스트 파일은 소스 구조 반영: `infomesh/p2p/dht.py` → `tests/test_dht.py`.
- 모든 공개 함수/메서드에 최소 하나의 테스트 필요.
- 인라인 셋업 대신 fixture와 factory 사용.

### 의존성 & 패키지 관리

- **패키지 관리자**: **uv** — 모든 의존성 해석, 가상 환경, 프로젝트 관리에 사용.
- 모든 의존성은 `pyproject.toml`의 `[project.dependencies]`에 선언.
- 개발 의존성은 `[dependency-groups]` (PEP 735) 또는 `[project.optional-dependencies.dev]`에 선언.
- 최소 버전만 고정 (예: `httpx>=0.27`), 정확한 버전 핀 금지.
- 락 파일: `uv.lock` — 재현 가능한 빌드를 위해 저장소에 커밋.
- `requirements.txt` 없음, `pip` 사용 금지 — `uv` 명령어 사용:
  - `uv sync` — 모든 의존성 설치 (`.venv` 자동 생성).
  - `uv sync --dev` — 개발 의존성 포함 설치.
  - `uv add <package>` — 새 의존성 추가.
  - `uv add --dev <package>` — 개발 의존성 추가.
  - `uv run <command>` — 프로젝트 환경에서 명령 실행.
  - `uv run pytest` — 테스트 실행.
  - `uv run infomesh start` — 애플리케이션 실행.

## 아키텍처 가이드라인

### P2P / DHT

- DHT는 Kademlia 기반. 노드 ID는 160비트. 거리 = XOR 메트릭.
- DHT의 두 가지 용도:
  1. **역인덱스**: `hash(keyword)` → `(peer_id, doc_id, score)` 포인터 목록, 해시에 가장 가까운 노드들에 저장.
  2. **크롤링 조율**: `hash(URL)` → 해시에 가장 가까운 노드가 해당 URL을 "소유"하고 크롤링 담당.
- 복제 팩터: 모든 저장 데이터에 대해 최소 N=3.

### 크롤링

- `robots.txt`를 항상 존중 — 엄격한 opt-out 준수 구현.
- 기본 정중함: 도메인당 초당 1회 이하 요청.
- 컨텐츠 추출에 `trafilatura` 사용. trafilatura가 `None`을 반환하면 페이지 건너뜀.
- 원문 텍스트 + 메타데이터 (제목, URL, 크롤링 시각, 언어) 저장.
- **시드 전략**: 카테고리별 큐레이팅된 시드 리스트 (기술 문서, 학술, 백과사전 등) + Common Crawl URL 가져오기 + DHT 할당 URL + 사용자 `crawl_url()` 제출 + 링크 팔로잉.
- **중복 제거**: 3계층 접근 — URL 정규화(canonical), 정확 중복(SHA-256 콘텐츠 해시를 DHT에 발행), 유사 중복(SimHash, 해밍 거리 ≤ 3).
- **크롤링 락**: 크롤링 전 `hash(url) = CRAWLING`을 DHT에 발행하여 레이스 컨디션 방지. 5분 후 타임아웃.
- **SPA/JS 렌더링**: Phase 0에서는 정적 HTML에 집중. JS가 필요한 페이지는 `js_required` DHT 태그를 사용하여 Playwright 지원 노드에 위임 (Phase 4).
- **대역폭 제한**: P2P 기본 ≤5 Mbps 업로드 / 10 Mbps 다운로드. `~/.infomesh/config.toml`로 설정 가능. 노드당 최대 5개 동시 크롤링 연결.
- **`crawl_url()` 속도 제한**: 노드당 60 URL/시간, 도메인당 대기 URL 10개, 최대 depth=3.

### 인덱싱

- 로컬 키워드 검색: SQLite FTS5 + BM25 랭킹.
- 로컬 벡터 검색: ChromaDB로 시맨틱/임베딩 기반 쿼리. 기본 모델: `all-MiniLM-L6-v2`. 벡터 검색은 **선택 사항** — FTS5 단독으로 동작 가능.
- 분산 인덱스: 크롤링 후 키워드 해시를 DHT에 발행.

### 검색 플로우

1. 쿼리 파싱 → 키워드 추출.
2. 로컬 인덱스 우선 검색 (목표: <10ms).
3. 키워드 해시를 DHT를 통해 담당 노드로 라우팅 (목표: ~500ms).
4. 로컬 + 원격 결과 병합, BM25 + 신선도 + 신뢰도로 랭킹.
5. 필요 시 상위 N개 결과의 전문 텍스트 가져오기 (목표: ~200ms).
6. MCP를 통해 반환 → 총 지연시간 목표: ~1초.

### MCP 도구

MCP 서버가 제공하는 도구:

| 도구 | 설명 |
|------|------|
| `search(query, limit)` | 네트워크 전체 검색, 로컬 + 원격 결과 병합 |
| `search_local(query, limit)` | 로컬 전용 검색 (오프라인 가능) |
| `fetch_page(url)` | URL의 전문 텍스트 반환 (인덱스 또는 실시간 크롤링) |
| `crawl_url(url, depth)` | URL을 네트워크에 추가하고 크롤링 |
| `network_stats()` | 네트워크 현황: 피어 수, 인덱스 크기, 크레딧 |

### 로컬 LLM 요약

- 노드는 선택적으로 로컬 LLM을 실행하여 크롤링된 컨텐츠의 요약을 생성할 수 있음.
- 요약은 전문 텍스트와 함께 로컬 인덱스에 저장되고 DHT를 통해 공유.
- 로컬 LLM이 있는 노드는 다른 피어의 요약 요청도 처리 가능.
- 추천 모델 (우선순위 순):
  1. **Qwen 2.5 (3B/7B)** — 다국어 품질 최고, Apache 2.0 라이선스.
  2. **Llama 3.x (3B/8B)** — 범용 성능 최강, 커뮤니티 생태계.
  3. **Gemma 3 (4B/12B)** — 크기 대비 성능 우수.
  4. **Phi-4 (3.8B/14B)** — 추론/요약에 강점.
- 지원 런타임: **ollama** (가장 간단), **llama.cpp** (경량 CPU), **vLLM** (GPU 처리량).
- 최소 사양: 8GB RAM CPU에서 3B Q4 양자화 모델.
- summarizer 모듈은 LLM 백엔드를 추상화하여 런타임을 교체 가능하게 설계.
- **에너지 인식 스케줄링**: 노드는 로컬 시간대와 심야전기 시간대(예: 23:00–07:00)를 설정 가능. LLM 집중 작업(일괄 요약, 피어 요청 처리)은 심야 시간대에 우선 스케줄링. 심야 시간대 LLM 운영 시 모든 LLM 관련 크레딧에 **1.5배 배율** 적용.
- **요약 검증**: 3단계 파이프라인 — (1) 키팩트 앵커링 + NLI 모순 감지로 자가 검증, (2) 복제 노드들의 독립 요약으로 교차 검증, (3) 검증 이력 기반 평판 신뢰.
- 모든 요약에 `content_hash`를 함께 저장하여 누구든 원문 대조 가능.

### 크레딧 시스템

크레딧은 피어별 로컬 추적 — 블록체인 불필요.

#### 공식

```
C_earned = Σ (W_i × Q_i × M_i)
```

- `W_i` = 행동 유형 `i`의 자원 가중치 (자원 비용에 비례하여 정규화)
- `Q_i` = 수행 수량
- `M_i` = 시간 배율 (기본 1.0; 심야 LLM 작업 시 1.5)

#### 자원 가중치

가중치는 **크롤링 = 1.0**을 기준 단위로 정규화.
모든 가중치는 대략적인 상대 자원 비용(CPU, 대역폭, 저장공간)을 반영.

| 행동 | 가중치 (W) | 카테고리 | 근거 |
|------|-----------|---------|------|
| 크롤링 | **1.0** /페이지 | 기본 | 기준 단위: CPU + 대역폭 + 파싱 |
| 쿼리 처리 | **0.5** /쿼리 | 기본 | 크롤링보다 자원 소모 적음 |
| 문서 호스팅 | **0.1** /시간 | 기본 | 수동적 저장공간 + 대역폭 |
| 네트워크 가동 | **0.5** /시간 | 기본 | 네트워크 가용성 가치 |
| LLM 요약 (자체) | **1.5** /페이지 | LLM | 높은 연산 비용, 지배적이지 않게 상한 설정 |
| LLM 요청 처리 (피어용) | **2.0** /요청 | LLM | 타인 기여, 네트워크 가치 높음 |
| Git PR — 문서/타이포 | **1,000** /머지된 PR | 보너스 | 문서 또는 타이포 수정 |
| Git PR — 버그 수정 | **10,000** /머지된 PR | 보너스 | 테스트 포함 버그 수정 |
| Git PR — 기능 추가 | **50,000** /머지된 PR | 보너스 | 새로운 기능 구현 |
| Git PR — 핵심/아키텍처 | **100,000** /머지된 PR | 보너스 | 핵심 아키텍처 또는 대규모 기능 |

#### 시간 배율 (M)

- 모든 **기본** 행동: `M = 1.0` 항상.
- **LLM** 행동 일반 시간대: `M = 1.0`.
- **LLM** 행동 심야 시간대: `M = 1.5`.
- 심야 시간대는 노드별 설정 (기본값: 현지 시간 23:00–07:00).
- 네트워크는 일괄 요약 요청을 현재 심야 시간대인 노드에 우선 라우팅.

#### 검색 비용

```
C_search = 0.1 / tier(contribution_score)
```

| 티어 | 기여 점수 | 검색 비용 | 설명 |
|------|----------|----------|------|
| 1 | < 100 | 0.100 | 신규 / 낮은 기여자 |
| 2 | 100 – 999 | 0.050 | 중간 기여자 |
| 3 | ≥ 1000 | 0.033 | 높은 기여자 |

#### 공정성 보장

- **크롤링만 하는 노드** (LLM 없음)가 시간당 10페이지를 크롤링하면 시간당 10크레딧 획득 → 최악 티어에서도 **시간당 100회 검색** 가능. 비 LLM 참여자가 자원 부족에 빠지지 않음을 보장.
- **LLM 가중치 상한**: LLM 관련 수입이 노드 전체 크레딧의 ~60%를 넘지 않도록 설계. LLM은 네트워크를 위한 보너스이지 참여 필수 조건이 아님.
- **가동 가중치 (0.5/시간)**: 하드웨어 성능과 무관하게 상시 접속 노드를 보상.
- **검색은 절대 차단되지 않음** — 크레딧이 0이 되어도 검색 가능 (아래 무이자 채무 참고).
- 기여 점수가 높은 노드는 높은 신뢰도와 쿼리 라우팅 우선권 부여.

#### 무이자 채무 (Zero-Dollar Debt)

크레딧이 소진(잔액 ≤ 0)되면 유예/채무 주기에 진입:

| 상태 | 조건 | 검색 비용 |
|------|------|----------|
| **NORMAL** | 잔액 > 0 | 티어 기반 일반 비용 |
| **GRACE** | 잔액 ≤ 0, 72시간 이내 | 일반 비용 (페널티 없음) |
| **DEBT** | 잔액 ≤ 0, 72시간 초과 | 2배 일반 비용 |

- 채무는 **크레딧** 단위, 돈이 아님. 신용카드, 달러, 구독 없음.
- 회복: 일반 기여(크롤링, 호스팅, 가동)로 크레딧 획득.
- 잔액이 양수로 돌아오면 채무 상태가 NORMAL로 리셋.

### 콘텐츠 무결성 & 신뢰

- **콘텐츠 증명**: 크롤링 시 `SHA-256(raw_response)` + `SHA-256(extracted_text)` 계산, 피어 개인키로 서명, DHT에 발행.
- **랜덤 감사**: 노드당 ~1회/시간. 3개 감사 노드가 랜덤 URL을 독립 재크롤링하여 원본 노드와 `content_hash` 비교. 불일치 = 신뢰 감점.
- **통합 신뢰 점수**: `Trust = 0.15×가동시간 + 0.25×기여량 + 0.40×감사통과율 + 0.20×요약품질`. 등급: 신뢰(≥0.8), 보통(0.5–0.8), 의심(0.3–0.5), 불신(<0.3).
- **조작 탐지**: 감사 실패 3회 → 네트워크 격리. 소스 URL은 항상 재크롤링 가능한 근거.

### 네트워크 보안

- **시빌 방어**: PoW 노드 ID 생성 (평균 CPU에서 ~30초) + DHT 버킷당 /24 서브넷 최대 3개 노드.
- **이클립스 방어**: 3개 이상 독립 부트스트랩 소스 + 라우팅 테이블 서브넷 다양성 + 주기적 라우팅 갱신.
- **DHT 포이즈닝 방어**: 키워드별 발행 속도 제한 (10회/시간/노드) + 서명된 발행물 + 콘텐츠 해시 검증.
- **크레딧 파밍 방지**: 신규 노드 24시간 수습 기간 (감사 빈도 상향) + 통계 이상 감지 + 원본 HTTP 해시 감사.
- **키 관리**: `~/.infomesh/keys/`에 Ed25519 키 쌍, `infomesh keys rotate`로 교체, 서명된 DHT 레코드로 폐기.

## 법적 준수

- **robots.txt**: 엄격 준수.
- **저작권**: 전문 텍스트는 캐시 목적으로만 저장; 검색 결과에는 스니펫만 반환.
- **GDPR**: 개인정보 포함 페이지 제외 옵션 제공. 서명된 DHT 레코드를 통한 분산 삭제.
- **이용약관**: 크롤링 금지 사이트 블랙리스트 유지. robots.txt 이상의 이용약관 패턴 자동 감지.
- **DMCA**: 서명된 삭제 요청을 DHT를 통해 전파; 노드는 24시간 내 준수 의무.
- **`fetch_page()`**: 페이월 감지, 캐시 TTL (7일), 호출당 최대 100KB, 출처 표시 필수.
- **LLM 요약**: AI 생성 라벨 표시, `content_hash`로 원본 연결, 항상 원본 URL 제공.

## 개발 단계

| 단계 | 초점 | 상태 |
|------|------|------|
| 0 | MVP — 단일 노드 로컬 크롤링 + 인덱스 + MCP + robots.txt + 중복제거 + 시드 + zstd + 설정 + CLI | **완료** |
| 1 | 인덱스 공유 — 스냅샷, Common Crawl 가져오기, 벡터 검색, SimHash | **완료** |
| 2 | P2P 네트워크 — libp2p, DHT, 분산 크롤링 및 인덱스, 크롤링 락, 시빌/이클립스 방어 | **완료** |
| 3 | 품질 + 인센티브 — 랭킹, 크레딧, 신뢰 점수, 증명 체인, 감사, LLM 검증, DMCA/GDPR | **완료** |
| 4 | 프로덕션 — 링크 그래프, LLM 재랭킹, 출처 표시, 법적 준수 | **완료** |
| 5A | 코어 안정성 — 자원 거버너, 자동 재크롤링, 쿼리 캐시, 로드 가드 | **완료** |
| 5B | 검색 품질 & 신뢰 — 지연시간 인식 라우팅, 머클 트리 무결성 | **완료** |
| 5C | 커뮤니티 & 릴리스 준비 — Docker, 키 로테이션, mDNS, LICENSE, CONTRIBUTING | **완료** |
| 5D | 마무리 — LLM 평판, 시간대 검증, PyPI 준비, README | **완료** |

## MCP 웹 검색 API — 연동 & 키워드

InfoMesh는 LLM 기반 에이전트나 IDE가 연동할 수 있는 **MCP 기반 웹 검색 API**로
기능합니다. 유료 웹 검색 API에 대한 실시간, 오픈소스 대안입니다.

### InfoMesh가 제공하는 것 (MCP 검색 도구로서)

- **AI 에이전트를 위한 웹 검색** — 모든 MCP 호환 클라이언트에서 오픈 웹 검색
  (VS Code Copilot, Claude Desktop, Cursor, Windsurf, Continue, Cline 등)
- **실시간 웹 크롤링 API** — 임의의 URL을 온디맨드로 크롤링하고 즉시 인덱싱
- **전체 페이지 텍스트 가져오기** — URL로 웹 페이지의 추출된 전체 텍스트 반환
- **오프라인 / 로컬 검색** — 인터넷 없이 로컬 캐시 인덱스 검색
- **네트워크 통계 API** — 노드 상태, 피어 수, 인덱스 크기, 크레딧 잔액 조회

### MCP 도구 사양

| MCP 도구명 | HTTP 유사 표현 | 입력 | 출력 |
|-----------|---------------|------|------|
| `search` | `GET /search?q=...&limit=10` | `query: str`, `limit: int` | URL 포함 랭킹된 텍스트 스니펫 |
| `search_local` | `GET /search/local?q=...` | `query: str`, `limit: int` | 로컬 전용 랭킹 결과 |
| `fetch_page` | `GET /fetch?url=...` | `url: str` | 추출된 전체 텍스트 (최대 100KB) |
| `crawl_url` | `POST /crawl` | `url: str`, `depth: int` | 크롤링 확인 + 페이지 메타데이터 |
| `network_stats` | `GET /stats` | (없음) | JSON: 피어, 인덱스 크기, 크레딧 |

### IDE / 클라이언트 연동

InfoMesh는 **stdio 트랜스포트**를 통해 모든 MCP 호환 클라이언트와 연동합니다:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "infomesh",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

**지원 클라이언트**: VS Code (GitHub Copilot), Claude Desktop, Cursor, Windsurf,
Continue, Cline, 모든 MCP 호환 LLM 에이전트.

### 검색 API 비교 키워드

InfoMesh는 다음에 대한 **무료, 오픈소스, 탈중앙화** 대안으로 볼 수 있습니다:
- 상용 웹 검색 API (쿼리당 과금, API 키 필요)
- SaaS LLM용 실시간 웹 접근 (쿼리당 과금)
- 독점 MCP 웹 검색 도구
- 그라운딩 / 검색 증강 생성 (RAG) 데이터 소스
- 상용 웹 크롤러 API

**핵심 차별점**:
- API 키 불필요 — 완전 탈중앙화, 중앙 서버 없음
- 쿼리당 과금 없음 — 기여(크롤링, 호스팅, 가동)로 검색 크레딧 획득
- 프라이버시 우선 — 검색 쿼리가 중앙에 기록되지 않음
- 셀프 호스팅 — 자신의 머신에서 실행, 자신의 인덱스
- MCP 네이티브 — LLM 도구 사용 연동을 위해 특별 설계
- 오프라인 가능 — 인터넷 없이 로컬 인덱스 동작
- 커뮤니티 주도 — 오픈소스, P2P, 벤더 종속 없음
