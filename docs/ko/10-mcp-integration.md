# MCP 연결 가이드

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/)를 통해 AI 어시스턴트를 InfoMesh에 연결하는 방법을 설명합니다.

---

## MCP란?

MCP는 AI 어시스턴트(Claude, GitHub Copilot 등)가 외부 도구를 호출할 수 있게 하는 오픈 프로토콜입니다.
InfoMesh는 **15개의 도구**를 MCP로 제공합니다 — search, search_local, fetch_page, crawl_url, network_stats,
batch_search, suggest, register_webhook, analytics, explain, search_history, search_rag,
extract_answer, fact_check —
AI 어시스턴트가 여러분의 분산 인덱스를 통해 웹 검색을 수행할 수 있습니다.

## 사용 가능한 MCP 도구

### 핵심 검색 도구

| 도구 | 설명 | 주요 매개변수 |
|------|------|-------------|
| `search` | P2P 네트워크 검색 (로컬 + 분산) | `query`, `limit`, `format`, `language`, `date_from`, `date_to`, `include_domains`, `exclude_domains`, `offset`, `snippet_length`, `session_id` |
| `search_local` | 로컬 인덱스만 검색 (오프라인 가능) | `search`와 동일 |
| `batch_search` | 여러 쿼리를 한 번에 실행 (최대 10개) | `queries`, `limit`, `format` |
| `suggest` | 자동완성 / 검색 제안 | `prefix`, `limit` |

### 콘텐츠 접근 도구

| 도구 | 설명 | 주요 매개변수 |
|------|------|-------------|
| `fetch_page` | URL의 전체 텍스트 가져오기 (캐시 또는 실시간) | `url`, `format` |
| `crawl_url` | URL을 크롤링하여 인덱스에 추가 | `url`, `depth`, `force`, `webhook_url` |

### 인텔리전스 도구 (v0.2.0 신규)

| 도구 | 설명 | 주요 매개변수 |
|------|------|-------------|
| `explain` | 결과별 점수 분석 (BM25, 신선도, 신뢰도) | `query`, `limit` |
| `search_rag` | RAG 최적화 청크 출력 + 출처 표시 | `query`, `limit`, `chunk_size` |
| `extract_answer` | 신뢰도 점수가 포함된 직접 답변 추출 | `query`, `limit` |
| `fact_check` | 인덱싱된 소스와 교차 검증 | `claim`, `limit` |
| `search_history` | 검색 기록 조회 또는 삭제 | `action` (`"list"` 또는 `"clear"`) |

### 인프라 도구

| 도구 | 설명 | 주요 매개변수 |
|------|------|-------------|
| `network_stats` | 노드 상태: 인덱스 크기, 피어 수, 크레딧 | `format` |
| `analytics` | 검색 분석 (횟수, 지연시간) | `format` |
| `register_webhook` | 크롤 완료 웹훅 등록 | `url` |

### 공통 검색 매개변수

모든 검색 도구 (`search`, `search_local`, `batch_search`)에서 지원:

| 매개변수 | 타입 | 설명 |
|---------|------|------|
| `format` | `"text"` \| `"json"` | 출력 형식 (기본값: `"text"`) |
| `language` | string | ISO 639-1 언어 코드 필터 (예: `"en"`, `"ko"`) |
| `date_from` | number | Unix 타임스탬프 — 이후 크롤된 문서만 |
| `date_to` | number | Unix 타임스탬프 — 이전 크롤된 문서만 |
| `include_domains` | string[] | 이 도메인의 결과만 포함 |
| `exclude_domains` | string[] | 이 도메인의 결과 제외 |
| `offset` | integer | N개 결과 건너뛰기 (페이지네이션) |
| `snippet_length` | integer | 최대 스니펫 문자 수 (10–1000, 기본값 200) |
| `session_id` | string | 대화형 검색용 세션 ID |

### JSON 출력

`format: "json"` 지정 시, 응답에는 다음이 포함됩니다:

```json
{
  "total": 42,
  "elapsed_ms": 12.3,
  "source": "local_fts5",
  "results": [...],
  "quota": {
    "credit_balance": 125.5,
    "state": "normal",
    "search_cost": 0.033
  },
  "api_version": "2025.1"
}
```

### 인증

`INFOMESH_API_KEY` 환경 변수 설정 시 API 키 인증이 필요합니다.
설정되면 모든 도구 호출에 `api_key` 매개변수를 포함해야 합니다.

---

## 빠른 시작

### 1. 설치 & 실행 (한 줄 명령)

가장 빠른 방법 — 클론, 설정 불필요:

```bash
# uv 설치 (없는 경우)
curl -LsSf https://astral.sh/uv/install.sh | sh

# MCP 서버 바로 실행 (PyPI에서 infomesh 자동 다운로드)
uvx infomesh mcp
```

### 2. 또는 영구 설치

```bash
# 도구로 설치 (시스템 전역 사용 가능)
uv tool install infomesh
infomesh mcp

# 또는 pip
pip install infomesh
infomesh mcp
```

MCP 서버는 **stdio** (stdin/stdout)로 통신합니다 — 네트워크 포트를 열지 않습니다.
AI 클라이언트가 InfoMesh를 서브프로세스로 실행하고 파이프를 통해 JSON-RPC 메시지를 교환합니다.

---

## IDE & 클라이언트 설정

### VS Code (GitHub Copilot)

VS Code 설정에 추가 (`.vscode/settings.json` 또는 사용자 설정):

```jsonc
// 권장: uvx 사용 (클론/설치 불필요)
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "uvx",
        "args": ["infomesh", "mcp"]
      }
    }
  }
}
```

`uv tool install` 또는 `pip install`로 설치한 경우:

```jsonc
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "infomesh",
        "args": ["mcp"]
      }
    }
  }
}
```

설정 추가 후:
1. 명령 팔레트 열기 (`Ctrl+Shift+P` / `Cmd+Shift+P`)
2. **"MCP: List Servers"** 검색하여 InfoMesh가 표시되는지 확인
3. Copilot Chat 사용 — InfoMesh 도구를 자동으로 인식하고 사용합니다

### VS Code (MCP `.json` 파일 — 대안)

워크스페이스에 `.vscode/mcp.json` 생성:

```json
{
  "servers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
또는 `%APPDATA%\Claude\claude_desktop_config.json` (Windows) 편집:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

저장 후 Claude Desktop을 재시작합니다. 🔧 메뉴에서 InfoMesh 도구를 확인할 수 있습니다.

### Cursor

**Cursor Settings → MCP**에서 추가:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### Windsurf

Windsurf MCP 설정 (`~/.windsurf/mcp_config.json`)에 추가:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### JetBrains IDE (IntelliJ, PyCharm, WebStorm 등)

AI Assistant가 포함된 JetBrains IDE는 MCP를 지원합니다:

1. **Settings → Tools → AI Assistant → MCP Servers** 열기
2. **Add** (+) 클릭 후 설정:
   - **Name**: `infomesh`
   - **Command**: `uvx`
   - **Arguments**: `infomesh mcp`

또는 설정 파일을 직접 편집:

```json
{
  "servers": {
    "infomesh": {
      "command": "uvx",
      "args": ["infomesh", "mcp"]
    }
  }
}
```

### Zed

Zed 설정 (`~/.config/zed/settings.json`)에 추가:

```json
{
  "context_servers": {
    "infomesh": {
      "command": {
        "path": "uvx",
        "args": ["infomesh", "mcp"]
      }
    }
  }
}
```

### Neovim (MCP 플러그인 사용)

MCP 호환 Neovim 플러그인 (예: `mcp.nvim`) 사용 시:

```lua
require("mcp").setup({
  servers = {
    infomesh = {
      command = "uvx",
      args = { "infomesh", "mcp" },
    },
  },
})
```

---

## 프로그래밍 방식 MCP 클라이언트 (Python)

Python 코드에서 InfoMesh MCP 서버에 직접 연결할 수 있습니다:

```python
import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    server = StdioServerParameters(
        command="uv",
        args=["run", "infomesh", "mcp"],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 검색
            result = await session.call_tool(
                "search", {"query": "python asyncio", "limit": 5}
            )
            print(result.content[0].text)

            # URL 크롤링
            result = await session.call_tool(
                "crawl_url", {"url": "https://docs.python.org/3/", "depth": 1}
            )
            print(result.content[0].text)

asyncio.run(main())
```

전체 동작 예제는 [`examples/mcp_client.py`](../examples/mcp_client.py)를 참고하세요.

### TypeScript / JavaScript

Node.js 애플리케이션의 경우 `examples/typescript/`의 TypeScript 예제를 참고하세요:

```bash
cd examples/typescript
npm install
npx tsx mcp_client.ts     # 전체 MCP 클라이언트 데모
npx tsx http_client.ts    # Admin API 클라이언트
```

TypeScript 클라이언트는 JSON 출력, 검색 필터, 배치 검색, 제안,
세션 등 모든 MCP 도구를 시연합니다.

---

## HTTP 전송 모드

stdio 외에도 컨테이너 및 원격 에이전트용 HTTP Streamable 전송을 지원합니다:

```bash
# HTTP로 MCP 서버 시작
infomesh mcp --http --host 0.0.0.0 --port 8081
```

Docker/Kubernetes 배포에서 stdio를 사용할 수 없는 경우에 유용합니다.
MCP 클라이언트를 `http://<host>:8081/mcp`에 연결하세요.

---

## Docker & Kubernetes 배포

### Docker Compose (멀티 노드)

```bash
# 3노드 로컬 클러스터 시작
docker compose up -d

# 노드: node1, node2, node3
# Admin API: localhost:8080, :8082, :8084
# MCP HTTP:  localhost:8081, :8083, :8085
```

전체 설정은 `docker-compose.yml`을 참고하세요.

### Kubernetes

```bash
# 모든 매니페스트 적용
kubectl apply -f k8s/

# 생성되는 리소스:
# - Namespace: infomesh
# - ConfigMap: 공유 config.toml
# - Secret: 선택적 API 키
# - StatefulSet: 영구 스토리지가 있는 3개 레플리카
# - Services: 헤드리스 + LoadBalancer
```

StatefulSet에는 라이브니스 (`/health`) 및 레디니스 (`/readiness`) 프로브가 포함되어 있습니다.

---

## 로컬 HTTP API (대안)

MCP를 지원하지 않는 클라이언트의 경우, 노드 실행 시 (`infomesh start`) 로컬 REST API도 제공됩니다:

```bash
# 헬스 체크
curl http://localhost:8080/health

# 레디니스 프로브 (DB 확인)
curl http://localhost:8080/readiness

# 노드 상태
curl http://localhost:8080/status

# 인덱스 통계
curl http://localhost:8080/index/stats

# 크레딧 잔액
curl http://localhost:8080/credits/balance

# 검색 분석
curl http://localhost:8080/analytics
```

`INFOMESH_API_KEY` 설정 시 `x-api-key` 헤더를 통한 API 키 인증을 지원합니다.

API는 `127.0.0.1`에만 바인딩됩니다 — 외부 네트워크에 노출되지 않습니다.

---

## 환경 변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `INFOMESH_DATA_DIR` | 데이터 디렉토리 경로 | `~/.infomesh` |
| `INFOMESH_CONFIG` | 설정 파일 경로 | `~/.infomesh/config.toml` |
| `INFOMESH_API_KEY` | 인증용 API 키 (선택 사항) | *(없음)* |

---

## 문제 해결

### VS Code에서 "Server not found"
- `uv`가 PATH에 있는지 확인: `which uv`
- 필요하면 절대 경로 사용: `/home/user/.cargo/bin/uv`
- 출력 패널 → "MCP"에서 오류 로그 확인

### "No results found"
- 인덱스가 비어있을 수 있습니다. 먼저 페이지를 크롤링하세요: `uvx infomesh crawl https://docs.python.org/3/`
- 또는 노드를 시작하세요: `uvx infomesh start`

### MCP 서버가 즉시 종료됨
- `uvx infomesh mcp`를 수동으로 실행하여 오류 출력 확인
- 소스에서 실행하는 경우 모든 의존성이 설치되었는지 확인: `uv sync`

### 키 권한 오류
- InfoMesh는 키를 `~/.infomesh/keys/`에 저장합니다. 디렉토리가 쓰기 가능한지 확인하세요.
- 키 파일은 현재 사용자 소유여야 합니다 (chmod 600).

---

## MCP 모듈 아키텍처

MCP 서버 코드는 **단일 책임 원칙 (SRP)** 에 따라 4개의 전문 모듈로 분리됩니다:

| 모듈 | 책임 | 대략적인 라인 수 |
|------|------|------------------|
| `mcp/server.py` | 연결 레이어 — `Server` 인스턴스 생성, 툴 등록, 핸들러로 디스패치, stdio/HTTP 서버 실행 | ~330 |
| `mcp/tools.py` | 툴 스키마 정의 (`get_all_tools()`), 필터 추출 (`extract_filters()`), API 키 확인 | ~340 |
| `mcp/handlers.py` | 모든 `handle_*` 함수 — 인자 검증, 서비스 레이어 위임, 응답 포맷팅 | ~900 |
| `mcp/session.py` | `SearchSession`, `AnalyticsTracker`, `WebhookRegistry` 헬퍼 클래스 | ~110 |

이 분리를 통해 **`server.py`에 비즈니스 로직이 포함되지 않습니다** — 핸들러로만 디스패치하고,
핸들러는 다시 `infomesh.services` 함수로 위임합니다.

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [보안 감사](08-security-audit.md) · [콘솔 대시보드](09-console-dashboard.md) · [배포](11-publishing.md) · [FAQ](12-faq.md)*
