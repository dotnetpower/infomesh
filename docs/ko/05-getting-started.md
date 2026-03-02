# 시작 가이드 — 첫 실행 안내

이 가이드는 InfoMesh의 설치, 첫 실행, P2P 네트워크 연결, 첫 크롤링 수행,
그리고 멀티 노드 피어링까지의 전 과정을 안내합니다.

---

## 1. InfoMesh 설치

### 방법 A: `uvx`로 바로 실행 (설치 불필요)

```bash
uvx infomesh mcp
```

MCP 서버를 바로 실행합니다. IDE에서 InfoMesh를 체험할 때 적합합니다.

### 방법 B: `uv`로 설치

```bash
uv tool install infomesh
```

### 방법 C: `pip`으로 설치

```bash
pip install infomesh
```

> **참고**: 기본 설치에는 MCP, 크롤링, 로컬 검색에 필요한 모든 것이 포함됩니다
> — 네이티브 빌드 도구가 필요 없습니다. P2P 네트워킹을 활성화하려면
> `pip install 'infomesh[p2p]'`로 설치하세요 (Linux에서 `build-essential`,
> `libgmp-dev` 등이 필요합니다).
> 자세한 내용은 [FAQ](12-faq.md)를 참조하세요.

설치 후 확인:

```bash
infomesh --version
# InfoMesh v0.1.x
```

---

## 2. 첫 실행

전체 노드를 처음 실행합니다:

```bash
infomesh start
```

InfoMesh는 다음 순서로 첫 실행 점검을 수행합니다:

```
InfoMesh v0.1.6 starting...
  Peer ID: 12D3KooWAbCdEfGh...
  Data dir: /home/user/.infomesh
```

### 단계 1: Git 설치 확인

InfoMesh는 시스템에 `git`이 설치되어 있는지 확인합니다. Git은 크로스 노드
크레딧 집계를 위해 GitHub 이메일을 자동 감지하는 데 사용됩니다.

**git이 설치된 경우:**

```
  ✔ GitHub detected: alice@example.com (from git config)
```

`git config --global user.email`에 설정된 이메일을 자동으로 읽어옵니다.
추가 작업이 필요 없습니다.

**git이 설치되지 않은 경우:**

```
  ⚠ git not found — install git to auto-detect your GitHub identity.
    Install: https://git-scm.com/downloads
    Or set manually: infomesh config set node.github_email "you@email.com"
  Enter GitHub email (or press Enter to skip):
```

다음 중 하나를 선택할 수 있습니다:
- GitHub 이메일을 직접 입력하거나,
- Enter를 눌러 건너뛰거나 (크레딧이 로컬에만 저장됨),
- 나중에 git을 설치하고 재시작합니다.

### 단계 2: GitHub 계정 연결

git이 설치되어 있지만 이메일이 설정되지 않은 경우 (또는 `config.toml`에
설정하지 않은 경우), 대화형 프롬프트가 표시됩니다:

```
  ⚠ GitHub not linked — credits will be local-only.
  Link your GitHub account now? (enables cross-node credits) [Y/n]: y
  GitHub email: alice@example.com
  ✔ GitHub linked: alice@example.com
    Credits will aggregate across all nodes using this email.
```

**왜 GitHub를 연결하나요?**

- 어떤 노드에서 획득한 크레딧이든 GitHub 이메일로 집계됩니다.
- 여러 머신에서 InfoMesh를 실행하면 모든 크레딧이 합산됩니다.
- 높은 기여 점수는 더 저렴한 검색 비용을 제공합니다.
- 나중에 언제든지 설정하거나 변경할 수 있습니다:

```bash
infomesh config set node.github_email "alice@example.com"
```

### 단계 3: 사전 점검

계정 설정 후 InfoMesh는 사전 점검을 실행합니다:

```
  ⏳ Running preflight checks... ✔
```

디스크 공간, 네트워크 연결, 포트 가용성을 확인합니다.

### 단계 4: 키 생성

첫 실행 시 Ed25519 키 쌍이 자동으로 생성됩니다:

```
  Peer ID: 12D3KooWAbCdEfGh...
  Data dir: /home/user/.infomesh
```

키는 `~/.infomesh/keys/`에 저장됩니다. 이 키는 P2P 네트워크에서 노드를
식별하며, 신뢰와 크레딧에 연결되어 있으므로 안전하게 보관하세요.

---

## 3. 부트스트랩 & 네트워크 연결

시작 후 InfoMesh는 부트스트랩 노드에 연결하여 P2P 네트워크에 참여합니다:

```
  Connecting to P2P network...
  ✔ Connected to 3 peers
```

### 부트스트랩 동작 방식

1. InfoMesh는 내장된 부트스트랩 노드 목록(`bootstrap/nodes.json`)을 가지고 있습니다.
   이 노드들은 안정성을 위해 여러 Azure 리전에 배포되어 있습니다.
2. 노드가 TCP 4001 포트를 통해 알려진 피어에 연결합니다.
3. Kademlia DHT를 통해 추가 피어를 발견합니다.
4. 연결되면 노드가 분산 해시 테이블에 참여합니다.
5. 성공적으로 연결된 모든 피어가 `~/.infomesh/peer_store.db`에 저장됩니다.

### 현재 부트스트랩 노드

| 리전 | 주소 | 비고 |
|------|------|------|
| US East | `20.42.12.161:4001` | Azure B1s 부트스트래퍼 |

부트스트랩 노드는 커뮤니티에서 관리합니다. 더 많은 노드가 참여할수록
네트워크는 이러한 초기 진입점에 대한 의존도가 낮아집니다.

### 부트스트랩 노드가 다운된 경우?

InfoMesh는 다중 계층 폴백을 사용하여 피어를 발견합니다:

1. **영속 피어 저장소** — 재시작 시 이전에 연결했던 피어를 `peer_store.db`에서
   로드하여 직접 연결합니다. 부트스트랩이 필요 없습니다.
2. **PEX (Peer Exchange)** — 5분마다 연결된 피어에게 알고 있는 피어 목록을
   요청합니다. 발견된 피어는 피어 저장소에 저장됩니다.
3. **mDNS** — 같은 LAN의 피어가 멀티캐스트 UDP를 통해 자동으로 발견됩니다
   (인터넷 불필요).
4. **수동 설정** — 알고 있는 피어의 주소를 `config.toml`에 추가할 수 있습니다.

즉, 노드가 네트워크에 한 번이라도 연결된 적이 있다면, 모든 부트스트랩 서버가
오프라인이어도 다시 참여할 수 있습니다.

### 부트스트랩 노드 목록

기본 부트스트랩 노드는 자동으로 로드됩니다. 사용자 지정 부트스트랩 노드를
설정할 수도 있습니다:

```bash
infomesh config set network.bootstrap_nodes '["/ip4/1.2.3.4/tcp/4001/p2p/12D3KooW..."]'
```

### 방화벽

P2P에 완전히 참여하려면 TCP 포트 **4001**(인바운드 + 아웃바운드)을 여세요.
NAT 뒤에 있는 경우에도 클라이언트 모드로 동작하지만 연결이 제한될 수 있습니다.

---

## 4. 첫 크롤링

연결 후 InfoMesh는 설정된 시드를 기반으로 크롤링을 시작합니다:

```bash
# 특정 URL을 수동으로 크롤링
infomesh crawl https://docs.python.org

# 또는 자동 크롤링 루프에 시드 URL을 맡기기
infomesh start   # 시드가 자동으로 크롤링됩니다
```

### 크롤링 과정

1. **URL 할당**: DHT가 `hash(URL)` 기반으로 URL 소유권을 할당합니다.
2. **robots.txt 확인**: 모든 도메인의 robots.txt를 가져와 준수합니다.
3. **콘텐츠 추출**: `trafilatura`가 본문 텍스트를 추출합니다.
4. **중복 제거**: 3단계 중복 제거 (URL 정규화 → SHA-256 → SimHash).
5. **인덱싱**: 콘텐츠가 SQLite FTS5에 키워드 검색용으로 저장됩니다.
6. **DHT 게시**: 키워드 해시가 DHT에 게시되어 다른 노드가 인덱싱된
   콘텐츠를 찾을 수 있습니다.

### 시드 카테고리

InfoMesh에는 선별된 시드 목록이 포함되어 있습니다:

| 카테고리 | 파일 | 설명 |
|----------|------|------|
| 기술 문서 | `seeds/tech-docs.txt` | Python, MDN, Rust, Go 문서 |
| 학술 | `seeds/academic.txt` | ArXiv, PubMed, 학술 소스 |
| 백과사전 | `seeds/encyclopedia.txt` | Wikipedia, 백과사전 사이트 |
| 퀵스타트 | `seeds/quickstart.txt` | 선별된 시작 세트 |

시작 시 카테고리를 선택하거나 기본값을 사용합니다:

```bash
infomesh crawl --category tech-docs
```

### 진행 상황 모니터링

대시보드를 사용하여 실시간으로 크롤링을 확인할 수 있습니다:

```bash
infomesh dashboard
```

또는 CLI로 통계를 확인합니다:

```bash
infomesh index stats
```

---

## 5. 두 번째 노드 — 피어링

다른 사용자가 다른 머신에 InfoMesh를 설치하면, 두 노드가 자동으로
서로를 발견하고 협력합니다.

### 노드 B 설치

두 번째 머신에서:

```bash
pip install 'infomesh[p2p]'
infomesh start
```

노드 B도 동일한 첫 실행 과정을 거칩니다:

1. Git 확인 + GitHub 연결 프롬프트
2. 키 생성 (고유한 Peer ID)
3. 부트스트랩 연결 (동일한 부트스트랩 노드)
4. DHT 탐색 — 노드 B가 노드 A를 발견 (역방향도 동일)

### 노드가 피어링되면 일어나는 일

```
노드 A                          노드 B
  │                               │
  ├── 부트스트랩에 연결 ──────────┤
  │                               │
  ├── DHT: 노드 B 발견 ──────────┤
  │                               │
  ├── 키워드 인덱스 공유 ─────────┤
  │   (hash(keyword) → doc_id)    │
  │                               │
  ├── 노드 B의 인덱스 수신 ──────┤
  │                               │
  ├── 분산 검색 동작 ─────────────┤
  │   (DHT를 통해 쿼리 라우팅)    │
  │                               │
  └── 크롤링 협력 ────────────────┘
      (해시 기반 URL 할당)
```

### 분산 검색

피어링 후 검색이 양쪽 노드를 포함합니다:

```bash
infomesh search "python asyncio tutorial"
```

1. 쿼리 키워드가 해시됩니다.
2. DHT가 각 키워드 해시에 가장 가까운 노드로 쿼리를 라우팅합니다.
3. 참여하는 모든 노드의 결과가 병합되고 랭킹됩니다.
4. 응답이 반환됩니다 — 보통 약 1초 이내.

### 크롤링 협력

DHT가 중복 크롤링을 방지합니다:

- `hash("https://docs.python.org")`가 특정 노드에 매핑됩니다.
- 해당 노드가 URL을 "소유"하고 크롤링합니다.
- 다른 노드가 같은 URL을 요청하면 소유 노드에서 결과를 받습니다.
- 크롤 락이 경합 조건을 방지합니다 (5분 타임아웃).

---

## 6. MCP 사용 (IDE 연동)

InfoMesh는 IDE에서 MCP 도구로 사용하도록 설계되었습니다:

### VS Code (GitHub Copilot)

`.vscode/mcp.json`에 추가:

```json
{
  "servers": {
    "infomesh": {
      "command": "infomesh",
      "args": ["mcp"]
    }
  }
}
```

### Claude Desktop

`~/.config/claude/claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "infomesh",
      "args": ["mcp"]
    }
  }
}
```

설정 후 AI 어시스턴트가 `search`, `fetch_page`, `crawl_url` 등의 도구를
호출할 수 있습니다. 전체 도구 레퍼런스는 [MCP 연동](10-mcp-integration.md)을
참조하세요.

---

## 7. 설정 레퍼런스

InfoMesh는 `~/.infomesh/config.toml`에 설정을 저장합니다:

```toml
[node]
data_dir = "~/.infomesh"
github_email = "alice@example.com"
role = "full"

[crawl]
max_depth = 0
politeness_delay = 1.0

[network]
listen_port = 4001
bootstrap_nodes = ["default"]   # 내장된 bootstrap/nodes.json 사용
```

기본값 대신 (또는 추가로) 사용자 지정 부트스트랩 노드를 사용하려면:

```toml
[network]
bootstrap_nodes = [
  "default",
  "/ip4/YOUR.IP.HERE/tcp/4001/p2p/12D3KooW..."
]
```

현재 설정 확인:

```bash
infomesh config show
```

개별 값 설정:

```bash
infomesh config set node.github_email "alice@example.com"
infomesh config set crawl.max_depth 5
```

---

## 요약 — 첫 실행 타임라인

```
설치 (pip/uv)
    │
    ▼
infomesh start
    │
    ├─ 1. Git 확인 ──── GitHub 이메일 자동 감지
    │                    또는 수동 입력 프롬프트
    │
    ├─ 2. GitHub 연결 ── 크로스 노드 크레딧 연결
    │                    (건너뛰기 가능, 나중에 config으로 설정)
    │
    ├─ 3. 사전 점검 ──── 디스크, 네트워크, 포트 확인
    │
    ├─ 4. 키 생성 ────── Ed25519 키 쌍 생성
    │                    고유한 Peer ID 할당
    │
    ├─ 5. 부트스트랩 ── 알려진 노드에 연결
    │                    Kademlia DHT 참여
    │
    ├─ 6. 크롤링 ────── 시드 URL 가져오기
    │                    콘텐츠 인덱싱 (FTS5)
    │                    DHT에 게시
    │
    └─ 7. 준비 완료 ── 로컬 + 네트워크 검색 가능
                        MCP 도구 사용 가능
                        대시보드: `infomesh dashboard`
```

> **팁**: 첫 실행 시 GitHub 이메일을 연결하면 모든 노드에서 크레딧을 획득하고
> 축적할 수 있습니다. 크레딧은 만료되지 않으며 높은 기여 점수는 더 저렴한
> 검색 비용을 제공합니다.
