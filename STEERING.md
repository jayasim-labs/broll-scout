# B-Roll Scout — Steering Document

> **Purpose**: Give any AI agent (or returning human) full project context in one file.
> Point agents here first: "Read STEERING.md before starting."

---

## 1. What Is B-Roll Scout?

A pipeline for video editors: paste a script → get timestamped YouTube B-roll clips.

**Users**: Video editors on Mac/Windows with a local companion app running.
**Infra**: Next.js frontend + FastAPI backend on EC2 + local Flask companion.

---

## 2. Architecture

```
┌─────────────────────┐     HTTPS      ┌──────────────────────┐
│  Next.js Frontend   │ ◄────────────► │  FastAPI on EC2      │
│  (localhost:3000)   │   API proxy     │  (broll.jayasim.com) │
└────────┬────────────┘                 └──────────┬───────────┘
         │ browser relay                           │ agent_queue
         │ (poll tasks, post results)              │ (create_task → wait_for_result)
         ▼                                         │
┌─────────────────────┐                            │
│  Companion Flask    │ ◄──────────────────────────┘
│  (localhost:9876)   │   tasks: search, transcript,
│  yt-dlp, Whisper,   │   whisper, match_timestamp,
│  Ollama, clips      │   clip, video_details
└─────────────────────┘
```

### Why this design?
- YouTube blocks AWS IPs → searches/transcripts must run locally via companion
- Ollama/Whisper need GPU → companion runs on editor's machine
- EC2 orchestrates the pipeline, stores results in DynamoDB

---

## 3. Tech Stack

| Layer | Stack |
|-------|-------|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind, shadcn/ui |
| Backend | Python 3.10+, FastAPI, Pydantic v2, boto3 |
| Database | AWS DynamoDB (10 tables, prefix `broll_`) |
| Local ML | Ollama (Qwen3 8B, Gemma4 26B), Whisper (large-v3-turbo) |
| Search/Media | yt-dlp, ffmpeg, youtube-transcript-api |
| AI APIs | OpenAI GPT-4o/4o-mini, Gemini 1.5 Flash (optional) |

---

## 4. Pipeline Stages (per job)

| Stage | Service | Runs on | Model |
|-------|---------|---------|-------|
| 1. Translate & Segment | `translator.py` | EC2 | GPT-4o |
| 2. Search YouTube | `searcher.py` → companion | Companion | yt-dlp |
| 3. Fetch Transcripts | `transcriber.py` → companion | Companion | youtube-transcript-api / Whisper |
| 4. Timestamp Match | `matcher.py` → companion | Companion | Ollama (or GPT-4o-mini fallback) |
| 5. Rank & Score | `ranker.py` | EC2 | Algorithmic |
| 6. Re-search gaps | `searcher.py` | Both | GPT-4o-mini |

---

## 5. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Browser relays tasks (not companion→EC2 directly) | Companion is behind NAT; browser can reach both |
| DynamoDB not SQL | Serverless, pay-per-use, good for key-value job data |
| Ollama for matching | $0 cost, runs on editor's GPU, fast enough |
| Whisper semaphore (1 at a time) | GPU-heavy, concurrent runs thrash memory |
| Agent-gone detection (45s threshold) | Prevents jobs hanging when companion dies |
| Search cache in DynamoDB (7-day TTL) | Shared across editors, survives restarts |
| yt-dlp rate limiter (2.5 req/s) | Avoids YouTube IP bans |

---

## 6. Settings Flow (how to add a new setting)

1. **Backend default**: Add to `DEFAULTS` dict in `app/config.py`
2. **TypeScript type**: Add to `PipelineSettings` in `lib/types.ts`
3. **Validation**: Add case in `settings_service.py` `_validate_value()`
4. **UI**: Add control in `app/settings/page.tsx`
5. **Usage**: Read via `self._get("key", default)` in any service

---

## 7. Companion Task Flow (how to add a new task type)

1. **Companion handler**: Add `elif task_type == "my_task":` in `/execute` in `companion.py`
2. **Service call**: In the relevant service, `agent_queue.create_task("my_task", payload)` then `agent_queue.wait_for_result(task_id)`
3. **No changes needed** in agent-status.tsx or main.py — the relay is generic

---

## 8. Deployment

| Target | Command | What it does |
|--------|---------|-------------|
| EC2 backend | `bash scripts/deploy.sh` | rsync app/ + pip install + systemctl restart |
| Frontend (local) | `npm run dev` | Next.js dev server on :3000 |
| Companion (local) | `./broll-companion/setup.command` (Mac) | Installs deps + launches companion + Next.js |
| DynamoDB tables | `python scripts/create_tables.py` | Creates all 10 tables (run once on EC2) |

---

## 9. Current State & Recent Work

### Completed
- Multi-model support (Qwen3, Gemma4, Llama) with Settings UI selector
- Whisper GPU acceleration (MPS on Mac, CUDA on Windows) with model selector (large-v3-turbo default)
- Whisper concurrency control (semaphore, 1 at a time, queue position in UI)
- Browser cookie support for yt-dlp (anti-bot detection)
- yt-dlp rate limiter + burst staggering
- DynamoDB persistent search cache (7-day TTL)
- Transcript source routing (prefer companion over EC2)
- Batched yt-dlp video detail fetches
- Agent-gone detection (45s threshold, fail-all-pending)
- Unified setup scripts (Mac + Windows)
- Ollama auto-upgrade + graceful GPU unload on exit

### Known Issues
- Jobs list endpoint defaults to 30 items — sidebar uses `?limit=100` now but could need pagination for heavy users
- yt-dlp `"Requested format is not available"` — mitigated with `bestaudio` fallback but not 100%
- YouTube IP blocking on EC2 — mitigated by routing through companion, but pure-server fallback is degraded

---

## 10. Optimization Backlog (from `optimisation` file)

| # | Optimization | Status | Files |
|---|-------------|--------|-------|
| 1 | Global video dedup across shots | Not started | `background.py` |
| 2 | Two-stage streaming (overlap search + transcript) | Not started | `background.py` |
| 3 | Parallel local compute (separate thread pools) | Not started | `companion.py`, `main.py` |

---

## 11. Token-Efficient Agent Usage Tips

### Starting a new conversation
```
Read STEERING.md, then .cursor/rules/project-context.mdc.
```
This gives agents ~2K tokens of context instead of exploring for 10K+.

### Scoping requests
- **Be specific**: "Fix the yt-dlp format error in companion.py whisper_transcribe" not "fix yt-dlp"
- **Name the file**: "In app/services/matcher.py, change X" saves the agent from searching
- **State the constraint**: "Don't change the API contract" or "Backend only, no frontend changes"

### Avoiding redundant exploration
- For architecture questions → point to this file
- For "where is X?" → use the Key Files table in section above
- For settings changes → follow the 5-step flow in section 6
- For new companion tasks → follow the 3-step flow in section 7

### What NOT to ask agents to explore
- Don't ask "explore the codebase" — give the specific area
- Don't ask "what does this project do?" — point to STEERING.md
- Don't re-explain past decisions — they're in section 5

### Useful starter prompts
- "Read STEERING.md. Then [your actual task]."
- "The pipeline is in app/background.py. I need to change how [X] works in stage [N]."
- "The companion is in broll-companion/companion.py. Add a new task type for [X]."
- "Deploy to EC2 after changes: bash scripts/deploy.sh"

---

## 12. File Index (quick reference)

### Backend (Python — EC2)
```
app/
  main.py              # FastAPI routes, job CRUD, agent endpoints
  background.py        # Pipeline orchestration (THE core file)
  config.py            # DEFAULTS + env settings
  models/schemas.py    # Pydantic models
  services/
    translator.py      # GPT-4o script→segments
    searcher.py        # YouTube search + Gemini expansion
    transcriber.py     # Transcript cascade + Whisper gate
    matcher.py         # Ollama/API timestamp matching
    ranker.py          # Scoring + dedup
    storage.py         # DynamoDB CRUD
    settings_service.py # Pipeline settings
    usage_service.py   # Cost tracking
    library.py         # Clip library search
    expand_shots.py    # "Add another shot" feature
  utils/
    agent_queue.py     # Task queue (EC2↔companion bridge)
    cost_tracker.py    # Per-job API cost tracking
```

### Frontend (TypeScript — Next.js)
```
app/
  page.tsx             # Main page (script input + sidebar)
  layout.tsx           # Root layout
  jobs/[id]/page.tsx   # Job results view
  projects/page.tsx    # Projects list
  settings/page.tsx    # Pipeline settings UI
  library/page.tsx     # Clip library
  usage/page.tsx       # Usage dashboard
  api/v1/**/route.ts   # API proxy routes to EC2
components/
  script-input.tsx     # Script textarea + options
  progress-tracker.tsx # Live job progress
  results-display.tsx  # Segments + clips view
  job-history.tsx      # Sidebar project/job tree
  agent-status.tsx     # Companion status badge + relay loop
  navbar.tsx           # Top navigation
lib/
  types.ts             # TypeScript interfaces
  backend.ts           # API URL + headers helper
  pipeline-defaults.ts # Frontend-side defaults
```

### Companion (Python — Local machine)
```
broll-companion/
  companion.py         # Flask server — yt-dlp, Whisper, Ollama, clips
  setup.sh             # Mac installer + launcher (unified)
  setup.ps1            # Windows installer + launcher (unified)
  setup.command        # Mac double-click wrapper
  setup.bat            # Windows double-click wrapper
  requirements.txt     # Companion Python deps
```

### Scripts & Config
```
scripts/
  deploy.sh            # EC2 deployment (rsync + restart)
  create_tables.py     # DynamoDB table creation
  dev.sh               # Local dev launcher
  setup_ec2.sh         # EC2 initial setup
requirements.txt       # Backend Python deps
package.json           # Frontend Node deps
.env.local             # BACKEND_URL + BACKEND_API_KEY
optimisation           # Design doc for future optimizations
```
