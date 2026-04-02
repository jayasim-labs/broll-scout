import asyncio
import json
import logging
import re

import httpx

from app.config import get_settings, DEFAULTS
from app.models.schemas import Segment, CandidateVideo
from app.utils.cost_tracker import get_cost_tracker
from app.utils import agent_queue

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


# ---------------------------------------------------------------------------
# Dispatchers — all searches go through the local yt-dlp companion agent
# ---------------------------------------------------------------------------

async def _dispatch_search(
    query: str,
    max_results: int = 5,
    job_id: str | None = None,
    backend: str = "ytdlp_only",
) -> list[dict]:
    task_id = await agent_queue.create_task("search", {
        "query": query,
        "max_results": max_results,
    })
    return await agent_queue.wait_for_result(task_id)


async def _dispatch_channel_search(
    channel_id: str,
    query: str,
    max_results: int = 5,
    job_id: str | None = None,
    backend: str = "ytdlp_only",
) -> list[dict]:
    task_id = await agent_queue.create_task("channel_search", {
        "channel_id": channel_id,
        "query": query,
        "max_results": max_results,
    })
    return await agent_queue.wait_for_result(task_id)


async def _dispatch_video_details(
    video_ids: list[str],
    job_id: str | None = None,
    backend: str = "ytdlp_only",
) -> list[dict]:
    task_id = await agent_queue.create_task("video_details", {
        "video_ids": video_ids,
    })
    return await agent_queue.wait_for_result(task_id)



# ---------------------------------------------------------------------------
# SearcherService
# ---------------------------------------------------------------------------

class SearcherService:
    """Searches YouTube/yt-dlp and optionally Gemini for candidate B-roll videos."""

    def __init__(self, pipeline_settings: dict | None = None):
        self._settings = get_settings()
        self._cost_tracker = get_cost_tracker()
        self._pipeline = pipeline_settings or {}

    def _get(self, key: str):
        if key in self._pipeline:
            return self._pipeline[key]
        return DEFAULTS.get(key)


    def _build_blocked_set(self) -> set[str]:
        blocked: list[str] = []
        blocked.extend(self._get("blocked_networks") or [])
        blocked.extend(self._get("blocked_studios") or [])
        blocked.extend(self._get("blocked_sports") or [])
        custom = self._get("custom_block_rules") or ""
        if custom:
            blocked.extend(line.strip() for line in custom.split("\n") if line.strip())
        return {name.lower() for name in blocked if name}

    @staticmethod
    def _is_blocked(channel_name: str, video_title: str, blocked_set: set[str]) -> bool:
        ch = channel_name.lower()
        for term in blocked_set:
            if term in ch:
                return True
        return False

    async def search_for_segment(
        self, segment: Segment, job_id: str | None = None, on_progress=None,
        seg_number: int = 0, total_segments: int = 0,
    ) -> list[CandidateVideo]:
        async def _emit(icon: str, text: str, depth: int = 2):
            if on_progress:
                try:
                    await on_progress(icon, text, depth)
                except Exception:
                    pass

        tier1_ids: list[str] = self._get("preferred_channels_tier1") or []
        tier2_names: list[str] = self._get("preferred_channels_tier2") or []
        results_per_query: int = self._get("youtube_results_per_query") or 5
        max_candidates: int = self._get("max_candidates_per_segment") or 12
        min_duration: int = self._get("min_video_duration_sec") or 120
        max_duration: int = self._get("max_video_duration_sec") or 5400

        all_video_ids: list[str] = []
        search_metadata: dict[str, dict] = {}

        query_text = " ".join(segment.key_terms)
        seg_label = segment.title[:50]

        scene_prefix = f"Scene {seg_number}/{total_segments}: " if seg_number else ""
        await _emit("search", f"{scene_prefix}\"{seg_label}\" — finding B-roll candidates", depth=1)

        def _collect(results: list[dict]) -> list[str]:
            ids = []
            for r in results:
                vid = r.get("video_id", "")
                if vid:
                    ids.append(vid)
                    if vid not in search_metadata:
                        search_metadata[vid] = r
            return ids

        # (a) Preferred Channel Search (Tier 1)
        if tier1_ids:
            await _emit("search", f"Checking {len(tier1_ids)} preferred channels")
            tier1_video_ids = await self._search_tier1_channels_full(
                tier1_ids, query_text, results_per_query, job_id, "ytdlp_only", _emit
            )
            all_video_ids.extend(_collect(tier1_video_ids))
            if tier1_video_ids:
                await _emit("check", f"Found {len(tier1_video_ids)} videos from preferred channels")
            else:
                await _emit("alert", f"No matching videos on preferred channels — moving to broader search")
        else:
            tier1_video_ids = []

        # (b) yt-dlp Primary Search
        queries_str = " → ".join(q[:40] for q in segment.search_queries[:3])
        await _emit("globe", f"Searching yt-dlp for: {queries_str}")
        yt_results = await self._search_youtube_primary_full(
            segment.search_queries, results_per_query, job_id, "ytdlp_only", _emit
        )
        all_video_ids.extend(_collect(yt_results))
        await _emit("check", f"yt-dlp returned {len(yt_results)} videos")

        # (c) Gemini Query Expansion (optional, off by default)
        if self._get("enable_gemini_expansion"):
            await _emit("sparkles", f"Asking Gemini AI to suggest creative search angles...")
            initial_titles = list({m.get("title", "") for m in search_metadata.values() if m.get("title")})
            expanded_results = await self._gemini_expand_and_search_full(
                segment.summary, initial_titles, results_per_query, job_id, "ytdlp_only", _emit
            )
            new_from_gemini = 0
            for r in expanded_results:
                vid = r.get("video_id", "")
                if vid and vid not in all_video_ids:
                    all_video_ids.append(vid)
                    new_from_gemini += 1
                    if vid not in search_metadata:
                        search_metadata[vid] = r
            if new_from_gemini:
                await _emit("sparkles", f"Gemini's creative queries found {new_from_gemini} more videos")

        # (d) Batch Video Details
        unique_ids = list(dict.fromkeys(all_video_ids))
        dupes_removed = len(all_video_ids) - len(unique_ids)
        if not unique_ids:
            await _emit("alert", f"No videos found from any source")
            return []

        dupe_note = f" ({dupes_removed} duplicates removed)" if dupes_removed else ""
        video_details = [search_metadata[vid] for vid in unique_ids if vid in search_metadata]
        ids_missing = [vid for vid in unique_ids if vid not in search_metadata]
        if ids_missing:
            await _emit("eye", f"Fetching details for {len(ids_missing)} videos not yet cached...")
            await _emit("terminal", f"▸ yt-dlp --dump-json for {len(ids_missing)} video IDs")
            extra = await _dispatch_video_details(ids_missing, job_id=job_id)
            video_details.extend(extra)
        else:
            await _emit("eye", f"Already have full metadata for all {len(video_details)} videos{dupe_note}")

        channel_stats: dict[str, dict] = {}

        # (f) Build CandidateVideo objects
        tier1_set = set(tier1_ids)
        tier2_lower = {name.lower() for name in tier2_names}
        blocked_set = self._build_blocked_set()

        candidates: list[CandidateVideo] = []
        seen_ids: set[str] = set()
        blocked_count = 0
        duration_filtered = 0
        vertical_filtered = 0

        for v in video_details:
            vid = v.get("video_id", "")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            duration = v.get("duration_seconds") or v.get("video_duration_seconds") or 0
            if duration < min_duration or duration > max_duration:
                duration_filtered += 1
                continue

            w = v.get("width") or 0
            h = v.get("height") or 0
            if w > 0 and h > 0 and h > w:
                vertical_filtered += 1
                continue

            ch_id = v.get("channel_id", "")
            ch_name = v.get("channel_name", "")
            ch_stats = channel_stats.get(ch_id, {})
            subscribers = ch_stats.get("subscriber_count") or v.get("channel_subscribers") or 0

            video_title = v.get("title") or v.get("video_title", "")
            if self._is_blocked(ch_name, video_title, blocked_set):
                blocked_count += 1
                continue

            candidate = CandidateVideo(
                video_id=vid,
                video_url=f"https://www.youtube.com/watch?v={vid}",
                video_title=video_title,
                channel_name=ch_name,
                channel_id=ch_id,
                channel_subscribers=subscribers or 0,
                thumbnail_url=v.get("thumbnail_url", ""),
                video_duration_seconds=duration,
                published_at=v.get("published_at", ""),
                view_count=v.get("view_count") or 0,
                is_preferred_tier1=ch_id in tier1_set,
                is_preferred_tier2=ch_name.lower() in tier2_lower,
                is_blocked=False,
            )
            candidates.append(candidate)

        filter_notes = []
        if duration_filtered:
            filter_notes.append(f"{duration_filtered} too short/long")
        if vertical_filtered:
            filter_notes.append(f"{vertical_filtered} vertical/shorts")
        if blocked_count:
            filter_notes.append(f"{blocked_count} from blocked channels")
        filter_text = f" (removed {', '.join(filter_notes)})" if filter_notes else ""
        await _emit("check", f"{len(candidates)} usable videos ready for transcript analysis{filter_text}")

        return candidates[:max_candidates]

    async def search_batch(
        self,
        segments: list[Segment],
        job_id: str | None = None,
        progress_callback=None,
        on_activity=None,
    ) -> dict[str, list[CandidateVideo]]:
        tier1_ids = self._get("preferred_channels_tier1") or []
        has_preferred = len(tier1_ids) > 0
        if has_preferred:
            max_concurrent = 3
        else:
            max_concurrent = min(self._get("max_concurrent_segments") or 3, 3)
        semaphore = asyncio.Semaphore(max_concurrent)
        results: dict[str, list[CandidateVideo]] = {}
        total = len(segments)
        completed = 0
        lock = asyncio.Lock()

        async def _process(seg_idx: int, seg: Segment):
            nonlocal completed
            async with semaphore:
                try:
                    candidates = await self.search_for_segment(
                        seg, job_id=job_id, on_progress=on_activity,
                        seg_number=seg_idx + 1, total_segments=total,
                    )
                except Exception:
                    logger.exception("Failed to search segment %s", seg.segment_id)
                    candidates = []
                results[seg.segment_id] = candidates
                async with lock:
                    completed += 1
                    if progress_callback:
                        try:
                            found = sum(len(v) for v in results.values())
                            await progress_callback(
                                completed, total,
                                f"Searched {completed} of {total} scenes — {found} videos found so far"
                            )
                        except Exception:
                            pass

        await asyncio.gather(*[_process(i, seg) for i, seg in enumerate(segments)])
        return results

    # ── Tier 1 channel search ───────────────────────────────────────────

    async def _search_tier1_channels_full(
        self,
        channel_ids: list[str],
        query: str,
        max_results: int,
        job_id: str | None,
        backend: str = "ytdlp_only",
        emit=None,
    ) -> list[dict]:
        all_results: list[dict] = []
        for ch_id in channel_ids:
            try:
                if emit:
                    url = f"https://www.youtube.com/channel/{ch_id}/search?query={query}"
                    await emit("terminal", f"▸ yt-dlp \"{url}\" --flat-playlist --playlist-end {max_results}", 3)
                results = await _dispatch_channel_search(
                    channel_id=ch_id, query=query,
                    max_results=max_results, job_id=job_id, backend=backend,
                )
                found = [r for r in results if r.get("video_id")]
                all_results.extend(found)
                if emit and found:
                    await emit("check", f"→ {len(found)} videos from channel {ch_id[:15]}…", 3)
            except Exception:
                logger.warning("Tier 1 channel search failed for %s", ch_id)
        return all_results

    # ── YouTube / yt-dlp primary search ─────────────────────────────────

    async def _search_youtube_primary_full(
        self,
        queries: list[str],
        max_results: int,
        job_id: str | None,
        backend: str = "ytdlp_only",
        emit=None,
    ) -> list[dict]:
        all_results: list[dict] = []
        for q in queries:
            try:
                if emit:
                    await emit("terminal", f"▸ yt-dlp \"ytsearch{max_results}:{q}\" --dump-json --flat-playlist", 3)
                results = await _dispatch_search(
                    query=q, max_results=max_results, job_id=job_id, backend=backend,
                )
                found = [r for r in results if r.get("video_id")]
                all_results.extend(found)
                if emit:
                    await emit("check", f"→ {len(found)} results for \"{q[:50]}\"", 3)
            except Exception:
                logger.warning("Search failed for query: %s", q)
        return all_results

    # ── Gemini query expansion ──────────────────────────────────────────

    async def _gemini_expand_and_search_full(
        self,
        summary: str,
        initial_titles: list[str],
        max_results: int,
        job_id: str | None,
        backend: str = "ytdlp_only",
        emit=None,
    ) -> list[dict]:
        api_key = self._settings.gemini_api_key
        if not api_key:
            return []

        titles_text = "\n".join(f"- {t}" for t in initial_titles[:20])
        prompt = (
            "Given this documentary script segment summary and these initial YouTube "
            "search results, suggest 5 additional search queries that would find better "
            "B-roll footage. Think laterally — historical footage, related events, expert "
            "interviews, scientific visualizations, archival material. "
            "Return as JSON array of strings only.\n\n"
            f"Summary: {summary}\n\n"
            f"Initial results:\n{titles_text}"
        )

        expanded_queries = await self._call_gemini(prompt, api_key, job_id)
        if not expanded_queries:
            return []

        if emit:
            queries_preview = ", ".join(f'"{q[:35]}"' for q in expanded_queries[:3])
            await emit("sparkles", f"Gemini suggested: {queries_preview}{'…' if len(expanded_queries) > 3 else ''}", 3)

        all_results: list[dict] = []
        for q in expanded_queries:
            try:
                if emit:
                    await emit("terminal", f"▸ yt-dlp \"ytsearch{max_results}:{q}\" --dump-json --flat-playlist", 3)
                results = await _dispatch_search(
                    query=q, max_results=max_results, job_id=job_id, backend=backend,
                )
                found = [r for r in results if r.get("video_id")]
                all_results.extend(found)
                if emit and found:
                    await emit("check", f"→ {len(found)} results for \"{q[:50]}\"", 3)
            except Exception:
                logger.warning("Expanded query search failed for: %s", q)
        return all_results

    async def _call_gemini(
        self, prompt: str, api_key: str, job_id: str | None
    ) -> list[str]:
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{GEMINI_URL}?key={api_key}",
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()

            if job_id:
                self._cost_tracker.track_gemini(job_id)

            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)

            queries = json.loads(text)
            if isinstance(queries, list):
                return [str(q) for q in queries if q]
        except Exception:
            logger.warning("Gemini query expansion failed")
        return []
