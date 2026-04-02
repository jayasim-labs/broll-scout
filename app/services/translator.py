import json
import logging

import httpx

from app.config import DEFAULTS, get_settings
from app.models.schemas import Segment
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Viral B-Roll Scout — a specialist in digital storytelling and YouTube retention for a Tamil-language documentary channel.

Do the following in one response:
1. Translate the following Tamil script to English.
2. Analyze the overall narrative arc to identify visual hooks, emotional peaks, and technical concepts that need strong B-roll support.
3. Break the English translation into segments — one segment for approximately every 1–2 minutes of script. A 30-minute script must yield at least 30 segments. Segment by visual need, not just topic change: if one topic has multiple distinct visual moments (e.g., a historical event, then a map, then a person), split them into separate segments.
4. For each segment, return:
   - segment_id (format: seg_001, seg_002, ...)
   - title (short, descriptive)
   - summary (2–3 sentences describing what this section covers)
   - visual_need (what the editor needs to SEE on screen: "aerial shot of ancient Rome", "chart showing GDP growth", "archival footage of the 1971 war", "close-up of circuit board manufacturing")
   - emotional_tone (the mood: "dramatic reveal", "explanatory calm", "tension building", "inspirational climax", "historical gravity")
   - key_terms (5–7 keywords a video editor would use to find relevant footage)
   - search_queries (3 distinct YouTube search queries — one broad, one specific, one lateral/creative. Bias toward documentary footage, archival material, and cinematic explainers — NOT news clips)
   - estimated_duration_seconds (rough estimate based on script length)

Return as valid JSON with two keys: "english_translation" (full translated text) and "segments" (JSON array). No prose, no markdown fences."""


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
    ) -> tuple[list[Segment], str]:
        """Translate a Tamil script and return (segments, english_translation).
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
        await _emit("brain", f"Identified {len(segments_raw)} scenes (your {estimated_minutes}-min script needs at least {estimated_minutes} scenes for good B-roll coverage)")

        if len(segments_raw) < estimated_minutes:
            await _emit("alert", f"Only {len(segments_raw)} scenes — not enough for a {estimated_minutes}-min video. Asking GPT-4o to break it into more fine-grained visual moments...")
            messages.append(
                {"role": "assistant", "content": json.dumps(data)}
            )
            messages.append({
                "role": "user",
                "content": (
                    f"The script is approximately {estimated_minutes} minutes. "
                    f"You returned only {len(segments_raw)} segments. "
                    f"I need at least {estimated_minutes} segments — one per minute. "
                    "Split the larger segments into more specific visual moments."
                ),
            })
            data = await self._call_openai(messages, translation_model)
            segments_raw = data.get("segments", [])
            await _emit("check", f"Better! Now {len(segments_raw)} scenes — each focused on a specific visual moment")

        max_segments = max(estimated_minutes * 2, 10)
        if len(segments_raw) > max_segments:
            await _emit("alert", f"GPT-4o generated {len(segments_raw)} scenes but your ~{estimated_minutes}-min script only needs ~{estimated_minutes}–{max_segments}. Trimming to the top {max_segments} to keep the search efficient.")
            segments_raw = segments_raw[:max_segments]

        cost_tracker = get_cost_tracker()
        if job_id:
            job_costs = cost_tracker.get_job_costs(job_id)
            if job_costs:
                job_costs.add_gpt4o(
                    self._last_input_tokens, self._last_output_tokens
                )

        segments = [Segment(**seg) for seg in segments_raw]
        english_translation = data.get("english_translation", "")

        logger.info(
            "Translation complete: %d segments, ~%d min script",
            len(segments),
            estimated_minutes,
        )
        return segments, english_translation

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
