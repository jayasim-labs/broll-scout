#!/bin/bash
# B-Roll Scout — Smart setup + launcher for macOS editors
# First run:  installs prerequisites (~20 min), then launches
# Every run:  skips what's already installed (~5 sec), then launches
# Usage:  bash setup.sh   (or double-click setup.command / Desktop shortcut)

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
ACTIVATE="$VENV_DIR/bin/activate"
COMPANION_PY="$SCRIPT_DIR/companion.py"

# Colors
R='\033[0;31m' G='\033[0;32m' Y='\033[0;33m' C='\033[0;36m' NC='\033[0m' B='\033[1m' DIM='\033[2m'

header() { echo -e "\n${C}${B}========================================${NC}"; echo -e "${C}${B}  B-Roll Scout${NC}"; echo -e "${C}${B}========================================${NC}\n"; }
step()   { echo -e "${Y}[$1] $2${NC}"; }
ok()     { echo -e "  ${G}✓ $1${NC}"; }
warn()   { echo -e "  ${Y}⚠ $1${NC}"; }
fail()   { echo -e "  ${R}✗ $1${NC}"; }

header

# Track whether anything was installed (for summary)
FIRST_RUN=false

# ─── Cleanup old instances ────────────────────────────────────────────
echo -e "${DIM}  Cleaning up old instances...${NC}"
lsof -ti:3000 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti:9876 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.5

NEXT_PID=""
cleanup() {
    echo ""
    echo -e "${Y}  Shutting down...${NC}"
    if [[ -n "$NEXT_PID" ]] && kill -0 "$NEXT_PID" 2>/dev/null; then
        kill "$NEXT_PID" 2>/dev/null
        wait "$NEXT_PID" 2>/dev/null
    fi
    lsof -ti:3000 2>/dev/null | xargs kill 2>/dev/null || true
    echo -e "${G}  Done.${NC}"
}
trap cleanup EXIT INT TERM

# ═══════════════════════════════════════════════════════════════════════
# PREREQUISITES — each section skips if already satisfied
# ═══════════════════════════════════════════════════════════════════════

# ─── 1. Homebrew ──────────────────────────────────────────────────────
step "1/8" "Homebrew..."

if command -v brew &>/dev/null; then
    ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
else
    FIRST_RUN=true
    warn "Homebrew not found. Installing (requires password)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    if command -v brew &>/dev/null; then
        ok "Homebrew installed"
    else
        fail "Could not install Homebrew. Install manually: https://brew.sh"
        exit 1
    fi
fi

# ─── 2. Node.js ──────────────────────────────────────────────────────
step "2/8" "Node.js..."

if command -v node &>/dev/null; then
    NODE_VER=$(node -v | sed 's/v//')
    MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
    if [[ "$MAJOR" -lt 18 ]]; then
        warn "Node.js $NODE_VER found (18+ recommended). Upgrading..."
        brew install node
        FIRST_RUN=true
    else
        ok "Node.js $NODE_VER"
    fi
else
    FIRST_RUN=true
    echo "  Installing Node.js via Homebrew..."
    brew install node
    if command -v node &>/dev/null; then
        ok "Node.js $(node -v)"
    else
        fail "Could not install Node.js. Install from https://nodejs.org"
        exit 1
    fi
fi

# ─── 3. Python 3 ─────────────────────────────────────────────────────
step "3/8" "Python 3..."

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 10 ]]; then
        PYTHON_CMD="python3"
        ok "Python $PY_VER"
    else
        warn "Python $PY_VER found (3.10+ required). Installing newer version..."
        brew install python@3.12
        PYTHON_CMD="python3"
        FIRST_RUN=true
    fi
else
    FIRST_RUN=true
    echo "  Installing Python 3.12 via Homebrew..."
    brew install python@3.12
    PYTHON_CMD="python3"
fi

if [[ -z "$PYTHON_CMD" ]] || ! command -v "$PYTHON_CMD" &>/dev/null; then
    fail "Python 3 not available. Install from https://python.org"
    exit 1
fi

# ─── 4. yt-dlp & ffmpeg ──────────────────────────────────────────────
step "4/8" "yt-dlp & ffmpeg..."

NEED_INSTALL=()
if ! command -v yt-dlp &>/dev/null; then NEED_INSTALL+=("yt-dlp"); fi
if ! command -v ffmpeg &>/dev/null; then NEED_INSTALL+=("ffmpeg"); fi

if [[ ${#NEED_INSTALL[@]} -gt 0 ]]; then
    FIRST_RUN=true
    echo "  Installing ${NEED_INSTALL[*]} via Homebrew..."
    brew install "${NEED_INSTALL[@]}"
fi

if command -v yt-dlp &>/dev/null && command -v ffmpeg &>/dev/null; then
    ok "yt-dlp $(yt-dlp --version) + ffmpeg"
else
    warn "Some tools could not be installed. Check brew output above."
fi

# ─── 5. npm packages ─────────────────────────────────────────────────
step "5/8" "npm packages..."

cd "$PROJECT_ROOT"
if [[ -d "node_modules" && -f "node_modules/.package-lock.json" ]]; then
    ok "node_modules present"
else
    FIRST_RUN=true
    echo "  Running npm install (first time)..."
    npm install --legacy-peer-deps 2>&1 | tail -3
    ok "npm dependencies installed"
fi

# ─── 6. Environment file ─────────────────────────────────────────────
step "6/8" "Environment (.env.local)..."

ENV_LOCAL="$PROJECT_ROOT/.env.local"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"

if [[ -f "$ENV_LOCAL" ]]; then
    ok ".env.local exists"
elif [[ -f "$ENV_EXAMPLE" ]]; then
    FIRST_RUN=true
    cp "$ENV_EXAMPLE" "$ENV_LOCAL"
    SECRET=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32 || true)
    sed -i '' "s/SESSION_SECRET=change-me-to-random-32-char-string/SESSION_SECRET=$SECRET/" "$ENV_LOCAL" 2>/dev/null || true
    ok "Created .env.local from template"
else
    FIRST_RUN=true
    echo -e "BACKEND_URL=https://broll.jayasim.com\nBACKEND_API_KEY=zQCtPzOz1LU2rDK-vtzzcWey18yO1ZgqyU4cCloWwZE" > "$ENV_LOCAL"
    ok "Created minimal .env.local"
fi

# Ensure BACKEND_URL points to production
if grep -q 'BACKEND_URL=http://localhost' "$ENV_LOCAL" 2>/dev/null; then
    sed -i '' 's|BACKEND_URL=http://localhost:[0-9]*|BACKEND_URL=https://broll.jayasim.com|' "$ENV_LOCAL"
    echo -e "  ${C}Fixed BACKEND_URL → https://broll.jayasim.com${NC}"
elif ! grep -q 'BACKEND_URL=' "$ENV_LOCAL" 2>/dev/null; then
    echo "BACKEND_URL=https://broll.jayasim.com" >> "$ENV_LOCAL"
fi
if ! grep -q 'BACKEND_API_KEY=' "$ENV_LOCAL" 2>/dev/null; then
    echo "BACKEND_API_KEY=zQCtPzOz1LU2rDK-vtzzcWey18yO1ZgqyU4cCloWwZE" >> "$ENV_LOCAL"
fi

# ─── 7. Python companion venv + packages ─────────────────────────────
step "7/8" "Python companion..."

if [[ -f "$ACTIVATE" ]]; then
    # shellcheck source=/dev/null
    source "$ACTIVATE"
    if python -c "import flask" 2>/dev/null; then
        ok "Virtual environment ready"
    else
        echo "  Dependencies missing. Reinstalling..."
        python -m pip install flask flask-cors yt-dlp youtube-transcript-api ollama --quiet
        ok "Companion packages reinstalled"
    fi
else
    FIRST_RUN=true
    echo "  Creating virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    # shellcheck source=/dev/null
    source "$ACTIVATE"
    echo "  Upgrading pip..."
    python -m pip install --upgrade pip --quiet 2>/dev/null
    echo "  Installing companion packages..."
    python -m pip install flask flask-cors yt-dlp youtube-transcript-api ollama --quiet
    ok "Companion packages installed"

    echo "  Installing Whisper AI (speech-to-text)..."
    if python -m pip install openai-whisper --quiet 2>/dev/null; then
        ok "Whisper installed"
    else
        warn "Whisper install failed (optional). Transcription will use YouTube captions only."
    fi
fi

# Update yt-dlp (quick, always do this)
echo -e "${DIM}  Updating yt-dlp...${NC}"
python -m pip install --upgrade yt-dlp --quiet 2>/dev/null
echo -e "  ${G}✓ yt-dlp up to date${NC}"

# ─── 8. Ollama (local LLM) ───────────────────────────────────────────
step "8/8" "Ollama..."

if command -v ollama &>/dev/null; then
    ok "Ollama installed"
else
    FIRST_RUN=true
    echo "  Installing Ollama via Homebrew..."
    if brew install ollama 2>/dev/null; then
        ok "Ollama installed"
    else
        warn "Could not install Ollama via brew."
        echo "  Download from: https://ollama.com/download/mac"
        echo "  Timestamp matching will use GPT-4o-mini (API) as fallback."
    fi
fi

# Ensure Ollama >= 0.20.0 (required for Gemma 4)
MIN_OLLAMA="0.20.0"
if command -v ollama &>/dev/null; then
    CURRENT_VER=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ -n "$CURRENT_VER" ]; then
        if ! printf '%s\n%s\n' "$MIN_OLLAMA" "$CURRENT_VER" | sort -V | head -1 | grep -qx "$MIN_OLLAMA"; then
            echo -e "  ${Y}Ollama $CURRENT_VER is too old for Gemma 4 (needs $MIN_OLLAMA+). Upgrading...${NC}"
            if brew upgrade ollama 2>/dev/null; then
                ok "Ollama upgraded to $(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
            else
                warn "Auto-upgrade failed. Run: brew upgrade ollama"
            fi
        else
            ok "Ollama $CURRENT_VER (Gemma 4 compatible)"
        fi
    fi
fi

# Start Ollama with parallel=3
if command -v ollama &>/dev/null; then
    echo -e "${DIM}  Starting Ollama (OLLAMA_NUM_PARALLEL=3)...${NC}"
    pkill -f "ollama serve" 2>/dev/null || true
    sleep 1
    export OLLAMA_NUM_PARALLEL=3
    ollama serve &>/dev/null &

    OLLAMA_UP=false
    for i in $(seq 1 15); do
        if curl -s http://127.0.0.1:11434/api/tags &>/dev/null; then
            echo -e "  ${G}✓ Ollama running (parallel=3)${NC}"
            OLLAMA_UP=true
            break
        fi
        sleep 1
    done

    # Pull models if missing (skips if already pulled)
    if $OLLAMA_UP; then
        INSTALLED=$(ollama list 2>/dev/null)
        if echo "$INSTALLED" | grep -q "qwen3:8b"; then
            ok "Qwen3 8B ready"
        else
            FIRST_RUN=true
            echo "  Pulling Qwen3 8B model (~5GB, one-time download)..."
            ollama pull qwen3:8b && ok "Qwen3 8B model ready" || warn "Qwen3 pull failed. Run: ollama pull qwen3:8b"
        fi

        if echo "$INSTALLED" | grep -q "gemma4:26b"; then
            ok "Gemma 4 26B ready"
        else
            FIRST_RUN=true
            echo "  Pulling Gemma 4 26B MoE model (~18GB, one-time download)..."
            echo "  This may take 10-20 minutes on first run."
            ollama pull gemma4:26b && ok "Gemma 4 26B MoE model ready" || warn "Gemma 4 pull failed. Pull from Settings or run: ollama pull gemma4:26b"
        fi

        echo -e "  ${C}Other models (Gemma 4 E4B, Llama 3.3 8B) can be pulled from the Settings page.${NC}"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════
# CREATE DESKTOP SHORTCUT (only on first run)
# ═══════════════════════════════════════════════════════════════════════

DESKTOP="$HOME/Desktop"
SHORTCUT="$DESKTOP/B-Roll Scout.command"
if [[ ! -f "$SHORTCUT" ]] || $FIRST_RUN; then
    cat > "$SHORTCUT" << SHORTCUTEOF
#!/bin/bash
# B-Roll Scout — double-click to launch
cd "$(dirname "\$0")"
exec bash "$SCRIPT_DIR/setup.sh"
SHORTCUTEOF
    chmod +x "$SHORTCUT"
    ok "'B-Roll Scout.command' shortcut on Desktop"
fi

# ─── Cookie extraction ───────────────────────────────────────────────
if [[ -z "${BROLL_COOKIE_BROWSER:-}" ]]; then
    export BROLL_COOKIE_BROWSER="chrome"
fi

# ═══════════════════════════════════════════════════════════════════════
# LAUNCH
# ═══════════════════════════════════════════════════════════════════════

echo ""
if $FIRST_RUN; then
    echo -e "${G}${B}========================================${NC}"
    echo -e "${G}${B}  Setup complete! Launching...${NC}"
    echo -e "${G}${B}========================================${NC}"
else
    echo -e "${G}  All checks passed.${NC}"
fi
echo ""

# ─── Start Next.js dev server in background ──────────────────────────
echo -e "  Starting web app on ${B}http://localhost:3000${NC} ..."
cd "$PROJECT_ROOT"
npx next dev &>/dev/null &
NEXT_PID=$!

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
    open "http://localhost:3000" 2>/dev/null || true
else
    echo -e "  ${Y}⚠ Web app may still be starting. Try http://localhost:3000 in a moment.${NC}"
fi

# ─── Info ─────────────────────────────────────────────────────────────
echo ""
echo -e "  Companion:  ${B}http://127.0.0.1:9876${NC}"
echo -e "  Web app:    ${B}http://localhost:3000${NC}"
echo -e "${DIM}  ────────────────────────────────────────${NC}"
echo -e "  Keep this terminal open. Press ${B}Ctrl+C${NC} to stop."
echo ""
echo -e "  ${G}Starting companion server...${NC}"
echo ""

# ─── Run companion in foreground ──────────────────────────────────────
if [[ ! -f "$COMPANION_PY" ]]; then
    fail "companion.py not found at $COMPANION_PY"
    exit 1
fi
python "$COMPANION_PY"
