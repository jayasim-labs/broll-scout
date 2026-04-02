import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.models.schemas import (
    CandidateVideo, JobStatus, MatchResult, RankedResult, Segment,
)
from app.services.matcher import MatcherService
from app.services.ranker import RankerService
from app.services.searcher import SearcherService
from app.services.settings_service import get_settings_service
from app.utils.quota_tracker import get_quota_tracker
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
) -> None:
    """Full pipeline: translate -> search -> match -> rank -> store."""
    storage = get_storage()
    cost_tracker = get_cost_tracker()

    settings_svc = get_settings_service()
    pipeline_cfg = await settings_svc.get_all_settings()
    pipeline_cfg["enable_gemini_expansion"] = enable_gemini_expansion

    start_time = time.time()
    script_hash = hashlib.sha256(script.encode()).hexdigest()[:16]

    get_quota_tracker().reset_for_job()
    cost_tracker.start_job(job_id)
    await storage.create_job(job_id, script_hash, editor_id, project_id=project_id, title=title)

    try:
        # --- Stage 1: Translation ---
        _set_progress(job_id, "translating", 5, "Translating and segmenting script...")


        word_count = len(script.split())
        script_duration = max(1, round(word_count / 100))

        async def _translator_progress(icon: str, text: str):
            _log_activity(job_id, icon, text, depth=1, group="translate")
    

        translator = TranslatorService()
        segments, english_translation = await translator.translate_and_segment(
            script, job_id, on_progress=_translator_progress,
        )

        translation_model = pipeline_cfg.get("translation_model", "gpt-4o")
        _log_activity(job_id, "brain", f"Translation done via {translation_model}! Your ~{script_duration}-minute script → {len(segments)} scenes, each needing different B-roll footage", group="translate")
        for i, seg in enumerate(segments, 1):
            _log_activity(job_id, "sparkles", f"Scene {i}: \"{seg.title}\" — looking for: {seg.visual_need}", depth=1, group="translate")


        await storage.store_segments(job_id, segments)
        await storage.update_job_status(
            job_id, JobStatus.PROCESSING,
            segment_count=len(segments),
            script_duration_minutes=script_duration,
            english_translation=english_translation,
        )

        # --- Stage 2: Searching ---
        est_search_sec = len(segments) * 8
        est_min = est_search_sec // 60
        est_sec = est_search_sec % 60
        est_str = f"{est_min}m {est_sec}s" if est_min else f"{est_sec}s"
        _set_progress(job_id, "searching", 20, f"Searching YouTube & yt-dlp for B-roll clips...")
        _log_activity(job_id, "search", f"Now hunting for B-roll videos for all {len(segments)} scenes (estimated ~{est_str})", group="search")
        sources = "preferred channels → YouTube/yt-dlp"
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

        async def search_activity(icon: str, text: str):
            _log_activity(job_id, icon, text, depth=1, group="search")

        candidates_by_segment = await searcher.search_batch(
            segments, job_id=job_id, progress_callback=search_progress,
            on_activity=search_activity,
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
                on_activity=search_activity,
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


        # --- Stage 3: Matching ---
        _set_progress(job_id, "matching", 55, "Watching videos and finding the exact moments you need...")
        est_match_sec = total_candidates * 4
        est_m_min = est_match_sec // 60
        est_m_sec = est_match_sec % 60
        est_m_str = f"{est_m_min}m {est_m_sec}s" if est_m_min else f"{est_m_sec}s"
        timestamp_model = pipeline_cfg.get("timestamp_model", "gpt-4o-mini")
        _log_activity(job_id, "eye", f"Now analyzing {total_candidates} videos to pinpoint the exact seconds that match your script (estimated ~{est_m_str})", group="match")
        _log_activity(job_id, "clock", f"For each video: fetch transcript (cache → YouTube captions → companion → Whisper base) → {timestamp_model} finds peak visual moment → validate timestamp", depth=1, group="match")

        matcher = MatcherService(pipeline_settings=pipeline_cfg)
        transcriber = TranscriberService()
        ranker = RankerService()

        max_concurrent_candidates = pipeline_cfg.get("max_concurrent_candidates", 3)
        segment_timeout = pipeline_cfg.get("segment_timeout_sec", 60)

        all_segment_results: Dict[str, List[RankedResult]] = {}
        total_segments = len(segments)
        match_start = time.time()

        for seg_idx, segment in enumerate(segments):
            pct = 55 + int(35 * seg_idx / max(total_segments, 1))
            if seg_idx > 0:
                per_seg = (time.time() - match_start) / seg_idx
                remaining = int(per_seg * (total_segments - seg_idx))
                time_note = f" (~{remaining}s remaining)"
            else:
                time_note = ""
            _set_progress(
                job_id, "matching", pct,
                f"Analyzing scene {seg_idx + 1} of {total_segments}: \"{segment.title}\"{time_note}",
            )
            seg_group = f"match-{segment.segment_id}"
            _log_activity(job_id, "zap", f"Scene {seg_idx + 1}/{total_segments}: \"{segment.title}\" — scanning videos for the perfect clip", depth=1, group="match")

            cands = candidates_by_segment.get(segment.segment_id, [])
            if not cands:
                _log_activity(job_id, "alert", f"No matching videos were found for \"{segment.title}\" — skipping this scene", depth=2, group=seg_group)
                all_segment_results[segment.segment_id] = []
                continue

            _log_activity(job_id, "mic", f"Fetching transcripts for {len(cands)} videos (cache → YouTube captions → companion → Whisper base)", depth=2, group=seg_group)

            async def _match_activity(icon: str, text: str):
                _log_activity(job_id, icon, text, depth=2, group=seg_group)

            try:
                matched = await asyncio.wait_for(
                    _match_candidates(
                        cands, segment, matcher, transcriber, job_id,
                        max_concurrent_candidates,
                        on_activity=_match_activity,
                        timestamp_model_name=timestamp_model,
                    ),
                    timeout=segment_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Segment %s timed out", segment.segment_id)
                _log_activity(job_id, "alert", f"\"{segment.title}\" took too long ({segment_timeout}s) — moving to next scene", depth=2, group=seg_group)
                matched = []

            if matched:
                _log_activity(job_id, "eye", f"Found relevant moments in {len(matched)} out of {len(cands)} videos for \"{segment.title}\"", depth=2, group=seg_group)
                for cand, match in matched[:3]:
                    ts_min = (match.start_time_seconds or 0) // 60
                    ts_sec = (match.start_time_seconds or 0) % 60
                    hook_text = f" — \"{match.the_hook}\"" if match.the_hook else ""
                    _log_activity(job_id, "sparkles", f"▶ \"{cand.video_title[:55]}\" at {ts_min}:{ts_sec:02d} ({match.confidence_score:.0%} match){hook_text}", depth=3, group=seg_group)

            ranked = ranker.rank_and_filter(matched, segment, settings=pipeline_cfg)
            if ranked:
                top = ranked[0]
                source_label = _TRANSCRIPT_SOURCE_LABELS.get(top.source_flag.value, top.source_flag.value) if hasattr(top, 'source_flag') else ""
                _log_activity(job_id, "filter", f"Best {len(ranked)} clips selected for \"{segment.title}\" (ranked by AI confidence, keyword density, views, channel, recency)", depth=2, group=seg_group)
            else:
                _log_activity(job_id, "filter", f"No clips passed quality filters for \"{segment.title}\"", depth=2, group=seg_group)
            all_segment_results[segment.segment_id] = ranked
    

        # --- Cross-segment dedup ---
        _log_activity(job_id, "shield", "Removing duplicate clips — making sure the same video isn't suggested for multiple scenes", group="rank")
        all_segment_results = ranker.deduplicate_across_segments(all_segment_results)

        all_results: List[RankedResult] = []
        for results in all_segment_results.values():
            all_results.extend(results)

        low_threshold = max(5, round(word_count / 100))
        minimum_results_met = len(all_results) >= script_duration

        # --- Recovery search ---
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
                    empty_segments, job_id=job_id,
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
                                ),
                                timeout=segment_timeout,
                            )
                            ranked = ranker.rank_and_filter(matched, seg, settings=pipeline_cfg)
                            all_segment_results[seg.segment_id] = ranked
                            all_results.extend(ranked)
                        except asyncio.TimeoutError:
                            pass

        minimum_results_met = len(all_results) >= script_duration

        # --- Stage 4: Storing ---
        _set_progress(job_id, "ranking", 95, "Saving your results...")
        if all_results:
            status_note = ""
            if not minimum_results_met:
                status_note = f" (target was {script_duration}+ clips for a ~{script_duration}-min script)"
            _log_activity(job_id, "check", f"All done! {len(all_results)} B-roll clips with exact timestamps found across {total_segments} scenes{status_note}", group="rank")
        else:
            qt = get_quota_tracker()
            if qt.is_quota_exhausted:
                _log_activity(job_id, "alert", "⚠ YouTube API daily quota exhausted and no local agent connected — no clips could be found. Install the B-Roll Scout companion app or try again tomorrow.", group="rank")
            else:
                _log_activity(job_id, "alert", "No clips found. Many videos had no transcripts available. Ensure the companion app is running so Whisper can transcribe audio locally.", group="rank")
        await storage.store_results(job_id, all_results)

        elapsed = round(time.time() - start_time, 2)
        api_costs = cost_tracker.end_job(job_id) or {}
        qt_stats = get_quota_tracker().stats
        api_costs["ytdlp_searches"] = qt_stats.get("ytdlp_searches_via_agent", 0)
        api_costs["ytdlp_detail_lookups"] = qt_stats.get("ytdlp_detail_lookups_via_agent", 0)
        api_costs["quota_exhausted"] = qt_stats.get("quota_exhausted", False)
        api_costs["search_mode"] = qt_stats.get("search_mode", "unknown")

        est_cost = api_costs.get("estimated_cost_usd", 0)
        _log_activity(job_id, "clock", f"Completed in {elapsed:.1f}s — estimated API cost: ${est_cost:.4f}", group="done")

        await storage.update_job_status(
            job_id, JobStatus.COMPLETE,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            result_count=len(all_results),
            api_costs=api_costs,
            minimum_results_met=minimum_results_met,
        )
        _set_progress(job_id, "completed", 100, "Scouting complete!")
        _log_activity(job_id, "check", "Done! Your B-roll results are ready.", group="done")

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
        qt_stats = get_quota_tracker().stats
        api_costs["ytdlp_searches"] = qt_stats.get("ytdlp_searches_via_agent", 0)
        api_costs["ytdlp_detail_lookups"] = qt_stats.get("ytdlp_detail_lookups_via_agent", 0)
        est_cost = api_costs.get("estimated_cost_usd", 0)
        if est_cost:
            _log_activity(job_id, "clock", f"Cancelled after {elapsed:.1f}s — API cost so far: ${est_cost:.4f}")
        await storage.update_job_status(
            job_id, JobStatus.CANCELLED,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            api_costs=api_costs,
        )
        _set_progress(job_id, "cancelled", 0, "Cancelled by user")

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job_id)
        _log_activity(job_id, "alert", f"Pipeline failed: {str(exc)[:200]}")
        elapsed = round(time.time() - start_time, 2)
        api_costs = cost_tracker.end_job(job_id) or {}
        qt_stats = get_quota_tracker().stats
        api_costs["ytdlp_searches"] = qt_stats.get("ytdlp_searches_via_agent", 0)
        api_costs["ytdlp_detail_lookups"] = qt_stats.get("ytdlp_detail_lookups_via_agent", 0)
        est_cost = api_costs.get("estimated_cost_usd", 0)
        if est_cost:
            _log_activity(job_id, "clock", f"Failed after {elapsed:.1f}s — API cost so far: ${est_cost:.4f}")
        await storage.update_job_status(
            job_id, JobStatus.FAILED,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            api_costs=api_costs,
        )
        _set_progress(job_id, "failed", 0, "Pipeline failed")

    finally:
        try:
            await get_usage_service().recalculate()
        except Exception:
            logger.warning("Failed to recalculate usage after job %s", job_id)


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
    timestamp_model_name: str = "gpt-4o-mini",
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
                    transcript.transcript_text, segment, video_meta, job_id
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
                    ts_min = (match.start_time_seconds or 0) // 60
                    ts_sec = (match.start_time_seconds or 0) % 60
                    await _emit("brain", f"  🤖 {timestamp_model_name} → \"{short_title}\" → {match.confidence_score:.0%} confidence at {ts_min}:{ts_sec:02d}")

                async with lock:
                    results.append((cand, match))
            except Exception:
                logger.exception(
                    "Failed to match %s for %s", cand.video_id, segment.segment_id
                )

    await asyncio.gather(*[process_one(c) for c in candidates])
    return results
