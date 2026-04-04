import asyncio
import json
import logging

import httpx

from app.config import DEFAULTS, get_settings
from app.models.schemas import BRollShot, ScriptContext, Segment
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Viral B-Roll Scout — a specialist in digital storytelling and YouTube retention for a Tamil-language documentary channel.

Do the following in one response:

1. Translate the following Tamil script to English.

2. FIRST, identify the overall script context and return a "script_context" object:
   - script_topic: The primary subject (e.g., "Sentinel Island and the Sentinelese tribe")
   - script_domain: The domain (e.g., "geography, anthropology, indigenous peoples")
   - geographic_scope: Specific regions/countries relevant (e.g., "Andaman Islands, Bay of Bengal, India")
   - temporal_scope: Time period covered (e.g., "prehistoric to present day, with focus on 2018 incident")
   - exclusion_context: What this video is NOT about — topics that share keywords but are unrelated (e.g., "NOT about mainland Indian forests, NOT about wildlife reserves, NOT about tourism destinations")

3. Break the English translation into segments based on NATURAL narrative shifts.
   Do NOT force one segment per minute. Some sections naturally run 2-3 minutes
   on the same theme — keep them as one segment. Other sections may shift
   topics every 30 seconds — split those.
   Typical range: 15-25 segments for a 30-minute script.
   Let the script's own rhythm dictate the splits.

4. For each segment, return:
   - segment_id (format: seg_001, seg_002, ...)
   - title (short, descriptive)
   - summary (2–3 sentences describing what this section covers)
   - visual_need (what the editor needs to SEE on screen — overall for this segment)
   - emotional_tone (the mood)
   - key_terms (5–7 keywords a video editor would use to find relevant footage)
   - search_queries: 3 distinct YouTube search queries for this segment overall. CRITICAL: Every query MUST include the script's specific context. BAD: "tropical forest documentary". GOOD: "Sentinel Island aerial forest footage".
   - estimated_duration_seconds (rough estimate of how long this section of the script runs)
   - context_anchor: A one-sentence statement connecting this segment to the overall script topic
   - negative_keywords: 3–5 terms that would indicate a WRONG match for this segment
   - broll_count: how many DIFFERENT B-roll clips this segment needs. Determine based on CONTENT, not duration:
       * 0 clips: host on camera, personal narration, interview clips where the speaker IS the visual, intro/outro cards, sections where the audience should focus on the narrator's face and emotion. Mark with broll_note explaining why.
       * 1 clip: a single concept, location, or event. One strong visual is enough.
       * 2-3 clips: multiple distinct visual ideas — e.g., a location PLUS a historical event PLUS data. Each shot must be a genuinely different visual need, NOT the same concept rephrased.
       * 4+ clips: rare. Only for rapid-fire montage sections or segments covering many distinct events/locations in sequence.
       Do NOT pad broll_count to hit a target number. If the script naturally needs 24 shots across 20 segments, return 24. Quality over quantity — precisely targeted shots are more useful than vague padded ones.
   - broll_note: if broll_count is 0, explain why (e.g., "Host on camera — no B-roll needed"). Otherwise null.
   - broll_shots: an array of EXACTLY broll_count objects, each describing a distinct B-roll shot:
       * shot_id: "{segment_id}_shot_{N}" (e.g., "seg_003_shot_1", "seg_003_shot_2")
       * visual_need: what the editor needs to see for THIS SPECIFIC shot (not the segment's general topic)
       * search_queries: 2-3 YouTube search queries for THIS specific shot, each containing at least one term from geographic_scope or script_topic
       * key_terms: 3-5 keywords for THIS shot

     Example for a 2-minute segment about "Sentinel Island's geography":
     broll_shots: [
       {
         "shot_id": "seg_005_shot_1",
         "visual_need": "Aerial/satellite view of North Sentinel Island showing its isolation",
         "search_queries": ["Sentinel Island satellite aerial view", "North Sentinel Island drone"],
         "key_terms": ["satellite", "aerial", "island", "isolation"]
       },
       {
         "shot_id": "seg_005_shot_2",
         "visual_need": "Dense tropical rainforest canopy from above — the impenetrable jungle",
         "search_queries": ["Andaman island rainforest canopy aerial", "dense jungle island drone footage"],
         "key_terms": ["rainforest", "canopy", "dense", "tropical"]
       },
       {
         "shot_id": "seg_005_shot_3",
         "visual_need": "Coral reef and shallow waters surrounding the island",
         "search_queries": ["coral reef island barrier aerial", "Andaman Islands coral reef footage"],
         "key_terms": ["coral", "reef", "shallow", "barrier"]
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
        script_context = ScriptContext(
            script_topic=ctx_raw.get("script_topic", ""),
            script_domain=ctx_raw.get("script_domain", ""),
            geographic_scope=ctx_raw.get("geographic_scope", ""),
            temporal_scope=ctx_raw.get("temporal_scope", ""),
            exclusion_context=ctx_raw.get("exclusion_context", ""),
        )

        logger.info(
            "Translation complete: %d segments, ~%d min script, topic=%s",
            len(segments),
            estimated_minutes,
            script_context.script_topic[:80],
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

        content = result["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from OpenAI, retrying with strict prompt")
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": "Your previous response was not valid JSON. Return valid JSON only.",
            })
            return await self._call_openai_strict(messages, model)

    async def _call_openai_strict(
        self, messages: list[dict], model: str
    ) -> dict:
        """Single retry for invalid JSON responses."""
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
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()

        result = response.json()
        usage = result.get("usage", {})
        self._last_input_tokens += usage.get("prompt_tokens", 0)
        self._last_output_tokens += usage.get("completion_tokens", 0)

        content = result["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenAI returned invalid JSON after retry: {content[:200]}"
            ) from exc
