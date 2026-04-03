# B-Roll Scout: Scaling from Internal Tool to SaaS Product

## Question 1: Can the frontend be at broll.jayasim.com while using local agents?

Yes. This is already how it works. Your editors visit broll.jayasim.com (hosted on EC2), and the browser JavaScript communicates with two backends:

- **EC2** (broll.jayasim.com) — API, database, frontend HTML, job orchestration, GPT-4o translation
- **localhost:9876** (companion app) — yt-dlp searches, Qwen3 8B matching

The companion app's CORS is set to accept requests from `https://broll.jayasim.com`. The browser acts as the relay. No changes needed.

**What editors run:** Only the companion app. They install it once, it auto-starts on login, sits in the system tray. Everything else is cloud.

---

## Question 2: What are the cost implications right now (internal use)?

### Current per-job cost breakdown

| Component | What it does | Cost per job | Runs where |
|---|---|---|---|
| GPT-4o | Tamil translation + segmentation + scene identification | ~$0.05–0.10 | EC2 → OpenAI API |
| GPT-4o (context audit) | Post-processing context consistency check | ~$0.02 | EC2 → OpenAI API |
| Qwen3 8B | ~128 timestamp matching calls | $0.00 | Editor's local machine |
| yt-dlp | YouTube search (when API quota exhausted) | $0.00 | Editor's local machine |
| YouTube API | Search + video details (when quota available) | $0.00 (free quota) | EC2 → YouTube |
| Google CSE | Secondary search | $0.00 (free tier: 100/day) | EC2 → Google |
| Gemini Flash | Query expansion | ~$0.01 | EC2 → Google |
| DynamoDB | Read/write all tables | ~$0.00 | AWS (free tier) |
| EC2 (t3.medium) | Runs FastAPI, serves frontend | ~$0.04/hr = ~$30/mo | AWS |

**Total per job: ~$0.08–0.13**
**Monthly cost for 4 jobs/day: ~$35–40 (EC2) + ~$10–15 (API calls) = ~$50/month**

The expensive parts (timestamp matching, YouTube search) are offloaded to the editor's local machine. Your server cost is mostly the EC2 instance running 24/7.

---

## Question 3: What changes to sell this as a SaaS service?

Two architecture options. The right choice depends on your pricing model.

### Option A: Companion App Model (users install locally)

**How it works:** Same as your internal setup. Users install the companion app on their machine. The companion handles yt-dlp searches and Qwen3 matching locally. Your server only handles GPT-4o translation, job orchestration, and database.

**Pros:**
- Your server cost stays low — no GPU needed
- Scales to hundreds of users without scaling server hardware
- Users' local machines do the heavy compute
- yt-dlp runs on residential IPs (no YouTube blocking)

**Cons:**
- Users must install software (friction — some will drop off)
- User experience depends on their hardware (8GB laptop = slow matching)
- Support burden: "the companion won't start" / "Ollama crashed" / "yt-dlp update broke things"
- Can't offer a "try it now" experience without installation
- Enterprise customers won't install random software on corporate machines

**Best for:** Individual creators, small teams, power users who don't mind a local install. Price-sensitive market.

**Server needed:** Same t3.medium you have now. Maybe t3.large ($60/mo) for more concurrent users.

**Pricing model:**
```
Free tier:     2 jobs/month, companion required, 10-min script max
Creator:       $15/month — 30 jobs/month, companion required
Pro:           $29/month — unlimited jobs, companion required, library access
```

**Your margin per Creator user:** User pays $15/mo. Your cost: ~$0.10/job × 30 jobs = $3/mo API + ~$0.50/mo server share. Margin: ~$11.50/user/month (77%).

---

### Option B: Full Server-Side Model (no companion needed)

**How it works:** Everything runs on your server. Users just open the website and paste their script. No installation. Your EC2 runs Ollama + Qwen3 for matching, yt-dlp for search, GPT-4o for translation. Fully cloud-based.

**Pros:**
- Zero friction — works in any browser, no install
- "Try it now" possible (free trial with no setup)
- Consistent experience regardless of user's hardware
- Enterprise-friendly (nothing installed on their machines)
- You control quality — no "my laptop is too slow" issues

**Cons:**
- You need a GPU server for Qwen3 matching
- yt-dlp on your server risks YouTube IP blocking (need rotating proxies or residential proxy service)
- Significantly higher server costs
- You absorb all compute costs

**Server needed:**

For Qwen3 8B matching on server:
```
g5.xlarge — 1x NVIDIA A10G (24GB VRAM), 4 vCPU, 16GB RAM
  On-demand: $1.006/hr = ~$734/month
  1-year reserved: ~$578/month
  3-year reserved: ~$397/month
  Spot instances: ~$0.40/hr = ~$296/month (but can be interrupted)
```

For yt-dlp without getting blocked:
```
Residential proxy service (Bright Data, Oxylabs, etc.): ~$50–100/month
  OR
Multiple cheap VPS with residential IPs: ~$30–60/month
  OR
Run yt-dlp on a pool of small EC2 instances with rotating IPs
```

**Total server cost (Option B):**
```
GPU instance (g5.xlarge, reserved):  ~$578/month
Regular EC2 (FastAPI + API):         ~$30/month
Residential proxies for yt-dlp:      ~$75/month
DynamoDB:                            ~$10/month (beyond free tier with many users)
GPT-4o API:                          ~$0.10/job × jobs
Total fixed:                         ~$693/month + variable API costs
```

**Pricing model:**
```
Free trial:    3 jobs free, no signup needed (just paste and try)
Starter:       $19/month — 20 jobs/month
Creator:       $39/month — 60 jobs/month, library access
Studio:        $79/month — unlimited jobs, priority processing, API access
Enterprise:    Custom — dedicated infrastructure, SLA, SSO
```

**Breakeven:** $693/mo fixed cost ÷ $39/mo Creator plan = 18 Creator users to break even. After that, each new user is nearly pure margin (marginal cost per job is only ~$0.10–0.15 in API calls).

---

### Option C: Hybrid Model (recommended for launch)

**How it works:** Website works without companion (server-side processing), but users CAN install the companion to get faster results and reduce your costs. Incentivize companion installation with higher job limits or priority processing.

```
Without companion (server-side):
  - Qwen3 matching runs on your GPU server
  - yt-dlp runs through proxy on your server
  - Slower (server shared across users), but zero friction

With companion (local processing):
  - Matching + search offloaded to user's machine
  - Faster for the user (dedicated local compute)
  - Cheaper for you (less GPU load)
  - Reward: higher job limits or priority queue
```

**Pricing model:**
```
Free trial:    3 jobs free, server-side, no install
Starter:       $19/month — 20 jobs/month server-side
                           40 jobs/month with companion installed
Creator:       $39/month — 60 jobs/month server-side
                           unlimited with companion installed
Studio:        $79/month — unlimited either way, API access
```

This way:
- New users try it instantly (no install barrier)
- Power users install the companion for better limits (you save GPU costs)
- Enterprise users who can't install software still get full functionality
- Your GPU server handles the free trial + Starter users, companion users offload themselves

---

## Server Architecture for SaaS (Option B or C)

```
                          ┌───────────────────────────────┐
                          │  CloudFront CDN               │
                          │  broll.jayasim.com            │
                          │  Static frontend (HTML/JS)    │
                          └──────────────┬────────────────┘
                                         │
                          ┌──────────────▼────────────────┐
                          │  ALB (Application Load        │
                          │  Balancer)                     │
                          └──────────┬───────┬────────────┘
                                     │       │
                    ┌────────────────▼┐    ┌─▼────────────────┐
                    │  EC2 t3.large    │    │  EC2 g5.xlarge    │
                    │  (API server)    │    │  (GPU worker)     │
                    │                  │    │                   │
                    │  FastAPI app     │    │  Ollama + Qwen3   │
                    │  Job orchestrator│    │  yt-dlp (proxied) │
                    │  GPT-4o calls    │    │  Whisper (future)  │
                    │  Settings API    │    │                   │
                    └───────┬──────────┘    └───────┬───────────┘
                            │                       │
                    ┌───────▼───────────────────────▼───────┐
                    │  DynamoDB                              │
                    │  jobs, segments, results, transcripts, │
                    │  feedback, settings, users             │
                    └───────────────────────────────────────┘
```

**Scale-up path:**
- 1–50 users: Single g5.xlarge handles all matching
- 50–200 users: Add SQS queue between API server and GPU worker, run 2 GPU instances
- 200+ users: Auto-scaling group of GPU workers, or switch to serverless GPU (AWS Inferentia, or RunPod/Modal.com for burst capacity)

---

## What changes for multi-tenant SaaS

### Authentication
- Add Clerk or Supabase Auth (email + Google login)
- JWT tokens on all API calls
- `user_id` column already exists on all DynamoDB tables (set to "internal" for MVP)

### User isolation
- Every DynamoDB query filters by `user_id`
- Each user sees only their jobs, results, library, settings
- Transcript cache is SHARED across users (the network effect: more users = more cached transcripts = faster jobs for everyone)

### Usage metering
```
user_usage table
  - user_id (PK)
  - month (SK) — "2026-04"
  - jobs_count — how many jobs this month
  - jobs_limit — based on plan
  - companion_connected — boolean
  - total_api_cost — accumulated GPT-4o + Gemini costs
```

### Billing
- Stripe integration for subscription management
- Plans: Free, Starter, Creator, Studio, Enterprise
- Webhook on job creation: check user's plan limit before processing

### Onboarding flow
```
1. User signs up (email or Google)
2. Free trial: 3 jobs, no credit card
3. Paste first script → results in 3–5 minutes
4. After 3 free jobs: "Upgrade to continue" paywall
5. Optional: "Install companion app for faster processing + higher limits"
```

---

## Cost comparison table — what to charge

| | Your cost per job | Suggested price per job (implied) | Your margin |
|---|---|---|---|
| Free trial | ~$0.12 (server-side) | $0.00 | -$0.12 (acquisition cost) |
| Starter ($19/mo, 20 jobs) | ~$0.12 × 20 = $2.40 | $0.95/job | 87% margin |
| Creator ($39/mo, 60 jobs) | ~$0.12 × 60 = $7.20 | $0.65/job | 82% margin |
| Creator + companion | ~$0.04 × 60 = $2.40 | $0.65/job | 94% margin |
| Studio ($79/mo, unlimited) | ~$0.12 × ~100 = $12.00 | $0.79/job (at 100 jobs) | 85% margin |

The companion model gives you 94% margins because the user's machine does the expensive compute.

---

## Summary — what to do in what order

### Phase 1 (now — internal use)
- Frontend at broll.jayasim.com ✅ (already done)
- Editors install companion app ✅ (already done)
- Cost: ~$50/month total

### Phase 2 (SaaS launch — start here)
- Add authentication (Clerk)
- Add Stripe billing
- Add usage metering
- Add user isolation on DynamoDB queries
- Keep companion-required model
- Launch at $15–39/month
- Cost: ~$60/month (same EC2 + auth services)
- Target: 20 paying users = $400–780/month revenue

### Phase 3 (growth — after 50+ users)
- Add GPU server (g5.xlarge) for server-side processing
- Add free trial (no companion needed)
- Add Hybrid model pricing (server-side vs companion tiers)
- Add residential proxies for server-side yt-dlp
- Cost: ~$700/month
- Target: 100 paying users = $2,000–4,000/month revenue

### Phase 4 (scale)
- Auto-scaling GPU workers
- SQS job queue
- Enterprise tier with SSO + dedicated infrastructure
- White-label option
- Multi-language support (Hindi, Telugu, Spanish)
- Cost: $2,000–5,000/month infrastructure
- Target: 500+ users