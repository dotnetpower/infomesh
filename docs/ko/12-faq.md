# FAQ — 자주 묻는 질문

## 일반

### InfoMesh란 무엇인가요?

InfoMesh는 **완전 분산형 P2P 검색 엔진**으로, LLM을 위해 설계되었습니다.
P2P 네트워크를 통해 웹을 크롤링, 인덱싱, 검색하며 MCP(Model Context Protocol)를
통해 결과를 제공합니다. 사람용 UI는 없으며 AI 어시스턴트가 직접 호출하는
도구로 설계되었습니다.

### 상용 검색 API와 어떻게 다른가요?

| 항목               | 상용 API              | InfoMesh                        |
|--------------------|-----------------------|---------------------------------|
| 비용               | 쿼리당 과금           | 무료 — 기여로 크레딧 획득       |
| 프라이버시         | 쿼리 로그 저장        | 중앙 쿼리 기록 없음             |
| 가용성             | 벤더 의존             | 셀프 호스팅, 오프라인 가능      |
| 프로토콜           | REST / 독점           | MCP (네이티브 LLM 통합)         |
| 아키텍처           | 중앙 집중형 SaaS      | 완전 분산형 P2P                 |

### 서버를 운영해야 하나요?

아니요. InfoMesh는 로컬 머신에서 실행됩니다. MCP 모드는 `uvx infomesh mcp`,
전체 노드(P2P, 크롤링, 대시보드 포함)는 `infomesh start`로 실행합니다.

---

## 설치 및 사전 요구사항

### 시스템 요구사항은?

- **Python 3.12+** (필수)
- **uv** 패키지 매니저 (권장) — `curl -LsSf https://astral.sh/uv/install.sh | sh`로 설치
- **ffplay** 또는 **mpv** — 선택 사항, 대시보드 배경음악에만 필요
- **psutil** — 선택 사항, 대시보드의 시스템 리소스 모니터링용
- 첫 설치와 크롤링에 인터넷 필요 (로컬 검색은 오프라인 가능)

#### P2P용 시스템 패키지 (선택)

`p2p` 추가 기능(`libp2p`)은 C 확장(`fastecdsa`, `coincurve`, `pynacl`)을 포함하며, 네이티브 빌드 도구가 필요합니다.
기본 설치에서는 **불필요**하며, `uv sync --extra p2p` 또는 `pip install infomesh[p2p]` 사용 시에만 필요합니다.

**Linux (Debian / Ubuntu):**
```bash
sudo apt-get update && sudo apt-get install -y build-essential python3-dev libgmp-dev
```

**macOS:**
```bash
brew install gmp
```

**Windows:** WSL2 사용 (권장) 또는 Visual Studio Build Tools + GMP 설치.

### 어떻게 설치하나요?

**가장 빠른 방법 (설치 없이):**
```bash
uvx infomesh status
```

**영구 설치:**
```bash
uv tool install infomesh        # 또는: pip install infomesh
```

**소스에서 설치:**
```bash
git clone https://github.com/dotnetpower/infomesh.git
cd infomesh
uv sync
uv run infomesh start
```

**Docker:**
```bash
docker build -t infomesh .
docker run -p 4001:4001 -p 8080:8080 infomesh
```

### 선택적 추가 기능은 어떤 것이 있나요?

```bash
pip install infomesh[vector]   # ChromaDB 벡터 검색
pip install infomesh[p2p]      # libp2p P2P 네트워킹
pip install infomesh[llm]      # 로컬 LLM 요약
pip install infomesh[all]      # 모두 포함
```

---

## MCP 통합

### VS Code (GitHub Copilot)에서 MCP 설정은?

`.vscode/mcp.json`에 추가:
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

### Claude Desktop / Cursor / Windsurf에서 MCP 설정은?

`claude_desktop_config.json` (또는 Cursor/Windsurf 설정 파일)에 추가:
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

### VS Code에서 "Server not found" 오류

1. `uv`가 PATH에 있는지 확인: `which uv`
2. 절대 경로 사용: `"command": "/home/you/.local/bin/uvx"`
3. VS Code MCP 출력 패널에서 상세 오류 확인
4. 터미널에서 직접 테스트: `uvx infomesh mcp`

### 검색 결과가 없을 때

로컬 인덱스가 비어 있습니다. 먼저 페이지를 크롤링하세요:
```bash
infomesh crawl https://docs.python.org --depth 2
```
또는 MCP에서 `crawl_url` 도구를 사용하세요.

### MCP 서버가 즉시 종료될 때

수동으로 실행하여 오류를 확인하세요:
```bash
uvx infomesh mcp
```
의존성이 누락된 경우: `uv sync` (소스) 또는 `pip install infomesh[all]`.

---

## 검색 및 크롤링

### 검색은 어떻게 작동하나요?

1. 쿼리에서 키워드 추출
2. 로컬 인덱스 먼저 검색 (10 ms 이내)
3. P2P 활성화 시 DHT를 통해 원격 피어에 키워드 해시 라우팅 (~500 ms)
4. 결과 병합 후 BM25 + 신선도 + 신뢰도로 랭킹
5. 총 목표 지연시간: ~1초

### 오프라인에서 검색할 수 있나요?

네. MCP의 `web_search`에서 `local_only=true` 또는 `infomesh search --local <쿼리>`를 사용하세요.
로컬에 캐시된 인덱스만 검색합니다.

### robots.txt는 준수하나요?

InfoMesh는 robots.txt를 **엄격하게** 준수합니다. 사이트가 크롤링을 허용하지
않으면 크롤링하지 않습니다. 이 동작은 비활성화할 수 없습니다.

### 크롤링 속도 제한은?

- 노드당 **시간당 60개 URL**
- 도메인당 최대 **10개 대기 URL**
- 최대 깊이 **3**
- 같은 도메인에 대해 **1초 딜레이**
- 최대 **5개 동시 연결**

### 인덱스에 페이지를 추가하는 방법은?

```bash
# CLI:
infomesh crawl https://example.com --depth 2

# MCP 도구:
# crawl_url 도구에 url="https://example.com", depth=2 사용
```

---

## 크레딧 시스템

### 크레딧은 어떻게 작동하나요?

크레딧은 네트워크 기여를 통해 로컬에서 적립됩니다. 블록체인도, 돈도,
구독도 없습니다. 적립한 크레딧으로 검색 쿼리를 실행합니다.

| 활동                | 적립 크레딧          |
|--------------------|----------------------|
| 크롤링              | 1.0 / 페이지         |
| 쿼리 처리           | 0.5 / 쿼리           |
| 문서 호스팅         | 0.1 / 시간           |
| 네트워크 가용시간   | 0.5 / 시간           |
| LLM 요약            | 1.5 / 페이지         |
| LLM 피어 서비스     | 2.0 / 요청           |

### 검색 비용은 얼마인가요?

| 기여 점수           | 검색 비용  | 설명              |
|--------------------|-----------|-------------------|
| < 100              | 0.100     | 신규 / 낮은 기여   |
| 100 – 999          | 0.050     | 보통 기여자        |
| ≥ 1000             | 0.033     | 높은 기여자        |

### 크레딧이 모두 소진되면?

검색은 **절대 차단되지 않습니다**. "제로 달러 부채" 모델을 사용합니다:

1. **정상**: 잔액 > 0 — 표준 검색 비용
2. **유예**: 잔액 ≤ 0, 72시간 이내 — 패널티 없음
3. **부채**: 잔액 ≤ 0, 72시간 경과 — 검색 비용 2배

크롤링이나 노드 가동으로 크레딧을 적립하면 복구됩니다.

### 코드 기여로 크레딧을 받을 수 있나요?

네. 병합된 Pull Request에 보너스 크레딧이 적립됩니다:

| PR 유형                  | 크레딧      |
|--------------------------|-------------|
| 문서 / 오타 수정         | 1,000       |
| 버그 수정 (테스트 포함)  | 10,000      |
| 신규 기능                | 50,000      |
| 주요 / 아키텍처          | 100,000     |

---

## 대시보드 및 BGM

### 대시보드를 어떻게 실행하나요?

```bash
infomesh dashboard
# 또는 특정 탭으로 바로 이동:
infomesh dashboard --tab credits
```

### 키보드 단축키는?

| 키     | 동작                |
|--------|---------------------|
| `1`–`5`| 탭 전환             |
| `/`    | 검색 입력 포커스    |
| `m`    | BGM 켜기/끄기       |
| `r`    | 강제 새로고침       |
| `q`    | 대시보드 종료       |
| `?`    | 도움말 표시         |

### BGM 오디오가 끊기거나 불안정할 때

보통 CPU 경합이 원인입니다 — 오디오 재생 프로세스가 크롤링, 인덱싱과
CPU를 놓고 경쟁합니다. 해결 방법:

1. **BGM은 v0.1.2부터 기본 꺼짐**입니다. `m` 키로 수동 활성화하거나
   `~/.infomesh/config.toml`에서 `bgm_auto_start = true` 설정
2. `ffplay` 대신 `mpv` 사용 — 더 가벼운 경향
3. 대시보드 새로고침 간격 줄이기:
   ```toml
   [dashboard]
   refresh_interval = 2.0
   ```
4. `minimal` 리소스 프로필 사용:
   ```toml
   [resource]
   profile = "minimal"
   ```

### BGM이 재생되지 않을 때

**ffplay** (ffmpeg의 일부) 또는 **mpv**가 설치되어 있는지 확인하세요:
```bash
# Debian/Ubuntu:
sudo apt install ffmpeg
# 또는:
sudo apt install mpv

# macOS:
brew install ffmpeg
# 또는:
brew install mpv
```

두 플레이어 모두 없으면 BGM은 자동으로 비활성화됩니다 (오류 없음).

---

## 설정

### 설정 파일 위치는?

`~/.infomesh/config.toml` — 첫 실행 시 자동 생성됩니다.

### 설정 변경 방법은?

TOML 파일을 직접 편집하거나 `infomesh config set` 사용:
```bash
infomesh config set crawl.max_concurrent 10
infomesh config set dashboard.theme dracula
infomesh config show
```

### 환경 변수를 사용할 수 있나요?

네. `INFOMESH_{섹션}_{키}` 형식을 사용하세요:
```bash
export INFOMESH_CRAWL_MAX_CONCURRENT=20
export INFOMESH_NODE_LISTEN_PORT=5001
```

### 사용 가능한 리소스 프로필은?

| 프로필         | CPU 코어 | 메모리   | 동시 크롤링 | 용도              |
|---------------|---------|---------|------------|-------------------|
| `minimal`     | 1       | 512 MB  | 2          | 저사양 장치       |
| `balanced`    | 2       | 2048 MB | 5          | 기본값            |
| `contributor` | 4       | 4096 MB | 10         | 적극적 기여자     |
| `dedicated`   | 8+      | 8192 MB | 20         | 전용 서버         |

### 사용 가능한 테마는?

`catppuccin-mocha` (기본), `textual-dark`, `textual-light`, `dracula`,
`tokyo-night`, `monokai`, `nord`, `gruvbox`, `textual-ansi`, `solarized-light`

---

## 네트워크 및 P2P

### InfoMesh는 어떤 포트를 사용하나요?

P2P 통신에 TCP 포트 **4001**, 로컬 관리 API에 포트 **8080**을 사용합니다.

### P2P에서 피어를 찾을 수 없을 때

1. 포트 4001이 열려 있는지 확인: `infomesh start`에 자동 포트 확인 포함
2. NAT 뒤에 있다면 라우터에서 포트 포워딩 설정
3. 방화벽 규칙 확인: `sudo iptables -L -n | grep 4001`
4. `~/.infomesh/config.toml`에서 부트스트랩 노드 연결 시도:
   ```toml
   [network]
   bootstrap_nodes = ["/ip4/x.x.x.x/tcp/4001/p2p/PEER_ID"]
   ```

### WSL2에서 실행 중인데 피어가 연결되지 않습니다

WSL2는 NAT된 가상 네트워크 안에서 실행됩니다. Windows 방화벽 규칙과
포트 프록시 포워딩이 모두 필요합니다.

**자동 설정 (권장):**
`infomesh start` 실행 시 WSL2를 자동 감지하고 스마트 설정 검사를
수행합니다:

1. **이미 설정됨** — 방화벽 규칙과 포트 프록시가 올바른 WSL2 IP로
   설정되어 있으면 프롬프트 없이 바로 진행됩니다:
   `✓ Firewall rule + port proxy already configured (→ <WSL_IP>:4001)`
2. **IP 변경됨** — 포트 프록시가 존재하지만 이전 WSL2 IP를 가리키는
   경우 (재부팅 후 흔히 발생), InfoMesh가 자동으로 업데이트합니다.
3. **누락됨** — 방화벽 규칙 또는 포트 프록시가 없으면 자동 설정을
   제안합니다. 이 경우 관리자 권한의 PowerShell이 필요합니다.

**수동 설정 (자동 설정 실패 시):**
1. Windows에서 관리자 PowerShell 열기:
   ```powershell
   # 포트 4001 Windows 방화벽 허용
   New-NetFirewallRule -DisplayName "InfoMesh-P2P-4001" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 4001

   # WSL IP 주소 확인
   wsl hostname -I

   # 포트 포워딩 설정 (<WSL_IP>를 위 명령의 IP로 교체)
   netsh interface portproxy add v4tov4 listenport=4001 listenaddress=0.0.0.0 connectport=4001 connectaddress=<WSL_IP>
   ```
2. 참고: WSL IP는 재부팅 시 변경되지만 InfoMesh가 다음 시작 시
   변경된 IP를 감지하고 자동으로 포트 프록시를 업데이트하므로
   수동 갱신은 일반적으로 필요하지 않습니다.

### 클라우드 VM에서 포트 4001을 여는 방법은?

InfoMesh는 AWS, Azure, GCP 환경을 자동 감지하고 방화벽 규칙을
자동 설정할 수 있습니다. 자동 설정이 실패하면 클라우드별 CLI를 사용하세요:

**Azure:**
```bash
az network nsg rule create -g <RG> --nsg-name <NSG> -n InfoMeshP2P \
  --priority 1100 --destination-port-ranges 4001 --access Allow \
  --protocol Tcp --direction Inbound
```

**AWS:**
```bash
aws ec2 authorize-security-group-ingress --group-id <SG_ID> \
  --protocol tcp --port 4001 --cidr 0.0.0.0/0
```

**GCP:**
```bash
gcloud compute firewall-rules create infomesh-p2p \
  --allow tcp:4001 --direction INGRESS
```

---

## 보안

### 키 관리는 어떻게 하나요?

InfoMesh는 `~/.infomesh/keys/`에 저장된 **Ed25519** 키 쌍을 사용합니다.

```bash
infomesh keys show     # 현재 키 지문 확인
infomesh keys rotate   # 새 키 쌍 생성 (이전 키는 폐기)
```

### "Permission denied on keys" 오류

```bash
chmod 600 ~/.infomesh/keys/private.key
chmod 644 ~/.infomesh/keys/public.key
```

### InfoMesh는 어떤 보안 조치를 사용하나요?

- **시빌 방어**: PoW 노드 ID 생성 + 서브넷 다양성 제한
- **DHT 검증**: 모든 값 크기 제한 (최대 1 MB), 서명된 게시
- **SSRF 보호**: URL 검증, 사설 IP 필터링
- **메시지 크기 제한**: 안전한 역직렬화로 네트워크 메시지 제한
- **콘텐츠 증명**: 크롤링 피어가 서명한 SHA-256 해시
- **무작위 감사**: 독립 노드에 의한 주기적 재크롤링 검증
- **통합 신뢰 점수**: 가동시간, 기여도, 감사 통과율, 요약 품질 기반

---

## 문제 해결

### 크롤링 중 SSL 인증서 오류

InfoMesh는 TLS 검증과 함께 `httpx`를 사용합니다. 특정 사이트에서
SSL 오류가 발생하면 크롤러가 경고를 기록하고 해당 페이지를 건너뜁니다.
만료되었거나 자체 서명된 인증서가 있는 사이트에서는 정상적인 동작입니다.

### `uv` 명령을 찾을 수 없을 때

`uv`를 설치하세요:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
그런 다음 셸을 재시작하거나 `source ~/.bashrc`를 실행하세요.

### `uv build`로 빌드가 실패할 때

프로젝트 루트 디렉토리에 있고 `hatchling`이 빌드 백엔드로 설정되어
있는지 확인하세요:
```bash
uv sync --dev
uv build
```

### 새 변경사항을 풀한 후 테스트 실패

```bash
uv sync --dev
uv run pytest tests/ \
  --ignore=tests/test_vector.py \
  --ignore=tests/test_libp2p_spike.py \
  -x -q --tb=short
```

### 버그를 신고하려면?

[github.com/dotnetpower/infomesh/issues](https://github.com/dotnetpower/infomesh/issues)에서
이슈를 열어주세요:
1. OS 및 Python 버전 (`python --version`)
2. InfoMesh 버전 (`infomesh --version`)
3. 재현 단계
4. 오류 메시지 또는 로그 출력

---

## 법률 및 준수

### robots.txt를 준수하나요?

네, 엄격하게 준수합니다. 웹사이트가 robots.txt를 통해 크롤링을 금지하면
InfoMesh는 해당 사이트를 크롤링하지 않습니다. 이 동작은 비활성화할 수 없습니다.

### DMCA 게시 중단은 어떻게 작동하나요?

서명된 게시 중단 요청이 DHT를 통해 전파됩니다. 모든 노드는 24시간 이내에
해당 콘텐츠를 로컬 인덱스에서 삭제해야 합니다.

### GDPR은?

InfoMesh는 서명된 DHT 항목을 통한 분산 삭제 레코드를 제공합니다.
개인 데이터가 포함된 페이지를 제외하도록 노드를 설정할 수 있습니다.

### 검색 쿼리가 기록되나요?

아니요. 검색 쿼리는 로컬에서 처리되거나 P2P 네트워크를 통해 임시로
라우팅됩니다. 쿼리를 기록하는 중앙 서버는 없습니다.
