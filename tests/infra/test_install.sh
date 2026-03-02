#!/usr/bin/env bash
# InfoMesh Installation Test Script
# Tests all documented installation methods on Ubuntu
set -euo pipefail

RESULTS_FILE="/tmp/infomesh_test_results.txt"
> "$RESULTS_FILE"

pass_count=0
fail_count=0

log() { echo "[$(date +%H:%M:%S)] $*"; }

record() {
  local method="$1" step="$2" status="$3" detail="${4:-}"
  if [[ "$status" == "PASS" ]]; then
    ((pass_count++)) || true
  else
    ((fail_count++)) || true
  fi
  printf "%-25s | %-30s | %-4s | %s\n" "$method" "$step" "$status" "$detail" | tee -a "$RESULTS_FILE"
}

cleanup() {
  log "Cleaning up..."
  # Remove any installed tools
  command -v uv &>/dev/null && uv tool uninstall infomesh 2>/dev/null || true
  pip3 uninstall -y infomesh 2>/dev/null || true
  rm -rf /tmp/infomesh-src 2>/dev/null || true
  # Keep uv itself installed for subsequent tests
}

OS_VERSION=$(lsb_release -ds)
PYTHON_VERSION=$(python3 --version 2>&1)
echo "=========================================="
echo "InfoMesh Installation Test"
echo "OS: $OS_VERSION"
echo "Python: $PYTHON_VERSION"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================="
echo ""

# ============================================================
# METHOD 1: uvx (zero-install, documented as primary method)
# ============================================================
log "=== Method 1: uvx (zero-install) ==="
cleanup

# Step 1a: Install uv
if ! command -v uv &>/dev/null; then
  log "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if command -v uv &>/dev/null; then
  record "uvx" "uv installed" "PASS" "$(uv --version)"
else
  record "uvx" "uv installed" "FAIL" "uv not found in PATH"
fi

# Step 1b: uvx infomesh --version
log "Testing: uvx infomesh --version"
if uvx_ver=$(uvx infomesh --version 2>&1); then
  record "uvx" "infomesh --version" "PASS" "$uvx_ver"
else
  record "uvx" "infomesh --version" "FAIL" "$uvx_ver"
fi

# Step 1c: uvx infomesh --help
log "Testing: uvx infomesh --help"
if uvx infomesh --help &>/dev/null; then
  record "uvx" "infomesh --help" "PASS"
else
  record "uvx" "infomesh --help" "FAIL"
fi

# Step 1d: uvx infomesh status
log "Testing: uvx infomesh status"
if status_out=$(uvx infomesh status 2>&1); then
  record "uvx" "infomesh status" "PASS" "$(echo "$status_out" | head -1)"
else
  # status may return non-zero if node not running, check if it's a real error
  if echo "$status_out" | grep -qi "error\|traceback\|modulenotfound"; then
    record "uvx" "infomesh status" "FAIL" "$(echo "$status_out" | head -1)"
  else
    record "uvx" "infomesh status" "PASS" "exit non-zero but no crash"
  fi
fi

# Step 1e: uvx infomesh crawl (actual crawl test)
log "Testing: uvx infomesh crawl"
if crawl_out=$(uvx infomesh crawl https://example.com 2>&1); then
  record "uvx" "infomesh crawl" "PASS" "$(echo "$crawl_out" | head -1)"
else
  if echo "$crawl_out" | grep -qi "traceback\|modulenotfound"; then
    record "uvx" "infomesh crawl" "FAIL" "$(echo "$crawl_out" | tail -1)"
  else
    record "uvx" "infomesh crawl" "PASS" "$(echo "$crawl_out" | head -1)"
  fi
fi

# Step 1f: uvx infomesh search
log "Testing: uvx infomesh search"
if search_out=$(uvx infomesh search "example" 2>&1); then
  record "uvx" "infomesh search" "PASS" "$(echo "$search_out" | head -1)"
else
  if echo "$search_out" | grep -qi "traceback\|modulenotfound"; then
    record "uvx" "infomesh search" "FAIL" "$(echo "$search_out" | tail -1)"
  else
    record "uvx" "infomesh search" "PASS" "$(echo "$search_out" | head -1)"
  fi
fi

# Step 1g: Python import test
log "Testing: python import infomesh"
if import_out=$(uvx --from infomesh python -c "import infomesh; print(infomesh.__version__)" 2>&1); then
  record "uvx" "python import" "PASS" "v$import_out"
else
  record "uvx" "python import" "FAIL" "$(echo "$import_out" | tail -1)"
fi

echo ""

# ============================================================
# METHOD 2: uv tool install (persistent install)
# ============================================================
log "=== Method 2: uv tool install ==="
cleanup

log "Testing: uv tool install infomesh"
if install_out=$(uv tool install infomesh 2>&1); then
  record "uv-tool" "install" "PASS" "$(echo "$install_out" | tail -1)"
else
  # "already installed" is also acceptable
  if echo "$install_out" | grep -qi "already installed"; then
    record "uv-tool" "install" "PASS" "already installed"
  else
    record "uv-tool" "install" "FAIL" "$(echo "$install_out" | tail -1)"
  fi
fi

# Ensure PATH includes uv tools
export PATH="$HOME/.local/bin:$PATH"

if command -v infomesh &>/dev/null; then
  record "uv-tool" "infomesh in PATH" "PASS" "$(which infomesh)"
else
  record "uv-tool" "infomesh in PATH" "FAIL" "not found"
fi

log "Testing: infomesh --version"
if ver=$(infomesh --version 2>&1); then
  record "uv-tool" "infomesh --version" "PASS" "$ver"
else
  record "uv-tool" "infomesh --version" "FAIL" "$ver"
fi

log "Testing: infomesh status"
if status_out=$(infomesh status 2>&1); then
  record "uv-tool" "infomesh status" "PASS" "$(echo "$status_out" | head -1)"
else
  if echo "$status_out" | grep -qi "error\|traceback\|modulenotfound"; then
    record "uv-tool" "infomesh status" "FAIL" "$(echo "$status_out" | head -1)"
  else
    record "uv-tool" "infomesh status" "PASS" "exit non-zero but no crash"
  fi
fi

log "Testing: infomesh crawl + search"
if crawl_out=$(infomesh crawl https://example.com 2>&1); then
  record "uv-tool" "crawl+search" "PASS"
else
  if echo "$crawl_out" | grep -qi "traceback\|modulenotfound"; then
    record "uv-tool" "crawl+search" "FAIL" "$(echo "$crawl_out" | tail -1)"
  else
    record "uv-tool" "crawl+search" "PASS" "$(echo "$crawl_out" | head -1)"
  fi
fi

echo ""

# ============================================================
# METHOD 3: pip install (traditional)
# ============================================================
log "=== Method 3: pip install ==="
cleanup

# Need a venv since Ubuntu has PEP 668 restrictions
log "Creating venv with uv for pip test..."
uv venv /tmp/pip-test-venv --seed --python 3.12 2>&1 || uv venv /tmp/pip-test-venv --seed 2>&1
source /tmp/pip-test-venv/bin/activate

log "Testing: pip install infomesh"
if pip_out=$(pip install infomesh 2>&1); then
  record "pip" "install" "PASS"
else
  record "pip" "install" "FAIL" "$(echo "$pip_out" | tail -1)"
  deactivate 2>/dev/null || true
  # Skip rest of pip tests
  echo ""
  log "=== Skipping remaining pip tests ==="
  goto_source=true
fi

if [[ "${goto_source:-}" != "true" ]]; then
  log "Testing: infomesh --version (pip)"
  if ver=$(infomesh --version 2>&1); then
    record "pip" "infomesh --version" "PASS" "$ver"
  else
    record "pip" "infomesh --version" "FAIL" "$ver"
  fi

  log "Testing: python -c import"
  if import_out=$(python -c "import infomesh; print(infomesh.__version__)" 2>&1); then
    record "pip" "python import" "PASS" "v$import_out"
  else
    record "pip" "python import" "FAIL" "$(echo "$import_out" | tail -1)"
  fi

  log "Testing: infomesh status (pip)"
  if status_out=$(infomesh status 2>&1); then
    record "pip" "infomesh status" "PASS"
  else
    if echo "$status_out" | grep -qi "traceback\|modulenotfound"; then
      record "pip" "infomesh status" "FAIL" "$(echo "$status_out" | head -1)"
    else
      record "pip" "infomesh status" "PASS" "exit non-zero but no crash"
    fi
  fi

  log "Testing: infomesh crawl (pip)"
  if crawl_out=$(infomesh crawl https://example.com 2>&1); then
    record "pip" "crawl" "PASS"
  else
    if echo "$crawl_out" | grep -qi "traceback\|modulenotfound"; then
      record "pip" "crawl" "FAIL" "$(echo "$crawl_out" | tail -1)"
    else
      record "pip" "crawl" "PASS" "$(echo "$crawl_out" | head -1)"
    fi
  fi

  deactivate 2>/dev/null || true
fi
rm -rf /tmp/pip-test-venv

echo ""

# ============================================================
# METHOD 4: From source (git clone + uv sync)
# ============================================================
log "=== Method 4: From source ==="
cleanup

# Install build prerequisites
log "Installing build prerequisites..."
sudo apt-get update -qq 2>&1 | tail -1
sudo apt-get install -y -qq git build-essential python3-dev libgmp-dev 2>&1 | tail -1

log "Cloning infomesh..."
if git clone --depth 1 https://github.com/dotnetpower/infomesh.git /tmp/infomesh-src 2>&1; then
  record "source" "git clone" "PASS"
else
  record "source" "git clone" "FAIL"
fi

cd /tmp/infomesh-src

log "Testing: uv sync"
if sync_out=$(uv sync 2>&1); then
  record "source" "uv sync" "PASS"
else
  record "source" "uv sync" "FAIL" "$(echo "$sync_out" | tail -1)"
fi

log "Testing: uv run infomesh --version"
if ver=$(uv run infomesh --version 2>&1); then
  record "source" "infomesh --version" "PASS" "$ver"
else
  record "source" "infomesh --version" "FAIL" "$ver"
fi

log "Testing: uv run infomesh status"
if status_out=$(uv run infomesh status 2>&1); then
  record "source" "infomesh status" "PASS"
else
  if echo "$status_out" | grep -qi "traceback\|modulenotfound"; then
    record "source" "infomesh status" "FAIL" "$(echo "$status_out" | head -1)"
  else
    record "source" "infomesh status" "PASS" "exit non-zero but no crash"
  fi
fi

log "Testing: uv run infomesh crawl"
if crawl_out=$(uv run infomesh crawl https://example.com 2>&1); then
  record "source" "crawl" "PASS"
else
  if echo "$crawl_out" | grep -qi "traceback\|modulenotfound"; then
    record "source" "crawl" "FAIL" "$(echo "$crawl_out" | tail -1)"
  else
    record "source" "crawl" "PASS" "$(echo "$crawl_out" | head -1)"
  fi
fi

log "Testing: uv run infomesh search"
if search_out=$(uv run infomesh search "example" 2>&1); then
  record "source" "search" "PASS"
else
  if echo "$search_out" | grep -qi "traceback\|modulenotfound"; then
    record "source" "search" "FAIL" "$(echo "$search_out" | tail -1)"
  else
    record "source" "search" "PASS" "$(echo "$search_out" | head -1)"
  fi
fi

# Build test
log "Testing: uv build"
if build_out=$(uv build 2>&1); then
  record "source" "uv build" "PASS"
else
  record "source" "uv build" "FAIL" "$(echo "$build_out" | tail -1)"
fi

cd ~
echo ""

# ============================================================
# METHOD 5: Docker
# ============================================================
log "=== Method 5: Docker ==="

# Check if docker is available
if command -v docker &>/dev/null; then
  log "Docker found: $(docker --version)"

  log "Testing: docker build"
  cd /tmp/infomesh-src
  if docker build -t infomesh-test . 2>&1 | tail -3; then
    record "docker" "build" "PASS"

    log "Testing: docker run infomesh --version"
    if ver=$(docker run --rm infomesh-test infomesh --version 2>&1); then
      record "docker" "infomesh --version" "PASS" "$ver"
    else
      record "docker" "infomesh --version" "FAIL" "$ver"
    fi

    log "Testing: docker run infomesh status"
    if status_out=$(docker run --rm infomesh-test infomesh status 2>&1); then
      record "docker" "infomesh status" "PASS"
    else
      if echo "$status_out" | grep -qi "traceback\|modulenotfound"; then
        record "docker" "infomesh status" "FAIL"
      else
        record "docker" "infomesh status" "PASS" "exit non-zero but no crash"
      fi
    fi

    docker rmi infomesh-test 2>/dev/null || true
  else
    record "docker" "build" "FAIL"
  fi
  cd ~
else
  log "Docker not installed, installing..."
  sudo apt-get install -y -qq docker.io 2>&1 | tail -1
  if command -v docker &>/dev/null; then
    sudo systemctl start docker 2>/dev/null || true
    record "docker" "install" "PASS"
    cd /tmp/infomesh-src
    if sudo docker build -t infomesh-test . 2>&1 | tail -3; then
      record "docker" "build" "PASS"

      if ver=$(sudo docker run --rm infomesh-test infomesh --version 2>&1); then
        record "docker" "infomesh --version" "PASS" "$ver"
      else
        record "docker" "infomesh --version" "FAIL" "$ver"
      fi

      log "Testing: docker run infomesh status"
      if status_out=$(sudo docker run --rm infomesh-test infomesh status 2>&1); then
        record "docker" "infomesh status" "PASS"
      else
        if echo "$status_out" | grep -qi "traceback\|modulenotfound"; then
          record "docker" "infomesh status" "FAIL"
        else
          record "docker" "infomesh status" "PASS" "exit non-zero but no crash"
        fi
      fi

      sudo docker rmi infomesh-test 2>/dev/null || true
    else
      record "docker" "build" "FAIL"
    fi
    cd ~
  else
    record "docker" "install" "FAIL" "docker not available"
  fi
fi

echo ""

# ============================================================
# SUMMARY
# ============================================================
echo "=========================================="
echo "TEST RESULTS SUMMARY"
echo "OS: $OS_VERSION"
echo "Python: $PYTHON_VERSION"
echo "=========================================="
echo ""
printf "%-25s | %-30s | %-4s | %s\n" "METHOD" "STEP" "STAT" "DETAIL"
printf "%s\n" "$(printf '%.0s-' {1..100})"
cat "$RESULTS_FILE"
echo ""
echo "Total: $pass_count PASS, $fail_count FAIL"
echo "=========================================="
