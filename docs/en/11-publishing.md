# Publishing InfoMesh to PyPI

Step-by-step guide for packaging and publishing InfoMesh to PyPI.

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager & build tool)
- PyPI account at [pypi.org](https://pypi.org)
- TestPyPI account at [test.pypi.org](https://test.pypi.org)

## Package Structure

InfoMesh uses **hatchling** as its build backend. Key packaging files:

```
infomesh/
├── pyproject.toml          # Package metadata, dependencies, build config
├── infomesh/__init__.py    # __version__ = "0.1.0"
├── infomesh/__main__.py    # CLI entry point
├── LICENSE                 # MIT License
├── README.md               # PyPI long description
├── seeds/                  # Bundled seed URL lists
├── bootstrap/              # Bootstrap node list
└── .github/workflows/
    ├── ci.yml              # CI: lint, test, build on push/PR
    └── publish.yml         # Publish to PyPI on release
```

## pyproject.toml Highlights

```toml
[project]
name = "infomesh"
version = "0.1.0"
requires-python = ">=3.12"
license = "MIT"

[project.scripts]
infomesh = "infomesh.__main__:main"      # CLI entry point

[project.optional-dependencies]
p2p = ["libp2p>=0.2"]                    # P2P networking
vector = ["chromadb>=0.5", ...]          # Semantic search
llm = ["ollama>=0.3"]                    # Local LLM
all = ["infomesh[p2p,vector,llm]"]       # Everything

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## Installation Options (After Publishing)

```bash
# Core only (search + crawl + MCP)
pip install infomesh

# With semantic vector search
pip install infomesh[vector]

# With P2P networking
pip install infomesh[p2p]

# With local LLM summarization
pip install infomesh[llm]

# Everything
pip install infomesh[all]

# Using uv
uv add infomesh
uv add "infomesh[all]"
```

---

## Build Locally

```bash
# Build sdist + wheel
uv build

# Check what's in the wheel
python -m zipfile -l dist/infomesh-*.whl

# Check metadata
python -m tarfile -l dist/infomesh-*.tar.gz | head -20
```

Expected output in `dist/`:
```
dist/
├── infomesh-0.1.0.tar.gz
└── infomesh-0.1.0-py3-none-any.whl
```

## Test Installation

```bash
# Install from the built wheel
pip install dist/infomesh-0.1.0-py3-none-any.whl

# Verify CLI works
infomesh --version
infomesh --help

# Verify Python import works
python -c "import infomesh; print(infomesh.__version__)"
```

---

## Publish to TestPyPI (Manual)

```bash
# 1. Build
uv build

# 2. Upload to TestPyPI
uv publish --publish-url https://test.pypi.org/legacy/

# 3. Test install from TestPyPI
pip install -i https://test.pypi.org/simple/ infomesh
```

## Publish to PyPI (Manual)

```bash
# 1. Build
uv build

# 2. Upload to PyPI
uv publish

# 3. Verify
pip install infomesh
infomesh --version
```

---

## Publish via GitHub Actions (Recommended)

The recommended approach uses **trusted publishing** — no API tokens needed.

### One-Time Setup

1. **PyPI**: Go to [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/)
   - Add a new "pending publisher":
     - **PyPI project name**: `infomesh`
     - **Owner**: `dotnetpower`
     - **Repository**: `infomesh`
     - **Workflow name**: `publish.yml`
     - **Environment name**: `pypi`

2. **TestPyPI**: Same at [test.pypi.org/manage/account/publishing](https://test.pypi.org/manage/account/publishing/)
   - **Environment name**: `testpypi`

3. **GitHub**: Create environments in repo settings:
   - Go to **Settings → Environments**
   - Create `pypi` environment (add protection rules like required reviewers)
   - Create `testpypi` environment

### Release Process

```bash
# 1. Update version
# Edit infomesh/__init__.py and pyproject.toml

# 2. Commit and tag
git add -A
git commit -m "release: v0.1.0"
git tag v0.1.0
git push origin main --tags

# 3. Create GitHub Release
# Go to GitHub → Releases → "Create a new release"
# Select tag v0.1.0, write release notes, click "Publish release"
# → GitHub Actions automatically builds and publishes to PyPI
```

---

## Version Bumping

Version is defined in two places (keep them in sync):

1. `pyproject.toml` → `version = "X.Y.Z"`
2. `infomesh/__init__.py` → `__version__ = "X.Y.Z"`

Versioning follows [SemVer](https://semver.org/):
- **0.1.x** — Alpha: API may change
- **0.2.x** — Beta: Stabilizing
- **1.0.0** — Stable: Public API frozen

---

## Pre-Release Checklist

- [ ] All tests pass: `uv run pytest`
- [ ] No lint errors: `uv run ruff check .`
- [ ] Version bumped in both `pyproject.toml` and `__init__.py`
- [ ] README.md is up to date
- [ ] CHANGELOG updated (if maintained)
- [ ] Build succeeds: `uv build`
- [ ] Test install works: `pip install dist/*.whl && infomesh --version`
- [ ] TestPyPI upload succeeds
- [ ] Git tag created: `git tag vX.Y.Z`
- [ ] GitHub Release created with release notes

---

## Troubleshooting

### "Invalid distribution" on upload
- Ensure `hatchling` is in `[build-system].requires`
- Run `uv build` and check that `dist/` contains both `.tar.gz` and `.whl`

### "Name already taken" on PyPI
- The name `infomesh` must be available. Check at [pypi.org/project/infomesh/](https://pypi.org/project/infomesh/)

### Missing files in wheel
- Check `[tool.hatch.build.targets.wheel]` in `pyproject.toml`
- Run `python -m zipfile -l dist/*.whl` to inspect contents

### Import errors after pip install
- Ensure all non-optional dependencies are in `[project.dependencies]`
- Test with a fresh virtualenv: `python -m venv /tmp/test && /tmp/test/bin/pip install dist/*.whl`

---

*Related docs: [Overview](01-overview.md) · [Architecture](02-architecture.md) · [Credit System](03-credit-system.md) · [Tech Stack](04-tech-stack.md) · [Legal](06-legal.md) · [Trust & Integrity](07-trust-integrity.md) · [Security Audit](08-security-audit.md) · [Console Dashboard](09-console-dashboard.md) · [MCP Integration](10-mcp-integration.md)*
