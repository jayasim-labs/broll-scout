# B-Roll Scout

**AI-powered B-roll discovery for documentary editors.** Paste a script in any language, and B-Roll Scout will translate it, break it into visual scenes, search YouTube for the best footage, read transcripts, pinpoint exact timestamps, and rank everything — so you can drop clips straight into your timeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Editor's Browser                               │
│                                                                         │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │ Script Input  │  │ Progress       │  │ Results      │  │ Settings │ │
│  │ (paste/type)  │→ │ Tracker (live) │→ │ Display      │  │ Page     │ │
│  └──────────────┘  └────────────────┘  └──────────────┘  └──────────┘ │
│         │                  ▲                  ▲                  │      │
└─────────┼──────────────────┼──────────────────┼──────────────────┼──────┘
          │ POST /api/v1/jobs│ GET .../status    │ GET /api/v1/jobs/│
          ▼                  │ (polling)         │ {id}             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Next.js Frontend (localhost:3000)                   │
│                  API routes proxy to FastAPI backend                     │
└─────────┬──────────────────┬──────────────────┬──────────────────┬──────┘
          │                  │                  │                  │
          ▼                  ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                FastAPI Backend (EC2 — broll.jayasim.com)                 │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │                   Pipeline (background task)                 │        │
│  │                                                              │        │
│  │  1. Translate ──→ 2. Segment ──→ 3. Search ──→ 4. Match     │        │
│  │   (GPT-4o)       (GPT-4o)     (YouTube API   (read video    │        │
│  │                                + Google CSE   transcripts,   │        │
│  │                                + Gemini AI)   GPT-4o-mini    │        │
│  │                                               finds exact    │        │
│  │                                               timestamps)    │        │
│  │                                                     │        │        │
│  │                                              5. Rank & Store │        │
│  │                                               (score, filter │        │
│  │                                                dedup, save)  │        │
│  └──────────────────────────────────────────────────────────────┘        │
│         │                                                               │
│         ▼                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │  DynamoDB     │  │  YouTube     │  │  OpenAI      │                  │
│  │  (5 tables)   │  │  Data API   │  │  GPT-4o/mini │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
│                     ┌──────────────┐  ┌──────────────┐                  │
│                     │  Google CSE  │  │  Gemini 1.5  │                  │
│                     │  (fallback)  │  │  Flash       │                  │
│                     └──────────────┘  └──────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Pipeline — Step by Step

When you click **Scout B-Roll**, the system runs a 5-stage pipeline:

### Stage 1: Translate & Segment

- **GPT-4o** translates the script to English (supports Tamil, Hindi, Spanish, or any language).
- The same call breaks the translation into **visual scenes** — each scene has a title, summary, emotional tone, visual need, key search terms, and optimized YouTube search queries.

### Stage 2: Multi-Source Search

For each scene, three search strategies run in parallel:

| Source | What it does |
|---|---|
| **Preferred Channels** | Searches your whitelisted channels first (Tier 1 = channel IDs, Tier 2 = channel names) |
| **YouTube Data API** | Runs the AI-generated search queries against all of YouTube |
| **Google Custom Search** | Finds documentary/explainer videos that YouTube search might miss |
| **Gemini 1.5 Flash** | Reads the initial results, then suggests creative lateral search queries and runs them |

### Stage 3: Transcript Matching

For every candidate video found:

1. Fetch the transcript (YouTube captions preferred, auto-captions as fallback)
2. Send the transcript + scene context to **GPT-4o-mini**
3. GPT-4o-mini returns the **exact start/end timestamps** of the most relevant clip, a confidence score, and a one-line "hook" explaining why this moment is perfect

### Stage 4: Ranking & Filtering

Each clip is scored on five weighted dimensions:

| Weight | Dimension | What it measures |
|---|---|---|
| 30% | Keyword Density | How many of the scene's key terms appear in the transcript excerpt |
| 20% | Viral Score | View count tier (>1M = perfect, >100K = strong) |
| 20% | Channel Authority | Preferred channels score highest, then subscriber count |
| 10% | Caption Quality | Manual captions > auto > whisper > none |
| 20% | Recency | Videos <2 years old score highest |

Blocked channels (news networks, movie studios, sports leagues) are filtered out. Duplicate videos across scenes are deduplicated, keeping the clip in the scene where it scored highest.

### Stage 5: Store & Display

Results are saved to DynamoDB and returned to the frontend. Each clip shows:
- Video title, channel, thumbnail
- Exact timestamp link (click to jump to the moment)
- Confidence score and relevance score
- Transcript excerpt around the matched moment
- "The hook" — why this clip works for this scene

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 15, React, TypeScript, Tailwind CSS, shadcn/ui |
| **Backend** | Python 3.12, FastAPI, Pydantic v2, asyncio |
| **AI Models** | OpenAI GPT-4o (translation), GPT-4o-mini (timestamp matching), Google Gemini 1.5 Flash (query expansion) |
| **Search APIs** | YouTube Data API v3, Google Custom Search API |
| **Transcripts** | `youtube-transcript-api` (no API quota cost) |
| **Storage** | AWS DynamoDB (5 tables: jobs, segments, results, transcripts, feedback) |
| **Hosting** | AWS EC2 (t3.small, Ubuntu), Nginx reverse proxy, Let's Encrypt SSL |
| **Domain** | `broll.jayasim.com` |

---

## Project Structure

```
BRoll Scout/
├── app/
│   ├── main.py                  # FastAPI application & endpoints
│   ├── background.py            # Pipeline orchestration & progress tracking
│   ├── config.py                # Settings model & pipeline defaults
│   ├── models/
│   │   └── schemas.py           # Pydantic models (Job, Segment, Result, etc.)
│   ├── services/
│   │   ├── translator.py        # GPT-4o script translation & segmentation
│   │   ├── searcher.py          # Multi-source video search (YouTube, CSE, Gemini)
│   │   ├── transcriber.py       # Video transcript fetching
│   │   ├── matcher.py           # GPT-4o-mini timestamp matching
│   │   ├── ranker.py            # Relevance scoring & filtering
│   │   ├── storage.py           # DynamoDB CRUD operations
│   │   └── settings_service.py  # User settings (DynamoDB-backed overrides)
│   ├── utils/
│   │   ├── youtube.py           # YouTube Data API wrapper
│   │   └── cost_tracker.py      # Per-job API cost tracking
│   ├── api/v1/                  # Next.js API routes (proxy to FastAPI)
│   ├── page.tsx                 # Main page (script input → progress → results)
│   ├── settings/page.tsx        # Settings page (4-tab configuration UI)
│   ├── layout.tsx               # Root layout
│   └── globals.css              # Global styles
├── components/
│   ├── script-input.tsx         # Script paste/type form
│   ├── progress-tracker.tsx     # Live pipeline progress with activity log
│   ├── results-display.tsx      # Segment cards with ranked clips
│   ├── job-history.tsx          # Sidebar job list
│   └── navbar.tsx               # Navigation bar
├── tests/
│   └── test_integration.py      # 26 integration tests
├── scripts/
│   ├── setup_ec2.sh             # EC2 provisioning script
│   ├── deploy.sh                # Code deployment script
│   └── create_tables.py         # DynamoDB table creation
├── requirements.txt             # Python dependencies
├── package.json                 # Node.js dependencies
└── pyproject.toml               # Python project config
```

---

## Getting Started

### Prerequisites

- **Node.js** 18+ and **npm**
- **Python** 3.12+
- API keys for: OpenAI, Google (YouTube Data API v3), Google Gemini
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

### 2. Configure Environment

Copy the example and fill in your keys:

```bash
cp .env.example .env
```

Required variables in `.env`:

```
OPENAI_API_KEY=sk-proj-...
GEMINI_API_KEY=AIzaSy...
YOUTUBE_API_KEY=AIzaSy...
GOOGLE_SEARCH_API_KEY=AIzaSy...
GOOGLE_SEARCH_CX=05102786c56f64aed
AWS_REGION=us-east-1
```

For the frontend, update `.env.local`:

```
BACKEND_URL=http://localhost:8000
```

### 3. Create DynamoDB Tables

```bash
python scripts/create_tables.py
```

This creates 5 tables with the `broll_` prefix in your configured AWS region.

### 4. Run Locally

Start both servers:

```bash
# Terminal 1: Backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## How to Use

### Scout B-Roll

1. **Paste your script** — any language works (Tamil, Hindi, Spanish, French, etc.)
2. Click **Scout B-Roll**
3. Watch the **live progress tracker** as the system:
   - Translates and segments your script
   - Searches multiple sources for each scene
   - Reads video transcripts and pinpoints exact timestamps
   - Ranks and filters the best clips
4. **Browse results** — each scene shows the top clips with:
   - Click the timestamp link to jump to the exact moment in the video
   - Confidence score (how well the timestamp matches your scene)
   - Relevance score (overall quality ranking)
   - Transcript excerpt showing what's said at that moment
5. **Export JSON** — download all results for use in your editing workflow

### Settings

Navigate to `/settings` to configure:

| Tab | What you can configure |
|---|---|
| **Source Management** | Preferred channels (Tier 1/Tier 2), public domain archives, stock platforms |
| **Blocked Sources** | News networks, movie studios, sports leagues, custom block rules |
| **Pipeline Parameters** | Search depth, result limits, video duration filters, ranking weights, AI models |
| **Special Instructions** | Custom instructions for the AI (e.g., "prefer aerial footage", "avoid news clips") |

---

## API Endpoints

### Jobs

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/jobs` | Create a new B-roll scouting job |
| `GET` | `/api/v1/jobs` | List all jobs |
| `GET` | `/api/v1/jobs/{id}` | Get full job results (segments + clips) |
| `GET` | `/api/v1/jobs/{id}/status` | Poll job progress (stage, percent, activity log) |

### Settings

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/settings` | Get all settings |
| `PUT` | `/api/v1/settings` | Update a single setting |
| `PUT` | `/api/v1/settings/bulk` | Update multiple settings at once |
| `POST` | `/api/v1/settings/reset` | Reset all settings to defaults |
| `POST` | `/api/v1/settings/channels/resolve` | Resolve a YouTube channel URL to ID + metadata |

### Other

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Health check (status, DB connection, version) |
| `POST` | `/api/v1/results/{id}/feedback` | Submit editor feedback on a clip |
| `GET` | `/api/v1/library/search` | Search past results by topic/date/rating |

---

## DynamoDB Schema

| Table | Partition Key | Sort Key | Purpose |
|---|---|---|---|
| `broll_jobs` | `job_id` | — | Job metadata, status, costs |
| `broll_segments` | `job_id` | `segment_id` | Translated scene data |
| `broll_results` | `job_id` | `result_id` | Ranked clip results with timestamps |
| `broll_transcripts` | `video_id` | — | Cached video transcripts |
| `broll_feedback` | `result_id` | — | Editor ratings & notes |

---

## Deployment (EC2)

The application runs on an AWS EC2 instance with:

- **Nginx** as a reverse proxy (handles SSL termination)
- **Let's Encrypt** via Certbot for automatic HTTPS
- **systemd** service for the FastAPI backend
- **IAM Instance Profile** for DynamoDB access (no keys on disk)

### Deploy Code Updates

```bash
bash scripts/deploy.sh
```

This syncs code via rsync, installs dependencies, and restarts the service.

### Manual Setup

For a fresh EC2 instance:

```bash
scp scripts/setup_ec2.sh ubuntu@<EC2_IP>:/tmp/
ssh ubuntu@<EC2_IP> "sudo bash /tmp/setup_ec2.sh"
bash scripts/deploy.sh
```

---

## Running Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/test_integration.py -v
```

The test suite covers:
- YouTube API quota detection and short-circuiting
- ISO 8601 duration parsing
- Blocked channel filtering
- Settings propagation (DynamoDB overrides > defaults)
- Searcher with mocked YouTube APIs
- FastAPI endpoints (health, job creation, 404 handling)
- In-memory progress tracking and activity log

---

## Cost Breakdown

Each job tracks API costs in real-time:

| API | Cost per call | Typical usage per job |
|---|---|---|
| GPT-4o (translation) | ~$0.01–0.05 | 1 call |
| GPT-4o-mini (timestamps) | ~$0.001 per video | 30–80 videos |
| YouTube Data API | Free (10,000 units/day quota) | ~1,500–2,500 units |
| Google Custom Search | $5/1,000 queries | 20–60 queries |
| Gemini 1.5 Flash | ~$0.0001/call | 5–15 calls |

**Typical job cost: $0.05–0.15**

---

## License

Private project — not open source.
