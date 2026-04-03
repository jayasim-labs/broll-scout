import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.models.schemas import (
    BRollShot, CandidateVideo, JobStatus, MatchResult, RankedResult, ScriptContext, Segment,
)
from app.services.matcher import MatcherService
from app.services.ranker import RankerService
from app.services.searcher import SearcherService
from app.services.settings_service import get_settings_service
from app.services.storage import get_storage
from app.services.transcriber import TranscriberService
from app.services.translator import TranslatorService
from app.utils.cost_tracker import get_cost_tracker
from app.services.usage_service import get_usage_service

logger = logging.getLogger(__name__)

_progress: Dict[str, dict] = {}


def get_job_progress(job_id: str) -> Optional[dict]:
    return _progress.get(job_id)


def _set_progress(job_id: str, stage: str, percent: int, message: str) -> None:
    existing = _progress.get(job_id, {})
    _progress[job_id] = {
        "stage": stage,
        "percent_complete": min(100, max(0, percent)),
        "message": message,
        "activity_log": existing.get("activity_log", []),
    }


def _log_activity(
    job_id: str, icon: str, text: str,
    depth: int = 0, group: str = "",
) -> None:
    existing = _progress.get(job_id, {})
    log = existing.get("activity_log", [])
    entry: dict = {
        "time": datetime.utcnow().isoformat() + "Z",
        "icon": icon,
        "text": text,
    }
    if depth:
        entry["depth"] = depth
    if group:
        entry["group"] = group
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    existing["activity_log"] = log
    _progress[job_id] = existing


async def run_pipeline(
    job_id: str,
    script: str,
    editor_id: str = "default_editor",
    enable_gemini_expansion: bool = False,
    project_id: str | None = None,
    title: str | None = None,
    category: str | None = None,
) -> None:
    """Full pipeline: translate -> search -> match -> rank -> store."""
    storage = get_storage()
    cost_tracker = get_cost_tracker()

    settings_svc = get_settings_service()
    pipeline_cfg = await settings_svc.get_all_settings()
    pipeline_cfg["enable_gemini_expansion"] = enable_gemini_expansion

    start_time = time.time()
    script_hash = hashlib.sha256(script.encode()).hexdigest()[:16]

    cost_tracker.start_job(job_id)
    await storage.create_job(job_id, script_hash, editor_id, project_id=project_id, title=title, category=category)

    try:
        # --- Stage 1: Translation ---
        _set_progress(job_id, "translating", 5, "Translating and segmenting script...")


        word_count = len(script.split())
        script_duration = max(1, round(word_count / 100))

        async def _translator_progress(icon: str, text: str):
            _log_activity(job_id, icon, text, depth=1, group="translate")
    

        translator = TranslatorService()
        segments, english_translation, script_context = await translator.translate_and_segment(
            script, job_id, on_progress=_translator_progress,
        )

        translation_model = pipeline_cfg.get("translation_model", "gpt-4o")
        total_shots = sum(seg.broll_count for seg in segments)
        no_broll_segs = sum(1 for seg in segments if seg.broll_count == 0)
        _log_activity(job_id, "brain", f"Translation done via {translation_model}! Your ~{script_duration}-minute script → {len(segments)} natural segments, {total_shots} B-roll shots needed ({no_broll_segs} host-on-camera segments)", group="translate")
        if script_context.script_topic:
            _log_activity(job_id, "shield", f"Context anchoring: \"{script_context.script_topic}\" — will reject clips about unrelated topics", depth=1, group="translate")
        for i, seg in enumerate(segments, 1):
            shot_note = f" ({seg.broll_count} shots)" if seg.broll_count > 1 else (" (no B-roll)" if seg.broll_count == 0 else "")
            _log_activity(job_id, "sparkles", f"Scene {i}: \"{seg.title}\"{shot_note} — {seg.visual_need}", depth=1, group="translate")

        await storage.store_segments(job_id, segments)
        await storage.update_job_status(
            job_id, JobStatus.PROCESSING,
            segment_count=len(segments),
            script_duration_minutes=script_duration,
            english_translation=english_translation,
            script_context=script_context.model_dump() if script_context.script_topic else None,
        )

        # --- Stage 2: Searching ---
        est_search_sec = len(segments) * 8
        est_min = est_search_sec // 60
        est_sec = est_search_sec % 60
        est_str = f"{est_min}m {est_sec}s" if est_min else f"{est_sec}s"
        _set_progress(job_id, "searching", 20, f"Searching YouTube & yt-dlp for B-roll clips...")
        _log_activity(job_id, "search", f"Now hunting for B-roll videos for all {len(segments)} scenes (estimated ~{est_str})", group="search")
        sources = "preferred channels → yt-dlp"
        if enable_gemini_expansion:
            sources += " → Gemini AI creative expansion"
        _log_activity(job_id, "clock", f"For each scene, I search: {sources}", depth=1, group="search")

        searcher = SearcherService(pipeline_settings=pipeline_cfg)

        search_start = time.time()

        async def search_progress(current: int, total: int, msg: str):
            pct = 20 + int(30 * current / max(total, 1))
            elapsed_s = time.time() - search_start
            if current > 0:
                per_seg = elapsed_s / current
                remaining = int(per_seg * (total - current))
                time_note = f" (~{remaining}s remaining)" if remaining > 0 else ""
            else:
                time_note = ""
            _set_progress(job_id, "searching", pct, f"{msg}{time_note}")

        async def search_activity(icon: str, text: str, depth: int = 2):
            _log_activity(job_id, icon, text, depth=depth, group="search")

        candidates_by_segment = await searcher.search_batch(
            segments, job_id=job_id, progress_callback=search_progress,
            on_activity=search_activity, script_context=script_context,
        )

        search_elapsed = round(time.time() - search_start, 1)
        total_candidates = sum(len(v) for v in candidates_by_segment.values())
        empty_segments = sum(1 for v in candidates_by_segment.values() if not v)
        _log_activity(job_id, "check", f"Search done in {search_elapsed}s! Found {total_candidates} potential B-roll videos across all {len(segments)} scenes", group="search")
        if empty_segments:
            _log_activity(job_id, "alert", f"{empty_segments} of {len(segments)} scenes had no candidate videos from search", depth=1, group="search")
        logger.info("Job %s search: %d candidates, %d empty segments", job_id, total_candidates, empty_segments)

        # --- Retry search until we have enough candidates (proportional to script length) ---
        # 1 candidate per 100 words: 3000 words → 30 target, 1500 words → 15 target
        min_target = max(10, round(word_count / 100))
        max_retries = 3
        retry_round = 0
        if total_candidates < min_target:
            _log_activity(job_id, "alert", f"Your ~{word_count}-word script needs at least {min_target} candidate videos, but only {total_candidates} found so far — retrying sparse scenes", group="search")

        while total_candidates < min_target and retry_round < max_retries:
            retry_round += 1
            sparse_segments = [
                seg for seg in segments
                if len(candidates_by_segment.get(seg.segment_id, [])) < 3
            ]
            if not sparse_segments:
                break

            _log_activity(job_id, "search", f"Retry round {retry_round}: re-searching {len(sparse_segments)} scenes that have <3 candidates", depth=1, group="search")
            _set_progress(job_id, "searching", 20 + retry_round * 5, f"Retry search round {retry_round} for sparse scenes...")

            retry_results = await searcher.search_batch(
                sparse_segments, job_id=job_id, progress_callback=search_progress,
                on_activity=search_activity, script_context=script_context,
            )
            for seg_id, new_cands in retry_results.items():
                existing = candidates_by_segment.get(seg_id, [])
                existing_ids = {c.video_id for c in existing}
                for c in new_cands:
                    if c.video_id not in existing_ids:
                        existing.append(c)
                        existing_ids.add(c.video_id)
                candidates_by_segment[seg_id] = existing

            total_candidates = sum(len(v) for v in candidates_by_segment.values())
            _log_activity(job_id, "check", f"After retry {retry_round}: {total_candidates} total candidates across {len(segments)} scenes", depth=1, group="search")
            logger.info("Job %s retry %d: %d total candidates", job_id, retry_round, total_candidates)


        # --- Stage 3: Matching (shot-level) ---
        _set_progress(job_id, "matching", 55, "Watching videos and finding the exact moments you need...")
        matcher_backend = pipeline_cfg.get("matcher_backend", "auto")
        matcher_model = pipeline_cfg.get("matcher_model", "qwen3:8b")
        if matcher_backend == "api":
            match_label = pipeline_cfg.get("timestamp_model", "gpt-4o-mini")
        elif matcher_backend == "local":
            match_label = f"Ollama/{matcher_model}"
        else:
            match_label = f"Ollama/{matcher_model} (API fallback)"

        active_segments = [seg for seg in segments if seg.broll_count > 0]
        skipped_segments = [seg for seg in segments if seg.broll_count == 0]
        total_active_shots = sum(seg.broll_count for seg in active_segments)

        _log_activity(job_id, "eye", f"Matching {total_active_shots} shots across {len(active_segments)} segments ({len(skipped_segments)} host-on-camera segments skipped)", group="match")
        _log_activity(job_id, "clock", f"For each shot: search → fetch transcript → {match_label} context check + timestamp → rank", depth=1, group="match")

        matcher = MatcherService(pipeline_settings=pipeline_cfg)
        transcriber = TranscriberService()
        ranker = RankerService()

        max_concurrent_candidates = pipeline_cfg.get("max_concurrent_candidates", 3)
        segment_timeout = pipeline_cfg.get("segment_timeout_sec", 300)

        all_segment_results: Dict[str, List[RankedResult]] = {}
        total_segments = len(active_segments)
        match_start = time.time()
        shots_processed = 0

        for seg_idx, segment in enumerate(active_segments):
            pct = 55 + int(35 * seg_idx / max(total_segments, 1))
            if seg_idx > 0:
                per_seg = (time.time() - match_start) / seg_idx
                remaining = int(per_seg * (total_segments - seg_idx))
                time_note = f" (~{remaining}s remaining)"
            else:
                time_note = ""
            _set_progress(
                job_id, "matching", pct,
                f"Segment {seg_idx + 1}/{total_segments}: \"{segment.title}\" ({segment.broll_count} shots){time_note}",
            )
            seg_group = f"match-{segment.segment_id}"
            _log_activity(job_id, "zap", f"Segment {seg_idx + 1}/{total_segments}: \"{segment.title}\" — {segment.broll_count} B-roll shots needed", depth=1, group="match")

            seg_ranked: List[RankedResult] = []
            shots_to_process = segment.broll_shots if segment.broll_shots else []

            if not shots_to_process:
                fallback_shot = BRollShot(
                    shot_id=f"{segment.segment_id}_shot_1",
                    visual_need=segment.visual_need,
                    search_queries=segment.search_queries,
                    key_terms=segment.key_terms,
                )
                shots_to_process = [fallback_shot]

            for shot_idx, shot in enumerate(shots_to_process):
                short_need = shot.visual_need[:55]
                _log_activity(job_id, "search", f"  Shot {shot_idx + 1}/{len(shots_to_process)}: \"{short_need}\"", depth=2, group=seg_group)

                cands = candidates_by_segment.get(segment.segment_id, [])
                shot_cands = cands

                if shot.search_queries:
                    try:
                        shot_cands_new = await searcher.search_for_shot(
                            shot, segment, job_id=job_id,
                            on_progress=lambda icon, text, depth=2: _log_activity(job_id, icon, text, depth=depth, group=seg_group),
                            script_context=script_context,
                        )
                        if shot_cands_new:
                            existing_ids = {c.video_id for c in cands}
                            for c in shot_cands_new:
                                if c.video_id not in existing_ids:
                                    cands.append(c)
                                    existing_ids.add(c.video_id)
                            shot_cands = shot_cands_new + [c for c in cands if c.video_id not in {sc.video_id for sc in shot_cands_new}]
                    except Exception:
                        logger.warning("Shot-specific search failed for %s, using segment candidates", shot.shot_id)

                if not shot_cands:
                    _log_activity(job_id, "alert", f"  No candidates for shot: \"{short_need}\"", depth=2, group=seg_group)
                    continue

                async def _match_activity(icon: str, text: str):
                    _log_activity(job_id, icon, text, depth=3, group=seg_group)

                try:
                    matched = await asyncio.wait_for(
                        _match_candidates(
                            shot_cands, segment, matcher, transcriber, job_id,
                            max_concurrent_candidates,
                            on_activity=_match_activity,
                            script_context=script_context,
                            shot=shot,
                        ),
                        timeout=segment_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Shot %s timed out", shot.shot_id)
                    matched = []

                ranked = ranker.rank_and_filter(matched, segment, settings=pipeline_cfg, script_context=script_context, shot=shot)
                if ranked:
                    _log_activity(job_id, "check", f"  ✓ Found clip for \"{short_need}\": \"{ranked[0].video_title[:50]}\" ({ranked[0].confidence_score:.0%})", depth=2, group=seg_group)
                    seg_ranked.extend(ranked[:1])
                else:
                    _log_activity(job_id, "alert", f"  ✗ No clip found for \"{short_need}\"", depth=2, group=seg_group)

                shots_processed += 1

            all_segment_results[segment.segment_id] = seg_ranked

        for seg in skipped_segments:
            all_segment_results[seg.segment_id] = []

        # --- Cross-segment dedup ---
        _log_activity(job_id, "shield", "Removing duplicate clips across segments", group="rank")
        all_segment_results = ranker.deduplicate_across_segments(all_segment_results)

        all_results: List[RankedResult] = []
        for results in all_segment_results.values():
            all_results.extend(results)

        # --- Context audit: single LLM call to flag outlier clips ---
        if script_context.script_topic and all_results:
            try:
                _log_activity(job_id, "shield", "Running context audit — checking all clips fit the documentary's theme", group="rank")
                all_results, flagged_count = await _audit_context(
                    all_results, script_context, matcher, job_id,
                )
                if flagged_count:
                    _log_activity(job_id, "alert", f"Context audit removed {flagged_count} clips that didn't match \"{script_context.script_topic}\"", depth=1, group="rank")
                else:
                    _log_activity(job_id, "check", "Context audit passed — all clips are contextually appropriate", depth=1, group="rank")
            except Exception:
                logger.warning("Context audit failed, keeping all results")

        # --- Validate shot coverage (quality check, not quantity enforcement) ---
        warnings = _validate_shot_coverage(segments, pipeline_cfg)
        coverage_assessment = _build_coverage_assessment(
            segments, all_results, script_duration, warnings,
        )
        if warnings:
            for w in warnings:
                _log_activity(job_id, "alert", w["message"], depth=1, group="rank")

        # --- Recovery search (only for segments with zero results) ---
        low_threshold = max(3, len(active_segments) // 3)
        if len(all_results) < low_threshold:
            logger.info(
                "Only %d results (below threshold %d), running recovery",
                len(all_results), low_threshold,
            )
            empty_segments = [
                seg for seg in segments
                if not all_segment_results.get(seg.segment_id)
            ]
            if empty_segments:
                _set_progress(job_id, "matching", 92, "Not enough clips found — running a broader search...")
                _log_activity(job_id, "alert", f"Only {len(all_results)} clips found so far — I need more. Re-searching {len(empty_segments)} scenes with broader, more creative queries", depth=1, group="rank")
                recovery = await searcher.search_batch(
                    empty_segments, job_id=job_id, script_context=script_context,
                )
                for seg in empty_segments:
                    new_cands = recovery.get(seg.segment_id, [])
                    if new_cands:
                        _log_activity(job_id, "search", f"Broader search found {len(new_cands)} new videos for \"{seg.title}\"", depth=2, group="rank")
                        try:
                            matched = await asyncio.wait_for(
                                _match_candidates(
                                    new_cands, seg, matcher, transcriber, job_id,
                                    max_concurrent_candidates,
                                    script_context=script_context,
                                ),
                                timeout=segment_timeout,
                            )
                            ranked = ranker.rank_and_filter(matched, seg, settings=pipeline_cfg, script_context=script_context)
                            all_segment_results[seg.segment_id] = ranked
                            all_results.extend(ranked)
                        except asyncio.TimeoutError:
                            pass

        shots_filled = len(all_results)
        shots_per_min = round(shots_filled / max(script_duration, 1), 2)

        # --- Stage 4: Storing ---
        _set_progress(job_id, "ranking", 95, "Saving your results...")
        if all_results:
            _log_activity(job_id, "check", f"All done! {shots_filled} B-roll clips found for {total_active_shots} shots across {len(active_segments)} segments ({shots_per_min} clips/min)", group="rank")
        else:
            _log_activity(job_id, "alert", "No clips found. Ensure the companion app is running so yt-dlp can search and Whisper can transcribe audio locally.", group="rank")
        await storage.store_results(job_id, all_results, category=category)

        elapsed = round(time.time() - start_time, 2)
        api_costs = cost_tracker.end_job(job_id) or {}
        api_costs["search_mode"] = "ytdlp"

        est_cost = api_costs.get("estimated_cost_usd", 0)
        _log_activity(job_id, "clock", f"Completed in {elapsed:.1f}s — estimated API cost: ${est_cost:.4f}", group="done")

        _set_progress(job_id, "completed", 100, "Scouting complete!")
        _log_activity(job_id, "check", "Done! Your B-roll results are ready.", group="done")

        final_log = _progress.get(job_id, {}).get("activity_log", [])
        await storage.update_job_status(
            job_id, JobStatus.COMPLETE,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            result_count=len(all_results),
            api_costs=api_costs,
            coverage_assessment=coverage_assessment,
            warnings=warnings,
            activity_log=final_log,
        )

        if project_id:
            try:
                await storage.update_project_stats(project_id)
            except Exception:
                logger.warning("Failed to update project stats for %s", project_id)

        logger.info("Job %s complete: %d results in %.1fs", job_id, len(all_results), elapsed)

    except asyncio.CancelledError:
        logger.info("Job %s cancelled by user", job_id)
        _log_activity(job_id, "alert", "Job cancelled by user.")
        elapsed = round(time.time() - start_time, 2)
        api_costs = cost_tracker.end_job(job_id) or {}
        est_cost = api_costs.get("estimated_cost_usd", 0)
        if est_cost:
            _log_activity(job_id, "clock", f"Cancelled after {elapsed:.1f}s — API cost so far: ${est_cost:.4f}")
        cancel_log = _progress.get(job_id, {}).get("activity_log", [])
        await storage.update_job_status(
            job_id, JobStatus.CANCELLED,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            api_costs=api_costs,
            activity_log=cancel_log,
        )
        _set_progress(job_id, "cancelled", 0, "Cancelled by user")

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job_id)
        _log_activity(job_id, "alert", f"Pipeline failed: {str(exc)[:200]}")
        elapsed = round(time.time() - start_time, 2)
        api_costs = cost_tracker.end_job(job_id) or {}
        est_cost = api_costs.get("estimated_cost_usd", 0)
        if est_cost:
            _log_activity(job_id, "clock", f"Failed after {elapsed:.1f}s — API cost so far: ${est_cost:.4f}")
        fail_log = _progress.get(job_id, {}).get("activity_log", [])
        await storage.update_job_status(
            job_id, JobStatus.FAILED,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            api_costs=api_costs,
            activity_log=fail_log,
        )
        _set_progress(job_id, "failed", 0, "Pipeline failed")

    finally:
        try:
            await get_usage_service().recalculate()
        except Exception:
            logger.warning("Failed to recalculate usage after job %s", job_id)


def _validate_shot_coverage(
    segments: List[Segment],
    settings: dict,
) -> List[dict]:
    """Quality check: flag segments that might need more visual variety.
    These are INFO-level notes for the editor, not hard errors."""
    warn_long_sec = settings.get("warn_long_no_broll_sec", 180)
    max_gap_sec = settings.get("max_no_broll_gap_sec", 300)
    warnings: List[dict] = []

    for seg in segments:
        if seg.broll_count == 0:
            continue
        dur = seg.estimated_duration_seconds or 0
        if dur > warn_long_sec and seg.broll_count == 1:
            warnings.append({
                "segment_id": seg.segment_id,
                "message": f"'{seg.title}' is {dur}s long with only 1 B-roll shot. The editor may want additional variety.",
                "severity": "info",
            })

    gap_acc = 0
    gap_segs: List[str] = []
    for seg in segments:
        if seg.broll_count == 0:
            gap_acc += seg.estimated_duration_seconds or 0
            gap_segs.append(seg.segment_id)
        else:
            if gap_acc > max_gap_sec:
                warnings.append({
                    "segment_id": gap_segs[0],
                    "message": f"Consecutive no-B-roll segments ({', '.join(gap_segs)}) span {gap_acc}s. Long stretch without visual variety.",
                    "severity": "info",
                })
            gap_acc = 0
            gap_segs = []
    if gap_acc > max_gap_sec:
        warnings.append({
            "segment_id": gap_segs[0],
            "message": f"Consecutive no-B-roll segments ({', '.join(gap_segs)}) span {gap_acc}s at the end of the script.",
            "severity": "info",
        })

    return warnings


def _build_coverage_assessment(
    segments: List[Segment],
    results: list,
    script_duration: int,
    warnings: List[dict],
) -> dict:
    """Build a neutral coverage summary for the job output."""
    total_shots = sum(seg.broll_count for seg in segments)
    no_broll_segs = [seg for seg in segments if seg.broll_count == 0]

    longest_gap = 0
    longest_gap_segs: List[str] = []
    gap_acc = 0
    gap_segs: List[str] = []
    for seg in segments:
        if seg.broll_count == 0:
            gap_acc += seg.estimated_duration_seconds or 0
            gap_segs.append(seg.segment_id)
        else:
            if gap_acc > longest_gap:
                longest_gap = gap_acc
                longest_gap_segs = list(gap_segs)
            gap_acc = 0
            gap_segs = []
    if gap_acc > longest_gap:
        longest_gap = gap_acc
        longest_gap_segs = list(gap_segs)

    note = (
        f"{len(results)} clips for {total_shots} shots across {len(segments)} segments. "
        f"{len(no_broll_segs)} segments are host-on-camera."
    )
    if longest_gap:
        note += f" Longest no-B-roll gap: {longest_gap}s ({', '.join(longest_gap_segs)})."

    return {
        "shots_per_minute": round(total_shots / max(script_duration, 1), 2),
        "clips_found": len(results),
        "total_shots": total_shots,
        "longest_no_broll_gap_seconds": longest_gap,
        "longest_no_broll_gap_segments": longest_gap_segs,
        "note": note,
        "warnings_count": len(warnings),
    }


_TRANSCRIPT_SOURCE_LABELS = {
    "cached_transcript": "DynamoDB cache",
    "youtube_captions": "YouTube manual captions",
    "youtube_auto_captions": "YouTube auto-captions",
    "whisper_transcription": "Whisper base (local)",
    "no_transcript": "no transcript available",
}


async def _match_candidates(
    candidates: List[CandidateVideo],
    segment: Segment,
    matcher: MatcherService,
    transcriber: TranscriberService,
    job_id: str,
    max_concurrent: int,
    on_activity=None,
    script_context: ScriptContext | None = None,
    shot: BRollShot | None = None,
) -> List[Tuple[CandidateVideo, MatchResult]]:
    semaphore = asyncio.Semaphore(max_concurrent)
    results: List[Tuple[CandidateVideo, MatchResult]] = []
    lock = asyncio.Lock()

    async def _emit(icon: str, text: str):
        if on_activity:
            try:
                await on_activity(icon, text)
            except Exception:
                pass

    async def process_one(cand: CandidateVideo):
        async with semaphore:
            try:
                transcript = await transcriber.get_transcript(
                    cand.video_id,
                    video_duration_seconds=cand.video_duration_seconds,
                    job_id=job_id,
                )

                source_label = _TRANSCRIPT_SOURCE_LABELS.get(
                    transcript.transcript_source.value, transcript.transcript_source.value
                )

                logger.info(
                    "Transcript for %s: source=%s has_text=%s",
                    cand.video_id, transcript.transcript_source.value,
                    bool(transcript.transcript_text),
                )

                short_title = cand.video_title[:45]
                if transcript.transcript_text:
                    await _emit("mic", f"  📄 \"{short_title}\" — transcript via {source_label}")
                else:
                    await _emit("alert", f"  ✗ \"{short_title}\" — tried cache → YouTube captions → companion → Whisper: all failed, no transcript")

                video_meta = {
                    "video_duration_seconds": cand.video_duration_seconds,
                    "video_title": cand.video_title,
                    "view_count": cand.view_count,
                    "transcript_source": transcript.transcript_source.value,
                }

                match = await matcher.find_timestamp(
                    transcript.transcript_text, segment, video_meta, job_id,
                    script_context=script_context,
                    shot=shot,
                )
                if matcher.context_matching_enabled:
                    match = matcher.validate_context_match(
                        match, cand.video_duration_seconds
                    )

                logger.info(
                    "Match for %s: confidence=%.2f valid=%s start=%s",
                    cand.video_id, match.confidence_score,
                    match.context_match_valid, match.start_time_seconds,
                )

                if transcript.transcript_text and match.confidence_score > 0:
                    s_start = match.start_time_seconds or 0
                    s_end = match.end_time_seconds or 0
                    ts_label = f"{s_start // 60}:{s_start % 60:02d}–{s_end // 60}:{s_end % 60:02d}"
                    model_label = match.matcher_source or "LLM"
                    await _emit("brain", f"  🤖 {model_label} → \"{short_title}\" → {match.confidence_score:.0%} confidence at {ts_label}")

                async with lock:
                    results.append((cand, match))
            except Exception:
                logger.exception(
                    "Failed to match %s for %s", cand.video_id, segment.segment_id
                )

    await asyncio.gather(*[process_one(c) for c in candidates])
    return results


async def _audit_context(
    all_results: List[RankedResult],
    script_context: ScriptContext,
    matcher: MatcherService,
    job_id: str | None,
) -> Tuple[List[RankedResult], int]:
    """Single LLM call to review all clip titles for context outliers."""
    if len(all_results) < 3:
        return all_results, 0

    clip_summaries = []
    for idx, r in enumerate(all_results):
        clip_summaries.append(
            f"{idx}. \"{r.video_title}\" by {r.channel_name} (for segment {r.segment_id})"
        )

    prompt = (
        "You are a documentary editor reviewing B-roll selections.\n\n"
        f"This documentary is about: {script_context.script_topic}\n"
        f"Geographic scope: {script_context.geographic_scope}\n"
        f"Domain: {script_context.script_domain}\n"
        f"NOT about: {script_context.exclusion_context}\n\n"
        "Selected B-roll clips:\n"
        + "\n".join(clip_summaries) +
        "\n\nReview each clip and flag any that are CONTEXTUALLY WRONG — "
        "they share keywords but are about something unrelated.\n\n"
        "Return JSON only:\n"
        '{"flagged": [{"index": 5, "reason": "explanation"}, ...]}\n'
        'If all are appropriate: {"flagged": []}'
    )

    backend = matcher._get("matcher_backend", "auto")
    parsed = await matcher._route_call(prompt, backend, job_id)
    if not parsed:
        return all_results, 0

    flagged_indices = set()
    for f in parsed.get("flagged", []):
        try:
            flagged_indices.add(int(f["index"]))
        except (KeyError, ValueError, TypeError):
            continue

    if not flagged_indices:
        return all_results, 0

    for idx in flagged_indices:
        if idx < len(all_results):
            reason = next(
                (f.get("reason", "") for f in parsed["flagged"] if f.get("index") == idx),
                "",
            )
            logger.info(
                "Context audit flagged [%d] %s — %s",
                idx, all_results[idx].video_title[:60], reason,
            )

    filtered = [r for i, r in enumerate(all_results) if i not in flagged_indices]
    return filtered, len(flagged_indices)
