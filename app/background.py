import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.config import DEFAULTS
from app.models.schemas import (
    CandidateVideo, JobStatus, MatchResult, RankedResult, Segment,
)
from app.services.matcher import MatcherService
from app.services.ranker import RankerService
from app.services.searcher import SearcherService
from app.services.storage import get_storage
from app.services.transcriber import TranscriberService
from app.services.translator import TranslatorService
from app.utils.cost_tracker import get_cost_tracker

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


def _log_activity(job_id: str, icon: str, text: str) -> None:
    existing = _progress.get(job_id, {})
    log = existing.get("activity_log", [])
    log.append({
        "time": datetime.utcnow().strftime("%H:%M:%S"),
        "icon": icon,
        "text": text,
    })
    if len(log) > 50:
        log = log[-50:]
    existing["activity_log"] = log
    _progress[job_id] = existing


async def run_pipeline(job_id: str, script: str, editor_id: str = "default_editor") -> None:
    """Full pipeline: translate -> search -> match -> rank -> store."""
    storage = get_storage()
    cost_tracker = get_cost_tracker()

    start_time = time.time()
    script_hash = hashlib.sha256(script.encode()).hexdigest()[:16]

    cost_tracker.start_job(job_id)
    await storage.create_job(job_id, script_hash, editor_id)

    try:
        # --- Stage 1: Translation ---
        _set_progress(job_id, "translating", 5, "Translating and segmenting script...")
        _log_activity(job_id, "brain", "Reading your Tamil script and sending it to GPT-4o for translation")
        translator = TranslatorService()
        segments, english_translation = await translator.translate_and_segment(script, job_id)

        word_count = len(script.split())
        script_duration = max(1, round(word_count / 150))

        _log_activity(job_id, "brain", f"GPT-4o translated {word_count} words and identified {len(segments)} distinct visual segments")
        for seg in segments:
            _log_activity(job_id, "sparkles", f"Segment \"{seg.title}\" — visual need: {seg.visual_need}")

        await storage.store_segments(job_id, segments)
        await storage.update_job_status(
            job_id, JobStatus.PROCESSING,
            segment_count=len(segments),
            script_duration_minutes=script_duration,
            english_translation=english_translation,
        )

        # --- Stage 2: Searching ---
        _set_progress(job_id, "searching", 20, f"Searching for B-roll across {len(segments)} segments...")
        _log_activity(job_id, "search", f"Starting multi-source search across YouTube, Google, and Gemini for {len(segments)} segments")
        searcher = SearcherService()

        async def search_progress(current: int, total: int, msg: str):
            pct = 20 + int(30 * current / max(total, 1))
            _set_progress(job_id, "searching", pct, msg)
            _log_activity(job_id, "globe", msg)

        candidates_by_segment = await searcher.search_batch(
            segments, job_id=job_id, progress_callback=search_progress,
        )

        total_candidates = sum(len(v) for v in candidates_by_segment.values())
        _log_activity(job_id, "check", f"Found {total_candidates} candidate videos across all segments")
        for seg_id, cands in candidates_by_segment.items():
            if cands:
                tier1 = sum(1 for c in cands if c.is_preferred_tier1)
                tier2 = sum(1 for c in cands if c.is_preferred_tier2)
                tier_info = ""
                if tier1:
                    tier_info += f", {tier1} from preferred channels"
                if tier2:
                    tier_info += f", {tier2} from trusted channels"
                _log_activity(job_id, "search", f"{seg_id}: {len(cands)} candidates{tier_info}")

        # --- Stage 3: Matching ---
        _set_progress(job_id, "matching", 55, "Finding timestamps and peak visual moments...")
        _log_activity(job_id, "eye", "Now analysing each video to find the exact moment that matches your script")
        matcher = MatcherService()
        transcriber = TranscriberService()
        ranker = RankerService()

        max_concurrent_candidates = DEFAULTS.get("max_concurrent_candidates", 3)
        segment_timeout = DEFAULTS.get("segment_timeout_sec", 60)

        all_segment_results: Dict[str, List[RankedResult]] = {}
        total_segments = len(segments)

        for seg_idx, segment in enumerate(segments):
            pct = 55 + int(35 * seg_idx / max(total_segments, 1))
            _set_progress(
                job_id, "matching", pct,
                f"Processing segment {seg_idx + 1}/{total_segments}: {segment.title}",
            )
            _log_activity(job_id, "zap", f"Processing segment {seg_idx + 1}/{total_segments}: \"{segment.title}\"")

            cands = candidates_by_segment.get(segment.segment_id, [])
            if not cands:
                _log_activity(job_id, "alert", f"No candidates found for \"{segment.title}\" — skipping")
                all_segment_results[segment.segment_id] = []
                continue

            _log_activity(job_id, "mic", f"Fetching transcripts for {len(cands)} videos (checking cache → YouTube captions → auto-captions)")

            try:
                matched = await asyncio.wait_for(
                    _match_candidates(
                        cands, segment, matcher, transcriber, job_id,
                        max_concurrent_candidates,
                    ),
                    timeout=segment_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Segment %s timed out", segment.segment_id)
                _log_activity(job_id, "alert", f"Segment \"{segment.title}\" timed out after {segment_timeout}s — moving on")
                matched = []

            if matched:
                _log_activity(job_id, "eye", f"GPT-4o-mini found timestamps in {len(matched)} of {len(cands)} videos for \"{segment.title}\"")
                for cand, match in matched[:3]:
                    hook_text = f" — \"{match.the_hook}\"" if match.the_hook else ""
                    _log_activity(job_id, "sparkles", f"  {cand.video_title[:60]} @ {match.start_time_seconds}s (confidence: {match.confidence_score:.0%}){hook_text}")

            ranked = ranker.rank_and_filter(matched, segment)
            _log_activity(job_id, "filter", f"Ranked and filtered to {len(ranked)} top clips for \"{segment.title}\"")
            all_segment_results[segment.segment_id] = ranked

        # --- Cross-segment dedup ---
        _log_activity(job_id, "shield", "Removing duplicate clips that appear across multiple segments")
        all_segment_results = ranker.deduplicate_across_segments(all_segment_results)

        all_results: List[RankedResult] = []
        for results in all_segment_results.values():
            all_results.extend(results)

        low_threshold = DEFAULTS.get("low_result_threshold", 20)
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
                _set_progress(job_id, "matching", 92, "Running recovery search for missing segments...")
                _log_activity(job_id, "alert", f"Only {len(all_results)} results found — below minimum. Re-searching {len(empty_segments)} empty segments with broader queries")
                recovery = await searcher.search_batch(
                    empty_segments, job_id=job_id,
                )
                for seg in empty_segments:
                    new_cands = recovery.get(seg.segment_id, [])
                    if new_cands:
                        _log_activity(job_id, "search", f"Recovery found {len(new_cands)} new candidates for \"{seg.title}\"")
                        try:
                            matched = await asyncio.wait_for(
                                _match_candidates(
                                    new_cands, seg, matcher, transcriber, job_id,
                                    max_concurrent_candidates,
                                ),
                                timeout=segment_timeout,
                            )
                            ranked = ranker.rank_and_filter(matched, seg)
                            all_segment_results[seg.segment_id] = ranked
                            all_results.extend(ranked)
                        except asyncio.TimeoutError:
                            pass

        minimum_results_met = len(all_results) >= script_duration

        # --- Stage 4: Storing ---
        _set_progress(job_id, "ranking", 95, "Storing results...")
        _log_activity(job_id, "check", f"Final tally: {len(all_results)} timestamped B-roll clips across {total_segments} segments")
        await storage.store_results(job_id, all_results)

        elapsed = round(time.time() - start_time, 2)
        api_costs = cost_tracker.end_job(job_id) or {}

        est_cost = api_costs.get("estimated_cost_usd", 0)
        _log_activity(job_id, "clock", f"Completed in {elapsed:.1f}s — estimated API cost: ${est_cost:.4f}")

        await storage.update_job_status(
            job_id, JobStatus.COMPLETE,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
            result_count=len(all_results),
            api_costs=api_costs,
            minimum_results_met=minimum_results_met,
        )
        _set_progress(job_id, "completed", 100, "Scouting complete!")
        _log_activity(job_id, "check", "Done! Your B-roll results are ready.")
        logger.info("Job %s complete: %d results in %.1fs", job_id, len(all_results), elapsed)

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job_id)
        _log_activity(job_id, "alert", f"Pipeline failed: {str(exc)[:200]}")
        elapsed = round(time.time() - start_time, 2)
        cost_tracker.end_job(job_id)
        await storage.update_job_status(
            job_id, JobStatus.FAILED,
            completed_at=datetime.utcnow().isoformat(),
            processing_time_seconds=elapsed,
        )
        _set_progress(job_id, "failed", 0, "Pipeline failed")


async def _match_candidates(
    candidates: List[CandidateVideo],
    segment: Segment,
    matcher: MatcherService,
    transcriber: TranscriberService,
    job_id: str,
    max_concurrent: int,
) -> List[Tuple[CandidateVideo, MatchResult]]:
    semaphore = asyncio.Semaphore(max_concurrent)
    results: List[Tuple[CandidateVideo, MatchResult]] = []
    lock = asyncio.Lock()

    async def process_one(cand: CandidateVideo):
        async with semaphore:
            try:
                transcript = await transcriber.get_transcript(
                    cand.video_id,
                    video_duration_seconds=cand.video_duration_seconds,
                    job_id=job_id,
                )

                video_meta = {
                    "video_duration_seconds": cand.video_duration_seconds,
                    "video_title": cand.video_title,
                    "view_count": cand.view_count,
                    "transcript_source": transcript.transcript_source.value,
                }

                match = await matcher.find_timestamp(
                    transcript.transcript_text, segment, video_meta, job_id
                )
                match = matcher.validate_context_match(
                    match, cand.video_duration_seconds
                )

                async with lock:
                    results.append((cand, match))
            except Exception:
                logger.exception(
                    "Failed to match %s for %s", cand.video_id, segment.segment_id
                )

    await asyncio.gather(*[process_one(c) for c in candidates])
    return results
