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
            segment, existing_needs, count, script_context
        )
        if not new_shots:
            logger.warning("No new shots generated for %s", seg_id)
            _emit_progress(job_id, seg_id, "error", "GPT-4o-mini could not generate a new visual idea")
            return

        for shot in new_shots:
            _emit_progress(job_id, seg_id, "generating",
                           f"New idea: \"{shot.visual_need}\"",
                           f"Queries: {', '.join(shot.search_queries[:3])}")

        searcher = SearcherService(pipeline_settings=pipeline_cfg)
        matcher = MatcherService(pipeline_settings=pipeline_cfg)
        transcriber = TranscriberService(pipeline_settings=pipeline_cfg)
        ranker = RankerService()
        timeout = pipeline_cfg.get("segment_timeout_sec", 300)
        max_concurrent = pipeline_cfg.get("max_concurrent_candidates", 3)

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
                           f"Found {len(cands)} candidate videos — fetching transcripts...")

            semaphore = asyncio.Semaphore(max_concurrent)
            matched: list = []
            match_done = 0

            async def _process(cand):
                nonlocal match_done
                async with semaphore:
                    try:
                        _emit_progress(job_id, seg_id, "transcripts",
                                       f"Fetching transcript for \"{cand.video_title[:50]}\"",
                                       f"https://youtube.com/watch?v={cand.video_id}")
                        transcript = await transcriber.get_transcript(
                            cand.video_id,
                            video_duration_seconds=cand.video_duration_seconds,
                            job_id=job_id,
                        )
                        _emit_progress(job_id, seg_id, "matching",
                                       f"Matching \"{cand.video_title[:50]}\" with Qwen3...",
                                       f"https://youtube.com/watch?v={cand.video_id}")
                        meta = {
                            "video_duration_seconds": cand.video_duration_seconds,
                            "video_title": cand.video_title,
                            "view_count": cand.view_count,
                            "transcript_source": transcript.transcript_source.value,
                        }
                        match_start = time.time()
                        match = await matcher.find_timestamp(
                            transcript.transcript_text, segment, meta, job_id,
                            script_context=script_context, shot=shot,
                        )
                        match_elapsed = time.time() - match_start
                        if matcher.context_matching_enabled:
                            match = matcher.validate_context_match(
                                match, cand.video_duration_seconds,
                            )
                        match_done += 1
                        conf = match.confidence_score
                        if conf > 0:
                            matched.append((cand, match))
                            _emit_progress(job_id, seg_id, "matching",
                                           f"Match found: \"{cand.video_title[:40]}\" — {conf:.0%} confidence ({match_elapsed:.1f}s)")
                        else:
                            _emit_progress(job_id, seg_id, "matching",
                                           f"No match in \"{cand.video_title[:40]}\" ({match_elapsed:.1f}s)")
                    except Exception:
                        match_done += 1
                        logger.exception("Match failed for %s", cand.video_id)
                        _emit_progress(job_id, seg_id, "matching",
                                       f"Error matching {cand.video_id}")

            _emit_progress(job_id, seg_id, "matching",
                           f"Running timestamp analysis on {len(cands)} videos...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*[_process(c) for c in cands]),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout matching expanded shot %s", shot.shot_id)
                _emit_progress(job_id, seg_id, "matching",
                               f"Timeout after {timeout}s — proceeding with {len(matched)} matches")

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


async def _generate_shots(
    segment: Segment,
    existing_needs: list[str],
    count: int,
    script_context: Optional[ScriptContext],
) -> list[BRollShot]:
    """Ask GPT-4o-mini for additional visual moments distinct from existing shots."""
    settings = get_settings()
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
        f'{{"shots": [{{"visual_need": "...", "search_queries": ["...", "..."], "key_terms": ["...", "..."]}}]}}'
    )

    existing_count = len(segment.broll_shots or [])

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
                        {"role": "system", "content": "You are a documentary B-roll planner. Return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()

        data = json.loads(resp.json()["choices"][0]["message"]["content"])
        raw_shots = data.get("shots", [])

        shots = []
        for i, s in enumerate(raw_shots[:count]):
            shot_num = existing_count + i + 1
            shots.append(BRollShot(
                shot_id=f"{segment.segment_id}_shot_{shot_num}",
                visual_need=s.get("visual_need", ""),
                search_queries=s.get("search_queries", []),
                key_terms=s.get("key_terms", []),
            ))
        return shots

    except Exception:
        logger.exception("Failed to generate expanded shots via LLM")
        return []
