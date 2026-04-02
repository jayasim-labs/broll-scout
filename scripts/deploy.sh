#!/bin/bash
set -euo pipefail

EC2_IP="50.19.105.212"
KEY="/Users/Jayasim/.ssh/broll-scout-key.pem"
APP_DIR="/opt/broll-scout"
SSH="ssh -i $KEY ubuntu@$EC2_IP"

echo "=== Deploying B-Roll Scout to EC2 ==="

echo ">> Syncing Python code..."
rsync -avz --delete \
  -e "ssh -i $KEY" \
  --include='app/***' \
  --include='requirements.txt' \
  --exclude='*' \
  "/Users/Jayasim/Documents/CursorCode/BRoll Scout/" \
  "ubuntu@$EC2_IP:/tmp/broll-deploy/"

echo ">> Moving code to $APP_DIR..."
$SSH "sudo rsync -a /tmp/broll-deploy/ $APP_DIR/ && sudo chown -R broll:broll $APP_DIR"

echo ">> Installing dependencies..."
$SSH "sudo -u broll $APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt"

echo ">> Restarting service..."
$SSH "sudo systemctl restart broll-scout && sleep 2 && sudo systemctl status broll-scout --no-pager"

echo "=== Deploy complete ==="
