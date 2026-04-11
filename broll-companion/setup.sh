#!/bin/bash
# B-Roll Scout — One-click setup for macOS editors
# Usage:  bash setup.sh   (or double-click setup.command)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Colors
R='\033[0;31m' G='\033[0;32m' Y='\033[0;33m' C='\033[0;36m' NC='\033[0m' B='\033[1m'

header() { echo -e "\n${C}${B}========================================${NC}"; echo -e "${C}${B}  B-Roll Scout — Editor Setup (macOS)${NC}"; echo -e "${C}${B}========================================${NC}\n"; }
step()   { echo -e "${Y}[$1] $2${NC}"; }
ok()     { echo -e "  ${G}✓ $1${NC}"; }
warn()   { echo -e "  ${Y}⚠ $1${NC}"; }
fail()   { echo -e "  ${R}✗ $1${NC}"; }

header

# ─── 1. Homebrew ──────────────────────────────────────────────────────
step "1/8" "Checking Homebrew..."

if command -v brew &>/dev/null; then
    ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
else
    warn "Homebrew not found. Installing (requires password)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
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
step "2/8" "Checking Node.js..."

if command -v node &>/dev/null; then
    NODE_VER=$(node -v | sed 's/v//')
    MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
    if [[ "$MAJOR" -lt 18 ]]; then
        warn "Node.js $NODE_VER found (18+ recommended). Upgrading..."
        brew install node
    else
        ok "Node.js $NODE_VER"
    fi
else
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
step "3/8" "Checking Python 3..."

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
    fi
else
    echo "  Installing Python 3.12 via Homebrew..."
    brew install python@3.12
    PYTHON_CMD="python3"
fi

if [[ -z "$PYTHON_CMD" ]] || ! command -v "$PYTHON_CMD" &>/dev/null; then
    fail "Python 3 not available. Install from https://python.org"
    exit 1
fi

# ─── 4. yt-dlp & ffmpeg ──────────────────────────────────────────────
step "4/8" "Checking yt-dlp and ffmpeg..."

NEED_INSTALL=()
if ! command -v yt-dlp &>/dev/null; then NEED_INSTALL+=("yt-dlp"); fi
if ! command -v ffmpeg &>/dev/null; then NEED_INSTALL+=("ffmpeg"); fi

if [[ ${#NEED_INSTALL[@]} -gt 0 ]]; then
    echo "  Installing ${NEED_INSTALL[*]} via Homebrew..."
    brew install "${NEED_INSTALL[@]}"
fi

if command -v yt-dlp &>/dev/null && command -v ffmpeg &>/dev/null; then
    ok "yt-dlp $(yt-dlp --version) and ffmpeg installed"
else
    warn "Some tools could not be installed. Check brew output above."
fi

# ─── 5. npm install ──────────────────────────────────────────────────
step "5/8" "Installing npm dependencies..."

cd "$PROJECT_ROOT"
npm install --legacy-peer-deps 2>&1 | tail -3
ok "npm dependencies installed"

# ─── 6. Environment file ─────────────────────────────────────────────
step "6/8" "Setting up environment (.env.local)..."

ENV_LOCAL="$PROJECT_ROOT/.env.local"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"

if [[ -f "$ENV_LOCAL" ]]; then
    ok ".env.local already exists (keeping your keys)"
elif [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_LOCAL"
    SECRET=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32 || true)
    sed -i '' "s/SESSION_SECRET=change-me-to-random-32-char-string/SESSION_SECRET=$SECRET/" "$ENV_LOCAL" 2>/dev/null || true
    ok "Created .env.local from template"
else
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
ok "Backend: broll.jayasim.com"

# ─── 7. Python companion venv + packages ─────────────────────────────
step "7/8" "Setting up Python companion..."

ACTIVATE="$VENV_DIR/bin/activate"
if [[ -f "$ACTIVATE" ]]; then
    ok "Virtual environment already exists"
else
    echo "  Creating virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    ok "Virtual environment created"
fi

# shellcheck source=/dev/null
source "$ACTIVATE"

echo "  Upgrading pip..."
python -m pip install --upgrade pip --quiet 2>/dev/null

echo "  Installing companion packages..."
python -m pip install flask flask-cors yt-dlp youtube-transcript-api ollama --quiet
ok "Companion packages installed"

echo "  Installing Whisper AI (speech-to-text, may take a minute)..."
if python -m pip install openai-whisper --quiet 2>/dev/null; then
    ok "Whisper installed"
    WHISPER_MODEL="$HOME/.cache/whisper/base.pt"
    if [[ -f "$WHISPER_MODEL" ]] && [[ $(stat -f%z "$WHISPER_MODEL" 2>/dev/null || stat -c%s "$WHISPER_MODEL" 2>/dev/null || echo 0) -gt 70000000 ]]; then
        ok "Whisper base model already downloaded"
    else
        echo "  Whisper model will download on first use (~150MB)"
    fi
else
    warn "Whisper install failed (optional). Transcription will use YouTube captions only."
fi

# ─── 8. Ollama (local LLM) ───────────────────────────────────────────
step "8/8" "Setting up Ollama (local LLM for free timestamp matching)..."

if command -v ollama &>/dev/null; then
    ok "Ollama already installed"
else
    echo "  Installing Ollama via Homebrew..."
    if brew install ollama 2>/dev/null; then
        ok "Ollama installed"
    else
        warn "Could not install Ollama via brew."
        echo "  Download from: https://ollama.com/download/mac"
        echo "  Timestamp matching will use GPT-4o-mini (API) as fallback."
    fi
fi

if command -v ollama &>/dev/null; then
    # Start Ollama if not running
    if ! curl -s http://127.0.0.1:11434/api/tags &>/dev/null; then
        echo "  Starting Ollama server..."
        ollama serve &>/dev/null &
        sleep 3
    fi

    echo "  Pulling Qwen3 8B model (~5GB, one-time download)..."
    echo "  This may take several minutes on first run."
    if ollama pull qwen3:8b; then
        ok "Qwen3 8B model ready"
    else
        warn "Model pull failed. Run 'ollama pull qwen3:8b' manually later."
    fi
fi

# ─── Done ─────────────────────────────────────────────────────────────
echo ""
echo -e "${G}${B}========================================${NC}"
echo -e "${G}${B}  Setup complete!${NC}"
echo -e "${G}${B}========================================${NC}"
echo ""
echo "  To start B-Roll Scout:"
echo "    cd $SCRIPT_DIR && bash start-companion.sh"
echo ""
echo "  Or double-click 'B-Roll Scout.command' on your Desktop."
echo ""

# Create Desktop shortcut (.command file)
DESKTOP="$HOME/Desktop"
SHORTCUT="$DESKTOP/B-Roll Scout.command"
cat > "$SHORTCUT" << 'SHORTCUTEOF'
#!/bin/bash
# B-Roll Scout — double-click to launch
cd "$(dirname "$0")"
SHORTCUTEOF
echo "exec bash \"$SCRIPT_DIR/start-companion.sh\"" >> "$SHORTCUT"
chmod +x "$SHORTCUT"
ok "'B-Roll Scout.command' shortcut created on Desktop"

echo ""
echo "  Starting B-Roll Scout now..."
echo ""

bash "$SCRIPT_DIR/start-companion.sh"
