import asyncio
import json
import logging
import math
import re as _re
from collections import Counter

import httpx

from app.config import DEFAULTS, get_settings
from app.models.schemas import BRollShot, ScriptContext, Segment
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)


# ── Server-side keyword extraction fallback ──────────────────────────────
# Used when GPT-4o returns no context_keywords or too few.
_STOPWORDS_EN = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "is", "are", "was", "were", "be", "been", "being", "with", "from",
    "by", "as", "its", "it", "this", "that", "these", "those", "their",
    "they", "them", "he", "she", "him", "her", "his", "we", "us", "our",
    "you", "your", "not", "no", "has", "have", "had", "will", "would",
    "could", "should", "may", "might", "shall", "can", "do", "does", "did",
    "about", "into", "between", "through", "during", "after", "before",
    "also", "but", "if", "then", "than", "when", "where", "who", "what",
    "which", "how", "why", "more", "most", "very", "just", "only", "even",
    "so", "all", "each", "every", "both", "any", "some", "many", "much",
    "such", "own", "other", "new", "old", "one", "two", "three", "first",
    "last", "long", "great", "little", "own", "same", "big", "high", "small",
    "large", "next", "early", "young", "important", "few", "public", "bad",
    "same", "able", "video", "footage", "documentary", "story", "network",
    "elite", "secret", "mystery", "real", "world", "global", "local",
    "inside", "behind", "files", "part", "full", "time", "year", "years",
    "people", "man", "woman", "way", "day", "thing", "life", "said",
    "know", "like", "come", "make", "take", "want", "give", "use", "find",
    "tell", "ask", "work", "seem", "feel", "try", "leave", "call",
    "need", "become", "keep", "let", "begin", "show", "hear", "play",
    "run", "move", "live", "believe", "bring", "happen", "write", "sit",
    "stand", "lose", "pay", "meet", "include", "continue", "set", "learn",
    "change", "lead", "understand", "watch", "follow", "stop", "create",
    "speak", "read", "allow", "add", "spend", "grow", "open", "walk",
    "win", "offer", "remember", "love", "consider", "appear", "buy",
    "wait", "serve", "die", "send", "expect", "build", "stay", "fall",
    "cut", "reach", "kill", "remain", "however", "still", "back", "here",
    "there", "now", "over", "well", "really", "already", "around", "never",
    "always", "often", "ever", "away", "again", "look", "point", "being",
    "went", "going", "got", "get", "made", "see", "seen", "says",
})


def _extract_keywords_from_text(text: str, top_n: int = 15) -> list[str]:
    """Extract the most important keywords from translated text using TF weighting
    with a strong bias toward capitalized words (likely proper nouns).

    Returns up to *top_n* keywords sorted by importance.
    """
    words = _re.findall(r"[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]", text)
    if not words:
        return []

    total = len(words)
    freq: Counter = Counter()
    cap_freq: Counter = Counter()

    for w in words:
        low = w.lower()
        if low in _STOPWORDS_EN or len(low) < 3:
            continue
        freq[low] += 1
        if w[0].isupper():
            cap_freq[w] += 1

    if not freq:
        return []

    # Score = tf * capital_boost
    # Capitalized words (proper nouns) get 3x boost
    scores: dict[str, float] = {}
    for word, count in freq.items():
        tf = count / total
        scores[word] = tf

    # Merge capitalized forms: prefer the capitalized version
    final: dict[str, float] = {}
    used_lower: set[str] = set()
    # First pass: capitalized words
    for cap_word, count in cap_freq.most_common():
        low = cap_word.lower()
        if low in used_lower:
            continue
        used_lower.add(low)
        base_score = scores.get(low, 0)
        final[cap_word] = base_score * 3.0

    # Second pass: remaining non-capitalized but frequent words
    for word, score in scores.items():
        if word not in used_lower:
            final[word] = score

    ranked = sorted(final.items(), key=lambda x: -x[1])
    return [word for word, _ in ranked[:top_n]]

SYSTEM_PROMPT = """You are the Viral B-Roll Scout — a specialist in digital storytelling and YouTube retention for a Tamil-language documentary channel.

Do the following in one response:

1. Translate the following Tamil script to English.

2. FIRST, identify the overall script context and return a "script_context" object:
   - script_topic: The primary subject (e.g., "Sentinel Island and the Sentinelese tribe")
   - script_domain: The domain (e.g., "geography, anthropology, indigenous peoples")
   - geographic_scope: Specific regions/countries relevant (e.g., "Andaman Islands, Bay of Bengal, India")
   - temporal_scope: Time period covered (e.g., "prehistoric to present day, with focus on 2018 incident")
   - exclusion_context: What this video is NOT about — topics that share keywords but are unrelated (e.g., "NOT about mainland Indian forests, NOT about wildlife reserves, NOT about tourism destinations")
   - context_keywords: An ordered list of 8–15 keywords/phrases that DEFINE this script's identity, ranked by importance. These are the terms that MUST appear in YouTube search queries to keep results on-topic. Rules:
       * Position 1–3: The MOST distinctive proper nouns or names — the words that, if missing from a query, would return completely wrong results. (e.g., for a script about Jeffrey Epstein: ["Epstein", "Jeffrey Epstein", "Ghislaine Maxwell"]. For Sentinel Island: ["Sentinel Island", "Sentinelese", "North Sentinel"])
       * Position 4–8: Important people, places, organizations, or events mentioned repeatedly (e.g., "Little St. James Island", "Bill Clinton", "Prince Andrew")
       * Position 9+: Supporting terms that add specificity (e.g., "sex trafficking", "plea deal", "FBI investigation")
       * NEVER include generic words like "elite", "network", "secret", "mystery", "story", "documentary", "footage", "investigation" — these match thousands of unrelated videos
       * Each entry should be a proper noun, specific name, or highly specific concept — the kind of word that narrows YouTube results to THIS script's topic

3. Break the English translation into segments based on narrative shifts.
   MANDATORY SEGMENT COUNT RULE: You MUST produce at least 1 segment per 2 minutes of script.
   A 20-minute script → minimum 10 segments (aim for 12-15).
   A 30-minute script → minimum 15 segments (aim for 16-22).
   A 40-minute script → minimum 20 segments (aim for 22-28).
   A 50-minute script → minimum 25 segments (aim for 26-35).
   If your output has fewer segments than the minimum, you have merged too aggressively — go back and split.
   Each distinct subtopic, event, era, location, or concept shift MUST be its own segment.
   Do NOT merge "Origin of X" + "Evolution of X" + "Modern X" into one segment — those are 3 separate segments.
   Segments should typically be 60-150 seconds each. Any segment over 180 seconds should be split unless it truly covers only one narrow topic.
   Each segment gets independent YouTube searches, so more segments = better search coverage for editors.

4. For each segment, return:
   - segment_id (format: seg_001, seg_002, ...)
   - title (short, descriptive)
   - summary (2–3 sentences describing what this section covers)
   - visual_need (what the editor needs to SEE on screen — overall for this segment)
   - emotional_tone: the emotional mood of this segment. Use one of: "urgent", "contemplative", "celebratory", "ominous", "neutral", "dramatic", "melancholic", "inspiring", "tense", "humorous"
   - key_terms (5–7 keywords a video editor would use to find relevant footage)
   - search_queries: 3 distinct YouTube search queries for this segment overall. CRITICAL: Every query MUST include the script's specific context. BAD: "tropical forest documentary". GOOD: "Sentinel Island aerial forest footage".
   - estimated_duration_seconds (rough estimate of how long this section of the script runs)
   - context_anchor: A one-sentence statement connecting this segment to the overall script topic
   - negative_keywords: 3–5 terms that would indicate a WRONG match for this segment
   - broll_count: how many DIFFERENT B-roll clips this segment needs. Editors need CHOICES — more distinct shots means more options to pick from. Determine based on CONTENT:
       * 0 clips: host on camera, personal narration, interview clips where the speaker IS the visual, intro/outro cards. Mark with broll_note explaining why.
       * 1 clip: a very brief single concept (rare — most segments benefit from 2+).
       * 2-3 clips: the STANDARD for most segments — a location PLUS a historical event PLUS a contextual visual. Each shot must be a genuinely different visual need.
       * 4+ clips: for segments covering multiple distinct events, locations, or concepts in sequence. This is COMMON for documentary content — don't hesitate to use it.
       AIM HIGH on broll_count — editors would rather have 40 diverse shots to choose from than 20 generic ones. When in doubt, add another shot with a different visual angle on the same topic. A 30-minute script typically needs 30-45 total B-roll shots across 12-20 segments. Do NOT reduce segment count to increase per-segment broll_count — keep segments granular AND give each one 2-4 shots.
   - broll_note: if broll_count is 0, explain why (e.g., "Host on camera — no B-roll needed"). Otherwise null.
   - broll_shots: an array of EXACTLY broll_count objects, each describing a distinct B-roll shot:
       * shot_id: "{segment_id}_shot_{N}" (e.g., "seg_003_shot_1", "seg_003_shot_2")
       * visual_need: what the editor needs to see for THIS SPECIFIC shot (not the segment's general topic)
       * visual_description: describe what the ideal footage LOOKS like — camera angle, motion, lighting, color palette, framing. E.g., "Wide aerial shot of a dense green island surrounded by turquoise water, camera slowly orbiting". This is separate from what the footage is ABOUT.
       * shot_intent: classify the visual relationship to the narration:
           - "literal": footage directly showing what's being discussed (e.g., narration about the Eiffel Tower → footage of the Eiffel Tower)
           - "illustrative": footage that represents or symbolizes the concept (e.g., narration about economic growth → footage of busy stock exchange floor)
           - "atmospheric": footage that sets mood/tone without direct topical connection (e.g., narration about uncertainty → slow-motion clouds, empty corridors, rain on windows)
       * scarcity: estimate how easy this footage is to find on YouTube:
           - "common": generic footage widely available (city skylines, nature shots, tech demos)
           - "medium": somewhat specific (particular historical events, specific locations, niche subjects)
           - "rare": very specific footage unlikely to be on YouTube (classified events, obscure historical moments, specific private locations)
       * preferred_source_type: what type of source video would be ideal — one of: "documentary", "news_clip", "stock_footage", "drone_aerial", "interview", "timelapse", "archival", "animation", or "" if no preference
       * search_queries: EXACTLY 3 YouTube search queries for THIS specific shot. Think about how YouTube videos are actually TITLED — not how an editor would describe the shot. The 3 queries MUST be diverse:
           1. SPECIFIC: exact event/subject name as a YouTuber would title it (e.g., "North Sentinel Island drone footage")
           2. DESCRIPTIVE: what the footage visually looks like (e.g., "isolated tropical island aerial drone footage 4K")
           3. DOCUMENTARY: topic + "documentary" or "history" or a creative synonym (e.g., "Sentinel Island documentary", "uncontacted tribe island footage")
         This diversity is CRITICAL — different phrasings find different videos.
         IMPORTANT: Avoid overly academic/niche queries that nobody would use as a YouTube title. "King Edward II ban on football" won't find results — but "medieval football history England" will.
       * key_terms: 3-5 keywords for THIS shot

     Example for a 2-minute segment about "Sentinel Island's geography":
     broll_shots: [
       {
         "shot_id": "seg_005_shot_1",
         "visual_need": "Aerial/satellite view of North Sentinel Island showing its isolation",
         "visual_description": "Wide overhead satellite or drone shot of a small green island surrounded by deep blue ocean, emphasizing its remoteness with no other land in frame",
         "shot_intent": "literal",
         "scarcity": "medium",
         "preferred_source_type": "drone_aerial",
         "search_queries": [
           "North Sentinel Island satellite view",
           "isolated tropical island aerial drone footage",
           "Sentinel Island documentary aerial"
         ],
         "key_terms": ["satellite", "aerial", "island", "isolation"]
       },
       {
         "shot_id": "seg_005_shot_2",
         "visual_need": "Dense tropical rainforest canopy from above — the impenetrable jungle",
         "visual_description": "Slow drone push over an endless canopy of tropical trees, rich greens, misty atmosphere, no clearings visible — conveying impassibility",
         "shot_intent": "illustrative",
         "scarcity": "common",
         "preferred_source_type": "drone_aerial",
         "search_queries": [
           "North Sentinel Island jungle canopy aerial",
           "dense tropical rainforest island drone footage",
           "tropical jungle canopy cinematic flyover"
         ],
         "key_terms": ["rainforest", "canopy", "dense", "tropical"]
       }
     ]

5. At the end, include a summary object "segment_summary":
   - total_segments: count
   - total_broll_shots: sum of all broll_count values
   - segments_needing_no_broll: count of segments with broll_count = 0
   - coverage_note: a brief assessment, e.g., "24 shots across 20 segments. 5 segments are host-on-camera. Longest gap without B-roll: seg_012-seg_013 (3 minutes) — narrator personal story section."

Return as valid JSON with four keys: "english_translation" (string), "script_context" (object), "segments" (JSON array), and "segment_summary" (object). No prose, no markdown fences."""


class TranslatorService:
    """Translates Tamil scripts to English and segments them via GPT-4o."""

    def __init__(self, pipeline_settings: dict | None = None):
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self._pipeline = pipeline_settings or {}

    def _get(self, key: str, fallback=None):
        if key in self._pipeline:
            return self._pipeline[key]
        return DEFAULTS.get(key, fallback)

    async def translate_and_segment(
        self,
        script: str,
        job_id: str | None = None,
        on_progress=None,
    ) -> tuple[list[Segment], str, ScriptContext]:
        """Translate a Tamil script and return (segments, english_translation, script_context).
        on_progress(icon, text) is called at each sub-step.
        """
        async def _emit(icon: str, text: str):
            if on_progress:
                try:
                    await on_progress(icon, text)
                except Exception:
                    pass

        translation_model = self._get("translation_model", "gpt-4o")
        special_instructions = self._get("special_instructions", "")

        word_count = len(script.split())
        estimated_minutes = max(1, round(word_count / 100))

        await _emit("brain", f"Your script is ~{word_count} words (~{estimated_minutes} minutes of video). Sending to GPT-4o to translate from Tamil to English")
        await _emit("clock", f"GPT-4o will also break your script into scenes and figure out what B-roll each scene needs. This usually takes 15–30 seconds")

        system = SYSTEM_PROMPT
        if special_instructions and special_instructions.strip():
            system += (
                "\n\n=== EDITOR PREFERENCES ===\n"
                "The editor has specified these preferences. Apply them when generating "
                "search queries and visual needs for each segment. These should influence "
                "WHAT kind of footage you search for.\n\n"
                f"{special_instructions.strip()}\n\n"
                "When these preferences affect search queries, adjust the query terms accordingly.\n"
                "For example:\n"
                '  - "prefer documentary over news" → use "documentary", "analysis", "explainer" '
                'in queries, avoid "news", "update", "breaking"\n'
                '  - "prefer aerial/drone shots" → include "aerial", "drone", "overhead" in queries\n'
                '  - "avoid partisan news" → use "analysis", "neutral", "independent" in queries'
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": script},
        ]

        await _emit("brain", "Translating, identifying visual moments, and preparing YouTube search queries...")
        data = await self._call_openai(messages, translation_model)
        await _emit("check", f"GPT-4o finished translating your script")

        segments_raw = data.get("segments", [])

        min_expected_segments = max(5, int(estimated_minutes / 3))
        if len(segments_raw) < min_expected_segments:
            logger.warning(
                "GPT-4o returned only %d segments for a %d-min script (expected at least %d). "
                "Retrying with stronger segmentation guidance.",
                len(segments_raw), estimated_minutes, min_expected_segments,
            )
            await _emit("alert", f"GPT-4o returned only {len(segments_raw)} segments for a {estimated_minutes}-min script — retrying with stronger guidance for at least {min_expected_segments} segments")
            retry_msg = (
                f"You returned {len(segments_raw)} segments for a {estimated_minutes}-minute script. "
                f"That is too few — you MUST produce at least {min_expected_segments} segments. "
                f"Each distinct subtopic, event, concept, or narrative shift needs its own segment. "
                f"Split any segment over 150 seconds into smaller parts. "
                f"Return the corrected full JSON with more granular segments."
            )
            messages.append({"role": "assistant", "content": json.dumps(data)})
            messages.append({"role": "user", "content": retry_msg})
            data = await self._call_openai(messages, translation_model)
            segments_raw = data.get("segments", [])
            await _emit("check", f"Retry produced {len(segments_raw)} segments")

        summary = data.get("segment_summary", {})
        total_shots = summary.get("total_broll_shots", 0)
        if not total_shots:
            total_shots = sum(s.get("broll_count", 1) for s in segments_raw)
        no_broll_count = summary.get("segments_needing_no_broll", 0)

        coverage_note = summary.get("coverage_note", "")
        shots_per_min = round(total_shots / max(estimated_minutes, 1), 2)
        await _emit("brain", f"Identified {len(segments_raw)} natural segments with {total_shots} B-roll shots ({no_broll_count} host-on-camera segments) — {shots_per_min} shots/min")
        if coverage_note:
            await _emit("check", f"Coverage: {coverage_note}")

        cost_tracker = get_cost_tracker()
        if job_id:
            job_costs = cost_tracker.get_job_costs(job_id)
            if job_costs:
                job_costs.add_gpt4o(
                    self._last_input_tokens, self._last_output_tokens
                )

        segments = [Segment(**seg) for seg in segments_raw]
        english_translation = data.get("english_translation", "")

        ctx_raw = data.get("script_context", {})
        gpt_keywords = ctx_raw.get("context_keywords", [])
        if not isinstance(gpt_keywords, list):
            gpt_keywords = []

        # Fallback: if GPT-4o returned fewer than 5 keywords, extract from text
        MIN_KEYWORDS = 5
        if len(gpt_keywords) < MIN_KEYWORDS and english_translation:
            extracted = _extract_keywords_from_text(english_translation, top_n=15)
            # Merge: GPT keywords first (they're higher quality), then fill with extracted
            seen_lower = {kw.lower() for kw in gpt_keywords}
            for kw in extracted:
                if kw.lower() not in seen_lower:
                    gpt_keywords.append(kw)
                    seen_lower.add(kw.lower())
                if len(gpt_keywords) >= 15:
                    break
            logger.info(
                "context_keywords supplemented by TF extraction: %d total keywords",
                len(gpt_keywords),
            )

        script_context = ScriptContext(
            script_topic=ctx_raw.get("script_topic", ""),
            script_domain=ctx_raw.get("script_domain", ""),
            geographic_scope=ctx_raw.get("geographic_scope", ""),
            temporal_scope=ctx_raw.get("temporal_scope", ""),
            exclusion_context=ctx_raw.get("exclusion_context", ""),
            context_keywords=gpt_keywords,
        )

        logger.info(
            "Translation complete: %d segments, ~%d min script, topic=%s, keywords=%s",
            len(segments),
            estimated_minutes,
            script_context.script_topic[:80],
            script_context.context_keywords[:5],
        )
        return segments, english_translation, script_context

    async def _call_openai(
        self, messages: list[dict], model: str
    ) -> dict:
        """Make an OpenAI chat completion request with retry on transient errors."""
        self._last_input_tokens = 0
        self._last_output_tokens = 0

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    response = await client.post(
                        self.api_url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": 0.7,
                            "max_tokens": 16384,
                            "response_format": {"type": "json_object"},
                        },
                    )
                if response.status_code == 429:
                    wait = (2 ** attempt) * 5
                    logger.warning("OpenAI rate limited (429), retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                break
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                if attempt < max_retries - 1:
                    wait = (2 ** attempt) * 3
                    logger.warning("OpenAI timeout (%s), retrying in %ds (attempt %d/%d)", type(e).__name__, wait, attempt + 1, max_retries)
                    await asyncio.sleep(wait)
                else:
                    raise

        result = response.json()
        usage = result.get("usage", {})
        self._last_input_tokens = usage.get("prompt_tokens", 0)
        self._last_output_tokens = usage.get("completion_tokens", 0)

        finish_reason = result["choices"][0].get("finish_reason", "")
        content = result["choices"][0]["message"]["content"]

        if finish_reason == "length":
            logger.warning(
                "GPT-4o output was truncated (finish_reason=length, %d output tokens). "
                "Script may be too long for a single call.",
                self._last_output_tokens,
            )
            raise RuntimeError(
                f"GPT-4o output was truncated at {self._last_output_tokens} tokens "
                f"(finish_reason=length). The script is too long for the model's output "
                f"limit. Try a shorter script or split it into parts."
            )

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from OpenAI (finish_reason=%s), retrying with strict prompt", finish_reason)
            return await self._call_openai_strict(messages, model)

    async def _call_openai_strict(
        self, messages: list[dict], model: str
    ) -> dict:
        """Fresh retry for invalid JSON — don't append the broken response as context."""
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 16384,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()

        result = response.json()
        usage = result.get("usage", {})
        self._last_input_tokens += usage.get("prompt_tokens", 0)
        self._last_output_tokens += usage.get("completion_tokens", 0)

        finish_reason = result["choices"][0].get("finish_reason", "")
        content = result["choices"][0]["message"]["content"]

        if finish_reason == "length":
            raise RuntimeError(
                f"GPT-4o output truncated on retry ({self._last_output_tokens} tokens). "
                f"Script is too long for a single translation call."
            )

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenAI returned invalid JSON after retry (finish_reason={finish_reason}): {content[:200]}"
            ) from exc
