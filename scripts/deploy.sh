#!/bin/bash
# Deploy FastAPI backend to EC2: rsync app/ + requirements.txt, pip install, restart systemd.
#
# Usage:
#   bash scripts/deploy.sh
#
# Optional environment (defaults match this project's EC2):
#   BROLL_EC2_HOST   — public IP or hostname (default: 50.19.105.212)
#   BROLL_EC2_USER   — SSH user (default: ubuntu)
#   BROLL_SSH_KEY    — path to PEM (default: ~/.ssh/broll-scout-key.pem)
#   BROLL_APP_DIR    — remote install path (default: /opt/broll-scout)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BROLL_EC2_HOST="${BROLL_EC2_HOST:-50.19.105.212}"
BROLL_EC2_USER="${BROLL_EC2_USER:-ubuntu}"
BROLL_SSH_KEY="${BROLL_SSH_KEY:-$HOME/.ssh/broll-scout-key.pem}"
BROLL_APP_DIR="${BROLL_APP_DIR:-/opt/broll-scout}"

if [[ ! -f "$BROLL_SSH_KEY" ]]; then
  echo "ERROR: SSH key not found: $BROLL_SSH_KEY"
  echo "Set BROLL_SSH_KEY to your EC2 .pem path, or place the key at the default location."
  exit 1
fi

SSH_OPTS=(-i "$BROLL_SSH_KEY" -o StrictHostKeyChecking=accept-new)
SSH=(ssh "${SSH_OPTS[@]}" "${BROLL_EC2_USER}@${BROLL_EC2_HOST}")

echo "=== Deploying B-Roll Scout to EC2 (${BROLL_EC2_USER}@${BROLL_EC2_HOST}) ==="

echo ">> Syncing Python code..."
rsync -avz --delete \
  -e "ssh -i \"$BROLL_SSH_KEY\" -o StrictHostKeyChecking=accept-new" \
  --include='app/' \
  --include='app/**' \
  --include='requirements.txt' \
  --exclude='*' \
  "$PROJECT_ROOT/" \
  "${BROLL_EC2_USER}@${BROLL_EC2_HOST}:/tmp/broll-deploy/"

echo ">> Moving code to $BROLL_APP_DIR..."
"${SSH[@]}" "sudo rsync -a /tmp/broll-deploy/ $BROLL_APP_DIR/ && sudo chown -R broll:broll $BROLL_APP_DIR"

echo ">> Installing dependencies..."
PIP_OUT=$("${SSH[@]}" "sudo -u broll $BROLL_APP_DIR/venv/bin/pip install -q -r $BROLL_APP_DIR/requirements.txt" 2>&1)
NEW_PKGS=$(echo "$PIP_OUT" | grep -v "already satisfied" || true)
if [[ -n "$NEW_PKGS" ]]; then
  echo "$NEW_PKGS"
else
  echo "   All dependencies up to date."
fi

echo ">> Restarting service..."
"${SSH[@]}" "sudo systemctl restart broll-scout && sleep 2 && sudo systemctl status broll-scout --no-pager -l" 2>&1 | \
  grep -E '(Active:|Main PID:|Memory:|=== Deploy|Uvicorn running)' || true

STATUS=$("${SSH[@]}" "systemctl is-active broll-scout" 2>/dev/null || true)
if [[ "$STATUS" == "active" ]]; then
  echo "=== Deploy complete — service is running ==="
else
  echo "=== WARNING: service status is '$STATUS' — check logs with: ssh ${BROLL_EC2_USER}@${BROLL_EC2_HOST} journalctl -u broll-scout -n 50 ==="
  exit 1
fi
echo "Health check: curl -sS https://broll.jayasim.com/api/v1/health"
