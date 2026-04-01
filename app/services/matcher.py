import json
import logging

import httpx

from app.config import get_settings, DEFAULTS
from app.models.schemas import Segment, MatchResult, TranscriptSource
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)

TIMESTAMP_PROMPT_TEMPLATE = """You are the Viral B-Roll Extractor. Given a video's captions with timestamps and a script segment description, your job is to find the PEAK VISUAL MOMENT — not just where the topic is discussed, but where the most visually compelling, high-retention footage exists.

Segment summary: {summary}
Visual need: {visual_need}
Emotional tone: {emotional_tone}
Key terms: {key_terms}

Captions:
{transcript}

Instructions:
1. Find the section of the video that best matches the visual need described above.
2. Within that section, pinpoint the "peak" — the moment with the highest visual impact. Prefer: cinematic footage, archival material, data visualizations, dramatic reveals, expert demonstrations, aerial/drone shots, scientific animations. Avoid: talking heads, static interview frames, title cards, end screens.
3. The clip should be 15–90 seconds long. If the best moment is shorter, expand slightly. If longer, narrow to the most impactful portion.
4. Do NOT return timestamp 0:00 unless the video genuinely opens with the relevant visual content.

Return JSON only:
{{
  "start_time_seconds": int,
  "end_time_seconds": int,
  "excerpt": "relevant transcript text from this window (max 200 words)",
  "confidence_score": float (0.0 to 1.0),
  "relevance_note": "one sentence on why this section matches the topic",
  "the_hook": "one sentence on why this specific timestamp is VISUALLY compelling — what makes it a 'peak' moment an editor would want"
}}

If no relevant section exists, return confidence_score: 0.0."""


class MatcherService:
    """Finds peak visual moments in transcripts using GPT-4o-mini."""

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.api_url = "https://api.openai.com/v1/chat/completions"

    async def find_timestamp(
        self,
        transcript_text: str | None,
        segment: Segment,
        video_metadata: dict,
        job_id: str | None = None,
    ) -> MatchResult:
        if not transcript_text:
            return MatchResult(
                confidence_score=0.0,
                source_flag=TranscriptSource.NONE,
            )

        model = DEFAULTS.get("timestamp_model", "gpt-4o-mini")
        special_instructions = DEFAULTS.get("special_instructions", "")
        max_words = 12000
        words = transcript_text.split()
        if len(words) > max_words:
            transcript_text = " ".join(words[:max_words])

        prompt = TIMESTAMP_PROMPT_TEMPLATE.format(
            summary=segment.summary,
            visual_need=segment.visual_need,
            emotional_tone=segment.emotional_tone,
            key_terms=", ".join(segment.key_terms),
            transcript=transcript_text,
        )
        if special_instructions:
            prompt += f"\n\nAdditional instructions:\n{special_instructions}"

        source_str = video_metadata.get("transcript_source", "no_transcript")
        try:
            source_flag = TranscriptSource(source_str)
        except ValueError:
            source_flag = TranscriptSource.NONE

        parsed = await self._call_model(prompt, model, job_id)
        if parsed is None:
            return MatchResult(confidence_score=0.0, source_flag=source_flag)

        excerpt = parsed.get("excerpt", "")
        max_excerpt = DEFAULTS.get("transcript_excerpt_max_words", 200)
        excerpt_words = excerpt.split()
        if len(excerpt_words) > max_excerpt:
            excerpt = " ".join(excerpt_words[:max_excerpt])

        return MatchResult(
            start_time_seconds=parsed.get("start_time_seconds"),
            end_time_seconds=parsed.get("end_time_seconds"),
            transcript_excerpt=excerpt or None,
            confidence_score=min(1.0, max(0.0, float(parsed.get("confidence_score", 0.0)))),
            relevance_note=parsed.get("relevance_note"),
            the_hook=parsed.get("the_hook"),
            source_flag=source_flag,
            context_match_valid=True,
        )

    def validate_context_match(
        self, match: MatchResult, video_duration_seconds: int
    ) -> MatchResult:
        start = match.start_time_seconds
        end = match.end_time_seconds

        if start is not None and start >= video_duration_seconds:
            match.context_match_valid = False
            return match

        if end is not None and end > video_duration_seconds:
            match.end_time_seconds = max(0, video_duration_seconds - 5)

        if start is not None and end is not None and (end - start) < 10:
            match.context_match_valid = False
            return match

        if start is not None and video_duration_seconds > 0:
            if start > video_duration_seconds - 30:
                match.confidence_score = max(0.0, match.confidence_score - 0.3)

        return match

    async def _call_model(
        self, prompt: str, model: str, job_id: str | None
    ) -> dict | None:
        if not self.api_key:
            logger.error("No OpenAI API key configured")
            return None

        messages = [
            {"role": "system", "content": "You are the Viral B-Roll Extractor."},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        self.api_url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": 0.3,
                            "response_format": {"type": "json_object"},
                        },
                    )
                    resp.raise_for_status()

                result = resp.json()
                usage = result.get("usage", {})

                if job_id:
                    costs = get_cost_tracker().get_job_costs(job_id)
                    if costs:
                        costs.add_gpt4o_mini(
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                        )

                content = result["choices"][0]["message"]["content"]
                return json.loads(content)

            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("Invalid JSON from matcher, retrying")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": "Return valid JSON only.",
                    })
                    continue
                logger.error("Matcher JSON parse failed after retry")
                return None
            except Exception:
                logger.exception("Matcher API call failed")
                return None

        return None
