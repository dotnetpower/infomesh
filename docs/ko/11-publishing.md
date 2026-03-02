# InfoMesh PyPI 퍼블리싱 가이드

InfoMesh를 PyPI에 패키징하고 퍼블리싱하는 단계별 가이드입니다.

---

## 사전 요구사항

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (패키지 매니저 & 빌드 도구)
- [pypi.org](https://pypi.org) 계정
- [test.pypi.org](https://test.pypi.org) 계정

## 패키지 구조

InfoMesh는 **hatchling**을 빌드 백엔드로 사용합니다. 주요 패키징 파일:

```
infomesh/
├── pyproject.toml          # 패키지 메타데이터, 의존성, 빌드 설정
├── infomesh/__init__.py    # __version__ = "0.1.6"
├── infomesh/__main__.py    # CLI 엔트리 포인트
├── LICENSE                 # MIT 라이선스
├── README.md               # PyPI 상세 설명
├── seeds/                  # 번들 시드 URL 목록
├── bootstrap/              # 부트스트랩 노드 목록
└── .github/workflows/
    ├── ci.yml              # CI: 린트, 테스트, 빌드 (push/PR 시)
    └── publish.yml         # PyPI 퍼블리싱 (릴리스 시)
```

## pyproject.toml 주요 내용

```toml
[project]
name = "infomesh"
version = "0.1.6"
requires-python = ">=3.12"
license = "MIT"

[project.scripts]
infomesh = "infomesh.__main__:main"      # CLI 엔트리 포인트

[project.optional-dependencies]
p2p = ["trio>=0.22", "libp2p>=0.2"]   # P2P 네트워킹
vector = ["chromadb>=0.5", ...]          # 시맨틱 검색
llm = ["ollama>=0.3"]                    # 로컬 LLM
all = ["infomesh[p2p,vector,llm]"]       # 전체

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## 설치 옵션 (퍼블리싱 후)

```bash
# 코어 (검색 + 크롤 + MCP)
pip install infomesh

# P2P 네트워킹 포함 (네이티브 빌드 도구 필요)
pip install 'infomesh[p2p]'

# 시맨틱 벡터 검색 포함
pip install infomesh[vector]

# 로컬 LLM 요약 포함
pip install infomesh[llm]

# 전체 (P2P + 벡터 + LLM)
pip install infomesh[all]

# uv 사용
uv add infomesh
uv add "infomesh[all]"
```

---

## 로컬 빌드

```bash
# sdist + wheel 빌드
uv build

# wheel 내용 확인
python -m zipfile -l dist/infomesh-*.whl

# 메타데이터 확인
python -m tarfile -l dist/infomesh-*.tar.gz | head -20
```

`dist/` 예상 출력:
```
dist/
├── infomesh-0.1.0.tar.gz
└── infomesh-0.1.0-py3-none-any.whl
```

## 테스트 설치

```bash
# 빌드된 wheel에서 설치
pip install dist/infomesh-0.1.0-py3-none-any.whl

# CLI 동작 확인
infomesh --version
infomesh --help

# Python 임포트 확인
python -c "import infomesh; print(infomesh.__version__)"
```

---

## TestPyPI 퍼블리싱 (수동)

```bash
# 1. 빌드
uv build

# 2. TestPyPI에 업로드
uv publish --publish-url https://test.pypi.org/legacy/

# 3. TestPyPI에서 테스트 설치
pip install -i https://test.pypi.org/simple/ infomesh
```

## PyPI 퍼블리싱 (수동)

```bash
# 1. 빌드
uv build

# 2. PyPI에 업로드
uv publish

# 3. 확인
pip install infomesh
infomesh --version
```

---

## GitHub Actions를 통한 퍼블리싱 (권장)

**trusted publishing**을 사용하므로 API 토큰이 필요 없습니다.

### 최초 설정

1. **PyPI**: [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/)으로 이동
   - "pending publisher" 추가:
     - **PyPI project name**: `infomesh`
     - **Owner**: `dotnetpower`
     - **Repository**: `infomesh`
     - **Workflow name**: `publish.yml`
     - **Environment name**: `pypi`

2. **TestPyPI**: [test.pypi.org/manage/account/publishing](https://test.pypi.org/manage/account/publishing/)에서 동일하게 설정
   - **Environment name**: `testpypi`

3. **GitHub**: 리포지토리 설정에서 환경 생성:
   - **Settings → Environments** 이동
   - `pypi` 환경 생성 (필요시 리뷰어 승인 등 보호 규칙 추가)
   - `testpypi` 환경 생성

### 릴리스 프로세스

```bash
# 1. 버전 업데이트
# infomesh/__init__.py와 pyproject.toml 편집

# 2. 커밋 및 태그
git add -A
git commit -m "release: v0.1.0"
git tag v0.1.0
git push origin main --tags

# 3. GitHub Release 생성
# GitHub → Releases → "Create a new release"
# 태그 v0.1.0 선택, 릴리스 노트 작성, "Publish release" 클릭
# → GitHub Actions가 자동으로 빌드 및 PyPI 퍼블리싱
```

---

## 버전 관리

버전은 두 곳에서 정의됩니다 (항상 동기화 유지):

1. `pyproject.toml` → `version = "X.Y.Z"`
2. `infomesh/__init__.py` → `__version__ = "X.Y.Z"`

버전 체계는 [SemVer](https://semver.org/lang/ko/)를 따릅니다:
- **0.1.x** — 알파: API 변경 가능
- **0.2.x** — 베타: 안정화 단계
- **1.0.0** — 안정: 공개 API 고정

---

## 릴리스 전 체크리스트

- [ ] 모든 테스트 통과: `uv run pytest`
- [ ] 린트 오류 없음: `uv run ruff check .`
- [ ] `pyproject.toml`과 `__init__.py` 모두 버전 업데이트
- [ ] README.md 최신 상태
- [ ] CHANGELOG 업데이트 (유지 관리 시)
- [ ] 빌드 성공: `uv build`
- [ ] 테스트 설치 동작: `pip install dist/*.whl && infomesh --version`
- [ ] TestPyPI 업로드 성공
- [ ] Git 태그 생성: `git tag vX.Y.Z`
- [ ] GitHub Release 생성 및 릴리스 노트 작성

---

## 문제 해결

### 업로드 시 "Invalid distribution"
- `[build-system].requires`에 `hatchling`이 있는지 확인
- `uv build` 실행 후 `dist/`에 `.tar.gz`와 `.whl` 모두 있는지 확인

### PyPI에서 "Name already taken"
- `infomesh` 이름이 사용 가능한지 확인: [pypi.org/project/infomesh/](https://pypi.org/project/infomesh/)

### wheel에 파일 누락
- `pyproject.toml`의 `[tool.hatch.build.targets.wheel]` 확인
- `python -m zipfile -l dist/*.whl`로 내용 점검

### pip install 후 임포트 오류
- 모든 필수 의존성이 `[project.dependencies]`에 있는지 확인
- 깨끗한 가상환경에서 테스트: `python -m venv /tmp/test && /tmp/test/bin/pip install dist/*.whl`

---

*관련 문서: [개요](01-overview.md) · [아키텍처](02-architecture.md) · [크레딧 시스템](03-credit-system.md) · [기술 스택](04-tech-stack.md) · [법적 고려사항](06-legal.md) · [신뢰 & 무결성](07-trust-integrity.md) · [보안 감사](08-security-audit.md) · [콘솔 대시보드](09-console-dashboard.md) · [MCP 연동](10-mcp-integration.md) · [FAQ](12-faq.md)*
