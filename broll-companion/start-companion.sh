#!/bin/bash
# B-Roll Scout — Daily launcher for macOS editors
# Starts the Next.js dev server (port 3000) AND the Python companion (port 9876).
# Usage:  bash start-companion.sh   (or double-click "B-Roll Scout.command")

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ACTIVATE="$VENV_DIR/bin/activate"
COMPANION_PY="$SCRIPT_DIR/companion.py"

R='\033[0;31m' G='\033[0;32m' Y='\033[0;33m' C='\033[0;36m' NC='\033[0m' B='\033[1m' DIM='\033[2m'

echo ""
echo -e "${C}${B}========================================${NC}"
echo -e "${C}${B}  B-Roll Scout${NC}"
echo -e "${C}${B}========================================${NC}"
echo ""
echo -e "  Keep this terminal open while using B-Roll Scout."
echo -e "  To stop: close this window or press ${B}Ctrl+C${NC}."
echo ""

NEXT_PID=""
cleanup() {
    echo ""
    echo -e "${Y}  Shutting down...${NC}"
    # Kill Next.js dev server
    if [[ -n "$NEXT_PID" ]] && kill -0 "$NEXT_PID" 2>/dev/null; then
        kill "$NEXT_PID" 2>/dev/null
        wait "$NEXT_PID" 2>/dev/null
    fi
    # Kill anything on port 3000 we may have spawned
    lsof -ti:3000 2>/dev/null | xargs kill 2>/dev/null || true
    echo -e "${G}  Done.${NC}"
}
trap cleanup EXIT INT TERM

# ─── Kill old instances ───────────────────────────────────────────────
echo -e "${DIM}  Cleaning up old instances...${NC}"
lsof -ti:3000 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti:9876 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.5

# ─── Check if setup has been run ─────────────────────────────────────
if [[ ! -f "$ACTIVATE" ]]; then
    echo -e "${Y}  First launch detected. Running setup...${NC}"
    echo ""
    bash "$SCRIPT_DIR/setup.sh"
    exit 0
fi

# ─── Activate venv ───────────────────────────────────────────────────
echo -e "${DIM}  Activating Python environment...${NC}"
# shellcheck source=/dev/null
source "$ACTIVATE"

# Quick health check
if ! python -c "import flask" 2>/dev/null; then
    echo -e "${Y}  Dependencies missing. Running setup...${NC}"
    bash "$SCRIPT_DIR/setup.sh"
    exit 0
fi

# ─── Update yt-dlp ───────────────────────────────────────────────────
echo -e "${DIM}  Updating yt-dlp...${NC}"
python -m pip install --upgrade yt-dlp --quiet 2>/dev/null
echo -e "  ${G}✓ yt-dlp up to date${NC}"

# ─── Cookie extraction ───────────────────────────────────────────────
if [[ -z "${BROLL_COOKIE_BROWSER:-}" ]]; then
    export BROLL_COOKIE_BROWSER="chrome"
fi

# ─── Start Ollama with parallel=3 ────────────────────────────────────
if command -v ollama &>/dev/null; then
    echo -e "${DIM}  Restarting Ollama (OLLAMA_NUM_PARALLEL=3)...${NC}"
    pkill -f "ollama serve" 2>/dev/null || true
    sleep 1
    export OLLAMA_NUM_PARALLEL=3
    ollama serve &>/dev/null &
    # Wait for Ollama to respond
    OLLAMA_UP=false
    for i in $(seq 1 15); do
        if curl -s http://127.0.0.1:11434/api/tags &>/dev/null; then
            echo -e "  ${G}✓ Ollama running (parallel=3)${NC}"
            OLLAMA_UP=true
            break
        fi
        sleep 1
    done

    # Auto-pull required models if missing
    if $OLLAMA_UP; then
        INSTALLED=$(ollama list 2>/dev/null)
        if ! echo "$INSTALLED" | grep -q "qwen3:8b"; then
            echo -e "${DIM}  Pulling Qwen3 8B (~5GB)...${NC}"
            ollama pull qwen3:8b && echo -e "  ${G}✓ Qwen3 8B ready${NC}" || echo -e "  ${Y}⚠ Qwen3 pull failed${NC}"
        fi
        if ! echo "$INSTALLED" | grep -q "gemma4:26b"; then
            echo -e "${DIM}  Pulling Gemma 4 26B MoE (~18GB, first time only)...${NC}"
            ollama pull gemma4:26b && echo -e "  ${G}✓ Gemma 4 26B ready${NC}" || echo -e "  ${Y}⚠ Gemma 4 pull failed — pull from Settings later${NC}"
        fi
    fi
else
    echo -e "  ${Y}⚠ Ollama not found — install from https://ollama.com${NC}"
fi

# ─── Check companion.py ──────────────────────────────────────────────
if [[ ! -f "$COMPANION_PY" ]]; then
    echo -e "  ${R}✗ companion.py not found at $COMPANION_PY${NC}"
    exit 1
fi

# ─── Check node_modules ──────────────────────────────────────────────
if [[ ! -d "$PROJECT_ROOT/node_modules" ]]; then
    echo -e "${Y}  node_modules not found. Running npm install...${NC}"
    cd "$PROJECT_ROOT"
    npm install --legacy-peer-deps
fi

# ─── Start Next.js dev server in background ──────────────────────────
echo ""
echo -e "  Starting web app on ${B}http://localhost:3000${NC} ..."

cd "$PROJECT_ROOT"
npx next dev &>/dev/null &
NEXT_PID=$!

# Wait for port 3000
echo -e "${DIM}  Waiting for web app to start...${NC}"
READY=false
for i in $(seq 1 30); do
    if lsof -ti:3000 &>/dev/null; then
        READY=true
        break
    fi
    sleep 1
done

if $READY; then
    echo -e "  ${G}✓ Web app running on http://localhost:3000${NC}"
    # Open browser
    open "http://localhost:3000" 2>/dev/null || true
else
    echo -e "  ${Y}⚠ Web app may still be starting. Try http://localhost:3000 in a moment.${NC}"
fi

# ─── Info ─────────────────────────────────────────────────────────────
echo ""
echo -e "  Companion:  ${B}http://127.0.0.1:9876${NC}"
echo -e "  Web app:    ${B}http://localhost:3000${NC}"
echo -e "${DIM}  ────────────────────────────────────────${NC}"
echo ""
echo -e "  ${G}Starting companion server...${NC}"
echo ""

# ─── Run companion in foreground ──────────────────────────────────────
python "$COMPANION_PY"
