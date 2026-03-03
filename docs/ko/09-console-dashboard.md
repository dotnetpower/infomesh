# InfoMesh 콘솔 대시보드 (콘솔 앱 UI)

## 개요

InfoMesh의 노드 상태를 모니터링하기 위한 **콘솔 앱 UI (Textual 기반 TUI)**
대시보드입니다. 별도의 웹 서버 없이 터미널에서 직접 실행되므로 SSH 접속, 모바일 터미널 앱
(Termux, Blink 등), 저사양 서버 환경에서도 사용할 수 있습니다.

## 기술 선택: Textual

| 항목 | 선택 | 이유 |
|------|------|------|
| 프레임워크 | **Textual** (≥1.0) | Rich 기반, 반응형 CSS 레이아웃, 마우스/키보드 지원 |
| 대안 비교 | curses/blessed/urwid | Textual이 CSS 레이아웃, 위젯 시스템, 테스트 가능성에서 압도적 |

## 탭 구성 (5개)

### Tab 1: Overview (개요)
```
┌─ InfoMesh Dashboard ─────────────────────────── v0.1.0 ─┐
│                                                          │
│  ┌─ Node ──────────────┐  ┌─ Resources ──────────────┐  │
│  │ Peer ID: Qm...3kF   │  │ CPU:  ████░░░░░░  38%    │  │
│  │ State:  🟢 Running   │  │ RAM:  ██████░░░░  62%    │  │
│  │ Uptime: 3d 14h 22m  │  │ Disk: ████████░░  81%    │  │
│  │ Version: 0.1.0      │  │ Net↑: 2.1/5.0 Mbps       │  │
│  │ GitHub:  user@e...   │  │ Net↓: 4.3/10.0 Mbps      │  │
│  │ Data dir: ~/.info... │  │                           │  │
│  └──────────────────────┘  └──────────────────────────┘  │
│                                                          │
│  ┌─ Activity (last 1h) ──────────────────────────────┐  │
│  │ Crawled:    142 pages    ▁▃▅▇▅▃▁▃▅▇███▅▃▁        │  │
│  │ Indexed:    138 docs     ▁▃▅▇▅▃▁▃▅▇███▅▃▁        │  │
│  │ Searches:    23 queries  ▁▁▃▁▁▅▃▁▁▃▁▇▅▁▁        │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Recent Events ───────────────────────────────────┐  │
│  │ 14:23:01  Crawled example.com/page1       +1.0 cr │  │
│  │ 14:22:58  🔍 "python async" (12 results, 8ms)     │  │
│  │ 14:22:45  Peer Qm...xY2 connected                │  │
│  │ 14:22:30  Index snapshot exported (2.3 MB)        │  │
│  │ 14:22:15  Crawled docs.python.org/3/      +1.0 cr │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

> **구현 노트**: NodeInfoPanel에 Peers 대신 Data dir 표시.
> GitHub 이메일은 `git config user.email`에서 자동 감지되어 표시됨;
> 미연결 시 `not connected`으로 표시. 값은 대시보드 시작 시 한 번만 조회하여 캐시됨.
> ResourcePanel은 `psutil` 설치 시 CPU/RAM 표시, 미설치 시 N/A.
> 리소스 바 색상은 사용률에 따라 자동 전환 (≥90% 빨강, ≥70% 노랑).

### Tab 2: Crawl (크롤)
```
┌─ Crawl ─────────────────────────────────────────────────┐
│                                                          │
│  Workers: 3/5 active    Rate: 42 pages/hr                │
│  Queue:   156 pending   Errors: 2 (1.4%)                 │
│                                                          │
│  ┌─ Top Domains ─────────────────────────────────────┐  │
│  │ docs.python.org      ████████████  234 pages      │  │
│  │ en.wikipedia.org     █████████     178 pages      │  │
│  │ developer.mozilla.org ███████      145 pages      │  │
│  │ stackoverflow.com    █████         98 pages       │  │
│  │ arxiv.org            ███           67 pages       │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Live Feed ───────────────────────────────────────┐  │
│  │ ✓  docs.python.org/3/tutorial/       1.2s  4.2KB │  │
│  │ ✓  en.wikipedia.org/wiki/P2P         0.8s  8.1KB │  │
│  │ ✗  example.com/blocked  robots.txt   —     —     │  │
│  │ ✓  arxiv.org/abs/2401.01234          2.1s  3.7KB │  │
│  │ ⟳  developer.mozilla.org/en-US/...  crawling...  │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Tab 3: Search (검색)
```
┌─ Search ────────────────────────────────────────────────┐
│                                                          │
│  🔍 Query: [python async tutorial________________]       │
│                                                          │
│  Found 12 results (8ms, local):                          │
│                                                          │
│  ┌─ Results ─────────────────────────────────────────┐  │
│  │ 1. Python Asyncio Tutorial                        │  │
│  │    https://docs.python.org/3/library/asyncio.html │  │
│  │    BM25=2.341  Fresh=0.95  Trust=0.88  Auth=0.72  │  │
│  │    Score: 1.8234                                   │  │
│  │    This module provides infrastructure for writing │  │
│  │    single-threaded concurrent code using...        │  │
│  │                                                    │  │
│  │ 2. Async IO in Python                             │  │
│  │    https://realpython.com/async-io-python/        │  │
│  │    BM25=2.102  Fresh=0.82  Trust=0.91  Auth=0.68  │  │
│  │    Score: 1.6891                                   │  │
│  │    Async IO is a concurrent programming design... │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Tab 4: Network (네트워크)
```
┌─ Network ───────────────────────────────────────────────┐
│                                                          │
│  ┌─ P2P Status ──────────┐  ┌─ DHT ─────────────────┐  │
│  │ State: � Offline      │  │ Keys stored:   1,234  │  │
│  │ Peers: 0 connected    │  │ Lookups/hr:      456   │  │
│  │ Bootstrap: 3 nodes    │  │ Publications:    89    │  │
│  │ Port:  4001 TCP       │  │                        │  │
│  │ Replication: 3x       │  │                        │  │
│  └────────────────────────┘  └────────────────────────┘  │
│                                                          │
│  ┌─ Connected Peers ─────────────────────────────────┐  │
│  │ Peer ID          Latency   Trust    State         │  │
│  │ Qm...aB2         23ms     0.92     active        │  │
│  │ Qm...cD4         45ms     0.85     active        │  │
│  │ Qm...eF6        102ms     0.78     idle          │  │
│  │ Qm...gH8         67ms     0.71     active        │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Bandwidth ───────────────────────────────────────┐  │
│  │ Upload:   ▁▃▅▇▅▃▁▃▅▇  2.1/5.0 Mbps              │  │
│  │ Download: ▃▅▇█▇▅▃▅▇█  4.3/10.0 Mbps             │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

> **구현 노트**: P2P Status에 Bootstrap 노드 수와 Replication factor 표시.
> Peer 테이블 컬럼: Peer ID, Latency, Trust, State (4열).
> 대역폭 스파크라인은 현재값/제한값 형식으로 표시.

### Tab 5: Credits (크레딧)
```
┌─ Credits ───────────────────────────────────────────────┐
│                                                          │
│  ┌─ Balance ─────────────────────────────────────────┐  │
│  │                                                    │  │
│  │  Balance:  1,234.50 credits    Tier: ⭐⭐⭐ (3)     │  │
│  │  Earned:   1,456.75            Search cost: 0.033  │  │
│  │  Spent:      222.25            Score: 1,456.75     │  │
│  │                                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Earnings Breakdown ──────────────────────────────┐  │
│  │ Crawling       ██████████████  702.0  (48.2%)     │  │
│  │ Uptime         ████████        396.0  (27.2%)     │  │
│  │ Query Process  ████            178.5  (12.2%)     │  │
│  │ LLM (own)      ███             135.0   (9.3%)     │  │
│  │ Doc Hosting    ██               45.25  (3.1%)     │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Recent Transactions ─────────────────────────────┐  │
│  │ 14:23:01  +1.000  crawl    example.com/page1      │  │
│  │ 14:22:58  -0.033  search   "python async"         │  │
│  │ 14:22:30  +0.500  uptime   1 hour                 │  │
│  │ 14:22:15  +1.000  crawl    docs.python.org/3/     │  │
│  │ 14:21:00  +1.500  llm_own  summarize page #456    │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

## 모바일 대응 (40 컬럼 모드)

Textual의 반응형 CSS를 활용하여 좁은 화면(40칸 미만)에서는 자동으로 단일 컬럼
레이아웃으로 전환합니다.

```
┌─ InfoMesh ──── v0.1.0 ─┐
│                          │
│ Peer: Qm...3kF           │
│ State: 🟢 Running        │
│ Uptime: 3d 14h           │
│ Peers: 12                │
│                          │
│ CPU:  ████░░░░  38%      │
│ RAM:  ██████░░  62%      │
│ Disk: ████████  81%      │
│                          │
│ Crawled : 142 pages/hr   │
│ Indexed : 138 docs/hr    │
│ Searches:  23 queries/hr │
└──────────────────────────┘
```

## 모듈 구조

```
infomesh/dashboard/            # 16개 모듈, 2,340행
├── __init__.py
├── app.py              # DashboardApp (메인 Textual Application, 276행)
├── bgm.py              # BGMPlayer (배경 음악 재생 — mpv/ffplay/aplay, 228행)
├── text_report.py      # Rich 기반 텍스트 리포트 (비대화형 폴백, 333행)
├── screens/
│   ├── __init__.py
│   ├── overview.py     # OverviewPane — NodeInfoPanel, ResourcePanel, ActivityPanel, LiveLog (284행)
│   ├── crawl.py        # CrawlPane — CrawlStatsPanel, TopDomainsPanel, LiveLog (182행)
│   ├── search.py       # SearchPane — Input, SearchResultsPanel (151행)
│   ├── network.py      # NetworkPane — P2PStatusPanel, DHTPanel, PeerTable, BandwidthPanel (246행)
│   └── credits.py      # CreditsPane — BalancePanel, EarningsBreakdownPanel, TransactionTable (289행)
├── widgets/
│   ├── __init__.py
│   ├── sparkline.py    # SparklineChart (Unicode 블럭 문자 미니 차트, 75행)
│   ├── bar_chart.py    # BarChart + BarItem (수평 막대 그래프, 90행)
│   ├── resource_bar.py # ResourceBar (CPU/RAM/Disk/Net 리소스 바, 80행)
│   └── live_log.py     # LiveLog (실시간 이벤트 로그, RichLog 기반, 96행)
└── dashboard.tcss      # Textual CSS 스타일시트 (반응형 레이아웃)
```

## CLI 명령

```bash
# 대시보드 실행
infomesh dashboard

# 특정 탭으로 시작
infomesh dashboard --tab credits

# 사용 가능한 탭: overview, crawl, search, network, credits (기본값: overview)
infomesh dashboard -t network
```

## 앱 아키텍처

```
DashboardApp (App[None])
├── Header — 제목 "InfoMesh Dashboard" + 버전 표시
├── TabbedContent (initial=선택된 탭)
│   ├── TabPane "Overview" → OverviewPane
│   │   ├── Horizontal
│   │   │   ├── NodeInfoPanel (Peer ID, State, Uptime, Version, Data dir)
│   │   │   └── ResourcePanel (CPU, RAM, Disk, Net↑, Net↓)
│   │   ├── ActivityPanel (Crawled/Indexed/Searches + SparklineChart ×3)
│   │   └── LiveLog (이벤트 피드)
│   ├── TabPane "Crawl" → CrawlPane
│   │   ├── CrawlStatsPanel (Workers, Queue, Rate, Errors)
│   │   ├── TopDomainsPanel (SQL GROUP BY domain → BarChart)
│   │   └── LiveLog (크롤 피드)
│   ├── TabPane "Search" → SearchPane
│   │   ├── Input (검색 쿼리)
│   │   └── SearchResultsPanel (BM25 점수 + 스니펫)
│   ├── TabPane "Network" → NetworkPane
│   │   ├── Horizontal
│   │   │   ├── P2PStatusPanel (State, Peers, Bootstrap, Port, Replication)
│   │   │   └── DHTPanel (Keys, Lookups/hr, Publications)
│   │   ├── PeerTable (DataTable: Peer ID, Latency, Trust, State)
│   │   └── BandwidthPanel (Upload/Download SparklineChart + 현재값/제한값)
│   └── TabPane "Credits" → CreditsPane
│       ├── BalancePanel (Balance, Earned, Spent, Tier, Search cost)
│       ├── EarningsBreakdownPanel (action별 BarChart)
│       └── TransactionTable (DataTable: Time, Amount, Type, Note)
└── Footer — 키보드 단축키 표시
```

## 키보드 단축키

| 키 | 동작 | 범위 |
|----|------|------|
| `1`-`5` | 탭 전환 (Overview → Credits) | 전체 |
| `Tab` | 다음 위젯 포커스 | 전체 (Textual 기본) |
| `Shift+Tab` | 이전 위젯 포커스 | 전체 (Textual 기본) |
| `/` | 검색 입력 포커스 | Search 탭 전용 |
| `q` | 종료 | 전체 |
| `r` | 새로고침 (Overview, Crawl, Network, Credits) | 전체 |
| `m` | BGM 켜기/끄기 | 전체 |
| `?` | 도움말 알림 표시 (5초 타임아웃) | 전체 |

## BGM (배경 음악)

대시보드는 외부 플레이어 서브프로세스를 통해 배경 음악을 재생할 수 있습니다.

### 지원 플레이어

다음 순서로 자동 감지합니다: **mpv**, **ffplay** (ffmpeg 포함).
둘 다 없으면 BGM은 자동으로 비활성화됩니다 (오류 없음).

```bash
# Debian/Ubuntu 설치:
sudo apt install mpv     # 또는: sudo apt install ffmpeg

# macOS 설치:
brew install mpv         # 또는: brew install ffmpeg
```

### 설정

BGM은 **기본적으로 꺼져** 있습니다. `m` 키로 활성화하거나 설정 파일에서 변경하세요:

```toml
[dashboard]
bgm_auto_start = true    # false 가 기본값 — true 로 설정하면 시작 시 자동 재생
bgm_volume = 50           # 0–100
bgm_idle_stop = true      # 크롤링 유휴 시 BGM 자동 정지 (false로 설정하면 계속 재생)
```

### 자동 재시작

오디오 플레이어 프로세스가 예기치 않게 종료되면 BGM이 자동으로
재시작됩니다 (최대 5회 시도). 자동 재시작 시 알림이 표시됩니다.

### 성능 참고

리소스가 제한된 시스템(특히 WSL2)에서는 오디오 플레이어 프로세스가
크롤링 및 인덱싱과 CPU를 놓고 경쟁하여 음악이 끊길 수 있습니다. 이 경우:

1. `m` 키를 눌러 BGM 비활성화
2. `ffplay` 대신 `mpv` 사용 (CPU 사용량이 더 적음)
3. `refresh_interval` 을 높여 대시보드 오버헤드 감소
4. `minimal` 리소스 프로필 사용

## 구현 사양

- **의존성**: `textual>=1.0` (메인 deps에 추가, 현재 `textual==8.0.0`)
- **데이터 갱신 주기** (탭별 차등):
  - Overview: `set_interval(2.0)` — 2초마다 리소스/노드 상태 갱신
  - Crawl: `set_interval(3.0)` — 3초마다 도메인 통계 갱신
  - Network: `set_interval(2.0)` — 2초마다 P2P 상태 갱신
  - Credits: `set_interval(5.0)` — 5초마다 크레딧 데이터 갱신
  - Search: 자동 갱신 없음 (사용자 쿼리 입력 시에만 실행)
- **데이터 소스**:
  - `LocalStore` — 문서 수, 도메인 통계, 검색 (SQLite FTS5)
  - `CreditLedger` — 잔액, 수입 내역, 거래 기록
  - `psutil` (선택) — CPU/RAM 사용량 (미설치 시 N/A)
  - `shutil.disk_usage()` — 디스크 사용량
  - PID 파일 (`infomesh.pid`) — 노드 실행 상태 확인
  - `KeyPair.load()` — Peer ID 로드
- **에러 처리**: 데이터 소스 미연결 시 "N/A" 또는 안내 메시지 표시, 모든 `refresh_data()`에 `contextlib.suppress(Exception)` 적용
- **테스트**: pytest 단위/통합 테스트 53개 (`tests/test_dashboard.py`)
  - 위젯 테스트: SparklineChart (6), BarChart (4), ResourceBar (4), LiveLog (1)
  - 헬퍼 함수 테스트: `_format_uptime()`, `_is_node_running()`, `_get_peer_id()` (7)
  - 스크린 테스트: SearchResultsPanel (3), CreditsHelpers (2), NetworkPanels (1), CrawlStatsPanel (2)
  - 앱/CLI 테스트: DashboardApp (3), CLI command (1), BarItem (3), Sparkline edge cases (4)
  - BGM/텍스트 리포트 테스트: BGMPlayer, text_report 등 추가 테스트 (12)

## 위젯 구현 세부

### SparklineChart
- Unicode 블럭 문자 사용: `" ▁▂▃▄▅▆▇█"` (9단계)
- `reactive` 속성으로 데이터 변경 시 자동 리렌더링
- `push_value(max_points=30)` — 최대 30개 데이터 포인트 유지
- 값 범위 자동 정규화 (min-max 스케일링)

### BarChart
- `BarItem` 데이터클래스: label, value, color, suffix
- `█`/`░` 문자로 수평 막대 렌더링 (기본 너비 20자)
- 최대값 대비 비율 + 전체 합계 대비 퍼센트 동시 표시
- Rich `Table.grid()`으로 정렬된 레이아웃

### ResourceBar
- 사용률에 따른 색상 자동 전환: ≥90% 빨강, ≥70% 노랑, 기본 지정색
- 퍼센트(%) 또는 커스텀 단위(Mbps 등) 지원
- 기본 막대 너비 12자

### LiveLog
- `RichLog` 상속, `auto_scroll=True`
- 최대 200줄 유지 (`max_lines=200`)
- 전용 로깅 메서드: `log_event()`, `log_crawl()`, `log_search()`, `log_peer()`
- 타임스탬프 자동 추가 (`HH:MM:SS` 형식)

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [보안 감사](08-security-audit.md) · [MCP 연동](10-mcp-integration.md) · [배포](11-publishing.md) · [FAQ](12-faq.md)*
