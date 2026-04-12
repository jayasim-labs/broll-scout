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
"${SSH[@]}" "sudo -u broll $BROLL_APP_DIR/venv/bin/pip install -r $BROLL_APP_DIR/requirements.txt"

echo ">> Restarting service..."
"${SSH[@]}" "sudo systemctl restart broll-scout && sleep 2 && sudo systemctl status broll-scout --no-pager"

echo "=== Deploy complete ==="
echo "Health check: curl -sS https://broll.jayasim.com/api/v1/health"
