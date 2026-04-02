# B-Roll Scout

**AI-powered B-roll discovery for documentary editors.** Paste a script in any language, and B-Roll Scout will translate it, break it into visual scenes, search YouTube for the best footage, read transcripts, pinpoint exact timestamps, and rank everything — so you can drop clips straight into your timeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              EDITOR'S MACHINE                                   │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                     Browser (localhost:3000)                               │  │
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
                     HTTPS (broll.jayasim.com)
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    EC2 (t3.small, Ubuntu) — broll.jayasim.com                    │
│                    Nginx → Let's Encrypt SSL → FastAPI (port 8000)               │
│                                                                                 │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                     Pipeline (asyncio background task)                     │  │
│  │                                                                           │  │
│  │  ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌──────────────┐  │  │
│  │  │ 1. TRANSLATE│    │ 2. SEARCH  │    │ 3. MATCH   │    │ 4. RANK      │  │  │
│  │  │            │    │            │    │            │    │              │  │  │
│  │  │ GPT-4o     │ →  │ YouTube API│ →  │ Transcript │ →  │ 5-dimension  │  │  │
│  │  │            │    │ yt-dlp     │    │ + GPT-4o-  │    │ scoring      │  │  │
│  │  │ Tamil →    │    │ (via agent)│    │ mini       │    │              │  │  │
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
│  │ (in-memory)      │    │ (5 tables)       │    │ GPT-4o / mini    │          │
│  │                  │    │ jobs, segments,  │    │                  │          │
│  │ EC2 creates tasks│    │ results,         │    │ Gemini 1.5 Flash │          │
│  │ Browser polls &  │    │ transcripts,     │    │ (optional)       │          │
│  │ relays to        │    │ feedback         │    │                  │          │
│  │ companion        │    └──────────────────┘    └──────────────────┘          │
│  └──────────────────┘                                                          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

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

---

## Pipeline — Step by Step

When you click **Scout B-Roll**, the system runs a 5-stage pipeline:

### Stage 1: Translate & Segment — `GPT-4o` (EC2)

- **One API call** to GPT-4o translates the script from Tamil (or any language) to English.
- The same call breaks the translation into **visual scenes** — each scene has a title, summary, emotional tone, visual need, key search terms, and 3 YouTube search queries (broad, specific, creative).
- If the script is long (e.g., 30 minutes), GPT-4o is asked to produce at least 30 scenes — one per minute.

### Stage 2: Multi-Source Search — `yt-dlp` (Companion) + optional `Gemini 1.5 Flash` (EC2)

For each scene, searches run concurrently (2 scenes at a time via companion, 5 via YouTube API):

| Source | Where It Runs | What It Does |
|---|---|---|
| **Preferred Channels** | Companion (yt-dlp) or EC2 (YouTube API) | Searches your whitelisted channels first |
| **YouTube/yt-dlp** | Companion (yt-dlp) or EC2 (YouTube API) | Runs the AI-generated search queries |
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
- Duration: videos must be 2–90 minutes
- Blocked channels: news networks (CNN, BBC, Fox...), movie studios (Disney, Warner...), sports leagues (FIFA, NFL...)
- Cross-segment deduplication: same video kept only in the scene where it scored highest

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
| **Search** | YouTube Data API v3, yt-dlp (local companion) |
| **Transcripts** | `youtube-transcript-api`, OpenAI Whisper (local fallback) |
| **Storage** | AWS DynamoDB (5 tables: jobs, segments, results, transcripts, feedback) |
| **Hosting** | AWS EC2 (t3.small, Ubuntu), Nginx reverse proxy, Let's Encrypt SSL |
| **Domain** | `broll.jayasim.com` |

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
│   │   ├── searcher.py          # Multi-source video search (YouTube API, yt-dlp, Gemini)
│   │   ├── transcriber.py       # 4-level transcript cascade (cache → direct → agent → Whisper)
│   │   ├── matcher.py           # GPT-4o-mini timestamp matching & validation
│   │   ├── ranker.py            # 5-dimension relevance scoring, filtering, dedup
│   │   ├── storage.py           # DynamoDB CRUD operations
│   │   └── settings_service.py  # User settings (DynamoDB-backed overrides)
│   ├── utils/
│   │   ├── youtube.py           # YouTube Data API wrapper
│   │   ├── agent_queue.py       # In-memory task queue for browser↔companion relay
│   │   ├── quota_tracker.py     # YouTube API daily quota tracking
│   │   └── cost_tracker.py      # Per-job API cost tracking
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
│   ├── companion.py             # Flask app: yt-dlp search, transcript fetch, Whisper transcription
│   ├── requirements.txt         # flask, flask-cors, yt-dlp, youtube-transcript-api, openai-whisper
│   ├── install.bat              # Windows one-click installer (Python venv, ffmpeg, yt-dlp, Whisper)
│   ├── start-companion.bat      # Windows launcher (double-click to start)
│   └── update.bat               # Windows updater (keeps yt-dlp and packages current)
├── tests/
│   └── test_integration.py      # 70 integration tests
├── scripts/
│   ├── setup_ec2.sh             # EC2 provisioning script
│   ├── deploy.sh                # Code deployment script (rsync + restart)
│   ├── create_tables.py         # DynamoDB table creation
│   ├── cleanup_dynamo.sh        # Clean up stale DynamoDB data
│   └── test_e2e_flow.py         # Standalone E2E pipeline test
├── requirements.txt             # Python backend dependencies
├── package.json                 # Node.js frontend dependencies
└── pyproject.toml               # Python project config + pytest settings
```

---

## Getting Started

### Prerequisites

- **Node.js** 18+ and **npm**
- **Python** 3.12+
- API keys: OpenAI, YouTube Data API v3
- Optional: Google Gemini API key (only if you want Gemini expansion)
- AWS account with DynamoDB access

### 1. Clone & Install

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

### 2. Install Companion App (Editor's Machine)

**Windows (editor's machine):**
```
cd broll-companion
install.bat
```

The installer automatically sets up: Python venv, yt-dlp, ffmpeg, youtube-transcript-api, openai-whisper, Flask. Then use `start-companion.bat` to launch, `update.bat` to keep yt-dlp current.

**macOS:**
```bash
cd broll-companion
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Required variables in `.env`:

```
OPENAI_API_KEY=sk-proj-...
YOUTUBE_API_KEY=AIzaSy...
AWS_REGION=us-east-1
```

Optional (only needed if you enable Gemini expansion):
```
GEMINI_API_KEY=AIzaSy...
```

### 4. Create DynamoDB Tables

```bash
python scripts/create_tables.py
```

### 5. Run Locally

Start all three services:

```bash
# Terminal 1: Backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Frontend
npm run dev

# Terminal 3: Companion (required for yt-dlp and Whisper)
cd broll-companion && python companion.py
```

**Windows editors** — just double-click `broll-companion\start-companion.bat` instead of Terminal 3.

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
6. **Export JSON** — download for use in your editing workflow

### Settings

Navigate to `/settings` to configure:

| Tab | What You Can Configure |
|---|---|
| **Source Management** | Preferred channels (Tier 1/Tier 2), public domain archives, stock platforms |
| **Blocked Sources** | News networks, movie studios, sports leagues, custom block rules |
| **Pipeline Parameters** | Search depth, result limits, video duration filters, ranking weights, AI models |
| **Special Instructions** | Custom instructions for the AI (e.g., "prefer aerial footage") |

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

### Settings & Library

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/settings` | Get all settings |
| `PUT` | `/api/v1/settings` | Update a single setting |
| `PUT` | `/api/v1/settings/bulk` | Update multiple settings |
| `POST` | `/api/v1/settings/reset` | Reset to defaults |
| `POST` | `/api/v1/results/{id}/feedback` | Submit editor feedback |
| `GET` | `/api/v1/library/search` | Search past results |
| `GET` | `/api/v1/health` | Health check |

---

## DynamoDB Schema

| Table | Partition Key | Sort Key | Purpose |
|---|---|---|---|
| `broll_jobs` | `job_id` | — | Job metadata, status, costs |
| `broll_segments` | `job_id` | `segment_id` | Translated scene data |
| `broll_results` | `job_id` | `result_id` | Ranked clip results with timestamps |
| `broll_transcripts` | `video_id` | — | Cached transcripts (YouTube, Whisper) — avoids re-fetching |
| `broll_feedback` | `result_id` | — | Editor ratings & notes |

---

## Cost Breakdown

| API | Cost per Call | Typical Usage per Job |
|---|---|---|
| GPT-4o (translation) | ~$0.01–0.05 | 1 call |
| GPT-4o-mini (timestamps) | ~$0.001 per video | 30–80 videos |
| YouTube Data API | Free (10,000 units/day quota) | ~1,500 units (auto-falls back to yt-dlp) |
| Gemini 1.5 Flash (if enabled) | ~$0.0001/call | 5–15 calls |
| Whisper (local) | Free | Only for videos without captions |
| yt-dlp (local) | Free | All searches when YouTube API quota exhausted |

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
