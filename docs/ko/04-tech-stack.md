# InfoMesh — 기술 스택 및 코딩 규칙

---

## 1. 기술 스택

| 레이어 | 기술 | 비고 |
|--------|------|------|
| 언어 | **Python 3.12+** | 최신 문법 사용 (타입 힌트, `match`, `type` 문, `StrEnum`, `TypeVar` 기본값 등) |
| P2P 네트워크 | **libp2p (py-libp2p)** | DHT, NAT 트래버설, 암호화 내장 |
| DHT | **Kademlia** | 검증된 분산 해시 테이블 (인덱스 + 크롤링 조율) |
| 크롤링 | **httpx + asyncio** | 비동기 고성능 HTTP 클라이언트 |
| HTML 파싱 | **trafilatura** | 본문 추출 정확도 최고 |
| 키워드 인덱스 | **SQLite FTS5** | 설치 불필요, 임베디드 전문 검색 |
| 벡터 인덱스 | **ChromaDB** | 시맨틱 검색, 임베딩 저장 |
| MCP 서버 | **mcp-python-sdk** | VS Code / Claude 연동 |
| 관리 API | **FastAPI** | 로컬 상태 조회/설정 변경 |
| 직렬화 | **msgpack** | JSON보다 빠르고 작음 |
| 압축 | **zstd** | 레벨 조절 가능 압축; 유사 문서에 딕셔너리 모드 지원 |
| 로컬 LLM | **ollama / llama.cpp** | 선택적 로컬 요약 (Qwen 2.5, Llama 3.x 등) |
| 로깅 | **structlog** | 모든 라이브러리 코드의 구조화된 로깅 |
| 패키지 매니저 | **uv** | 빠른 Python 패키지/프로젝트 매니저 (pip/venv 대체) |

### 선택적 / 폴백 의존성

| 패키지 | 사용 시점 |
|---------|----------|
| **BeautifulSoup4** | trafilatura 실패 시 HTML 파싱 폴백 |
| **vLLM** | 고성능 GPU 추론 (올라마/llama.cpp 대안) |
| **sentence-transformers** | ChromaDB 벡터 인덱스용 임베딩 생성 |

---

## 2. 프로젝트 구조

```
infomesh/
├── pyproject.toml
├── infomesh/
│   ├── __init__.py          # 패키지 루트
│   ├── __main__.py          # CLI 진입점
│   ├── config.py            # 설정 관리
│   ├── p2p/                 # P2P 네트워크 레이어
│   │   ├── node.py          #   피어 메인 프로세스
│   │   ├── dht.py           #   Kademlia DHT
│   │   ├── routing.py       #   쿼리 라우팅
│   │   ├── replication.py   #   문서/인덱스 복제
│   │   └── protocol.py      #   메시지 프로토콜 정의
│   ├── crawler/             # 웹 크롤러
│   │   ├── worker.py        #   비동기 크롤링 워커
│   │   ├── scheduler.py     #   URL 할당 (DHT 기반)
│   │   ├── parser.py        #   HTML → 텍스트 추출
│   │   ├── robots.py        #   robots.txt 준수
│   │   ├── dedup.py         #   중복 제거 파이프라인 (URL, SHA-256, SimHash)
│   │   └── seeds.py         #   시드 URL 관리 및 카테고리 선택
│   ├── index/               # 검색 인덱스
│   │   ├── local_store.py   #   SQLite FTS5 로컬 인덱스
│   │   ├── vector_store.py  #   ChromaDB 벡터 인덱스
│   │   ├── distributed.py   #   DHT 역인덱스 발행/질의
│   │   └── ranking.py       #   BM25 + 신선도 + 신뢰도
│   ├── search/              # 검색 엔진
│   │   ├── query.py         #   쿼리 파싱 + 분산 검색 오케스트레이션
│   │   └── merge.py         #   다중 노드 결과 병합
│   ├── mcp/                 # MCP 서버
│   │   └── server.py        #   search(), search_local(), fetch_page(), crawl_url(), network_stats()
│   ├── api/                 # 로컬 관리 API
│   │   └── local_api.py     #   FastAPI (상태 조회, 설정 변경)
│   ├── credits/             # 인센티브 시스템
│   │   └── ledger.py        #   로컬 크레딧 원장
│   ├── trust/               # 신뢰 & 무결성
│   │   ├── attestation.py   #   콘텐츠 증명 체인 (서명, 검증)
│   │   ├── audit.py         #   랜덤 감사 시스템
│   │   └── scoring.py       #   통합 신뢰 점수 계산
│   ├── summarizer/          # 로컬 LLM 요약
│   │   ├── engine.py        #   LLM 백엔드 추상화 (ollama, llama.cpp)
│   │   ├── summarize.py     #   콘텐츠 요약 파이프라인
│   │   └── verify.py        #   요약 검증 (키팩트 앵커링, NLI)
│   └── compression/         # 데이터 압축
│       └── zstd.py          #   zstd 압축 + 딕셔너리 지원
├── bootstrap/
│   └── nodes.json           # 부트스트랩 노드 목록
├── seeds/                   # 내장 시드 URL 목록
│   ├── tech-docs.txt        #   기술 문서 URL
│   ├── academic.txt         #   학술 논문 소스 URL
│   └── encyclopedia.txt     #   백과사전 URL
├── tests/
│   ├── conftest.py          # 공유 픽스처
│   ├── test_dht.py
│   ├── test_crawler.py
│   ├── test_index.py
│   ├── test_search.py
│   ├── test_credits.py
│   ├── test_trust.py
│   ├── test_summarizer.py
│   └── test_mcp.py
└── docs/
```

---

## 3. 코딩 규칙

### 3.1 일반

- **언어**: 모든 소스 코드, 주석, docstring, 커밋 메시지, PR은 **영어**로 작성.
- **Python 버전**: 3.12+ — 최신 문법 사용 (`match/case`, `type` 문, `StrEnum`, `TypeVar` 기본값).
- **비동기 우선**: 모든 I/O 바운드 코드는 `async/await` + `asyncio` 사용. 이벤트 루프에서 블로킹 I/O 금지.
- **타입 힌트**: 모든 공개 함수와 클래스 속성에 필수. 전방 참조 시 `from __future__ import annotations` 사용.

### 3.2 스타일 & 포매팅

- 포매터: **ruff format** (기본 설정, 줄 길이 88).
- 린터: **ruff** — `select = ["E", "F", "I", "UP", "B", "SIM"]`.
- 임포트 순서: stdlib → 서드파티 → 로컬 (ruff/isort 적용).
- `os.path` 대신 `pathlib.Path` 선호.

### 3.3 네이밍

| 대상 | 규칙 | 예시 |
|------|------|------|
| 모듈/패키지 | `snake_case` | `local_store.py` |
| 클래스 | `PascalCase` | `SearchResult` |
| 함수/메서드/변수 | `snake_case` | `parse_query()` |
| 상수 | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| 비공개 멤버 | 단일 밑줄 | `_internal_state` |

### 3.4 에러 처리

- 구체적 예외 타입 사용 — 빈 `except:` 금지.
- `structlog` 또는 stdlib `logging` 사용 — 라이브러리 코드에서 `print()` 금지.
- 네트워크/IO 실패 시 지수 백오프(exponential backoff)로 재시도.

### 3.5 테스팅

- 프레임워크: **pytest** + **pytest-asyncio** (비동기 테스트).
- 테스트 파일은 소스 레이아웃 미러링: `infomesh/p2p/dht.py` → `tests/test_dht.py`.
- 모든 공개 함수/메서드에 최소 하나의 테스트.
- 인라인 셋업보다 fixture와 factory 사용.

---

## 4. 의존성 & 패키지 관리

### uv 사용

`uv`를 모든 의존성 해결, 가상 환경, 프로젝트 관리에 사용.

- 모든 의존성은 `pyproject.toml`의 `[project.dependencies]`에 선언.
- 개발 의존성은 `[dependency-groups]` (PEP 735) 또는 `[project.optional-dependencies.dev]`에 선언.
- 최소 버전만 고정 (예: `httpx>=0.27`), 정확한 버전 고정 없음.
- 락 파일: `uv.lock` — 재현 가능한 빌드를 위해 저장소에 커밋.
- `requirements.txt` 없음, `pip` 없음 — `uv` 명령만 사용.

### 주요 명령

```bash
uv sync              # 모든 의존성 설치 (.venv 자동 생성)
uv sync --dev        # 개발 의존성 포함 설치
uv add <package>     # 새 의존성 추가
uv add --dev <pkg>   # 개발 의존성 추가
uv run <command>     # 프로젝트 환경에서 명령 실행
uv run pytest        # 테스트 실행
uv run infomesh start  # 애플리케이션 실행
```

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [보안 감사](08-security-audit.md) · [콘솔 대시보드](09-console-dashboard.md) · [MCP 연동](10-mcp-integration.md) · [배포](11-publishing.md)*
