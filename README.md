# B-Roll Scout

**AI-powered B-roll discovery for documentary editors.** Paste a script in any language, and B-Roll Scout will translate it, identify visual moments, search YouTube for the best footage, match exact timestamps using local AI, and rank everything — so every clip is topically accurate and ready to drop into your timeline.

---

## Pipeline Flow

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                      │
│   SCRIPT IN (Tamil, Hindi, any language)                                             │
│       │                                                                              │
│       ▼                                                                              │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  STAGE 1 — TRANSLATE & SEGMENT             GPT-4o   │                           │
│   │                                                      │                           │
│   │  • Translate script to English                       │                           │
│   │  • Extract script_context (topic, geography, era,    │                           │
│   │    domain, exclusion context, 15 ranked keywords)    │                           │
│   │  • Break into granular segments (12–25 for 30 min)   │                           │
│   │    Enforced: min 1 segment per 3 minutes of script   │                           │
│   │    Auto-retry if GPT-4o under-segments               │                           │
│   │  • Per segment: identify 2–4 B-roll SHOTS            │                           │
│   │    Each shot gets:                                   │                           │
│   │    - visual_need + visual_description                │                           │
│   │    - shot_intent (literal / illustrative /           │                           │
│   │      atmospheric) — affects scoring weights          │                           │
│   │    - 5 YouTube-optimized search queries (specific,   │                           │
│   │      descriptive, documentary, broad, creative       │                           │
│   │      synonym)                                        │                           │
│   │    - key_terms, scarcity estimate,                   │                           │
│   │      preferred_source_type                           │                           │
│   │                                                      │                           │
│   │  Cost: ~$0.03–0.10 (one API call, retry if needed)   │                           │
│   └──────────────────────┬───────────────────────────────┘                           │
│                          │                                                           │
│                          ▼                                                           │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  STAGE 2 — SEARCH                          yt-dlp   │                           │
│   │                                             local    │                           │
│   │                                                      │                           │
│   │  For each shot's 5 queries:                          │                           │
│   │  • Preferred channels searched first (Tier 1 + 2)    │                           │
│   │  • yt-dlp searches YouTube (up to 12 results/query)  │                           │
│   │  • Hard filters: duration (120s–5400s), aspect       │                           │
│   │    ratio (no 9:16 Shorts), blocked channels          │                           │
│   │  • Title/description relevance pre-filter:           │                           │
│   │    lightweight keyword check discards irrelevant     │                           │
│   │    videos BEFORE transcript fetch (saves time)       │                           │
│   │  • Context-aware query enrichment: broad vs narrow   │                           │
│   │    topic detection, Indian language variants          │                           │
│   │  • Up to 20 candidates per shot kept                 │                           │
│   │                                                      │                           │
│   │  Cost: $0 (all local, no YouTube API quota)          │                           │
│   └──────────────────────┬───────────────────────────────┘                           │
│                          │                                                           │
│                          ▼                                                           │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  STAGE 3 — TRANSCRIPT FETCH             Streaming    │                           │
│   │                                                      │                           │
│   │  Priority queue (shorter videos first), 5 parallel   │                           │
│   │  workers, up to 15 min timeout:                      │                           │
│   │                                                      │                           │
│   │  Transcript cascade (first success wins):            │                           │
│   │    1. DynamoDB cache — instant, previously fetched    │                           │
│   │    2. YouTube manual captions — best quality          │                           │
│   │    3. YouTube auto captions — decent quality          │                           │
│   │    4. Whisper base (local) — companion downloads      │                           │
│   │       audio + transcribes (~45s per video)            │                           │
│   │                                                      │                           │
│   │  Streaming: fetch starts as soon as search finds     │                           │
│   │  a video — no waiting for all searches to finish     │                           │
│   │                                                      │                           │
│   │  Cost: $0 (all local)                                │                           │
│   └──────────────────────┬───────────────────────────────┘                           │
│                          │                                                           │
│                          ▼                                                           │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  STAGE 4 — TIMESTAMP MATCHING       Qwen3 8B        │                           │
│   │                                     via Ollama       │                           │
│   │                                     (local, $0)      │                           │
│   │                                                      │                           │
│   │  For each (video, shot) pair:                        │                           │
│   │  • Context gate: does this video actually match      │                           │
│   │    the documentary topic, geography, time period?    │                           │
│   │    If not → confidence: 0, context_match: false      │                           │
│   │  • Two-axis scoring:                                 │                           │
│   │    - visual_fit (how it LOOKS vs what shot needs)    │                           │
│   │    - topical_fit (how well content matches topic)    │                           │
│   │    Combined using shot_intent weights                │                           │
│   │  • Find exact start/end timestamps of peak visual   │                           │
│   │    moment — avoids intros, outros, end screens       │                           │
│   │  • Returns: confidence, excerpt, the_hook,           │                           │
│   │    match_reasoning, relevance_note                   │                           │
│   │                                                      │                           │
│   │  Fallback (optional, off by default):                │                           │
│   │  • api_fallback_enabled → GPT-4o-mini retries ALL    │                           │
│   │    failures (~$0.001/clip)                            │                           │
│   │  • confident_fallback_enabled → GPT-4o-mini retries  │                           │
│   │    ONLY zero-confidence results (context mismatches  │                           │
│   │    from Ollama are trusted)                           │                           │
│   │                                                      │                           │
│   │  Cost: $0 local / ~$0.001 per API fallback           │                           │
│   └──────────────────────┬───────────────────────────────┘                           │
│                          │                                                           │
│                          ▼                                                           │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  STAGE 5 — RANK & SELECT              Algorithmic    │                           │
│   │                                                      │                           │
│   │  7-dimension weighted scoring per clip:              │                           │
│   │   35% AI confidence (from matcher)                   │                           │
│   │   20% Visual/topical fit (intent-weighted)           │                           │
│   │   10% View count (viral tier)                        │                           │
│   │   10% Channel authority (tier + subscribers)         │                           │
│   │    5% Caption quality (manual > auto > whisper)      │                           │
│   │   10% Recency (publish date)                         │                           │
│   │   10% Script context relevance (title vs topic,      │                           │
│   │       keywords, domain, geography, temporal)         │                           │
│   │                                                      │                           │
│   │  • Scarcity-based thresholds (COMMON/MEDIUM/RARE)    │                           │
│   │  • Top 5 clips per shot for editors to choose from   │                           │
│   │  • Cross-segment dedup (same video OK if different   │                           │
│   │    timestamps)                                       │                           │
│   │                                                      │                           │
│   │  Cost: $0                                            │                           │
│   └──────────────────────┬───────────────────────────────┘                           │
│                          │                                                           │
│                          ▼                                                           │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  STAGE 6 — RE-SEARCH (automatic)       GPT-4o-mini  │                           │
│   │                                        or Ollama     │                           │
│   │                                                      │                           │
│   │  For shots with fewer clips than top_results_per_    │                           │
│   │  shot (default 5):                                   │                           │
│   │  • Up to 3 re-search attempts                        │                           │
│   │  • GPT-4o-mini generates 5 alternative queries       │                           │
│   │    (told the originals failed)                       │                           │
│   │  • New YouTube search → transcript → match → rank    │                           │
│   │  • If new match > old match → upgraded               │                           │
│   │                                                      │                           │
│   │  Cost: ~$0.001 total (or $0 if lightweight=ollama)   │                           │
│   └──────────────────────┬───────────────────────────────┘                           │
│                          │                                                           │
│                          ▼                                                           │
│   ┌──────────────────────────────────────────────────────┐                           │
│   │  RESULTS — /jobs/{id}                                │                           │
│   │                                                      │                           │
│   │  • Segments with shots, up to 5 clips each           │                           │
│   │  • Embedded YouTube preview with clip range controls  │                           │
│   │  • Confidence scores, match reasoning, "the hook"    │                           │
│   │  • One-click clip download (yt-dlp, best quality)    │                           │
│   │  • "Add another shot" button per segment             │                           │
│   │  • Coverage assessment and shot warnings             │                           │
│   │  • Full activity log with pipeline trace             │                           │
│   │  • Mark as Used & Save to Library                    │                           │
│   └──────────────────────────────────────────────────────┘                           │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Model Usage — Complete Reference

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                            MODEL USAGE MAP                                       │
│                                                                                  │
│  ┌──────────────────────┐  ┌───────────────────────┐  ┌─────────────────────┐   │
│  │     PAID APIs         │  │    LOCAL (FREE)        │  │   CONFIGURABLE      │   │
│  │                      │  │                       │  │                     │   │
│  │  GPT-4o              │  │  Qwen3 8B (Ollama)    │  │  "Lightweight       │   │
│  │  └─ Translation      │  │  └─ Timestamp match    │  │   model" setting:   │   │
│  │     & segmentation   │  │  └─ Context audit      │  │                     │   │
│  │     (1–2 calls/job)  │  │  (100–200 calls/job)   │  │  DEFAULT:           │   │
│  │                      │  │                       │  │  GPT-4o-mini        │   │
│  │  GPT-4o-mini         │  │  Whisper base          │  │  └─ Re-search       │   │
│  │  └─ Zero-confidence  │  │  └─ Audio → text       │  │     queries         │   │
│  │     rescue (opt-in)  │  │  (0–10 calls/job)      │  │  └─ "Add another    │   │
│  │  └─ Full API         │  │                       │  │     shot" ideation  │   │
│  │     fallback (opt-in)│  │  yt-dlp                │  │                     │   │
│  │                      │  │  └─ YouTube search      │  │  OPTION:            │   │
│  │  ~$0.03–0.10/job     │  │  └─ Video details       │  │  Ollama/local LLM   │   │
│  │                      │  │  └─ Audio download      │  │  ($0, shares GPU)   │   │
│  │                      │  │  └─ Clip download       │  │                     │   │
│  └──────────────────────┘  └───────────────────────┘  └─────────────────────┘   │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

| Stage | Model | Where | Calls/Job | Cost | Setting |
|-------|-------|-------|-----------|------|---------|
| **Translation & segmentation** | GPT-4o | EC2 → OpenAI API | 1–2 (auto-retry if <min segments) | ~$0.03–0.10 | `translation_model` |
| **Timestamp matching** | Qwen3 8B | Local (Ollama via companion) | 100–200 | $0 | `matcher_backend` + `matcher_model` |
| **Zero-confidence rescue** | GPT-4o-mini | EC2 → OpenAI API | 0 (disabled by default) | ~$0.001 each | `confident_fallback_enabled` |
| **Full API fallback** | GPT-4o-mini | EC2 → OpenAI API | 0 (disabled by default) | ~$0.001 each | `api_fallback_enabled` |
| **Re-search (alt queries)** | GPT-4o-mini **or** Ollama | Configurable | 0–10 | ~$0.0001 each or $0 | `lightweight_model` |
| **"Add another shot"** | GPT-4o-mini **or** Ollama | Configurable | 0 (editor-triggered) | ~$0.0001 or $0 | `lightweight_model` |
| **Transcription (YT captions)** | YouTube's own | Free | Auto | $0 | — |
| **Transcription (Whisper)** | Whisper base (77MB) | Local (companion) | 0–10 | $0 | `whisper_max_video_duration_min` |
| **YouTube search** | yt-dlp | Local (companion) | 100–200 | $0 | — |
| **Clip download** | yt-dlp + ffmpeg | Local (companion) | Editor-triggered | $0 | — |

**Typical job cost: $0.03–0.10** — almost entirely the GPT-4o translation call. Everything else runs locally for free.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            EDITOR'S MACHINE                                  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │        Browser — Next.js UI (localhost:3000 or Vercel)                 │  │
│  │                                                                        │  │
│  │  ┌──────────────┐  ┌───────────────┐  ┌────────────┐  ┌──────────┐   │  │
│  │  │ Script Input  │  │ Progress      │  │ Job Results│  │ Settings │   │  │
│  │  │ + Category    │→ │ Tracker       │→ │ /jobs/{id} │  │ Library  │   │  │
│  │  │              │  │ (live log)    │  │ + Expand   │  │ Usage    │   │  │
│  │  └──────────────┘  └───────────────┘  └────────────┘  └──────────┘   │  │
│  │         │                  ▲ Agent Loop                                │  │
│  │         │                  │ (polls EC2 for tasks,                     │  │
│  │         │                  │  relays to companion,                     │  │
│  │         │                  │  returns results)                         │  │
│  └─────────┼──────────────────┼──────────────────────────────────────────┘  │
│            │                  │                                              │
│            │                  ▼                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │       Companion App (localhost:9876) — Flask + Ollama                  │  │
│  │                                                                        │  │
│  │  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌─────────┐ ┌───────────┐  │  │
│  │  │ yt-dlp   │ │ youtube-   │ │ Whisper  │ │ yt-dlp  │ │ Ollama    │  │  │
│  │  │ search   │ │ transcript │ │ base     │ │ video   │ │ Qwen3 8B  │  │  │
│  │  │ +channel │ │ -api       │ │ (77MB)   │ │ details │ │           │  │  │
│  │  │ search   │ │            │ │ audio→   │ │ +clip   │ │ matching  │  │  │
│  │  │          │ │ manual→    │ │ text     │ │ download│ │ +audit    │  │  │
│  │  │          │ │ auto→any   │ │          │ │         │ │ +lightLLM │  │  │
│  │  └──────────┘ └────────────┘ └──────────┘ └─────────┘ └───────────┘  │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
          HTTPS — API only: broll.jayasim.com → FastAPI (/api/v1/...)
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│               EC2 (t3.small, Ubuntu) — broll.jayasim.com                     │
│               Nginx → Let's Encrypt SSL → FastAPI (port 8000)                │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │          Pipeline (asyncio) — Context Anchoring Throughout              │  │
│  │                                                                        │  │
│  │  1. TRANSLATE    2. SEARCH +      3. MATCH         4. RANK + AUDIT    │  │
│  │     & SEGMENT       TRANSCRIPTS      TIMESTAMPS       + RE-SEARCH     │  │
│  │                                                                        │  │
│  │  GPT-4o          yt-dlp            Qwen3 8B         7-dim scoring     │  │
│  │  (1 API call)    (companion)       (companion)      dedup + audit     │  │
│  │                  Whisper           sequential       re-search pass     │  │
│  │  Segments →      (companion)       matching         for low-conf      │  │
│  │  Shots →         streaming                                            │  │
│  │  5 queries       deduped                                              │  │
│  │  per shot                                                             │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────┐    ┌────────────────┐    ┌────────────────┐             │
│  │ Agent Task     │    │ DynamoDB       │    │ OpenAI API     │             │
│  │ Queue          │    │ (9 tables)     │    │                │             │
│  │ (in-memory)    │    │                │    │ GPT-4o (trans) │             │
│  │                │    │ jobs, segments │    │ GPT-4o-mini    │             │
│  │ EC2 creates    │    │ results,       │    │ (lightweight   │             │
│  │ tasks, browser │    │ transcripts,   │    │  tasks only)   │             │
│  │ polls & relays │    │ feedback,      │    │                │             │
│  │ to companion   │    │ settings,      │    │                │             │
│  │                │    │ projects,      │    │                │             │
│  │                │    │ usage, cache   │    │                │             │
│  └────────────────┘    └────────────────┘    └────────────────┘             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## How Tasks Flow Between EC2, Browser, and Companion

The system uses a **browser-relayed agent pattern** because YouTube blocks requests from cloud IPs (like AWS EC2). The editor's local machine acts as the bridge:

```
EC2 Pipeline                    Browser Agent Loop              Companion (localhost:9876)
─────────────                   ──────────────────              ──────────────────────────

Pipeline needs a yt-dlp
search, transcript, or
timestamp match    ───────────►  POST /api/v1/agent/poll
                                (claims pending tasks)
agent_queue.create_task()                    │
         │                                   │
         │ (awaits result)                   ▼
         │                       POST localhost:9876/execute
         │                       { task_type: "search" | "match_timestamp" | ...,
         │                         payload: { ... } }
         │                                   │
         │                                   ▼
         │                       Companion runs yt-dlp / Ollama / Whisper
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
| `channel_search` | `yt-dlp` channel search | Same reason |
| `video_details` | `yt-dlp --dump-json` | Metadata fetch |
| `transcript` | `youtube-transcript-api` | YouTube blocks transcript API from AWS |
| `whisper` | `yt-dlp -x` + Whisper base model | Audio download + local transcription |
| `match_timestamp` | Ollama Qwen3 8B — context-aware matching | Zero cost, structured JSON, local inference |
| `lightweight_llm` | Ollama Qwen3 8B — query generation & ideation | Zero cost (when `lightweight_model=ollama`) |
| `clip` | `yt-dlp --download-sections` + ffmpeg | Downloads a specific time range as MP4 |

---

## Pipeline — Step by Step

### Stage 1: Translate, Segment & Extract Shots — `GPT-4o` (EC2)

One API call to GPT-4o:

- **Translates** the script from any language to English
- **Extracts `script_context`**: topic, geographic scope, temporal scope, domain, exclusion context
- **Segments** into 15–25 natural narrative sections (not forced 1-per-minute)
- **Per segment**, identifies B-roll **shots** — each with:
  - `visual_need` — what the editor needs to see
  - **5 diverse search queries** (specific, descriptive, documentary, broader context, alternative angle)
  - `key_terms` for matching
- Segments marked as "host on camera" get `broll_count: 0` and are skipped

### Stage 2: Search + Transcript Fetch (Streaming) — `yt-dlp` + `Whisper` (Local)

Runs as a **streaming pipeline** — search and transcript fetch overlap:

1. All shots searched concurrently (5 at a time, 4 results per query)
2. **Global dedup**: same video found by multiple shots → transcript fetched once
3. As each search completes, new videos immediately queue for transcript fetch
4. **Transcript cascade**: DynamoDB cache → YouTube captions → companion API → Whisper local

Typical: 200+ search queries → 100–150 unique videos → 100+ transcripts

### Stage 3: Timestamp Matching — `Qwen3 8B` (Local via Ollama)

For each (video, shot) pair:

1. **Context gate**: Does this video actually discuss the documentary's topic? If not → `confidence: 0`, rejected
2. **Timestamp extraction**: Find the exact start/end seconds of the peak visual moment
3. Returns confidence score, transcript excerpt, relevance note, and "the hook"

Runs sequentially (one match at a time) with `OLLAMA_NUM_PARALLEL=3` for optimal local GPU usage.

### Stage 4: Rank, Dedup & Select

**7-dimension weighted scoring:**

| Weight | Dimension | What It Measures |
|--------|-----------|------------------|
| 35% | AI Confidence | LLM confidence that the clip matches the shot |
| 20% | Visual/Topical Fit | Intent-weighted combination of visual_fit and topical_fit from matcher |
| 10% | Viral Score | View count tier (>1M = 1.0, >100K = 0.8, >10K = 0.5, below = 0.2) |
| 10% | Channel Authority | Preferred Tier 1 = 1.0, Tier 2 = 0.9, subscriber-based tiers below |
| 5% | Caption Quality | Manual = 1.0, Auto = 0.8, Whisper = 0.6, None = 0.3 |
| 10% | Recency | Full score within 2 years, 0.7 within 4 years, 0.4 older |
| 10% | Context Relevance | Title checked against script topic, context_keywords, domain, geography, temporal scope |

**Scarcity-based thresholds**: COMMON topics (0.30), MEDIUM (0.25), RARE (0.15) — clips below threshold only kept if no better alternatives exist.

**Hard filters**: context mismatch → rejected, negative keywords → rejected, blocked channels → excluded, cross-segment dedup (same video OK if different timestamps).

**Top 5 clips per shot** (configurable via `top_results_per_shot`) — gives editors multiple choices.

### Stage 5: Re-Search Pass (Automatic)

After ranking, shots with fewer clips than `top_results_per_shot` (default 5) get up to **3 re-search attempts**:

1. GPT-4o-mini (or Ollama if configured) generates **5 alternative search queries** — told the originals failed
2. New YouTube search with the alternative queries → up to 8 new candidates per attempt
3. New candidates go through transcript fetch → matching → ranking
4. If the best new result beats the old one → **upgraded**

Activity log shows: `"Upgrade: 'Satellite images...' — 30% → 62%"`

### "Add Another Shot" (Editor-Triggered)

Editors can click **"+ Add another shot"** on any segment to:

1. GPT-4o-mini (or Ollama) generates a new visual moment for that segment
2. Mini-pipeline: search → transcripts → match (sequential with early exit at ≥50% confidence) → rank
3. Results shown inline with real-time progress bar and activity log
4. Progress persists across page navigation

---

## Cost Breakdown

| Item | Cost | Notes |
|------|------|-------|
| **GPT-4o** (translation + segmentation) | ~$0.03–0.10/job | 1–2 calls per job (auto-retries if under-segmented) |
| **GPT-4o-mini** (re-search + expand) | ~$0.001/job | 0–10 calls; switch to Ollama for $0 |
| **GPT-4o-mini** (zero-confidence rescue) | $0 default | Opt-in via `confident_fallback_enabled`; ~$0.001/clip |
| **Qwen3 8B** (matching) | $0 | Local via Ollama |
| **Whisper** (transcription) | $0 | Local, only when no YouTube captions |
| **yt-dlp** (search + details + clips) | $0 | Local via companion |
| **AWS EC2** (t3.small) | ~$16.56/month | Always running |
| **AWS DynamoDB** | ~$1.00/month | Storage + read/write |
| **AWS Route 53** | ~$0.50/month | DNS |

**Typical job total: $0.05–0.10** — almost entirely the GPT-4o translation call. All matching, search, and transcription runs locally for free.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 15, React, TypeScript, Tailwind CSS, shadcn/ui |
| **Backend** | Python 3.12, FastAPI, Pydantic v2, asyncio |
| **AI — Translation** | OpenAI GPT-4o |
| **AI — Matching** | Qwen3 8B via Ollama (local) |
| **AI — Lightweight tasks** | GPT-4o-mini or Ollama (configurable) |
| **AI — Transcription** | OpenAI Whisper base (local) |
| **Search** | yt-dlp (local companion) — no YouTube API quota needed |
| **Transcripts** | youtube-transcript-api + Whisper fallback |
| **Storage** | AWS DynamoDB (9 tables) |
| **Hosting (API)** | AWS EC2 (t3.small), Nginx, SSL — `https://broll.jayasim.com` |
| **Hosting (UI)** | Next.js on Vercel or localhost:3000 |

---

## Project Structure

```
BRoll Scout/
├── app/
│   ├── main.py                  # FastAPI endpoints, agent queue API
│   ├── background.py            # Pipeline orchestration: streaming search + dedup +
│   │                            #   sequential matching + re-search pass
│   ├── config.py                # Settings model & pipeline defaults
│   ├── models/
│   │   └── schemas.py           # Pydantic models (Job, Segment, BRollShot, RankedResult, etc.)
│   ├── services/
│   │   ├── translator.py        # GPT-4o: translate, segment, extract shots (5 queries/shot)
│   │   ├── searcher.py          # Context-aware YouTube search via yt-dlp companion
│   │   ├── transcriber.py       # 4-level transcript cascade (cache → YT → companion → Whisper)
│   │   ├── matcher.py           # Qwen3 8B: context gate + timestamp extraction
│   │   ├── ranker.py            # 7-dimension scoring, hard filters, dedup
│   │   ├── expand_shots.py      # "Add another shot" + re-search query generation
│   │   ├── storage.py           # DynamoDB CRUD
│   │   ├── settings_service.py  # Settings (DynamoDB-backed) + YouTube channel resolution
│   │   └── usage_service.py     # API cost aggregation
│   ├── utils/
│   │   ├── agent_queue.py       # In-memory task queue for browser↔companion relay
│   │   └── cost_tracker.py      # Per-job cost tracking
│   ├── api/v1/                  # Next.js API routes (proxy to FastAPI)
│   ├── page.tsx                 # Script input + processing page
│   ├── jobs/[id]/page.tsx       # Dedicated job results page (persists on refresh)
│   ├── projects/page.tsx        # Projects listing
│   ├── library/page.tsx         # B-Roll library: search, filter, reuse past clips
│   ├── settings/page.tsx        # Settings (models, channels, weights, instructions)
│   ├── usage/page.tsx           # Usage & cost tracking dashboard
│   └── layout.tsx               # Root layout
├── components/
│   ├── script-input.tsx         # Script paste/upload + category selector
│   ├── progress-tracker.tsx     # Live pipeline progress with activity log
│   ├── results-display.tsx      # Segment cards with shots, clips, expand button
│   ├── agent-status.tsx         # Companion health badge + agent task relay loop
│   ├── job-history.tsx          # Sidebar job list
│   └── navbar.tsx               # Navigation bar
├── lib/
│   └── types.ts                 # TypeScript interfaces
├── broll-companion/
│   ├── companion.py             # Flask: yt-dlp, transcripts, Whisper, Ollama, clips
│   ├── requirements.txt         # Python dependencies
│   ├── setup.bat / setup.ps1    # Windows: install + launch (smart — skips what's installed)
│   ├── setup.sh / setup.command # macOS: install + launch (smart — skips what's installed)
│   ├── stop.bat                 # Force-kill all processes (Windows)
│   └── update.bat               # Update yt-dlp and packages (Windows)
├── scripts/
│   ├── dev.sh                   # macOS dev: kills stale, starts Ollama + companion + Next.js
│   ├── deploy.sh                # EC2 deployment (rsync + restart)
│   ├── setup_ec2.sh             # EC2 provisioning
│   ├── create_tables.py         # DynamoDB table creation
│   └── build_editor_package.sh  # Build editor zip (standalone Next.js + companion)
├── tests/
│   └── test_integration.py      # Integration tests
├── requirements.txt             # Python backend dependencies
├── package.json                 # Node.js frontend dependencies
└── pyproject.toml               # Python project config
```

---

## Configurable Settings

All settings are saved to DynamoDB and take effect immediately — no redeploy needed. Accessible via `/settings`.

### Models & Matching

| Setting | Default | Options | Description |
|---------|---------|---------|-------------|
| `translation_model` | `gpt-4o` | gpt-4o, gpt-4o-mini | Script translation and segmentation |
| `matcher_backend` | `auto` | auto, local, api | Routing for timestamp matching |
| `matcher_model` | `qwen3:8b` | qwen3:8b, qwen3:4b, llama3.3:8b | Local LLM for Ollama |
| `lightweight_model` | `gpt-4o-mini` | gpt-4o-mini, ollama | Re-search queries + "Add another shot" ideation |
| `api_fallback_enabled` | `false` | true/false | GPT-4o-mini retries ALL Ollama failures |
| `confident_fallback_enabled` | `false` | true/false | GPT-4o-mini retries only zero-confidence results (context mismatches trusted) |
| `timestamp_model` | `gpt-4o-mini` | gpt-4o-mini, gpt-4o | API model used for fallback matching |
| `confidence_threshold` | `0.15` | 0.1–0.9 | Minimum confidence to include a clip |
| `top_results_per_shot` | `5` | 1–10 | How many final clips per shot for editors to choose from |

### Search

| Setting | Default | Description |
|---------|---------|-------------|
| `youtube_results_per_query` | `12` | Results per yt-dlp search (auto-scaled for multi-query shots) |
| `max_candidates_per_shot` | `20` | Max videos kept per shot for matching |
| `max_candidates_per_segment` | `20` | Max videos per segment overall |

### Video Filtering

| Setting | Default | Description |
|---------|---------|-------------|
| `min_video_duration_sec` | `120` | Exclude shorter videos (trailers, shorts) |
| `max_video_duration_sec` | `5400` | Exclude longer videos (90 min) |
| `filter_9_16_shorts` | `true` | Drop ~9:16 portrait videos (YouTube Shorts shape) |
| `prefer_min_subscribers` | `5000` | Lower authority score below this subscriber count |
| `whisper_max_video_duration_min` | `60` | Max video length for Whisper transcription |

### Ranking Weights

| Weight | Default | Dimension |
|--------|---------|-----------|
| `weight_ai_confidence` | 0.35 | LLM confidence from matcher |
| `weight_fit_score` | 0.20 | Intent-weighted visual_fit + topical_fit |
| `weight_viral_score` | 0.10 | View count tier (>1M = 1.0, >100K = 0.8, etc.) |
| `weight_channel_authority` | 0.10 | Preferred tier + subscriber count |
| `weight_caption_quality` | 0.05 | Transcript source (manual > auto > whisper > none) |
| `weight_recency` | 0.10 | Publish date |
| `weight_context_relevance` | 0.10 | Title vs script topic, keywords, domain, geography |

---

## Getting Started

### Quick Start (macOS — Developer)

```bash
git clone https://github.com/jayasim-labs/BRoll-Scout.git
cd BRoll-Scout

# Install
npm install
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
brew install ollama && ollama pull qwen3:8b

# Configure
cp .env.example .env   # Add OPENAI_API_KEY, AWS credentials
python scripts/create_tables.py

# Run (starts Ollama + companion + Next.js)
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### Editor Setup (macOS — One Click)

1. Open Terminal in the project folder
2. Run `bash broll-companion/setup.sh` — installs Homebrew, Node, Python, yt-dlp, ffmpeg, Ollama, Qwen3 8B (~5–10 min first time)
3. Creates a **"B-Roll Scout.command"** shortcut on your Desktop
4. Daily: double-click the shortcut — starts everything, opens browser

Or double-click `broll-companion/setup.command` in Finder.

### Editor Setup (Windows — One Click)

1. Download [broll-scout-editor.zip](https://github.com/jayasim-labs/broll-scout/releases/latest/download/broll-scout-editor.zip)
2. Unzip and double-click `setup.bat` — installs Python, ffmpeg, Ollama, Qwen3 8B (~5–10 min first time)
3. If an Ollama window opens during setup, close it — setup will continue
4. Daily: double-click **"B-Roll Scout"** shortcut on Desktop
5. Two terminal windows will open (companion + web app) — keep both open while using the app

---

## API Endpoints

### Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/jobs` | Create a job |
| `GET` | `/api/v1/jobs` | List all jobs |
| `GET` | `/api/v1/jobs/{id}` | Get full job results |
| `GET` | `/api/v1/jobs/{id}/status` | Poll progress |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel a running job |
| `POST` | `/api/v1/jobs/{id}/segments/{seg_id}/expand-shots` | Add another shot |
| `GET` | `/api/v1/jobs/{id}/segments/{seg_id}/expand-progress` | Poll expansion progress |

### Projects, Library, Settings, Agent

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST/GET/PUT/DELETE` | `/api/v1/projects[/{id}]` | Project CRUD |
| `GET` | `/api/v1/library/search` | Search past clips (category, deep search) |
| `GET/PUT` | `/api/v1/settings` | Read/update pipeline settings |
| `POST` | `/api/v1/settings/resolve-channel` | Resolve YouTube channel by URL/handle/name |
| `POST/GET` | `/api/v1/settings/channels[/add/remove]` | Channel CRUD |
| `POST` | `/api/v1/agent/poll` | Browser claims pending tasks |
| `POST` | `/api/v1/agent/result` | Browser submits companion results |
| `POST` | `/api/v1/results/{id}/feedback` | Editor feedback (rating, clip_used) |

---

## DynamoDB Tables

| Table | Key | Purpose |
|-------|-----|---------|
| `broll_jobs` | `job_id` | Job metadata, status, costs, script_context |
| `broll_segments` | `job_id` + `segment_id` | Segments with shots, context anchors, negative keywords |
| `broll_results` | `job_id` + `result_id` | Ranked clips with timestamps, confidence, shot_id |
| `broll_transcripts` | `video_id` | Cached transcripts (YouTube + Whisper) |
| `broll_feedback` | `result_id` | Editor ratings and clip-used tracking |
| `broll_settings` | `setting_key` | Pipeline settings (overrides defaults) |
| `broll_channel_cache` | `channel_id` | YouTube channel metadata cache |
| `broll_projects` | `project_id` | Project groupings with category |
| `broll_usage` | `period` | Aggregated cost tracking |

---

## Deployment

```bash
bash scripts/deploy.sh    # Syncs to EC2, installs deps, restarts service
```

---

## License

Private project — not open source.
