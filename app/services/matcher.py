import json
import logging

import httpx

from app.config import get_settings, DEFAULTS
from app.models.schemas import BRollShot, ScriptContext, Segment, MatchResult, TranscriptSource
from app.utils.cost_tracker import get_cost_tracker
from app.utils import agent_queue

logger = logging.getLogger(__name__)

TIMESTAMP_PROMPT_TEMPLATE = """You are the Viral B-Roll Extractor finding B-roll for a specific documentary.

=== OVERALL DOCUMENTARY CONTEXT ===
This documentary is about: {script_topic}
Domain: {script_domain}
Geographic scope: {geographic_scope}
Time period: {temporal_scope}
This documentary is NOT about: {exclusion_context}

=== THIS SEGMENT ===
Segment summary: {summary}
Context anchor: {context_anchor}
Emotional tone: {emotional_tone}

=== SPECIFIC SHOT NEEDED ===
Visual need: {visual_need}
Key terms: {key_terms}
Shot context: {shot_context}

=== TERMS THAT INDICATE A WRONG MATCH ===
If the transcript contains these terms prominently, this video is likely NOT relevant: {negative_keywords}

Video duration: {video_duration} seconds (~{video_duration_min} minutes)

Captions (format is either "M:SS text" or "[M:SS → M:SS] text" where M is minutes, SS is seconds):
{transcript}

CRITICAL INSTRUCTIONS:
1. CONTEXT CHECK FIRST: Does this video actually discuss {script_topic} or a directly related topic? If the video is about a different region, different subject, or different context that merely shares similar keywords, return confidence_score: 0.0 and context_match: false immediately.
2. Check for CONTEXT MISMATCH:
   - Geographic mismatch: Video discusses a different location than {geographic_scope} → REJECT
   - Temporal mismatch: Video discusses a different time period → REJECT
   - Subject mismatch: Video discusses a tangentially related but different topic → REJECT
   - If unsure whether context matches, set confidence_score below 0.3
3. Only if the video passes the context check: find the PEAK VISUAL MOMENT — not just where the topic is mentioned, but the most visually compelling, high-retention footage.
4. Read the ENTIRE transcript. The best moment is almost never in the first 30 seconds.
5. Look for scenes with: cinematic footage, archival material, dramatic reveals, aerial/drone shots, on-location footage.
6. AVOID the intro (first 30s) and outro (last 30s).
7. The clip should be 15–90 seconds of continuous relevant content.
8. TIMESTAMP CONVERSION: 0:45 = 45s, 1:30 = 90s, 3:15 = 195s, 8:22 = 502s.
9. In relevance_note, explicitly state how the clip connects to {script_topic} — not just keyword overlap.

Return JSON only:
{{
  "start_time_seconds": int,
  "end_time_seconds": int,
  "excerpt": "relevant transcript text (max 200 words)",
  "confidence_score": float (0.0 to 1.0),
  "context_match": true/false,
  "context_mismatch_reason": "string or null — if false, explain why",
  "relevance_note": "must reference the overall documentary topic",
  "the_hook": "why this timestamp is VISUALLY compelling"
}}

If no relevant section exists or context doesn't match, return confidence_score: 0.0 and context_match: false."""


class MatcherService:
    """Finds peak visual moments in transcripts.

    Supports three backends controlled by the ``matcher_backend`` setting:
      - ``"auto"``  (default) — try local Ollama via companion, fall back to API
      - ``"local"`` — always use local Ollama, fail if unavailable
      - ``"api"``   — always use OpenAI API (original behaviour)
    """

    def __init__(self, pipeline_settings: dict | None = None):
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self._pipeline = pipeline_settings or {}

    def _get(self, key: str, fallback=None):
        if key in self._pipeline:
            return self._pipeline[key]
        return DEFAULTS.get(key, fallback)

    @property
    def context_matching_enabled(self) -> bool:
        return bool(self._get("enable_context_matching", True))

    async def find_timestamp(
        self,
        transcript_text: str | None,
        segment: Segment,
        video_metadata: dict,
        job_id: str | None = None,
        script_context: ScriptContext | None = None,
        shot: BRollShot | None = None,
    ) -> MatchResult:
        if not transcript_text:
            return MatchResult(
                confidence_score=0.0,
                source_flag=TranscriptSource.NONE,
            )

        special_instructions = self._get("special_instructions", "")
        max_words = 12000
        words = transcript_text.split()
        if len(words) > max_words:
            transcript_text = " ".join(words[:max_words])

        ctx = script_context or ScriptContext()
        video_duration = video_metadata.get("video_duration_seconds", 0) or 0

        if shot:
            visual_need = shot.visual_need
            key_terms = ", ".join(shot.key_terms) if shot.key_terms else ", ".join(segment.key_terms)
            shot_context = f"This is shot {shot.shot_id} — find footage matching THIS specific visual need, not the segment's general topic."
        else:
            visual_need = segment.visual_need
            key_terms = ", ".join(segment.key_terms)
            shot_context = "Match the segment's overall visual need."

        prompt = TIMESTAMP_PROMPT_TEMPLATE.format(
            script_topic=ctx.script_topic or "general documentary",
            script_domain=ctx.script_domain or "general",
            geographic_scope=ctx.geographic_scope or "not specified",
            temporal_scope=ctx.temporal_scope or "not specified",
            exclusion_context=ctx.exclusion_context or "none specified",
            summary=segment.summary,
            context_anchor=segment.context_anchor or segment.summary,
            visual_need=visual_need,
            emotional_tone=segment.emotional_tone,
            key_terms=key_terms,
            shot_context=shot_context,
            negative_keywords=", ".join(segment.negative_keywords) if segment.negative_keywords else "none",
            transcript=transcript_text,
            video_duration=video_duration,
            video_duration_min=round(video_duration / 60, 1),
        )
        if special_instructions:
            prompt += f"\n\nAdditional instructions:\n{special_instructions}"

        source_str = video_metadata.get("transcript_source", "no_transcript")
        try:
            source_flag = TranscriptSource(source_str)
        except ValueError:
            source_flag = TranscriptSource.NONE

        backend = self._get("matcher_backend", "auto")
        parsed = await self._route_call(prompt, backend, job_id)

        if parsed is None:
            return MatchResult(confidence_score=0.0, source_flag=source_flag)

        actual_source = parsed.pop("_matcher_source", None)

        ctx_match = parsed.get("context_match", True)
        ctx_reason = parsed.get("context_mismatch_reason")

        if ctx_match is False:
            logger.info(
                "Context mismatch rejected: %s — %s",
                video_metadata.get("video_title", "")[:60],
                ctx_reason or "unknown",
            )
            return MatchResult(
                confidence_score=0.0,
                source_flag=source_flag,
                context_match=False,
                context_mismatch_reason=ctx_reason,
                context_match_valid=False,
                matcher_source=actual_source,
            )

        excerpt = parsed.get("excerpt", "")
        max_excerpt = self._get("transcript_excerpt_max_words", 200)
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
            context_match=True,
            context_mismatch_reason=None,
            matcher_source=actual_source,
        )

    def validate_context_match(
        self, match: MatchResult, video_duration_seconds: int
    ) -> MatchResult:
        start = match.start_time_seconds
        end = match.end_time_seconds

        cap_end = self._get("cap_end_timestamp", True)
        verify_end_screen = self._get("verify_timestamp_not_end_screen", True)

        if start is not None and start >= video_duration_seconds:
            match.context_match_valid = False
            return match

        if end is not None and end > video_duration_seconds:
            if cap_end:
                match.end_time_seconds = max(0, video_duration_seconds - 5)
            end = match.end_time_seconds

        if start is not None and end is not None and (end - start) < 10:
            match.confidence_score = max(0.0, match.confidence_score - 0.1)

        if verify_end_screen and start is not None and video_duration_seconds > 0:
            if start > video_duration_seconds - 30:
                match.confidence_score = max(0.0, match.confidence_score - 0.3)

        if start is not None and start < 15 and video_duration_seconds > 120:
            logger.warning(
                "Suspiciously early timestamp %ds for %d-second video — likely intro, penalizing",
                start, video_duration_seconds,
            )
            match.confidence_score = max(0.0, match.confidence_score - 0.15)

        return match

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _route_call(
        self, prompt: str, backend: str, job_id: str | None,
    ) -> dict | None:
        """Route timestamp matching to local Ollama or OpenAI API."""
        matcher_model = self._get("matcher_model", "qwen3:8b")

        if backend == "local":
            result = await self._call_local(prompt, job_id)
            if result:
                result["_matcher_source"] = f"Ollama/{matcher_model}"
            return result

        if backend == "api":
            model = self._get("timestamp_model", "gpt-4o-mini")
            result = await self._call_api(prompt, model, job_id)
            if result:
                result["_matcher_source"] = model
            return result

        # "auto" — try local first, optionally fall back to API
        if agent_queue.is_agent_available():
            try:
                result = await self._call_local(prompt, job_id)
                if result and result.get("context_match") is False:
                    result["_matcher_source"] = f"Ollama/{matcher_model}"
                    return result
                if result and result.get("confidence_score", 0) > 0:
                    result["_matcher_source"] = f"Ollama/{matcher_model}"
                    return result
                if result and result.get("matcher_source") == "local_unavailable":
                    logger.info("Local model unavailable")
                else:
                    logger.info("Local model returned zero confidence")
            except Exception:
                logger.warning("Local matcher failed")

        api_fallback = self._get("api_fallback_enabled", False)
        if not api_fallback:
            logger.info("API fallback disabled — returning no match (enable api_fallback_enabled in settings to use GPT-4o-mini)")
            return None

        model = self._get("timestamp_model", "gpt-4o-mini")
        logger.info("Falling back to API model %s", model)
        result = await self._call_api(prompt, model, job_id)
        if result:
            result["_matcher_source"] = model
        return result

    # ------------------------------------------------------------------
    # Local Ollama via companion agent
    # ------------------------------------------------------------------

    async def _call_local(
        self, prompt: str, job_id: str | None,
    ) -> dict | None:
        if not agent_queue.is_agent_available():
            logger.info("No agent available for local matching")
            return {"confidence_score": 0, "matcher_source": "local_unavailable"}

        matcher_model = self._get("matcher_model", "qwen3:8b")
        task_id = await agent_queue.create_task("match_timestamp", {
            "prompt": prompt,
            "model": matcher_model,
        })
        results = await agent_queue.wait_for_result(task_id, timeout=120)
        if not results:
            logger.warning("Local match task timed out or returned empty")
            return None

        parsed = results[0]
        source = parsed.get("matcher_source", "local")

        if source in ("local_unavailable", "local_error"):
            return parsed

        if job_id:
            costs = get_cost_tracker().get_job_costs(job_id)
            if costs:
                latency = parsed.get("matcher_latency_ms", 0)
                costs.add_local_match(latency)

        logger.info(
            "Local match: confidence=%.2f model=%s latency=%dms",
            parsed.get("confidence_score", 0),
            parsed.get("matcher_model", matcher_model),
            parsed.get("matcher_latency_ms", 0),
        )
        return parsed

    # ------------------------------------------------------------------
    # OpenAI API (original path)
    # ------------------------------------------------------------------

    async def _call_api(
        self, prompt: str, model: str, job_id: str | None,
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
