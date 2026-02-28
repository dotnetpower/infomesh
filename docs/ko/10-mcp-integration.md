# MCP 연결 가이드

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/)를 통해 AI 어시스턴트를 InfoMesh에 연결하는 방법을 설명합니다.

---

## MCP란?

MCP는 AI 어시스턴트(Claude, GitHub Copilot 등)가 외부 도구를 호출할 수 있게 하는 오픈 프로토콜입니다.
InfoMesh는 5개의 도구를 MCP로 제공합니다 — search, search_local, fetch_page, crawl_url, network_stats —
AI 어시스턴트가 여러분의 분산 인덱스를 통해 웹 검색을 수행할 수 있습니다.

## 사용 가능한 MCP 도구

| 도구 | 설명 | 매개변수 |
|------|------|---------|
| `search` | P2P 네트워크 검색 (로컬 + 분산) | `query` (string), `limit` (int, 기본값 10) |
| `search_local` | 로컬 인덱스만 검색 (오프라인 가능) | `query` (string), `limit` (int, 기본값 10) |
| `fetch_page` | URL의 전체 텍스트 가져오기 (캐시 또는 실시간) | `url` (string) |
| `crawl_url` | URL을 크롤링하여 인덱스에 추가 | `url` (string), `depth` (int, 기본값 0, 최대 3) |
| `network_stats` | 노드 상태: 인덱스 크기, 피어 수, 크레딧 | *(없음)* |

---

## 빠른 시작

### 1. InfoMesh 설치

```bash
# uv 사용 (권장)
git clone https://github.com/dotnetpower/infomesh.git
cd infomesh
uv sync

# pip 사용 (PyPI 등록 후)
pip install infomesh
```

### 2. MCP 서버 시작

```bash
# Stdio 모드 (VS Code, Claude Desktop 등에서 사용)
uv run infomesh mcp

# pip으로 설치한 경우
infomesh mcp
```

MCP 서버는 **stdio** (stdin/stdout)로 통신합니다 — 네트워크 포트를 열지 않습니다.
AI 클라이언트가 InfoMesh를 서브프로세스로 실행하고 파이프를 통해 JSON-RPC 메시지를 교환합니다.

---

## IDE & 클라이언트 설정

### VS Code (GitHub Copilot)

VS Code 설정에 추가 (`.vscode/settings.json` 또는 사용자 설정):

```jsonc
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "uv",
        "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"],
        "env": {}
      }
    }
  }
}
```

InfoMesh가 pip으로 전역 설치된 경우:

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

**워크스페이스 수준** 설정 (`.vscode/settings.json`):
```jsonc
{
  "mcp": {
    "servers": {
      "infomesh": {
        "command": "uv",
        "args": ["run", "infomesh", "mcp"],
        "env": {
          "INFOMESH_DATA_DIR": "${workspaceFolder}/.infomesh"
        }
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
      "command": "uv",
      "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"]
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
      "command": "uv",
      "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"]
    }
  }
}
```

pip으로 설치한 경우:

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

저장 후 Claude Desktop을 재시작합니다. 🔧 메뉴에서 InfoMesh 도구를 확인할 수 있습니다.

### Cursor

**Cursor Settings → MCP**에서 추가:

```json
{
  "mcpServers": {
    "infomesh": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"]
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
      "command": "uv",
      "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"]
    }
  }
}
```

### JetBrains IDE (IntelliJ, PyCharm, WebStorm 등)

AI Assistant가 포함된 JetBrains IDE는 MCP를 지원합니다:

1. **Settings → Tools → AI Assistant → MCP Servers** 열기
2. **Add** (+) 클릭 후 설정:
   - **Name**: `infomesh`
   - **Command**: `uv`
   - **Arguments**: `run --directory /path/to/infomesh infomesh mcp`

또는 설정 파일을 직접 편집:

```json
{
  "servers": {
    "infomesh": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"]
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
        "path": "uv",
        "args": ["run", "--directory", "/path/to/infomesh", "infomesh", "mcp"]
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
      command = "uv",
      args = { "run", "--directory", "/path/to/infomesh", "infomesh", "mcp" },
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

---

## 로컬 HTTP API (대안)

MCP를 지원하지 않는 클라이언트의 경우, 노드 실행 시 (`infomesh start`) 로컬 REST API도 제공됩니다:

```bash
# 헬스 체크
curl http://localhost:8080/health

# 노드 상태
curl http://localhost:8080/status

# 인덱스 통계
curl http://localhost:8080/index/stats

# 크레딧 잔액
curl http://localhost:8080/credits/balance
```

API는 `127.0.0.1`에만 바인딩됩니다 — 외부 네트워크에 노출되지 않습니다.

---

## 환경 변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `INFOMESH_DATA_DIR` | 데이터 디렉토리 경로 | `~/.infomesh` |
| `INFOMESH_CONFIG` | 설정 파일 경로 | `~/.infomesh/config.toml` |

---

## 문제 해결

### VS Code에서 "Server not found"
- `uv`가 PATH에 있는지 확인: `which uv`
- 필요하면 절대 경로 사용: `/home/user/.cargo/bin/uv`
- 출력 패널 → "MCP"에서 오류 로그 확인

### "No results found"
- 인덱스가 비어있을 수 있습니다. 먼저 노드를 시작하세요: `uv run infomesh start`
- 또는 페이지를 크롤링하세요: `uv run infomesh crawl https://docs.python.org/3/`

### MCP 서버가 즉시 종료됨
- `uv run infomesh mcp`를 수동으로 실행하여 오류 출력 확인
- 모든 의존성이 설치되었는지 확인: `uv sync`

### 키 권한 오류
- InfoMesh는 키를 `~/.infomesh/keys/`에 저장합니다. 디렉토리가 쓰기 가능한지 확인하세요.
- 키 파일은 현재 사용자 소유여야 합니다 (chmod 600).

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [보안 감사](08-security-audit.md) · [콘솔 대시보드](09-console-dashboard.md) · [배포](11-publishing.md)*
