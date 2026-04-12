import asyncio
import hashlib
import logging
import random
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
from app.utils.quota_tracker import get_quota_tracker
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

        num_batches = max(1, len(all_shots) // 4)
        est_search_sec = len(all_shots) * 12 + num_batches * 80
        est_min = est_search_sec // 60
        est_sec = est_search_sec % 60
        est_str = f"{est_min}m {est_sec}s" if est_min else f"{est_sec}s"
        _set_progress(job_id, "searching", 20, f"Searching YouTube for {len(all_shots)} B-roll shots...")
        sources = "yt-dlp"
        if pipeline_cfg.get("enable_preferred_channel_search"):
            sources = "preferred channels → " + sources
        if enable_gemini_expansion:
            sources += " → Gemini AI creative expansion"
        _log_activity(job_id, "search", f"Searching {total_active_shots} shots for B-roll videos (estimated ~{est_str})", group="search")
        _log_activity(job_id, "clock", f"Search: {sources} → transcripts fetched sequentially", depth=1, group="search")

        searcher = SearcherService(pipeline_settings=pipeline_cfg)
        transcriber = TranscriberService(pipeline_settings=pipeline_cfg)
        search_start = time.time()

        # Shared state for search dedup
        video_pool: Dict[str, CandidateVideo] = {}
        video_to_shots: Dict[str, List[str]] = {}
        shot_candidates: Dict[str, List[CandidateVideo]] = {}
        shots_searched = 0
        search_lock = asyncio.Lock()

        search_launched = 0

        async def _search_one_shot(seg: Segment, shot: BRollShot):
            nonlocal shots_searched, search_launched
            short_need = shot.visual_need[:50] if shot.visual_need else shot.shot_id

            async with search_lock:
                search_launched += 1
                shot_num = search_launched
            queries = shot.search_queries or seg.search_queries or []
            q_preview = f" — queries: {', '.join(q[:30] for q in queries[:2])}" if queries else ""
            _log_activity(job_id, "globe",
                f"Shot {shot_num}/{len(all_shots)}: \"{short_need}\" — searching ...{q_preview}",
                depth=1, group="search")

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

            new_videos = 0
            async with search_lock:
                for c in cands:
                    vid = c.video_id
                    if vid not in video_pool:
                        video_pool[vid] = c
                        video_to_shots[vid] = []
                        new_videos += 1
                    if shot.shot_id not in video_to_shots[vid]:
                        video_to_shots[vid].append(shot.shot_id)

                shots_searched += 1
                pct = 20 + int(20 * shots_searched / max(len(all_shots), 1))
                elapsed_s = time.time() - search_start

                if cands:
                    reused = len(cands) - new_videos
                    parts = []
                    if new_videos:
                        parts.append(f"{new_videos} new")
                    if reused:
                        parts.append(f"{reused} already in pool")
                    detail = f" ({', '.join(parts)})" if parts else ""
                    _log_activity(job_id, "check",
                        f"Shot {shot_num}/{len(all_shots)}: \"{short_need}\" → {len(cands)} candidates{detail} — {len(video_pool)} total videos",
                        depth=1, group="search")
                else:
                    _log_activity(job_id, "alert",
                        f"Shot {shot_num}/{len(all_shots)}: \"{short_need}\" → no results",
                        depth=1, group="search")

                if shots_searched > 0:
                    per_shot = elapsed_s / shots_searched
                    remaining = int(per_shot * (len(all_shots) - shots_searched))
                    time_note = f" (~{remaining}s remaining)" if remaining > 0 else ""
                else:
                    time_note = ""
                _set_progress(job_id, "searching", pct, f"Shot {shots_searched}/{len(all_shots)}, {len(video_pool)} videos found{time_note}")

        # ══════════════════════════════════════════════════════════════
        # Stage 2a: Search — find all candidate videos (sequential)
        # ══════════════════════════════════════════════════════════════
        # Process shots ONE AT A TIME with batch-level pacing.
        # Each shot fires ~3 yt-dlp queries. YouTube's rate limit window
        # is roughly ~15-20 requests per 2-minute window. With 3 queries
        # per shot, we hit the limit after ~5 shots. Inserting a longer
        # cooldown every BATCH_SIZE shots stays under the threshold.
        shuffled_shots = list(all_shots)
        random.shuffle(shuffled_shots)

        SEARCH_INTER_SHOT_DELAY = 3.0  # seconds between shots within a batch
        SEARCH_BATCH_SIZE = 4          # shots per batch before cooldown
        SEARCH_BATCH_COOLDOWN = 75.0   # seconds to pause between batches

        for i, (seg, shot) in enumerate(shuffled_shots):
            try:
                await _search_one_shot(seg, shot)
            except Exception:
                logger.warning("Search failed for shot %s", shot.shot_id)
            if i < len(shuffled_shots) - 1:
                shot_num = i + 1
                if shot_num % SEARCH_BATCH_SIZE == 0:
                    cooldown = SEARCH_BATCH_COOLDOWN + random.uniform(5, 15)
                    _log_activity(job_id, "clock",
                        f"Rate-limit cooldown: pausing {int(cooldown)}s after {shot_num} shots "
                        f"({len(shuffled_shots) - shot_num} remaining)",
                        depth=1, group="search")
                    await asyncio.sleep(cooldown)
                else:
                    await asyncio.sleep(SEARCH_INTER_SHOT_DELAY + random.uniform(0.5, 1.5))

        search_elapsed = round(time.time() - search_start, 1)
        total_pairs = sum(len(c) for c in shot_candidates.values())
        unique_videos = len(video_pool)
        empty_shots = sum(1 for c in shot_candidates.values() if not c)
        saved_fetches = total_pairs - unique_videos
        search_min = round(search_elapsed / 60, 1)
        search_label = f"{search_min}m" if search_min >= 1 else f"{search_elapsed}s"
        _log_activity(job_id, "check",
            f"Video discovery done in {search_label} — "
            f"{total_pairs} candidate-shot pairs → {unique_videos} unique videos ({saved_fetches} duplicate fetches saved)",
            group="search")
        if empty_shots:
            _log_activity(job_id, "alert", f"{empty_shots} of {len(all_shots)} shots had no candidate videos", depth=1, group="search")

        # ══════════════════════════════════════════════════════════════
        # Stage 2b: Transcripts — simple sequential loop (no workers)
        # ══════════════════════════════════════════════════════════════
        # Process each video one at a time: cache → captions → Whisper.
        # The companion handles one task at a time anyway, so sequential
        # gives identical throughput with zero concurrency bugs.

        # Cooldown: search phase fires many yt-dlp queries in a short burst.
        # YouTube rate-limits per IP; jumping straight into subtitle fetching
        # triggers 429s. A short pause lets the rate-limit window reset.
        SEARCH_COOLDOWN_SEC = 30
        _log_activity(job_id, "clock",
            f"Cooling down {SEARCH_COOLDOWN_SEC}s before transcript fetch (YouTube rate-limit buffer) ...",
            depth=1, group="search")
        _set_progress(job_id, "searching", 42,
                      f"Cooldown {SEARCH_COOLDOWN_SEC}s — YouTube rate-limit buffer before transcript fetch")
        await asyncio.sleep(SEARCH_COOLDOWN_SEC)

        transcript_cache: Dict[str, Optional[str]] = {}
        transcript_sources: Dict[str, str] = {}
        failed_fetches: set = set()
        whisper_failure_reasons: dict[str, int] = {}
        whisper_count = 0

        # Sort by duration (shorter first) so fast videos finish early
        sorted_videos = sorted(
            video_pool.values(),
            key=lambda c: c.video_duration_seconds or 9999,
        )

        _log_activity(job_id, "clock",
            f"Fetching transcripts for {len(sorted_videos)} videos (cache → captions → Whisper) ...",
            group="transcript")

        for i, cand in enumerate(sorted_videos):
            vid = cand.video_id
            yt_link = f"https://youtu.be/{vid}"
            short_title = cand.video_title[:45]
            dur_min = round(cand.video_duration_seconds / 60, 1) if cand.video_duration_seconds else 0

            _log_activity(job_id, "clock",
                f"[{i+1}/{len(sorted_videos)}] \"{short_title}\" ({dur_min}m) — fetching transcript ... — {yt_link}",
                depth=1, group="transcript")

            async def _on_whisper_start(v_id: str, dur_s: int):
                nonlocal whisper_count
                whisper_count += 1
                dur_m = round(dur_s / 60, 1)
                _log_activity(job_id, "clock",
                    f"🎙️ Whisper #{whisper_count} for \"{short_title}\" ({dur_m}m video) "
                    f"— downloading audio + transcribing locally — {yt_link}",
                    depth=2, group="transcript")

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
                is_whisper = "Whisper" in source_label

                if t.transcript_text:
                    timing = f" [{fetch_elapsed}s]" if fetch_elapsed >= 2 else ""
                    if is_whisper:
                        _log_activity(job_id, "check",
                            f"✅ Whisper done for \"{short_title}\" ({dur_min}m video){timing} — {yt_link}",
                            depth=2, group="transcript")
                    else:
                        _log_activity(job_id, "mic",
                            f"📄 \"{short_title}\" — transcript via {source_label}{timing} — {yt_link}",
                            depth=2, group="transcript")
                else:
                    failed_fetches.add(vid)
                    affected = video_to_shots.get(vid, [])
                    if t.whisper_attempted:
                        reason = t.whisper_failure_reason or "audio_download_failed"
                        whisper_failure_reasons[reason] = whisper_failure_reasons.get(reason, 0) + 1
                        reason_labels = {
                            "timeout": "Whisper task timed out",
                            "transcription_timeout": "Whisper transcription exceeded 20 min limit",
                            "no_agent": "companion agent unavailable",
                            "audio_download_failed": "audio download failed (restricted/age-gated)",
                            "video_fallback_failed": "audio + video fallback both failed",
                            "all_formats_failed": "all download formats failed (restricted video)",
                            "download_timeout": "audio download timed out",
                        }
                        reason_text = reason_labels.get(reason, f"Whisper failed: {reason}")
                        _log_activity(job_id, "alert",
                            f"✗ \"{short_title}\" ({dur_min}m video) — {reason_text} — {yt_link} (affects {len(affected)} shots)",
                            depth=2, group="transcript")
                    else:
                        _log_activity(job_id, "alert",
                            f"✗ \"{short_title}\" ({dur_min}m video) — no transcript available — {yt_link} (affects {len(affected)} shots)",
                            depth=2, group="transcript")
            except Exception:
                logger.exception("Transcript fetch failed for %s", vid)
                transcript_cache[vid] = None
                transcript_sources[vid] = "error"
                failed_fetches.add(vid)
                _log_activity(job_id, "alert",
                    f"✗ \"{short_title}\" — fetch error — {yt_link}",
                    depth=2, group="transcript")

            # Progress update with ETA
            done = i + 1
            pct = 40 + int(15 * done / max(len(sorted_videos), 1))
            transcript_elapsed = time.time() - search_start - SEARCH_COOLDOWN_SEC
            if done > 0 and transcript_elapsed > 0:
                per_video = transcript_elapsed / done
                remaining_videos = len(sorted_videos) - done
                eta_sec = int(per_video * remaining_videos)
                if eta_sec >= 3600:
                    eta_label = f" — ~{eta_sec // 3600}h {(eta_sec % 3600) // 60}m remaining"
                elif eta_sec >= 60:
                    eta_label = f" — ~{eta_sec // 60}m remaining"
                elif eta_sec > 0:
                    eta_label = f" — ~{eta_sec}s remaining"
                else:
                    eta_label = ""
            else:
                eta_label = ""
            _set_progress(job_id, "searching", min(pct, 55),
                          f"Transcripts: {done}/{len(sorted_videos)} fetched ({whisper_count} via Whisper){eta_label}")

        total_pipeline_elapsed = round(time.time() - search_start, 1)

        videos_with_transcript = sum(1 for v in transcript_cache.values() if v)
        failed_count = len(failed_fetches)

        # Transcript source breakdown
        source_counts: dict[str, int] = {}
        for src in transcript_sources.values():
            source_counts[src] = source_counts.get(src, 0) + 1
        whisper_ok = sum(v for k, v in source_counts.items() if "Whisper" in k)
        cache_hits = source_counts.get("DynamoDB cache", 0)
        yt_manual = source_counts.get("YouTube manual captions", 0)
        yt_auto = source_counts.get("YouTube auto-captions", 0)
        error_count = source_counts.get("error", 0)

        # ── Transcript & Whisper summary ──

        _log_activity(job_id, "check",
            f"Fetched transcripts for {unique_videos} videos → {videos_with_transcript} succeeded, {failed_count} failed",
            group="transcript")

        breakdown_parts = []
        if cache_hits:
            breakdown_parts.append(f"{cache_hits} cached")
        if yt_manual:
            breakdown_parts.append(f"{yt_manual} YouTube captions")
        if yt_auto:
            breakdown_parts.append(f"{yt_auto} auto-captions")
        if whisper_ok:
            breakdown_parts.append(f"{whisper_ok} Whisper")
        if failed_count:
            breakdown_parts.append(f"{failed_count} failed")
        if error_count:
            breakdown_parts.append(f"{error_count} errors")

        if breakdown_parts:
            _log_activity(job_id, "check", f"Sources: {' · '.join(breakdown_parts)}", depth=1, group="transcript")

        if whisper_count > 0:
            whisper_failed = whisper_count - whisper_ok
            if whisper_ok > 0 and whisper_failed == 0:
                _log_activity(job_id, "check",
                    f"🎙️ Whisper: all {whisper_ok} transcriptions succeeded",
                    depth=1, group="transcript")
            else:
                reason_summary_parts = []
                if whisper_failure_reasons.get("timeout"):
                    reason_summary_parts.append(f"{whisper_failure_reasons['timeout']} timed out")
                if whisper_failure_reasons.get("transcription_timeout"):
                    reason_summary_parts.append(f"{whisper_failure_reasons['transcription_timeout']} transcription timed out (>20min)")
                if whisper_failure_reasons.get("audio_download_failed"):
                    reason_summary_parts.append(f"{whisper_failure_reasons['audio_download_failed']} audio download failed")
                if whisper_failure_reasons.get("download_timeout"):
                    reason_summary_parts.append(f"{whisper_failure_reasons['download_timeout']} download timed out")
                if whisper_failure_reasons.get("video_fallback_failed"):
                    reason_summary_parts.append(f"{whisper_failure_reasons['video_fallback_failed']} video fallback failed")
                if whisper_failure_reasons.get("all_formats_failed"):
                    reason_summary_parts.append(f"{whisper_failure_reasons['all_formats_failed']} restricted")
                other_reasons = sum(v for k, v in whisper_failure_reasons.items()
                                    if k not in ("timeout", "transcription_timeout", "audio_download_failed", "download_timeout", "video_fallback_failed", "all_formats_failed", "no_agent"))
                if other_reasons:
                    reason_summary_parts.append(f"{other_reasons} other")
                reason_detail = f" ({', '.join(reason_summary_parts)})" if reason_summary_parts else ""

                if whisper_ok > 0:
                    _log_activity(job_id, "alert",
                        f"🎙️ Whisper: {whisper_ok} succeeded, {whisper_failed} failed{reason_detail}",
                        depth=1, group="transcript")
                else:
                    _log_activity(job_id, "alert",
                        f"🎙️ Whisper: all {whisper_failed} attempted transcriptions failed{reason_detail}",
                        depth=1, group="transcript")
        elif unique_videos > 0 and videos_with_transcript == unique_videos:
            _log_activity(job_id, "check",
                f"🎙️ Whisper not needed — all {unique_videos} videos had YouTube captions or cached transcripts",
                depth=1, group="transcript")

        # High failure rate warning
        if failed_count > 0 and unique_videos > 0:
            fail_pct = round(failed_count / unique_videos * 100)
            if fail_pct >= 40:
                _log_activity(job_id, "alert",
                    f"⚠️ {failed_count} of {unique_videos} videos ({fail_pct}%) had no transcript at all. "
                    f"Likely restricted/age-gated videos or YouTube rate-limiting. "
                    f"Only {videos_with_transcript} videos can proceed to matching.",
                    depth=1, group="transcript")

        _log_activity(job_id, "check",
            f"Transcript phase complete — {videos_with_transcript} videos ready for timestamp matching",
            group="transcript")

        logger.info(
            "Job %s streaming search+fetch: %d pairs, %d unique, %d transcripts, %d failed in %.1fs | sources: %s",
            job_id, total_pairs, unique_videos, videos_with_transcript, len(failed_fetches), total_pipeline_elapsed,
            dict(source_counts),
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
        skipped_no_transcript = sum(len(c) for c in shot_candidates.values()) - total_match_pairs

        _set_progress(job_id, "matching", 55, f"Matching {total_match_pairs} video-shot pairs with local AI...")
        _log_activity(job_id, "eye",
            f"Matching {total_active_shots} shots against {videos_with_transcript} videos with transcripts "
            f"({total_match_pairs} video-shot pairs to process"
            + (f", {skipped_no_transcript} pairs skipped — no transcript" if skipped_no_transcript else "")
            + ")", group="match")
        _log_activity(job_id, "clock", f"Each pair gets a dedicated {match_label} call — processing sequentially (1 at a time)", depth=1, group="match")

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
                    "channel_name": cand.channel_name,
                    "view_count": cand.view_count,
                    "transcript_source": transcript_sources.get(vid, "unknown"),
                    "is_preferred_tier1": cand.is_preferred_tier1,
                    "is_preferred_tier2": cand.is_preferred_tier2,
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
        avg_match = round(match_elapsed / max(total_match_pairs, 1), 1)
        match_min = round(match_elapsed / 60, 1)
        _log_activity(job_id, "check",
            f"Matching done in {match_min}m ({match_elapsed}s) via {match_label} — "
            f"{matches_with_result} clips from {total_match_pairs} pairs "
            f"(avg {avg_match}s per match)",
            group="match")

        # Rank per shot, keep top clips per shot, assemble segment results
        # Session-level dedup: track used (video_id, timestamp_bucket) across all shots
        used_timestamps: Dict[str, set] = {}
        all_segment_results: Dict[str, List[RankedResult]] = {}
        keep_per_shot = int(pipeline_cfg.get("top_results_per_shot", 5))

        for seg in active_segments:
            seg_ranked: List[RankedResult] = []
            seg_group = f"match-{seg.segment_id}"
            shots_for_seg = [shot for s, shot in all_shots if s.segment_id == seg.segment_id]

            for shot in shots_for_seg:
                matched = shot_match_results.get(shot.shot_id, [])
                ranked = ranker.rank_and_filter(
                    matched, seg, settings=pipeline_cfg,
                    script_context=script_context, shot=shot,
                    used_timestamps=used_timestamps,
                )
                short_need = shot.visual_need[:55]
                if ranked:
                    kept = ranked[:keep_per_shot]
                    for r in kept:
                        if r.start_time_seconds is not None:
                            bucket = r.start_time_seconds // 30
                            used_timestamps.setdefault(r.video_id, set()).add(bucket)
                    best = kept[0]
                    vf_label = f" vf={best.visual_fit:.0%}" if best.visual_fit > 0 else ""
                    tf_label = f" tf={best.topical_fit:.0%}" if best.topical_fit > 0 else ""
                    extra = f" (+{len(kept)-1} alt)" if len(kept) > 1 else ""
                    _log_activity(job_id, "check", f"✓ \"{short_need}\" → \"{best.video_title[:50]}\" ({best.relevance_score:.0%}{vf_label}{tf_label}){extra}", depth=2, group=seg_group)
                    seg_ranked.extend(kept)
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

        # --- Re-search pass: failure-mode-aware, 2-attempt cap ---
        from app.models.schemas import AuditStatus, Scarcity, ShotIntent
        RESEARCH_THRESHOLD = 0.5
        MAX_RESEARCH_ATTEMPTS = 3
        
        low_conf_shots: list[tuple[Segment, BRollShot, Optional[RankedResult]]] = []
        for seg in active_segments:
            seg_results = all_segment_results.get(seg.segment_id, [])
            shots_for_seg = [shot for s, shot in all_shots if s.segment_id == seg.segment_id]
            for shot in shots_for_seg:
                matching_result = next((r for r in seg_results if r.shot_id == shot.shot_id), None)
                if matching_result is None or matching_result.relevance_score < RESEARCH_THRESHOLD:
                    low_conf_shots.append((seg, shot, matching_result))

        if low_conf_shots:
            _set_progress(job_id, "matching", 90, f"Re-searching {len(low_conf_shots)} low-confidence shots...")
            _log_activity(job_id, "search", f"Found {len(low_conf_shots)} shots below {RESEARCH_THRESHOLD:.0%} — running failure-mode-aware re-search (up to {MAX_RESEARCH_ATTEMPTS} attempts each)", group="research")

            from app.services.expand_shots import _generate_alternative_queries
            research_improved = 0

            # Detect consecutive similar low-scoring shots for consolidation suggestion
            prev_shot_desc = ""
            consecutive_similar = []
            for i, (seg, shot, existing) in enumerate(low_conf_shots):
                if prev_shot_desc and _text_similarity(prev_shot_desc, shot.visual_need) > 0.6:
                    consecutive_similar.append((i - 1, i))
                prev_shot_desc = shot.visual_need

            if consecutive_similar:
                for a, b in consecutive_similar[:3]:
                    _log_activity(job_id, "alert",
                                  f"Consolidation candidate: shots \"{low_conf_shots[a][1].visual_need[:40]}\" and \"{low_conf_shots[b][1].visual_need[:40]}\" are similar and both scored low",
                                  depth=1, group="research")

            for seg, shot, existing_result in low_conf_shots:
                old_score = existing_result.relevance_score if existing_result else 0
                short_need = shot.visual_need[:50]
                best_attempt_score = old_score
                best_attempt_result = existing_result

                for attempt in range(MAX_RESEARCH_ATTEMPTS):
                    try:
                        # Failure-mode-aware query generation
                        query_modifiers = []
                        if existing_result is None:
                            query_modifiers = ["footage", "b-roll", "cinematic"]
                            _log_activity(job_id, "search", f"Attempt {attempt + 1}: \"{short_need}\" — no results, broadening + trying different languages", depth=1, group="research")
                        elif existing_result.visual_fit < 0.4:
                            query_modifiers = ["footage", "b-roll", "cinematic", "drone"]
                            _log_activity(job_id, "search", f"Attempt {attempt + 1}: \"{short_need}\" — low visual_fit ({existing_result.visual_fit:.0%}), adding visual modifiers", depth=1, group="research")
                        elif existing_result.topical_fit < 0.4 and getattr(shot, 'shot_intent', ShotIntent.LITERAL) in (ShotIntent.ILLUSTRATIVE, ShotIntent.ATMOSPHERIC):
                            _log_activity(job_id, "search", f"Attempt {attempt + 1}: \"{short_need}\" — low topical_fit but {shot.shot_intent.value} shot, recalculating with intent weights before re-searching", depth=1, group="research")
                        else:
                            _log_activity(job_id, "search", f"Attempt {attempt + 1}: \"{short_need}\" — generating alternative queries", depth=1, group="research")

                        alt_queries = await _generate_alternative_queries(shot, script_context)
                        if not alt_queries:
                            break

                        if query_modifiers and alt_queries:
                            alt_queries.append(f"{alt_queries[0]} {query_modifiers[attempt % len(query_modifiers)]}")

                        alt_shot = BRollShot(
                            shot_id=shot.shot_id,
                            visual_need=shot.visual_need,
                            visual_description=getattr(shot, 'visual_description', ''),
                            search_queries=alt_queries,
                            key_terms=shot.key_terms,
                            shot_intent=getattr(shot, 'shot_intent', ShotIntent.LITERAL),
                            scarcity=getattr(shot, 'scarcity', Scarcity.COMMON),
                            preferred_source_type=getattr(shot, 'preferred_source_type', ''),
                        )
                        new_cands = await searcher.search_for_shot(alt_shot, seg, job_id=job_id, script_context=script_context)
                        if not new_cands:
                            continue

                        existing_video_ids = {r.video_id for r in all_results}
                        new_cands = [c for c in new_cands if c.video_id not in existing_video_ids]
                        if not new_cands:
                            _log_activity(job_id, "alert", f"Re-search attempt {attempt + 1} for \"{short_need}\" found only duplicates", depth=2, group="research")
                            continue

                        matched_new = await _match_candidates(
                            new_cands[:8], seg, matcher, transcriber, job_id,
                            max_concurrent_candidates,
                            script_context=script_context,
                            shot=alt_shot,
                        )
                        if not matched_new:
                            continue

                        ranked_new = ranker.rank_and_filter(matched_new, seg, settings=pipeline_cfg, script_context=script_context, shot=alt_shot)
                        if ranked_new and ranked_new[0].relevance_score > best_attempt_score:
                            best_attempt_result = ranked_new[0]
                            best_attempt_score = best_attempt_result.relevance_score
                            _log_activity(job_id, "check",
                                          f"Attempt {attempt + 1} upgrade: \"{short_need}\" → {best_attempt_score:.0%} (\"{best_attempt_result.video_title[:40]}\")",
                                          depth=1, group="research")
                            if best_attempt_score >= 0.6:
                                break

                    except Exception:
                        logger.exception("Re-search attempt %d failed for shot %s", attempt + 1, shot.shot_id)

                if best_attempt_result and best_attempt_score > old_score:
                    seg_results = all_segment_results.get(seg.segment_id, [])
                    if existing_result:
                        all_segment_results[seg.segment_id] = [
                            best_attempt_result if r.result_id == existing_result.result_id else r
                            for r in seg_results
                        ]
                    else:
                        all_segment_results.setdefault(seg.segment_id, []).append(best_attempt_result)
                    research_improved += 1
                elif best_attempt_result is None or best_attempt_score < RESEARCH_THRESHOLD:
                    _log_activity(job_id, "alert",
                                  f"No good match found for \"{short_need}\" after {MAX_RESEARCH_ATTEMPTS} attempts — keeping best available ({best_attempt_score:.0%})",
                                  depth=1, group="research")

            if research_improved:
                _log_activity(job_id, "check", f"Re-search improved {research_improved} of {len(low_conf_shots)} low-confidence shots", group="research")
                all_results = []
                for results in all_segment_results.values():
                    all_results.extend(results)
            else:
                _log_activity(job_id, "alert", f"Re-search could not improve any of the {len(low_conf_shots)} low-confidence shots", group="research")

        # --- Context audit: three-tier pass/review/reject ---
        if script_context.script_topic and all_results:
            try:
                _log_activity(job_id, "shield", "Running context audit — checking if clips would confuse viewers watching a documentary about \"{topic}\"".format(topic=script_context.script_topic[:60]), group="rank")
                all_results, review_count, reject_count = await _audit_context(
                    all_results, script_context, matcher, job_id,
                )
                if reject_count or review_count:
                    parts = []
                    if reject_count:
                        parts.append(f"{reject_count} rejected (0.40x penalty, moved to bottom)")
                    if review_count:
                        parts.append(f"{review_count} flagged for review (0.85x penalty, yellow badge)")
                    _log_activity(job_id, "alert", f"Context audit: {', '.join(parts)}", depth=1, group="rank")
                else:
                    _log_activity(job_id, "check", "Context audit passed — all clips appropriate for the documentary", depth=1, group="rank")
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
        elapsed_min = round(elapsed / 60, 1)
        elapsed_hr = round(elapsed / 3600, 1)
        api_costs = cost_tracker.end_job(job_id) or {}
        qt = get_quota_tracker()
        qt_stats = qt.stats
        api_costs["ytdlp_searches"] = qt_stats.get("ytdlp_searches_via_agent", 0)
        api_costs["ytdlp_detail_lookups"] = qt_stats.get("ytdlp_detail_lookups_via_agent", 0)
        api_costs["search_mode"] = qt_stats.get("search_mode", "ytdlp")
        qt.reset_for_job()

        est_cost = api_costs.get("estimated_cost_usd", 0)
        time_label = f"{elapsed_hr}h" if elapsed_hr >= 1 else f"{elapsed_min}m"
        _log_activity(job_id, "sparkles",
            f"Pipeline summary: {time_label} total | "
            f"GPT-4o (translation) → yt-dlp ({unique_videos} videos) → "
            f"Whisper ({whisper_ok} transcribed) → "
            f"{match_label} ({total_match_pairs} matches) → "
            f"{shots_filled} clips | cost: ${est_cost:.4f}",
            group="done")
        _log_activity(job_id, "clock", f"Completed in {time_label} ({elapsed:.0f}s)", group="done")

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
        qt = get_quota_tracker()
        qt_stats = qt.stats
        api_costs["ytdlp_searches"] = qt_stats.get("ytdlp_searches_via_agent", 0)
        api_costs["ytdlp_detail_lookups"] = qt_stats.get("ytdlp_detail_lookups_via_agent", 0)
        qt.reset_for_job()
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
        qt = get_quota_tracker()
        qt_stats = qt.stats
        api_costs["ytdlp_searches"] = qt_stats.get("ytdlp_searches_via_agent", 0)
        api_costs["ytdlp_detail_lookups"] = qt_stats.get("ytdlp_detail_lookups_via_agent", 0)
        qt.reset_for_job()
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


def _text_similarity(a: str, b: str) -> float:
    """Simple Jaccard word overlap for consolidation detection."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


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
                    "channel_name": cand.channel_name,
                    "view_count": cand.view_count,
                    "transcript_source": transcript.transcript_source.value,
                    "is_preferred_tier1": cand.is_preferred_tier1,
                    "is_preferred_tier2": cand.is_preferred_tier2,
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
) -> Tuple[List[RankedResult], int, int]:
    """Three-tier context audit: pass / review (0.85x) / reject (0.40x).

    Returns (updated_results, review_count, reject_count).
    Rejected clips are NEVER deleted — they get audit_status="reject" and a
    severe score penalty that sinks them below any passing clip.
    """
    from app.models.schemas import AuditStatus

    if len(all_results) < 3:
        for r in all_results:
            r.audit_status = AuditStatus.PASS
        return all_results, 0, 0

    clip_summaries = []
    for idx, r in enumerate(all_results):
        vf = f"visual_fit={r.visual_fit:.2f}" if r.visual_fit > 0 else ""
        clip_summaries.append(
            f"{idx}. \"{r.video_title}\" by {r.channel_name} "
            f"(segment {r.segment_id}, shot_intent={r.shot_intent.value}, "
            f"score={r.relevance_score:.2f} {vf})"
        )

    prompt = (
        "You are a documentary editor doing a final quality check on B-roll clips.\n\n"
        f"This documentary is about: {script_context.script_topic}\n"
        f"Geographic scope: {script_context.geographic_scope}\n"
        f"Domain: {script_context.script_domain}\n"
        f"Time period: {script_context.temporal_scope}\n"
        f"NOT about: {script_context.exclusion_context}\n\n"
        "For each clip, ask: 'Would this 5-10 second clip CONFUSE a viewer watching a documentary about "
        f"{script_context.script_topic}?'\n\n"
        "Selected B-roll clips:\n"
        + "\n".join(clip_summaries) +
        "\n\nCheck each clip for SPECIFIC CONCRETE problems:\n"
        "- Visible on-screen text about an unrelated topic\n"
        "- Watermarks or branding from other productions\n"
        "- Misleading visuals (clip about a different event/location with similar name)\n"
        "- Quality mismatch (low-res, phone footage in a polished documentary)\n\n"
        "IMPORTANT: Clips with shot_intent='atmospheric' or 'illustrative' get a LONGER LEASH — "
        "they don't need to be topically precise, just visually appropriate and not confusing.\n"
        "Clips with high visual_fit scores should also be given more latitude.\n\n"
        "Assign each clip a verdict:\n"
        "- 'pass': no issues, appropriate for the documentary\n"
        "- 'review': minor concerns the editor should be aware of (show a yellow warning badge)\n"
        "- 'reject': concrete problem that would confuse viewers (clip gets a severe score penalty but is NOT deleted)\n\n"
        "Return JSON only:\n"
        '{"audited": [{"index": 0, "verdict": "pass|review|reject", "reason": "why", "concern_type": "none|text_mismatch|watermark|misleading|quality|geographic_mismatch|temporal_mismatch"}, ...]}\n'
        "Include ALL clips in the response, not just flagged ones."
    )

    backend = matcher._get("matcher_backend", "auto")
    parsed = await matcher._route_call(prompt, backend, job_id)
    if not parsed:
        for r in all_results:
            r.audit_status = AuditStatus.PASS
        return all_results, 0, 0

    audit_map: dict[int, dict] = {}
    for entry in parsed.get("audited", parsed.get("flagged", [])):
        try:
            idx = int(entry["index"])
            audit_map[idx] = entry
        except (KeyError, ValueError, TypeError):
            continue

    review_count = 0
    reject_count = 0
    audit_records = []

    for idx, r in enumerate(all_results):
        entry = audit_map.get(idx, {})
        verdict = entry.get("verdict", "pass").lower().strip()
        reason = entry.get("reason", "")
        concern_type = entry.get("concern_type", "none")

        if verdict == "reject":
            r.audit_status = AuditStatus.REJECT
            r.audit_reason = reason
            r.relevance_score = round(max(0.0, r.relevance_score * 0.40), 4)
            reject_count += 1
            logger.info("Audit REJECT [%d] %s — %s", idx, r.video_title[:60], reason)
        elif verdict == "review":
            r.audit_status = AuditStatus.REVIEW
            r.audit_reason = reason
            r.relevance_score = round(max(0.0, r.relevance_score * 0.85), 4)
            review_count += 1
            logger.info("Audit REVIEW [%d] %s — %s", idx, r.video_title[:60], reason)
        else:
            r.audit_status = AuditStatus.PASS
            r.audit_reason = None

        audit_records.append({
            "clip_index": idx,
            "result_id": r.result_id,
            "video_id": r.video_id,
            "video_title": r.video_title[:100],
            "channel_name": r.channel_name,
            "segment_id": r.segment_id,
            "shot_id": r.shot_id,
            "relevance_score": r.relevance_score,
            "visual_fit": r.visual_fit,
            "topical_fit": r.topical_fit,
            "verdict": r.audit_status.value,
            "reason": reason,
            "concern_type": concern_type,
        })

    # Log audit decisions to DynamoDB
    if job_id and audit_records:
        try:
            storage = get_storage()
            await storage.store_audit_log(job_id, audit_records)
        except Exception:
            logger.warning("Failed to store audit log for job %s", job_id)

    # Re-sort: rejected clips sink to bottom but remain in list
    all_results.sort(key=lambda r: (
        0 if r.audit_status == AuditStatus.PASS else (1 if r.audit_status == AuditStatus.REVIEW else 2),
        -r.relevance_score,
    ))

    return all_results, review_count, reject_count
