# B-Roll Scout

**AI-powered B-roll discovery for documentary editors.** Paste a script in any language, and B-Roll Scout will translate it, break it into visual scenes, search YouTube for the best footage, read transcripts, pinpoint exact timestamps, and rank everything — so you can drop clips straight into your timeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              EDITOR'S MACHINE                                   │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │              Browser — Next.js UI (Vercel / your host, or localhost:3000)     │  │
│  │                                                                           │  │
│  │  ┌──────────────┐  ┌────────────────┐  ┌──────────────┐  ┌──────────┐   │  │
│  │  │ Script Input  │  │ Progress       │  │ Results      │  │ Settings │   │  │
│  │  │ + Gemini AI   │→ │ Tracker (live) │→ │ Display      │  │ Page     │   │  │
│  │  │   toggle      │  └────────────────┘  └──────────────┘  └──────────┘   │  │
│  │  └──────────────┘                                                         │  │
│  │         │                     ▲ Agent Loop                                │  │
│  │         │                     │ (polls EC2 for tasks,                     │  │
│  │         │                     │  relays to companion,                     │  │
│  │         │                     │  returns results)                         │  │
│  └─────────┼─────────────────────┼───────────────────────────────────────────┘  │
│            │                     │                                               │
│            │                     ▼                                               │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │              Companion App (localhost:9876) — Flask                        │  │
│  │                                                                           │  │
│  │  Runs locally to bypass YouTube's cloud-IP blocking:                      │  │
│  │                                                                           │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │  │
│  │  │ yt-dlp      │  │ youtube-     │  │ Whisper      │  │ yt-dlp       │  │  │
│  │  │ search      │  │ transcript-  │  │ base (77M)   │  │ video        │  │  │
│  │  │             │  │ api          │  │ local model  │  │ details      │  │  │
│  │  │ ytsearch,   │  │              │  │              │  │              │  │  │
│  │  │ channel     │  │ manual →     │  │ downloads    │  │ --dump-json  │  │  │
│  │  │ search      │  │ auto →       │  │ audio via    │  │ for single   │  │  │
│  │  │             │  │ any-lang     │  │ yt-dlp, then │  │ videos       │  │  │
│  │  │             │  │ fallback     │  │ transcribes  │  │              │  │  │
│  │  └─────────────┘  └──────────────┘  └──────────────┘  └──────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │
              HTTPS — API only: broll.jayasim.com → FastAPI (/api/v1/...)
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    EC2 (t3.small, Ubuntu) — broll.jayasim.com (API)              │
│                    Nginx → Let's Encrypt SSL → FastAPI (port 8000)               │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                     Pipeline (asyncio background task)                     │  │
│  │                                                                           │  │
│  │  ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌──────────────┐  │  │
│  │  │ 1. TRANSLATE│    │ 2. SEARCH  │    │ 3. MATCH   │    │ 4. RANK      │  │  │
│  │  │            │    │            │    │            │    │              │  │  │
│  │  │ GPT-4o     │ →  │ yt-dlp     │ →  │ Transcript │ →  │ 5-dimension  │  │  │
│  │  │            │    │ (via       │    │ + GPT-4o-  │    │ scoring      │  │  │
│  │  │ Tamil →    │    │ companion) │    │ mini       │    │              │  │  │
│  │  │ English    │    │            │    │            │    │ Keyword 30%  │  │  │
│  │  │ + segment  │    │ Optional:  │    │ Finds exact│    │ Viral   20%  │  │  │
│  │  │ into scenes│    │ Gemini AI  │    │ start/end  │    │ Channel 20%  │  │  │
│  │  │            │    │ expansion  │    │ timestamps │    │ Caption 10%  │  │  │
│  │  │ 1 API call │    │            │    │            │    │ Recency 20%  │  │  │
│  │  └────────────┘    └────────────┘    └────────────┘    └──────────────┘  │  │
│  │                                                               │           │  │
│  │                                                        5. STORE & DEDUP   │  │
│  │                                                        Save to DynamoDB   │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐          │
│  │ Agent Task Queue │    │ DynamoDB         │    │ OpenAI API       │          │
│  │ (in-memory)      │    │ (9 tables)       │    │ GPT-4o / mini    │          │
│  │                  │    │ jobs, segments,  │    │                  │          │
│  │ EC2 creates tasks│    │ results,         │    │ Gemini 1.5 Flash │          │
│  │ Browser polls &  │    │ transcripts,     │    │ (optional)       │          │
│  │ relays to        │    │ feedback         │    │                  │          │
│  │ companion        │    └──────────────────┘    └──────────────────┘          │
│  └──────────────────┘                                                          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**What runs where:** `broll.jayasim.com` on EC2 hosts the **FastAPI backend only** (pipeline, DynamoDB, agent queue). The **Next.js editor UI** is deployed separately — for example on Vercel — and must be configured with `BACKEND_URL=https://broll.jayasim.com` so its `/api/v1/*` routes proxy to EC2. Editors use the **UI URL** in the browser; opening the API host directly shows JSON/OpenAPI, not the scouting app.

---

## How Tasks Flow Between EC2, Browser, and Companion

The system uses a **browser-relayed agent pattern** because YouTube blocks requests from cloud IPs (like AWS EC2). The editor's local machine acts as the bridge:

```
EC2 Pipeline                    Browser Agent Loop              Companion (localhost:9876)
─────────────                   ──────────────────              ──────────────────────────
                                                                
Pipeline needs a yt-dlp         
search or transcript  ───────►  POST /api/v1/agent/poll
                                (claims pending tasks)
agent_queue.create_task()                    │
         │                                   │
         │ (awaits result)                   ▼
         │                       POST localhost:9876/execute
         │                       { task_type: "search",
         │                         payload: { query, max_results } }
         │                                   │
         │                                   ▼
         │                       Companion runs yt-dlp subprocess
         │                       Returns JSON results
         │                                   │
         │                                   ▼
         │                       POST /api/v1/agent/result
         │                       { task_id, status, result }
         │                                   │
         ◄───────────────────────────────────┘
agent_queue.wait_for_result()
returns data to pipeline
```

**Task types handled by the companion:**

| Task Type | What Runs Locally | Why Local |
|---|---|---|
| `search` | `yt-dlp ytsearch{N}:{query} --dump-json` | YouTube blocks yt-dlp from cloud IPs |
| `channel_search` | `yt-dlp https://youtube.com/channel/{id}/search?query=...` | Same reason |
| `video_details` | `yt-dlp https://youtube.com/watch?v={id} --dump-json` | Metadata fetch |
| `transcript` | `youtube-transcript-api` fetch | YouTube blocks transcript API from AWS |
| `whisper` | `yt-dlp -x --audio-format mp3` + Whisper `base` model | Audio download + local GPU/CPU transcription |
| `clip` | `yt-dlp --download-sections` + ffmpeg | Downloads a specific time range as MP4 for editors |

The companion app also supports **Chrome cookie extraction** (`--cookies-from-browser chrome`) for authenticated YouTube access, detected automatically at startup.

---

## Pipeline — Step by Step

When you click **Scout B-Roll**, the system runs a 5-stage pipeline:

### Stage 1: Translate & Segment — `GPT-4o` (EC2)

- **One API call** to GPT-4o translates the script from Tamil (or any language) to English.
- The same call breaks the translation into **visual scenes** — each scene has a title, summary, emotional tone, visual need, key search terms, and 3 YouTube search queries (broad, specific, creative).
- If the script is long (e.g., 30 minutes), GPT-4o is asked to produce at least 30 scenes — one per minute.

### Stage 2: Multi-Source Search — `yt-dlp` (Companion) + optional `Gemini 1.5 Flash` (EC2)

For each scene, searches run concurrently (3 scenes at a time via the companion app):

| Source | Where It Runs | What It Does |
|---|---|---|
| **Preferred Channels** | Companion (yt-dlp) | Searches your whitelisted channels first |
| **yt-dlp Search** | Companion (yt-dlp) | Runs the AI-generated search queries — no API quota |
| **Gemini AI Expansion** (optional, off by default) | EC2 → Companion | Gemini suggests 5 creative lateral queries, then searches them via yt-dlp |

**Long script retry logic:** For scripts >25 minutes, if fewer than 30 candidate videos are found, the pipeline automatically retries sparse scenes (up to 3 rounds) until it has enough candidates.

### Stage 3: Transcript + Timestamp Matching — Cascade (EC2 + Companion)

For every candidate video:

**3a. Get Transcript** — 4-level cascade:

```
1. DynamoDB Cache        → instant, free        (if previously fetched)
2. Direct YouTube API    → fast, free           (EC2, often blocked by YouTube)
3. Companion transcript  → fast, free           (local youtube-transcript-api)
4. Whisper transcription → slow, free           (local: yt-dlp audio + Whisper base model)
   Only for videos ≤60 min. Result cached in DynamoDB.
```

**3b. Find Timestamp** — `GPT-4o-mini` (EC2):

- Sends the transcript + scene context to GPT-4o-mini
- Returns the **exact start/end timestamps** of the peak visual moment
- Also returns a confidence score (0.0–1.0) and a one-line "hook" explaining why this clip is visually compelling
- Timestamps past video duration or in end-screen territory are penalized/invalidated

### Stage 4: Ranking & Filtering — Pure logic (EC2)

Each clip is scored on five weighted dimensions:

| Weight | Dimension | What It Measures |
|---|---|---|
| 30% | Keyword Density | How many of the scene's key terms appear in the transcript excerpt |
| 20% | Viral Score | View count tier (>1M = 1.0, >100K = 0.8, >10K = 0.5, else 0.2) |
| 20% | Channel Authority | Preferred Tier 1 = 1.0, Tier 2 = 0.9, >100K subs = 0.7, else 0.4 |
| 10% | Caption Quality | Manual = 1.0, Auto = 0.8, Whisper = 0.6, None = 0.3 |
| 20% | Recency | <2 years = 1.0, <4 years = 0.7, older = 0.4 |

**Hard filters applied:**
- Duration: videos must be 2–90 minutes (configurable)
- Blocked channels: substring match on channel name against blocked networks (CNN, BBC...), studios (Disney, Warner...), and sports leagues (FIFA, NFL...) — NOT matched against video title
- Timestamps in the first 15 seconds of long videos (likely intro) are confidence-penalized
- Timestamps landing in the last 30s of a video (end-screen territory) are penalized
- Clips shorter than 10 seconds receive a confidence penalty (not discarded)
- Cross-segment deduplication: same video allowed in multiple scenes if clip timestamps don't overlap (30s bucket)

### Stage 5: Store & Display

Results saved to DynamoDB and returned to the frontend. Each clip shows:
- Video title, channel, thumbnail
- Exact timestamp link (click to jump to the moment)
- Confidence score and relevance score
- Transcript excerpt around the matched moment
- "The hook" — why this clip works for this scene

---

## AI Models Used

| Model | Task | Where It Runs | When Called | Cost |
|---|---|---|---|---|
| **GPT-4o** | Translate Tamil → English, segment into scenes, generate search queries | EC2 → OpenAI API | Once per job | ~$0.01–0.05 |
| **GPT-4o-mini** | Read transcript, find peak visual moment, return exact start/end timestamps | EC2 → OpenAI API | Once per candidate video (30–80 per job) | ~$0.001 each |
| **Gemini 1.5 Flash** | Suggest 5 creative lateral search queries (optional, off by default) | EC2 → Google API | Once per scene (only if toggled on) | ~$0.0001 each |
| **Whisper `base`** (77M params) | Transcribe audio for videos without captions | Editor's machine (companion) | Only when no YouTube captions exist and video ≤60 min | Free (local) |

**Why this model allocation:**
- **GPT-4o** for translation: best multilingual quality, called only once — worth the cost
- **GPT-4o-mini** for timestamps: 17x cheaper than GPT-4o, handles structured JSON extraction from transcripts perfectly — this is the highest-volume call
- **Whisper `base`**: sweet spot between speed and accuracy for generating search-signal transcripts (not for subtitles) — runs on CPU in ~30s per video

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 15, React, TypeScript, Tailwind CSS, shadcn/ui |
| **Backend** | Python 3.12, FastAPI, Pydantic v2, asyncio |
| **AI Models** | OpenAI GPT-4o (translation), GPT-4o-mini (timestamps), Google Gemini 1.5 Flash (optional query expansion), OpenAI Whisper base (local transcription) |
| **Search** | yt-dlp (local companion) — no YouTube API quota needed |
| **Transcripts** | `youtube-transcript-api`, OpenAI Whisper (local fallback) |
| **Storage** | AWS DynamoDB (9 tables: jobs, segments, results, transcripts, feedback, settings, channel_cache, projects, usage) |
| **Hosting (API)** | AWS EC2 (t3.small, Ubuntu), Nginx, SSL — `https://broll.jayasim.com` serves **FastAPI only** |
| **Hosting (UI)** | Next.js on Vercel (or similar); set `BACKEND_URL=https://broll.jayasim.com` for API proxying |

---

## Project Structure

```
BRoll Scout/
├── app/
│   ├── main.py                  # FastAPI application, endpoints, agent queue API
│   ├── background.py            # Pipeline orchestration, progress tracking, retry logic
│   ├── config.py                # Settings model & pipeline defaults (models, weights, filters)
│   ├── models/
│   │   └── schemas.py           # Pydantic models (Job, Segment, Result, Transcript, etc.)
│   ├── services/
│   │   ├── translator.py        # GPT-4o script translation & segmentation
│   │   ├── searcher.py          # Multi-source video search (yt-dlp via companion, Gemini expansion)
│   │   ├── transcriber.py       # 4-level transcript cascade (cache → direct → agent → Whisper)
│   │   ├── matcher.py           # GPT-4o-mini timestamp matching & validation
│   │   ├── ranker.py            # 5-dimension relevance scoring, filtering, dedup
│   │   ├── storage.py           # DynamoDB CRUD operations
│   │   ├── settings_service.py  # User settings (DynamoDB-backed overrides)
│   │   └── usage_service.py     # API + AWS cost aggregation and reporting
│   ├── utils/
│   │   ├── agent_queue.py       # In-memory task queue for browser↔companion relay
│   │   └── cost_tracker.py      # Per-job API cost tracking (OpenAI, Gemini, AWS)
│   ├── api/v1/                  # Next.js API routes (proxy to FastAPI)
│   ├── page.tsx                 # Main page (script input → progress → results)
│   ├── settings/page.tsx        # Settings page (4-tab configuration UI)
│   ├── layout.tsx               # Root layout
│   └── globals.css              # Global styles
├── components/
│   ├── script-input.tsx         # Script paste/upload + Gemini AI toggle
│   ├── progress-tracker.tsx     # Live pipeline progress with timestamped activity log
│   ├── results-display.tsx      # Segment cards with ranked clips
│   ├── agent-status.tsx         # Companion health badge + browser agent relay loop
│   ├── job-history.tsx          # Sidebar job list
│   └── navbar.tsx               # Navigation bar
├── broll-companion/
│   ├── companion.py             # Flask app: yt-dlp search, transcript fetch, Whisper, clip download, Chrome cookies
│   ├── requirements.txt         # flask, flask-cors, yt-dlp, youtube-transcript-api, openai-whisper
│   ├── setup.bat                # Windows one-click setup (deps + companion + optional browser via app.url)
│   ├── load-app-url.bat         # Reads web UI URL from app.url (used by setup / start-companion)
│   ├── app.url.example          # Template: copy to app.url with your Next.js / Vercel URL
│   ├── launch-companion-server.bat  # Starts Flask only (used by setup.bat in a new window)
│   ├── install.bat              # Windows installer (advanced — assumes Python exists)
│   ├── start-companion.bat      # Daily launcher (kills previous, auto-setup, opens browser)
│   ├── stop.bat                 # Force-kills all B-Roll Scout processes (companion + web app)
│   └── update.bat               # Windows updater (keeps yt-dlp and packages current)
├── tests/
│   └── test_integration.py      # 70 integration tests
├── scripts/
│   ├── setup_ec2.sh             # EC2 provisioning script
│   ├── deploy.sh                # Code deployment script (rsync + restart)
│   ├── create_tables.py         # DynamoDB table creation
│   ├── cleanup_dynamo.sh        # Clean up stale DynamoDB data
│   ├── populate_channels_local.py  # One-time: populate channel_cache with avatars via yt-dlp
│   ├── test_e2e_flow.py         # Standalone E2E pipeline test
│   ├── package_companion.sh    # Package broll-companion into a zip (companion-only)
│   └── build_editor_package.sh # Build full editor zip (Next.js standalone + companion + node.exe)
├── requirements.txt             # Python backend dependencies
├── package.json                 # Node.js frontend dependencies
└── pyproject.toml               # Python project config + pytest settings
```

---

## Getting Started

There are two setup paths: one for **editors** (non-technical, Windows) and one for **developers** who want to run or modify the full stack.

---

### Editor Setup (Windows — One Click)

Editors get a **single zip** that contains everything -- the web app, the companion, and a portable Node.js runtime. No Node.js or npm install required. The web app runs locally at **http://localhost:3000** and talks to the backend API on EC2.

#### First-time setup

1. Download: [**broll-scout-editor.zip**](https://github.com/jayasim-labs/broll-scout/releases/latest/download/broll-scout-editor.zip) *(or ask your admin to share the zip)*
2. Unzip to any folder (e.g., Desktop or Documents)
3. Open the `broll-scout-editor` folder and **double-click `setup.bat`**

That's it. Setup automatically:
- Installs Python if missing (via `winget`)
- Installs `ffmpeg` (audio processing)
- Creates an isolated Python environment
- Installs `yt-dlp`, `youtube-transcript-api`, `openai-whisper`, `Flask`
- Downloads the Whisper AI model (77 MB, one-time)
- Creates a **"B-Roll Scout"** shortcut on your Desktop

Total time: ~3-5 minutes on first run. No terminal commands needed. After setup completes, it launches B-Roll Scout automatically.

#### Daily use

1. Double-click **"B-Roll Scout"** on your Desktop (or `start.bat` in the folder)
2. Keep the window open -- your browser opens to **http://localhost:3000** automatically
3. Paste your script and click **Scout B-Roll**
4. Close the window when you're done for the day

What happens behind the scenes when you click `start.bat`:
- **Kills any previous instances** first (no duplicates, ever)
- Starts the **web app** on `localhost:3000` (bundled Node.js + Next.js, no install needed)
- Starts the **companion** on `localhost:9876` (yt-dlp, Whisper, etc.)
- Opens your default browser to `http://localhost:3000`
- Auto-updates `yt-dlp` each launch (YouTube changes frequently)
- When you close the window or press Ctrl+C, both services stop cleanly

You can also double-click **`stop.bat`** at any time to force-kill all B-Roll Scout processes.

#### Updating

Double-click `update.bat` to update `yt-dlp` and Python packages. Do this if YouTube search/downloads stop working.

#### Troubleshooting (editors)

| Problem | Fix |
|---|---|
| "Python is not installed" | Setup tries to install it automatically. If it fails, download from [python.org](https://www.python.org/downloads/) -- check **"Add Python to PATH"** during install, then re-run `setup.bat` |
| "ffmpeg not found" warning | Whisper transcription won't work, but everything else will. Install later: `winget install Gyan.FFmpeg` |
| Browser says "Companion not connected" | Make sure the B-Roll Scout window is open and shows `Starting companion on http://127.0.0.1:9876` |
| YouTube search returns no results | Run `update.bat` to get the latest `yt-dlp` |
| Port 3000 already in use | Close any other app on port 3000, or edit `start.bat` to change `PORT=3000` to another port |

---

### Building the editor package (admin)

Run this on your dev machine (macOS/Linux) to produce the zip editors receive:

```bash
bash scripts/build_editor_package.sh
```

This:
1. Builds Next.js in standalone mode (self-contained server, no `node_modules` needed)
2. Downloads a portable `node.exe` for Windows (~40 MB, cached after first run)
3. Copies the companion files
4. Creates `setup.bat`, `start.bat`, `update.bat`
5. Zips everything to `dist/broll-scout-editor.zip`

The standalone Next.js server reads `BACKEND_URL=https://broll.jayasim.com` from its bundled `.env` so API calls proxy to EC2 automatically. To change the API endpoint, edit `webapp/.env` inside the zip before distributing.

---

### Developer Setup (Full Stack)

For running the entire stack locally (backend + frontend + companion) or contributing to the project.

#### Prerequisites

- **Node.js** 18+ and **npm**
- **Python** 3.12+
- API keys: OpenAI
- Optional: Google Gemini API key (only if you want Gemini expansion)
- AWS account with DynamoDB access

#### 1. Clone & Install

```bash
git clone https://github.com/jayasim-labs/BRoll-Scout.git
cd BRoll-Scout

# Frontend
npm install

# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2. Install Companion App

**macOS:**
```bash
cd broll-companion
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
```

**Windows:**
```
cd broll-companion
setup.bat
```

#### 3. Configure Environment

```bash
cp .env.example .env
```

Required variables in `.env`:

```
OPENAI_API_KEY=sk-proj-...
AWS_REGION=us-east-1
```

Optional (only needed if you enable Gemini expansion):
```
GEMINI_API_KEY=AIzaSy...
```

#### 4. Create DynamoDB Tables

```bash
python scripts/create_tables.py
```

#### 5. Run Locally

Start all three services:

```bash
# Terminal 1: Backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Frontend
npm run dev

# Terminal 3: Companion (required for yt-dlp and Whisper)
cd broll-companion && source .venv/bin/activate && python companion.py
```

Open [http://localhost:3000](http://localhost:3000).

---

## How to Use

### Scout B-Roll

1. **Paste your script** — any language works (Tamil, Hindi, Spanish, French, etc.)
2. **Toggle Gemini AI Expansion** (optional) — finds more creative B-roll but takes longer
3. Click **Scout B-Roll**
4. Watch the **live progress tracker** as the system:
   - Translates and segments your script (GPT-4o)
   - Searches preferred channels, then YouTube/yt-dlp for each scene
   - Reads video transcripts (YouTube captions → companion → Whisper fallback)
   - GPT-4o-mini finds exact timestamps in each video
   - Ranks and filters the best clips
   - For long scripts (>25 min): retries sparse scenes until 30+ candidates found
5. **Browse results** — each scene shows the top clip with:
   - Timestamp link to jump to the exact moment
   - Confidence and relevance scores
   - Transcript excerpt and "the hook"
6. **Preview & Clip** — for each result:
   - Click **Preview** to watch the clip inline with pre-filled start/end timestamps
   - Adjust the clip range with +/-10s controls
   - Click **Clip & Download** to download the exact segment as MP4 via the companion app
   - Click **Mark as Used** to save the clip to DynamoDB as part of your project

### Settings

Navigate to `/settings` to configure:

| Tab | What You Can Configure |
|---|---|
| **Source Management** | Preferred channels with avatars & subscriber counts (Tier 1 by ID, Tier 2 by name), public domain archives, stock footage platforms |
| **Blocked Sources** | News networks, movie studios, sports leagues (all use substring matching on channel name), custom keyword block rules |
| **Pipeline Parameters** | Search backend & depth, result limits, AI model selection (timestamp/translation), confidence threshold, Whisper settings, video duration filters, 5-dimension ranking weights with visual bar, performance tuning (concurrency, timeouts, recovery) — all with inline help text |
| **Special Instructions** | Custom instructions sent to the AI during translation/ranking, context-matching toggles (discard short clips, end-screen detection, timestamp capping) |

---

## API Endpoints

### Jobs

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/jobs` | Create a job (accepts `enable_gemini_expansion` flag) |
| `GET` | `/api/v1/jobs` | List all jobs |
| `GET` | `/api/v1/jobs/{id}` | Get full job results |
| `GET` | `/api/v1/jobs/{id}/status` | Poll progress (stage, percent, activity log) |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel a running job |

### Agent (Browser ↔ Companion Relay)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/agent/poll` | Browser claims pending tasks |
| `POST` | `/api/v1/agent/result` | Browser submits companion results |
| `GET` | `/api/v1/agent/status` | Queue status (pending, claimed, agents) |

### Settings & Channels

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/settings` | Get all settings (defaults merged with overrides) |
| `PUT` | `/api/v1/settings` | Update a single setting |
| `PUT` | `/api/v1/settings/bulk` | Update multiple settings at once |
| `POST` | `/api/v1/settings/reset` | Reset all settings to defaults |
| `POST` | `/api/v1/settings/channels/resolve` | Resolve a single channel ID to name/avatar/subs |
| `POST` | `/api/v1/settings/channels/resolve-bulk` | Resolve multiple channel IDs (for Tier 1 display) |
| `POST` | `/api/v1/settings/channels/resolve-names` | Resolve channel names to IDs/avatars (for Tier 2 display) |

### Feedback & Library

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/results/{id}/feedback` | Submit editor feedback (rating, clip_used, notes) |
| `GET` | `/api/v1/library/search` | Search past results |
| `GET` | `/api/v1/health` | Health check |

---

## DynamoDB Schema

| Table | Partition Key | Sort Key | Purpose |
|---|---|---|---|
| `broll_jobs` | `job_id` | — | Job metadata, status, costs |
| `broll_segments` | `job_id` | `segment_id` | Translated scene data |
| `broll_results` | `job_id` | `result_id` | Ranked clip results with timestamps, `clip_used` flag |
| `broll_transcripts` | `video_id` | — | Cached transcripts (YouTube, Whisper) — avoids re-fetching |
| `broll_feedback` | `result_id` | — | Editor ratings, clip-used tracking & notes |
| `broll_settings` | `setting_key` | — | User-configured pipeline settings (overrides defaults) |
| `broll_channel_cache` | `channel_id` | — | YouTube channel metadata cache (name, subscribers, avatar URL) |
| `broll_projects` | `project_id` | — | Project groupings for jobs |
| `broll_usage` | `period` | — | Aggregated API cost tracking per month/day |

---

## Pipeline Parameters Reference

All parameters are configurable via the Settings page (`/settings` → Pipeline Parameters tab). Defaults are in `app/config.py`.

### Search

| Parameter | Default | Description |
|---|---|---|
| `search_backend` | `ytdlp_only` | All searches via yt-dlp on the companion. No YouTube API quota consumed |
| `search_queries_per_segment` | 3 | YouTube search queries generated per scene |
| `youtube_results_per_query` | 5 | Results fetched per search query |
| `max_candidates_per_segment` | 12 | Max videos kept per scene for transcript analysis |
| `top_results_per_segment` | 3 | Clips shown per scene — more choices for the editor |
| `total_results_target` | 30 | Target total clips — triggers recovery search if below |
| `gemini_expanded_queries` | 5 | Creative lateral queries from Gemini (only when toggled on) |

### Timestamp Detection

| Parameter | Default | Description |
|---|---|---|
| `timestamp_model` | `gpt-4o-mini` | AI model for reading transcripts and finding timestamps |
| `translation_model` | `gpt-4o` | AI model for script translation and scene segmentation |
| `confidence_threshold` | 0.15 | Minimum AI confidence to include a clip (0.0–1.0) |
| `whisper_max_video_duration_min` | 60 | Max video length for Whisper fallback transcription |
| `whisper_audio_trim_min` | 20 | Only transcribe first N minutes of audio |

### Video Filtering

| Parameter | Default | Description |
|---|---|---|
| `min_video_duration_sec` | 120 | Exclude videos shorter than this |
| `max_video_duration_sec` | 5400 | Exclude videos longer than this |
| `prefer_min_subscribers` | 10000 | Channels below this get lower authority score (not excluded) |
| `recency_full_score_years` | 2 | Videos within this age get full recency score |
| `cap_end_timestamp` | true | Cap end timestamp at video duration - 5s |
| `verify_timestamp_not_end_screen` | true | Penalize timestamps in last 30s of video |

### Ranking Weights (auto-normalized to 1.0)

| Weight | Default | What It Measures |
|---|---|---|
| `weight_keyword_density` | 0.30 | Scene key terms found in transcript excerpt |
| `weight_viral_score` | 0.20 | View count tier (>1M=1.0, >100K=0.8, >10K=0.5, else 0.2) |
| `weight_channel_authority` | 0.20 | Channel tier and subscriber count |
| `weight_caption_quality` | 0.10 | Transcript source quality (manual > auto > Whisper > none) |
| `weight_recency` | 0.20 | Publish date relative to recency settings |

### Performance

| Parameter | Default | Description |
|---|---|---|
| `max_concurrent_segments` | 5 | Scenes searched in parallel |
| `segment_timeout_sec` | 60 | Max time per scene for transcript + matching |
| `low_result_threshold` | 20 | Triggers recovery search if total results below this |

---

## Cost Breakdown

| API | Cost per Call | Typical Usage per Job |
|---|---|---|
| GPT-4o (translation) | ~$0.01–0.05 | 1 call |
| GPT-4o-mini (timestamps) | ~$0.001 per video | 30–80 videos |
| Gemini 1.5 Flash (if enabled) | ~$0.0001/call | 5–15 calls |
| Whisper base (local) | Free | Only for videos without captions |
| yt-dlp (local) | Free | All YouTube searches and downloads |
| AWS EC2 (t3.small) | ~$16.56/month | Always running |
| AWS DynamoDB | ~$1.00/month | Storage + read/write |
| AWS Route 53 | ~$0.50/month | DNS hosted zone |

**Typical job cost: $0.03–0.10** (lower than before — Google CSE removed, Gemini off by default)

---

## Running Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/test_integration.py -v
```

**70 tests** covering:
- YouTube API quota detection and yt-dlp fallback
- Agent task queue (create → poll → submit → receive)
- Concurrent segment dispatching
- Transcript cascade (direct → agent → Whisper)
- Whisper fallback activation and caching
- Gemini expansion toggle (on/off)
- Matcher timestamp extraction and validation
- Ranker scoring, filtering, and deduplication
- Job cancellation flow
- Stale job cleanup on deploy
- FastAPI endpoints
- Progress tracking and activity log

---

## Deployment (EC2)

The backend runs on AWS EC2 with:

- **Nginx** reverse proxy (SSL termination)
- **Let's Encrypt** via Certbot for HTTPS
- **systemd** service (`broll-scout.service`) for the FastAPI backend
- **IAM Instance Profile** for DynamoDB access (no keys on disk)
- **Stale job cleanup** on every restart — processing jobs are marked as failed

### Deploy Code Updates

```bash
bash scripts/deploy.sh
```

Syncs code via rsync, installs dependencies, restarts the service, and cleans up stale jobs.

---

## License

Private project — not open source.
