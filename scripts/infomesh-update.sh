#!/bin/bash
# InfoMesh Auto-Update Script
# Feature #43: Systemd timer auto-update
#
# Install as systemd timer:
#   sudo cp scripts/infomesh-update.sh /usr/local/bin/
#   sudo chmod +x /usr/local/bin/infomesh-update.sh
#   sudo cp deploy/infomesh-update.timer /etc/systemd/system/
#   sudo cp deploy/infomesh-update.service /etc/systemd/system/
#   sudo systemctl enable --now infomesh-update.timer

set -euo pipefail

LOG_TAG="infomesh-update"

log() {
    logger -t "$LOG_TAG" "$@"
    echo "[$(date -Iseconds)] $*"
}

# Check for updates
log "Checking for InfoMesh updates..."

CURRENT=$(infomesh --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo "0.0.0")
LATEST=$(pip index versions infomesh 2>/dev/null | head -1 | grep -oP '\d+\.\d+\.\d+' || echo "0.0.0")

if [ "$CURRENT" = "$LATEST" ]; then
    log "Already up to date: v$CURRENT"
    exit 0
fi

log "Update available: v$CURRENT -> v$LATEST"

# Install update
if command -v uv &>/dev/null; then
    uv pip install --upgrade infomesh
else
    pip install --upgrade infomesh
fi

# Restart service if running
if systemctl is-active --quiet infomesh; then
    log "Restarting infomesh service..."
    systemctl restart infomesh
    log "Service restarted"
fi

NEW_VER=$(infomesh --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo "unknown")
log "Updated to v$NEW_VER"
