#!/usr/bin/env bash
# Dev launcher — starts companion + Next.js dev server
# Usage: npm run dev  (or: bash scripts/dev.sh)
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPANION_DIR="$ROOT/broll-companion"
VENV="$COMPANION_DIR/.venv"
COMPANION_PID=""

cleanup() {
  if [ -n "$COMPANION_PID" ] && kill -0 "$COMPANION_PID" 2>/dev/null; then
    echo ""
    echo "Stopping companion (PID $COMPANION_PID)..."
    kill "$COMPANION_PID" 2>/dev/null
    wait "$COMPANION_PID" 2>/dev/null || true
  fi
  lsof -ti:3000,3001 2>/dev/null | xargs kill -9 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Kill stale processes on the ports we need
lsof -ti:3000,3001,9876 2>/dev/null | xargs kill -9 2>/dev/null || true

# ---- Companion venv bootstrap ----
if [ ! -d "$VENV" ]; then
  echo "Creating companion venv..."
  python3 -m venv "$VENV"
fi

echo "Checking companion dependencies..."
"$VENV/bin/pip" install -q -r "$COMPANION_DIR/requirements.txt" 2>/dev/null

# ---- Ensure Ollama is running (with parallel matching) ----
if command -v ollama &>/dev/null; then
  # Always restart Ollama to guarantee OLLAMA_NUM_PARALLEL=3
  echo "Stopping Ollama (if running) to apply OLLAMA_NUM_PARALLEL=3..."
  pkill -f "ollama serve" 2>/dev/null || true
  if [[ "$(uname)" == "Darwin" ]]; then
    brew services stop ollama 2>/dev/null || true
  fi
  sleep 1

  if ! curl -s http://127.0.0.1:11434/api/tags &>/dev/null; then
    echo "Starting Ollama server (OLLAMA_NUM_PARALLEL=3)..."
    export OLLAMA_NUM_PARALLEL=3
    ollama serve &>/dev/null &
    for i in $(seq 1 15); do
      curl -s http://127.0.0.1:11434/api/tags &>/dev/null && break
      sleep 1
    done
  fi
fi

# ---- Start companion in background ----
echo "Starting companion on :9876..."
"$VENV/bin/python" "$COMPANION_DIR/companion.py" &
COMPANION_PID=$!

sleep 2
if ! kill -0 "$COMPANION_PID" 2>/dev/null; then
  echo "ERROR: Companion failed to start. Check output above."
  exit 1
fi

echo "Companion running (PID $COMPANION_PID)"
echo "Starting Next.js dev server..."
echo "---"

npx next dev
