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
    existing["activity_log"] = log
    _progress[job_id] = existing


def _compact_activity_log(log: list, max_entries: int = 800) -> list:
    """Compact the activity log for DynamoDB storage while preserving key events.

    Strategy: keep all depth-0/1 headers and group summaries, keep all warnings/errors
    (icon=alert), keep first+last entry per group of depth-2 successes, and collapse
    the middle into a count summary.
    """
    if len(log) <= max_entries:
        return log

    important = []
    group_items: dict[str, list] = {}

    for entry in log:
        icon = entry.get("icon", "")
        depth = entry.get("depth", 0)
        group = entry.get("group", "")

        is_alert = icon == "alert"
        is_header = depth <= 1
        is_important = is_alert or is_header or "timed out" in entry.get("text", "").lower()

        if is_important:
            important.append(entry)
        elif group:
            group_items.setdefault(group, []).append(entry)
        else:
            important.append(entry)

    compacted = list(important)

    budget = max_entries - len(compacted)
    num_groups = max(len(group_items), 1)
    per_group = max(3, budget // num_groups)

    for group_key, items in group_items.items():
        if len(items) <= per_group:
            compacted.extend(items)
        else:
            compacted.extend(items[:2])
            skipped = len(items) - 3
            compacted.append({
                "time": items[len(items) // 2].get("time", ""),
                "icon": "clock",
                "text": f"… {skipped} more events in this group (see full log during processing)",
                "depth": items[0].get("depth", 2),
                "group": group_key,
            })
            compacted.append(items[-1])

    compacted.sort(key=lambda e: e.get("time", ""))
    return compacted


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
    

        translator = TranslatorService(pipeline_settings=pipeline_cfg)
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

        # ══════════════════════════════════════════════════
        # Stage 2: Streaming Search + Transcript Fetch (overlapping)
        # As each search finds a new video, its transcript starts
        # fetching immediately. Matching waits for everything to complete.
        # ══════════════════════════════════════════════════

        active_segments = [seg for seg in segments if seg.broll_count > 0]
        skipped_segments = [seg for seg in segments if seg.broll_count == 0]
        total_active_shots = sum(seg.broll_count for seg in active_segments)

        all_shots: List[Tuple[Segment, BRollShot]] = []
        for seg in active_segments:
            shots_to_use = seg.broll_shots if seg.broll_shots else []
            if not shots_to_use:
                shots_to_use = [BRollShot(
                    shot_id=f"{seg.segment_id}_shot_1",
                    visual_need=seg.visual_need,
                    search_queries=seg.search_queries,
                    key_terms=seg.key_terms,
                )]
            for shot in shots_to_use:
                all_shots.append((seg, shot))

        est_search_sec = len(all_shots) * 5
        est_min = est_search_sec // 60
        est_sec = est_search_sec % 60
        est_str = f"{est_min}m {est_sec}s" if est_min else f"{est_sec}s"
        _set_progress(job_id, "searching", 20, f"Searching YouTube for {len(all_shots)} B-roll shots...")
        sources = "preferred channels → yt-dlp"
        if enable_gemini_expansion:
            sources += " → Gemini AI creative expansion"
        _log_activity(job_id, "search", f"Streaming pipeline: searching {total_active_shots} shots + fetching transcripts in parallel (estimated ~{est_str})", group="search")
        _log_activity(job_id, "clock", f"Search pipeline: {sources} → transcript fetch streams as videos are discovered", depth=1, group="search")

        searcher = SearcherService(pipeline_settings=pipeline_cfg)
        transcriber = TranscriberService(pipeline_settings=pipeline_cfg)
        search_start = time.time()

        # Shared state for streaming dedup
        video_pool: Dict[str, CandidateVideo] = {}
        video_to_shots: Dict[str, List[str]] = {}
        shot_candidates: Dict[str, List[CandidateVideo]] = {}
        transcript_cache: Dict[str, Optional[str]] = {}
        transcript_sources: Dict[str, str] = {}
        failed_fetches: set = set()
        video_pool_lock = asyncio.Lock()

        transcript_queue: asyncio.Queue = asyncio.Queue()
        search_semaphore = asyncio.Semaphore(5)
        transcript_semaphore = asyncio.Semaphore(pipeline_cfg.get("max_concurrent_candidates", 3))

        shots_searched = 0
        transcripts_fetched = 0
        progress_lock = asyncio.Lock()

        async def _search_one_shot(seg: Segment, shot: BRollShot):
            nonlocal shots_searched
            async with search_semaphore:
                try:
                    cands = await searcher.search_for_shot(
                        shot, seg, job_id=job_id,
                        script_context=script_context,
                    )
                    cands = cands or []
                    shot_candidates[shot.shot_id] = cands
                except Exception:
                    logger.warning("Search failed for shot %s", shot.shot_id)
                    shot_candidates[shot.shot_id] = []
                    cands = []

                # Locked pool update: queue new videos for transcript fetch
                async with video_pool_lock:
                    for c in cands:
                        vid = c.video_id
                        is_new = vid not in video_pool
                        if is_new:
                            video_pool[vid] = c
                            video_to_shots[vid] = []
                            await transcript_queue.put(vid)
                        if shot.shot_id not in video_to_shots[vid]:
                            video_to_shots[vid].append(shot.shot_id)

                async with progress_lock:
                    shots_searched += 1
                    pct = 20 + int(20 * shots_searched / max(len(all_shots), 1))
                    elapsed_s = time.time() - search_start
                    if shots_searched > 0:
                        per_shot = elapsed_s / shots_searched
                        remaining = int(per_shot * (len(all_shots) - shots_searched))
                        time_note = f" (~{remaining}s remaining)" if remaining > 0 else ""
                    else:
                        time_note = ""
                    _set_progress(job_id, "searching", pct, f"Shot {shots_searched}/{len(all_shots)}, {len(video_pool)} videos found{time_note}")

        async def _transcript_fetch_worker(worker_id: int):
            nonlocal transcripts_fetched
            while True:
                vid = await transcript_queue.get()
                if vid is None:
                    transcript_queue.task_done()
                    break

                async with transcript_semaphore:
                    cand = video_pool.get(vid)
                    if not cand:
                        transcript_queue.task_done()
                        continue
                    yt_link = f"https://youtu.be/{vid}"
                    short_title = cand.video_title[:45]
                    dur_min = round(cand.video_duration_seconds / 60, 1) if cand.video_duration_seconds else 0

                    async def _on_whisper_start(v_id: str, dur_s: int):
                        dur_m = round(dur_s / 60, 1)
                        _log_activity(job_id, "clock", f"🎙️ Whisper starting for \"{short_title}\" ({dur_m}m video) — downloading audio + transcribing locally — {yt_link}", depth=2, group="search")

                    try:
                        fetch_start = time.time()
                        t = await transcriber.get_transcript(
                            vid,
                            video_duration_seconds=cand.video_duration_seconds,
                            job_id=job_id,
                            on_whisper_start=_on_whisper_start,
                        )
                        fetch_elapsed = round(time.time() - fetch_start, 1)
                        transcript_cache[vid] = t.transcript_text
                        source_label = _TRANSCRIPT_SOURCE_LABELS.get(
                            t.transcript_source.value, t.transcript_source.value,
                        )
                        transcript_sources[vid] = source_label
                        if t.transcript_text:
                            timing = f" [{fetch_elapsed}s]" if fetch_elapsed >= 2 else ""
                            _log_activity(job_id, "mic", f"📄 \"{short_title}\" — transcript via {source_label}{timing} — {yt_link}", depth=2, group="search")
                        else:
                            failed_fetches.add(vid)
                            affected = video_to_shots.get(vid, [])
                            _log_activity(job_id, "alert", f"✗ \"{short_title}\" ({dur_min}m video) — no transcript available — {yt_link} (affects {len(affected)} shots)", depth=2, group="search")
                    except Exception:
                        logger.exception("Transcript fetch failed for %s (affects shots: %s)", vid, video_to_shots.get(vid, []))
                        transcript_cache[vid] = None
                        transcript_sources[vid] = "error"
                        failed_fetches.add(vid)
                        short_title = cand.video_title[:45]
                        _log_activity(job_id, "alert", f"✗ \"{short_title}\" — fetch error — {yt_link}", depth=2, group="search")

                async with progress_lock:
                    transcripts_fetched += 1
                    total_pool = max(len(video_pool), 1)
                    pct = 40 + int(15 * transcripts_fetched / total_pool)
                    _set_progress(job_id, "searching", min(pct, 55),
                                  f"Transcripts: {transcripts_fetched}/{total_pool} fetched")

                transcript_queue.task_done()

        # Start transcript fetch workers BEFORE searches begin
        NUM_FETCH_WORKERS = 3
        fetch_worker_tasks = [
            asyncio.create_task(_transcript_fetch_worker(i))
            for i in range(NUM_FETCH_WORKERS)
        ]

        # Run all searches concurrently — new videos are queued for fetching immediately
        await asyncio.gather(
            *[_search_one_shot(seg, shot) for seg, shot in all_shots],
            return_exceptions=True,
        )

        # All searches complete — no more new videos will be queued.
        # Wait for all queued transcript fetches to finish.
        TRANSCRIPT_FETCH_TIMEOUT = min(600, max(180, len(video_pool) * 3))
        total_to_fetch = len(video_pool)
        already_done = len(transcript_cache) + len(failed_fetches)
        remaining = total_to_fetch - already_done
        if remaining > 0:
            _log_activity(job_id, "clock", f"All searches done! Now waiting for {remaining} remaining transcript fetches (Whisper transcribes ~20s each on your local companion)", depth=1, group="search")
        else:
            _log_activity(job_id, "check", f"All {total_to_fetch} transcripts already fetched during search", depth=1, group="search")

        async def _transcript_progress_reporter():
            """Periodically log transcript fetch progress so the UI doesn't appear stuck."""
            last_count = len(transcript_cache) + len(failed_fetches)
            while True:
                await asyncio.sleep(15)
                done_now = len(transcript_cache) + len(failed_fetches)
                pending_now = total_to_fetch - done_now
                if pending_now <= 0:
                    break
                if done_now != last_count:
                    whisper_count = sum(1 for s in transcript_sources.values() if "Whisper" in s)
                    _log_activity(job_id, "clock", f"Transcripts: {done_now}/{total_to_fetch} done ({pending_now} remaining, {whisper_count} via Whisper so far)", depth=1, group="search")
                    _set_progress(job_id, "searching", 40 + int(15 * done_now / max(total_to_fetch, 1)),
                                  f"Fetching transcripts: {done_now}/{total_to_fetch} ({pending_now} remaining)")
                    last_count = done_now
                else:
                    _set_progress(job_id, "searching", 40 + int(15 * done_now / max(total_to_fetch, 1)),
                                  f"Waiting for Whisper transcription... ({pending_now} videos remaining)")

        progress_reporter = asyncio.create_task(_transcript_progress_reporter())

        timed_out = False
        try:
            await asyncio.wait_for(
                transcript_queue.join(),
                timeout=TRANSCRIPT_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            timed_out = True
            fetched_count = sum(1 for v in transcript_cache.values() if v)
            pending_count = total_to_fetch - len(transcript_cache) - len(failed_fetches)
            logger.error(
                "Job %s: transcript fetch timed out after %ds — %d/%d fetched, %d still pending",
                job_id, TRANSCRIPT_FETCH_TIMEOUT, fetched_count, total_to_fetch, pending_count,
            )
            _log_activity(job_id, "alert", f"Transcript fetch timed out after {TRANSCRIPT_FETCH_TIMEOUT // 60}m — proceeding with {fetched_count} of {total_to_fetch} transcripts", depth=1, group="search")
        finally:
            progress_reporter.cancel()
            try:
                await progress_reporter
            except asyncio.CancelledError:
                pass

        # Shut down workers — on timeout, cancel immediately instead of waiting
        if timed_out:
            for task in fetch_worker_tasks:
                task.cancel()
            await asyncio.gather(*fetch_worker_tasks, return_exceptions=True)
        else:
            for _ in range(NUM_FETCH_WORKERS):
                await transcript_queue.put(None)
            await asyncio.gather(*fetch_worker_tasks)

        search_elapsed = round(time.time() - search_start, 1)

        # Integrity check: every video should be accounted for
        accounted_for = set(transcript_cache.keys()) | failed_fetches
        missed_videos = set(video_pool.keys()) - accounted_for
        if missed_videos:
            logger.error(
                "Job %s: INTEGRITY CHECK — %d videos queued but never fetched: %s",
                job_id, len(missed_videos), list(missed_videos)[:10],
            )

        total_pairs = sum(len(c) for c in shot_candidates.values())
        unique_videos = len(video_pool)
        videos_with_transcript = sum(1 for v in transcript_cache.values() if v)
        empty_shots = sum(1 for c in shot_candidates.values() if not c)
        saved_fetches = total_pairs - unique_videos

        _log_activity(job_id, "check",
            f"Search + fetch done in {search_elapsed}s! "
            f"{total_pairs} candidate-shot pairs → {unique_videos} unique videos ({saved_fetches} duplicate fetches saved) → "
            f"{videos_with_transcript} transcripts ready",
            group="search")
        if empty_shots:
            _log_activity(job_id, "alert", f"{empty_shots} of {len(all_shots)} shots had no candidate videos", depth=1, group="search")
        logger.info(
            "Job %s streaming search+fetch: %d pairs, %d unique, %d transcripts, %d failed, %d missed in %.1fs",
            job_id, total_pairs, unique_videos, videos_with_transcript, len(failed_fetches), len(missed_videos), search_elapsed,
        )

        # ══════════════════════════════════════════════════
        # Stage 3: Matching (waits for search + fetch to fully complete)
        # ══════════════════════════════════════════════════

        matcher_backend = pipeline_cfg.get("matcher_backend", "auto")
        matcher_model = pipeline_cfg.get("matcher_model", "qwen3:8b")
        if matcher_backend == "api":
            match_label = pipeline_cfg.get("timestamp_model", "gpt-4o-mini")
        elif matcher_backend == "local":
            match_label = f"Ollama/{matcher_model}"
        else:
            match_label = f"Ollama/{matcher_model} (API fallback)"

        matcher = MatcherService(pipeline_settings=pipeline_cfg)
        ranker = RankerService()

        max_concurrent_candidates = pipeline_cfg.get("max_concurrent_candidates", 3)
        segment_timeout = pipeline_cfg.get("segment_timeout_sec", 300)

        shot_id_to_info: Dict[str, Tuple[Segment, BRollShot]] = {}
        for seg, shot in all_shots:
            shot_id_to_info[shot.shot_id] = (seg, shot)

        shot_match_results: Dict[str, List[Tuple[CandidateVideo, MatchResult]]] = {
            shot.shot_id: [] for _, shot in all_shots
        }

        match_start = time.time()

        match_tasks = []
        for shot_id, cands in shot_candidates.items():
            for cand in cands:
                if transcript_cache.get(cand.video_id):
                    match_tasks.append((cand.video_id, shot_id))

        total_match_pairs = len(match_tasks)

        _set_progress(job_id, "matching", 50, f"Matching {total_match_pairs} video-shot pairs with local AI...")
        _log_activity(job_id, "eye", f"Matching {total_active_shots} shots against {videos_with_transcript} videos with transcripts ({total_match_pairs} video-shot pairs to process)", group="match")
        _log_activity(job_id, "clock", f"Each pair gets a dedicated {match_label} call — processing sequentially (1 at a time)", depth=1, group="match")
        _log_activity(job_id, "zap", f"Running {total_match_pairs} matches", depth=1, group="match")

        matches_done = 0
        matches_with_result = 0

        for vid, shot_id in match_tasks:
            seg, shot = shot_id_to_info[shot_id]
            cand = video_pool[vid]
            transcript_text = transcript_cache.get(vid)
            yt_link = f"🔗 {vid}"
            word_count = len(transcript_text.split()) if transcript_text else 0
            short_title = cand.video_title[:40]
            short_need = shot.visual_need[:30]

            match_t0 = time.time()
            try:
                video_meta = {
                    "video_duration_seconds": cand.video_duration_seconds,
                    "video_title": cand.video_title,
                    "view_count": cand.view_count,
                    "transcript_source": transcript_sources.get(vid, "unknown"),
                }
                match_result = await matcher.find_timestamp(
                    transcript_text, seg, video_meta, job_id,
                    script_context=script_context,
                    shot=shot,
                )
                if matcher.context_matching_enabled:
                    match_result = matcher.validate_context_match(
                        match_result, cand.video_duration_seconds,
                    )

                match_dur = round(time.time() - match_t0, 1)
                model_label = match_result.matcher_source or "LLM"

                if transcript_text and match_result.confidence_score > 0:
                    matches_with_result += 1
                    s_start = match_result.start_time_seconds or 0
                    s_end = match_result.end_time_seconds or 0
                    ts_label = f"{s_start // 60}:{s_start % 60:02d}–{s_end // 60}:{s_end % 60:02d}"
                    _log_activity(job_id, "brain", f"🤖 {model_label} → \"{short_title}\" → {match_result.confidence_score:.0%} at {ts_label} (for \"{short_need}\") [{match_dur}s, {word_count} words] — {yt_link}", depth=3, group="match")
                else:
                    reason = ""
                    if match_result.context_match is False:
                        reason = " (context mismatch)"
                    elif model_label == "local_unavailable":
                        reason = " (agent unavailable)"
                    _log_activity(job_id, "clock", f"⏭️ No match in \"{short_title}\" for \"{short_need}\"{reason} [{match_dur}s, {word_count} words]", depth=3, group="match")

                shot_match_results[shot_id].append((cand, match_result))
            except Exception:
                logger.exception("Match failed: %s for shot %s", vid, shot_id)

            matches_done += 1
            pending = total_match_pairs - matches_done
            pct = 55 + int(35 * matches_done / max(total_match_pairs, 1))
            elapsed_s = time.time() - match_start
            if matches_done > 0:
                per_match = elapsed_s / matches_done
                remaining = int(per_match * pending)
                remaining_min = remaining // 60
                remaining_sec = remaining % 60
                if remaining_min > 0:
                    time_note = f" (~{remaining_min}m {remaining_sec}s remaining)"
                elif remaining > 0:
                    time_note = f" (~{remaining_sec}s remaining)"
                else:
                    time_note = ""
            else:
                time_note = ""
            _set_progress(job_id, "matching", pct, f"Matched {matches_done}/{total_match_pairs} — {matches_with_result} clips found — {pending} pending{time_note}")

        match_elapsed = round(time.time() - match_start, 1)
        _log_activity(job_id, "check", f"Matching done in {match_elapsed}s — {matches_with_result} clips from {total_match_pairs} pairs", depth=1, group="match")

        # Rank per shot, pick best clip, assemble segment results
        all_segment_results: Dict[str, List[RankedResult]] = {}

        for seg in active_segments:
            seg_ranked: List[RankedResult] = []
            seg_group = f"match-{seg.segment_id}"
            shots_for_seg = [shot for s, shot in all_shots if s.segment_id == seg.segment_id]

            for shot in shots_for_seg:
                matched = shot_match_results.get(shot.shot_id, [])
                ranked = ranker.rank_and_filter(matched, seg, settings=pipeline_cfg, script_context=script_context, shot=shot)
                short_need = shot.visual_need[:55]
                if ranked:
                    _log_activity(job_id, "check", f"✓ \"{short_need}\" → \"{ranked[0].video_title[:50]}\" ({ranked[0].confidence_score:.0%})", depth=2, group=seg_group)
                    seg_ranked.extend(ranked[:1])
                else:
                    _log_activity(job_id, "alert", f"✗ No clip for \"{short_need}\"", depth=2, group=seg_group)

            all_segment_results[seg.segment_id] = seg_ranked

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

        final_log = _compact_activity_log(_progress.get(job_id, {}).get("activity_log", []))
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
        cancel_log = _compact_activity_log(_progress.get(job_id, {}).get("activity_log", []))
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
        fail_log = _compact_activity_log(_progress.get(job_id, {}).get("activity_log", []))
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
