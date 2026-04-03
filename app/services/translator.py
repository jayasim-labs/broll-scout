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
   - broll_count: how many DIFFERENT B-roll clips this segment needs. Guidelines:
       * 0 clips: segment is host-on-camera, personal narration, or intro/outro that doesn't need B-roll
       * 1 clip: segment is under 45 seconds, or is a simple single-concept explanation
       * 2 clips: segment is 45-90 seconds, or covers two distinct visual ideas
       * 3 clips: segment is 90-150 seconds, or has multiple visual beats
       * 4+ clips: segment is over 150 seconds with several distinct visual moments
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
   The total_broll_shots MUST be >= script duration in minutes.
   If it's too low, add more shots to the longer segments.

Return as valid JSON with four keys: "english_translation" (string), "script_context" (object), "segments" (JSON array), and "segment_summary" (object). No prose, no markdown fences."""


class TranslatorService:
    """Translates Tamil scripts to English and segments them via GPT-4o."""

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.api_url = "https://api.openai.com/v1/chat/completions"

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

        translation_model = DEFAULTS.get("translation_model", "gpt-4o")
        special_instructions = DEFAULTS.get("special_instructions", "")

        word_count = len(script.split())
        estimated_minutes = max(1, round(word_count / 100))

        await _emit("brain", f"Your script is ~{word_count} words (~{estimated_minutes} minutes of video). Sending to GPT-4o to translate from Tamil to English")
        await _emit("clock", f"GPT-4o will also break your script into scenes and figure out what B-roll each scene needs. This usually takes 15–30 seconds")

        system = SYSTEM_PROMPT
        if special_instructions:
            system += f"\n\nAdditional instructions:\n{special_instructions}"

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

        await _emit("brain", f"Identified {len(segments_raw)} natural segments with {total_shots} total B-roll shots ({no_broll_count} segments need no B-roll)")

        if total_shots < estimated_minutes:
            await _emit("alert", f"Only {total_shots} B-roll shots — your {estimated_minutes}-min script needs at least {estimated_minutes}. Asking GPT-4o to add more shots to longer segments...")
            messages.append(
                {"role": "assistant", "content": json.dumps(data)}
            )
            messages.append({
                "role": "user",
                "content": (
                    f"The script is approximately {estimated_minutes} minutes. "
                    f"You returned {len(segments_raw)} segments with only {total_shots} total B-roll shots. "
                    f"I need at least {estimated_minutes} B-roll shots total. "
                    "Add more broll_shots to the longer segments — they need more visual variety. "
                    "Do NOT split segments artificially. Instead increase broll_count and add more broll_shots entries."
                ),
            })
            data = await self._call_openai(messages, translation_model)
            segments_raw = data.get("segments", [])
            total_shots = sum(s.get("broll_count", 1) for s in segments_raw)
            await _emit("check", f"Now {len(segments_raw)} segments with {total_shots} B-roll shots")

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
        """Make a single OpenAI chat completion request and return parsed JSON."""
        self._last_input_tokens = 0
        self._last_output_tokens = 0

        async with httpx.AsyncClient(timeout=120.0) as client:
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
        async with httpx.AsyncClient(timeout=120.0) as client:
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
