import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from app.config import DEFAULTS, get_settings
from app.models.schemas import BRollShot, RankedResult, ScriptContext, Segment
from app.services.matcher import MatcherService
from app.services.ranker import RankerService
from app.services.searcher import SearcherService
from app.services.settings_service import get_settings_service
from app.services.storage import get_storage
from app.services.transcriber import TranscriberService

logger = logging.getLogger(__name__)

_expand_progress: dict[str, dict] = {}


def _progress_key(job_id: str, segment_id: str) -> str:
    return f"{job_id}:{segment_id}"


def get_expand_progress(job_id: str, segment_id: str) -> dict | None:
    return _expand_progress.get(_progress_key(job_id, segment_id))


def _emit_progress(job_id: str, segment_id: str, phase: str, message: str, detail: str | None = None):
    key = _progress_key(job_id, segment_id)
    entry = {"time": time.strftime("%H:%M:%S"), "phase": phase, "message": message}
    if detail:
        entry["detail"] = detail
    if key not in _expand_progress:
        _expand_progress[key] = {"phase": phase, "log": [], "started_at": time.time()}
    _expand_progress[key]["phase"] = phase
    _expand_progress[key]["log"].append(entry)


async def expand_shots_for_segment(
    job_id: str,
    segment: Segment,
    count: int = 1,
    script_context: Optional[ScriptContext] = None,
) -> None:
    """Generate new B-roll shots for a segment and run the full search/match/rank pipeline."""
    seg_id = segment.segment_id
    key = _progress_key(job_id, seg_id)
    _expand_progress[key] = {"phase": "generating", "log": [], "started_at": time.time()}

    try:
        _emit_progress(job_id, seg_id, "generating", f"Asking GPT-4o-mini for {count} new visual idea(s)...")

        settings_svc = get_settings_service()
        pipeline_cfg = await settings_svc.get_all_settings()
        storage = get_storage()

        existing_needs = [
            s.visual_need for s in (segment.broll_shots or [])
        ]
        if hasattr(segment, "results"):
            existing_needs += [
                r.shot_visual_need for r in segment.results if r.shot_visual_need
            ]

        new_shots = await _generate_shots(
            segment, existing_needs, count, script_context, job_id=job_id,
        )
        if not new_shots:
            logger.warning("No new shots generated for %s", seg_id)
            _emit_progress(job_id, seg_id, "error", "GPT-4o-mini could not generate a new visual idea")
            return

        for shot in new_shots:
            _emit_progress(job_id, seg_id, "generating",
                           f"New idea: \"{shot.visual_need}\"",
                           f"Queries: {', '.join(shot.search_queries[:3])}")

        expand_cfg = dict(pipeline_cfg)
        expand_cfg["max_candidates_per_shot"] = 5
        expand_cfg["youtube_results_per_query"] = 5

        searcher = SearcherService(pipeline_settings=expand_cfg)
        matcher = MatcherService(pipeline_settings=expand_cfg)
        transcriber = TranscriberService(pipeline_settings=expand_cfg)
        ranker = RankerService()
        match_timeout = 90
        good_enough_confidence = 0.5

        new_results: list[RankedResult] = []
        for shot in new_shots:
            _emit_progress(job_id, seg_id, "searching",
                           f"Searching YouTube for \"{shot.visual_need[:60]}\"...")
            try:
                cands = await searcher.search_for_shot(
                    shot, segment, job_id=job_id, script_context=script_context,
                )
            except Exception:
                logger.exception("Search failed for expanded shot %s", shot.shot_id)
                _emit_progress(job_id, seg_id, "searching", f"Search failed for {shot.shot_id}")
                continue

            if not cands:
                _emit_progress(job_id, seg_id, "searching", "No candidate videos found")
                continue

            _emit_progress(job_id, seg_id, "transcripts",
                           f"Found {len(cands)} candidates — fetching transcripts (cached first)...")

            # Fetch all transcripts first, prioritizing cached ones
            cand_transcripts: list[tuple] = []
            for cand in cands:
                try:
                    _emit_progress(job_id, seg_id, "transcripts",
                                   f"Fetching transcript for \"{cand.video_title[:50]}\"",
                                   f"https://youtube.com/watch?v={cand.video_id}")
                    transcript = await transcriber.get_transcript(
                        cand.video_id,
                        video_duration_seconds=cand.video_duration_seconds,
                        job_id=job_id,
                    )
                    if transcript and transcript.transcript_text:
                        cand_transcripts.append((cand, transcript))
                except Exception:
                    logger.debug("Transcript failed for %s", cand.video_id)

            if not cand_transcripts:
                _emit_progress(job_id, seg_id, "transcripts", "No transcripts available for any candidate")
                continue

            _emit_progress(job_id, seg_id, "matching",
                           f"Matching {len(cand_transcripts)} videos sequentially (early exit on good match)...")

            # Sequential matching with early exit — stop when we find a strong match
            matched: list = []
            best_so_far = 0.0
            for i, (cand, transcript) in enumerate(cand_transcripts):
                _emit_progress(job_id, seg_id, "matching",
                               f"[{i+1}/{len(cand_transcripts)}] Matching \"{cand.video_title[:45]}\"...",
                               f"https://youtube.com/watch?v={cand.video_id}")
                try:
                    meta = {
                        "video_duration_seconds": cand.video_duration_seconds,
                        "video_title": cand.video_title,
                        "view_count": cand.view_count,
                        "transcript_source": transcript.transcript_source.value,
                    }
                    match_start = time.time()
                    match = await asyncio.wait_for(
                        matcher.find_timestamp(
                            transcript.transcript_text, segment, meta, job_id,
                            script_context=script_context, shot=shot,
                        ),
                        timeout=match_timeout,
                    )
                    match_elapsed = time.time() - match_start
                    if matcher.context_matching_enabled:
                        match = matcher.validate_context_match(
                            match, cand.video_duration_seconds,
                        )
                    conf = match.confidence_score
                    if conf > 0:
                        matched.append((cand, match))
                        if conf > best_so_far:
                            best_so_far = conf
                        _emit_progress(job_id, seg_id, "matching",
                                       f"Match: \"{cand.video_title[:40]}\" — {conf:.0%} ({match_elapsed:.1f}s)")
                        if conf >= good_enough_confidence:
                            _emit_progress(job_id, seg_id, "matching",
                                           f"Strong match found ({conf:.0%}) — skipping remaining candidates")
                            break
                    else:
                        _emit_progress(job_id, seg_id, "matching",
                                       f"No match in \"{cand.video_title[:40]}\" ({match_elapsed:.1f}s)")
                except asyncio.TimeoutError:
                    _emit_progress(job_id, seg_id, "matching",
                                   f"Timeout on \"{cand.video_title[:40]}\" (>{match_timeout}s) — skipping")
                except Exception:
                    logger.exception("Match failed for %s", cand.video_id)
                    _emit_progress(job_id, seg_id, "matching",
                                   f"Error matching {cand.video_id}")

            _emit_progress(job_id, seg_id, "ranking",
                           f"Ranking {len(matched)} matched candidates...")
            ranked = ranker.rank_and_filter(
                matched, segment, settings=pipeline_cfg,
                script_context=script_context, shot=shot,
            )
            if ranked:
                new_results.extend(ranked[:1])
                best = ranked[0]
                _emit_progress(job_id, seg_id, "ranking",
                               f"Best clip: \"{best.video_title[:50]}\" — {best.relevance_score:.0%} relevance",
                               f"https://youtube.com/watch?v={best.video_id}")

        if new_results:
            await storage.store_results(job_id, new_results)
            elapsed = time.time() - _expand_progress[key]["started_at"]
            _emit_progress(job_id, seg_id, "done",
                           f"Added {len(new_results)} new clip(s) in {elapsed:.0f}s")
            logger.info(
                "Expanded %d shots for segment %s in job %s",
                len(new_results), seg_id, job_id,
            )
        else:
            elapsed = time.time() - _expand_progress[key]["started_at"]
            _emit_progress(job_id, seg_id, "done",
                           f"No matching clips found after {elapsed:.0f}s — try again for a different idea")

    except Exception:
        logger.exception(
            "Failed to expand shots for segment %s in job %s",
            seg_id, job_id,
        )
        _emit_progress(job_id, seg_id, "error", "Pipeline error — check server logs")


async def _lightweight_llm_call(
    prompt: str,
    system_prompt: str = "Return valid JSON only.",
    job_id: str | None = None,
) -> dict | None:
    """Route a lightweight JSON-returning LLM call based on the `lightweight_model` setting.
    Returns parsed JSON dict, or None on failure."""
    settings_svc = get_settings_service()
    pipeline_settings = await settings_svc.get_all_settings()
    lightweight_model = pipeline_settings.get("lightweight_model", "gpt-4o-mini")

    if lightweight_model == "ollama":
        return await _lightweight_via_ollama(
            prompt, system_prompt, pipeline_settings, job_id=job_id,
        )
    else:
        return await _lightweight_via_openai(prompt, system_prompt)


async def _lightweight_via_openai(prompt: str, system_prompt: str) -> dict | None:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception:
        logger.exception("OpenAI lightweight call failed")
        return None


async def _lightweight_via_ollama(
    prompt: str, system_prompt: str, pipeline_settings: dict,
    job_id: str | None = None,
) -> dict | None:
    from app.utils import agent_queue

    if not agent_queue.is_agent_available():
        logger.warning("Companion not available for lightweight LLM — falling back to OpenAI")
        return await _lightweight_via_openai(prompt, system_prompt)

    matcher_model = pipeline_settings.get("matcher_model", "qwen3:8b")
    task_id = await agent_queue.create_task("lightweight_llm", {
        "prompt": prompt,
        "system_prompt": system_prompt + " /nothink",
        "model": matcher_model,
    }, job_id=job_id)
    results = await agent_queue.wait_for_result(task_id, timeout=60)
    if not results:
        logger.warning("Ollama lightweight call timed out — falling back to OpenAI")
        return await _lightweight_via_openai(prompt, system_prompt)

    result = results[0]
    if result.get("error"):
        logger.warning("Ollama lightweight call failed: %s — falling back to OpenAI", result["error"])
        return await _lightweight_via_openai(prompt, system_prompt)

    return result.get("result")


async def _generate_shots(
    segment: Segment,
    existing_needs: list[str],
    count: int,
    script_context: Optional[ScriptContext],
    job_id: str | None = None,
) -> list[BRollShot]:
    """Ask LLM for additional visual moments distinct from existing shots."""
    existing_list = "\n".join(f"- {n}" for n in existing_needs) if existing_needs else "(none)"
    topic = script_context.script_topic if script_context else "unknown"
    geo = script_context.geographic_scope if script_context else ""

    prompt = (
        f"You are a documentary B-roll planner.\n\n"
        f"Documentary topic: {topic}\n"
        f"Geographic scope: {geo}\n\n"
        f"Segment: \"{segment.title}\"\n"
        f"Summary: {segment.summary}\n"
        f"Duration: {segment.estimated_duration_seconds}s\n\n"
        f"Existing B-roll shots already assigned:\n{existing_list}\n\n"
        f"Suggest {count} additional DISTINCT visual moment(s) for this segment that are "
        f"genuinely different from the existing shots. Each must be a specific, "
        f"searchable visual — not a vague concept.\n\n"
        f"Return JSON only:\n"
        f'{{"shots": [{{"visual_need": "...", "visual_description": "describe what the footage LOOKS like - camera angle, motion, lighting", '
        f'"shot_intent": "literal|illustrative|atmospheric", "scarcity": "common|medium|rare", '
        f'"preferred_source_type": "documentary|news_clip|stock_footage|drone_aerial|interview|timelapse|archival|animation|", '
        f'"search_queries": ["...", "...", "...", "...", "..."], "key_terms": ["...", "..."]}}]}}'
    )

    existing_count = len(segment.broll_shots or [])

    try:
        data = await _lightweight_llm_call(
            prompt, "You are a documentary B-roll planner. Return valid JSON only.",
            job_id=job_id,
        )
        if not data:
            return []

        raw_shots = data.get("shots", [])
        shots = []
        for i, s in enumerate(raw_shots[:count]):
            shot_num = existing_count + i + 1
            shots.append(BRollShot(
                shot_id=f"{segment.segment_id}_shot_{shot_num}",
                visual_need=s.get("visual_need", ""),
                visual_description=s.get("visual_description", ""),
                search_queries=s.get("search_queries", []),
                key_terms=s.get("key_terms", []),
                shot_intent=s.get("shot_intent", "literal"),
                scarcity=s.get("scarcity", "common"),
                preferred_source_type=s.get("preferred_source_type", ""),
            ))
        return shots

    except Exception:
        logger.exception("Failed to generate expanded shots via LLM")
        return []


async def _generate_alternative_queries(
    shot: BRollShot,
    script_context: Optional[ScriptContext],
) -> list[str]:
    """Generate 5 alternative search queries for a shot that got low-confidence results."""
    topic = script_context.script_topic if script_context else "unknown"
    geo = script_context.geographic_scope if script_context else ""
    existing_queries = "\n".join(f"- {q}" for q in shot.search_queries)

    prompt = (
        f"A YouTube search for documentary B-roll footage returned poor results.\n\n"
        f"Documentary topic: {topic}\n"
        f"Geographic scope: {geo}\n"
        f"Visual need: {shot.visual_need}\n\n"
        f"Original search queries that FAILED to find good results:\n{existing_queries}\n\n"
        f"Generate 5 ALTERNATIVE YouTube search queries that might find this footage.\n"
        f"Each query must be substantially different from the originals — different phrasing, "
        f"different angles, synonyms, related concepts. Think about what a human would try "
        f"after the first search didn't work.\n\n"
        f"Rules:\n"
        f"- Each query must include at least one term related to \"{topic}\"\n"
        f"- Try: synonyms, broader categories, related events, different languages of the location name\n"
        f"- Try: adding \"footage\", \"documentary\", \"drone\", \"4K\", \"stock footage\"\n"
        f"- Try: the concept without the specific location, or the location without the specific concept\n\n"
        f"Return JSON only: {{\"queries\": [\"...\", \"...\", \"...\", \"...\", \"...\"]}}"
    )

    try:
        data = await _lightweight_llm_call(prompt)
        if not data:
            return []

        queries = data.get("queries", [])
        return [q for q in queries if isinstance(q, str) and q.strip()][:5]

    except Exception:
        logger.exception("Failed to generate alternative queries via LLM")
        return []
