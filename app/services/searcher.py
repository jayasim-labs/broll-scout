import asyncio
import json
import logging
import re
import time
from typing import Optional

import httpx

from app.config import get_settings, DEFAULTS
from app.models.schemas import BRollShot, ScriptContext, Segment, CandidateVideo
from app.utils.cost_tracker import get_cost_tracker
from app.utils import agent_queue

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

# In-memory search cache with 7-day TTL
_search_cache: dict[str, tuple[float, list[dict]]] = {}
SEARCH_CACHE_TTL = 7 * 24 * 3600

INDIAN_GEO_KEYWORDS = {
    "india", "indian", "tamil", "tamil nadu", "chennai", "mumbai", "delhi",
    "kolkata", "bengaluru", "hyderabad", "kerala", "karnataka", "maharashtra",
    "andhra", "telangana", "rajasthan", "gujarat", "punjab", "bengal",
    "assam", "bihar", "uttar pradesh", "madhya pradesh", "kashmir", "goa",
}

SOURCE_TYPE_MODIFIERS = {
    "documentary": ["documentary", "full documentary"],
    "news_clip": ["news report", "news coverage"],
    "stock_footage": ["stock footage", "b-roll footage", "royalty free"],
    "drone_aerial": ["drone footage", "aerial view", "drone 4k"],
    "interview": ["interview", "expert interview"],
    "timelapse": ["timelapse", "time lapse 4k"],
    "archival": ["archival footage", "historical footage", "archive"],
    "animation": ["animation", "animated explainer", "infographic"],
}


_GENERIC_WORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "is", "are", "was", "were", "be", "been", "with", "from", "by", "as",
    "its", "it", "this", "that", "these", "those", "their", "they",
    "not", "no", "has", "have", "had", "will", "would", "could", "should",
    "about", "into", "between", "through", "during", "after", "before",
    "global", "local", "new", "old", "big", "small", "great", "real",
    "secret", "network", "elite", "files", "story", "inside", "world",
    "documentary", "footage", "video", "clip", "full", "official",
    "scene", "part", "episode", "season", "series", "stock", "broll",
    "aerial", "drone", "cinematic", "visual", "visuals", "animation",
    "timelapse", "interview", "news", "report", "coverage",
})


def _get_anchor_keywords(script_context: ScriptContext) -> list[str]:
    """Return the top identity-bearing keywords for this script.

    Uses context_keywords (ranked by GPT-4o + TF fallback) if available,
    otherwise falls back to extracting from script_topic.
    """
    if script_context.context_keywords:
        return script_context.context_keywords

    # Legacy fallback: extract from script_topic
    topic = script_context.script_topic
    for sep in (" and ", " — ", " - ", ": "):
        if sep in topic:
            topic = topic.split(sep)[0]
            break
    words = [w for w in topic.split() if w.lower() not in _GENERIC_WORDS and len(w) > 2]
    return words[:5] if words else topic.split()[:1]


def _query_contains_anchor(query: str, anchor_keywords: list[str], top_n: int = 3) -> bool:
    """Check if the query already contains at least one of the top-N anchor keywords."""
    if not anchor_keywords:
        return True
    query_lower = query.lower()
    for kw in anchor_keywords[:top_n]:
        # Multi-word keywords: check as substring. Single words: word boundary.
        kw_lower = kw.lower()
        if " " in kw_lower:
            if kw_lower in query_lower:
                return True
        else:
            if kw_lower in query_lower:
                return True
    return False


def contextualize_query(query: str, script_context: ScriptContext) -> str:
    """Ensure every search query is anchored to the script's core subject.

    Uses ranked context_keywords from the script. The top keyword (position 0)
    is the primary anchor — the single most distinctive identifier.
    If the query already contains one of the top-3 keywords, it's considered
    anchored. Otherwise, prepend the #1 keyword.
    """
    if not script_context or not script_context.script_topic:
        return query
    anchor_keywords = _get_anchor_keywords(script_context)
    if not anchor_keywords:
        return query
    if _query_contains_anchor(query, anchor_keywords, top_n=3):
        return query
    return f"{anchor_keywords[0]} {query}"


def _is_indian_topic(script_context: Optional[ScriptContext]) -> bool:
    """Detect if the script relates to Indian topics for multilingual search."""
    if not script_context:
        return False
    text = f"{script_context.geographic_scope} {script_context.script_topic} {script_context.script_domain}".lower()
    return any(kw in text for kw in INDIAN_GEO_KEYWORDS)


def _generate_multilingual_queries(query: str, script_context: Optional[ScriptContext]) -> list[str]:
    """For Indian topics, generate parallel queries in Tamil and Hindi."""
    queries = [query]
    if _is_indian_topic(script_context):
        base = query.split()[:4]
        base_str = " ".join(base)
        queries.append(f"{base_str} தமிழ்")
        queries.append(f"{base_str} हिंदी")
    return queries


def _cached_search_key(query: str, max_results: int) -> str:
    return f"{query}::{max_results}"


def _get_cached_search(key: str) -> Optional[list[dict]]:
    entry = _search_cache.get(key)
    if entry and (time.time() - entry[0]) < SEARCH_CACHE_TTL:
        return entry[1]
    if entry:
        del _search_cache[key]
    return None


def _set_cached_search(key: str, results: list[dict]) -> None:
    _search_cache[key] = (time.time(), results)


def _is_portrait_aspect_ratio_9_16(width: int, height: int, tolerance: float) -> bool:
    """True when display size matches ~9:16 portrait (typical YouTube Shorts).

    Landscape and square are False. Other tall ratios (e.g. 4:5, 3:4) are False so
    vertical clips that are not Shorts-shaped can still be candidates.
    """
    if width <= 0 or height <= 0:
        return False
    if height <= width:
        return False
    ratio = width / height
    target = 9.0 / 16.0
    return abs(ratio - target) <= tolerance


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
    }, job_id=job_id)
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
    }, job_id=job_id)
    return await agent_queue.wait_for_result(task_id)


async def _dispatch_video_details(
    video_ids: list[str],
    job_id: str | None = None,
    backend: str = "ytdlp_only",
) -> list[dict]:
    task_id = await agent_queue.create_task("video_details", {
        "video_ids": video_ids,
    }, job_id=job_id)
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

    def _should_exclude_shorts_9_16_aspect(self, width: int, height: int) -> bool:
        """Exclude candidates whose resolution matches portrait ~9:16 (YouTube Shorts shape)."""
        flag = self._get("filter_9_16_shorts")
        if flag is False:
            return False
        tol_raw = self._get("shorts_9_16_aspect_tolerance")
        try:
            tol = float(tol_raw)
        except (TypeError, ValueError):
            tol = float(DEFAULTS["shorts_9_16_aspect_tolerance"])
        if tol <= 0:
            tol = float(DEFAULTS["shorts_9_16_aspect_tolerance"])
        return _is_portrait_aspect_ratio_9_16(width, height, tol)

    def _build_blocked_channel_ids(self) -> set[str]:
        sources = self._get("channel_sources") or []
        return {s["channel_id"] for s in sources if s.get("tier") == "blocked" and s.get("channel_id")}

    def _build_blocked_name_set(self) -> set[str]:
        blocked: list[str] = []
        blocked.extend(self._get("blocked_networks") or [])
        blocked.extend(self._get("blocked_studios") or [])
        blocked.extend(self._get("blocked_sports") or [])
        custom = self._get("custom_block_rules") or ""
        if custom:
            blocked.extend(line.strip() for line in custom.split("\n") if line.strip())
        return {name.lower() for name in blocked if name}

    def _build_preferred_channel_ids(self) -> tuple[set[str], set[str]]:
        sources = self._get("channel_sources") or []
        tier1 = {s["channel_id"] for s in sources if s.get("tier") == "tier1" and s.get("channel_id")}
        tier2 = {s["channel_id"] for s in sources if s.get("tier") == "tier2" and s.get("channel_id")}
        old_tier1 = set(self._get("preferred_channels_tier1") or [])
        return tier1 | old_tier1, tier2

    def _is_blocked(self, channel_id: str, channel_name: str, blocked_ids: set[str], blocked_names: set[str]) -> bool:
        if channel_id and channel_id in blocked_ids:
            return True
        ch = channel_name.lower()
        for term in blocked_names:
            if term in ch:
                return True
        return False

    async def search_for_segment(
        self, segment: Segment, job_id: str | None = None, on_progress=None,
        seg_number: int = 0, total_segments: int = 0,
        script_context: ScriptContext | None = None,
    ) -> list[CandidateVideo]:
        async def _emit(icon: str, text: str, depth: int = 2):
            if on_progress:
                try:
                    await on_progress(icon, text, depth)
                except Exception:
                    pass

        tier1_channel_ids, tier2_channel_ids = self._build_preferred_channel_ids()
        old_tier1_ids: list[str] = self._get("preferred_channels_tier1") or []
        tier1_ids = list(tier1_channel_ids | set(old_tier1_ids))
        results_per_query: int = self._get("youtube_results_per_query") or 8
        max_candidates: int = self._get("max_candidates_per_segment") or 15
        min_duration: int = int(self._get("min_video_duration_sec") or DEFAULTS["min_video_duration_sec"])
        max_duration: int = int(self._get("max_video_duration_sec") or DEFAULTS["max_video_duration_sec"])

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

        # (b) yt-dlp Primary Search — contextualize queries to prevent generic matches
        contextualized_queries = [
            contextualize_query(q, script_context) if script_context else q
            for q in segment.search_queries
        ]
        queries_str = " → ".join(q[:40] for q in contextualized_queries[:3])
        await _emit("globe", f"Searching yt-dlp for: {queries_str}")
        yt_results = await self._search_youtube_primary_full(
            contextualized_queries, results_per_query, job_id, "ytdlp_only", _emit
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
        blocked_ids = self._build_blocked_channel_ids()
        blocked_names = self._build_blocked_name_set()
        old_tier2_names = self._get("preferred_channels_tier2") or []
        tier2_name_lower = {name.lower() for name in old_tier2_names}

        candidates: list[CandidateVideo] = []
        seen_ids: set[str] = set()
        blocked_count = 0
        duration_filtered = 0
        shorts_aspect_filtered = 0

        for v in video_details:
            vid = v.get("video_id", "")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            duration = v.get("duration_seconds") or v.get("video_duration_seconds") or 0
            if duration < min_duration or duration > max_duration:
                duration_filtered += 1
                continue

            w = int(v.get("width") or 0)
            h = int(v.get("height") or 0)
            if self._should_exclude_shorts_9_16_aspect(w, h):
                shorts_aspect_filtered += 1
                continue

            ch_id = v.get("channel_id", "")
            ch_name = v.get("channel_name", "")
            ch_stats = channel_stats.get(ch_id, {})
            subscribers = ch_stats.get("subscriber_count") or v.get("channel_subscribers") or 0

            video_title = v.get("title") or v.get("video_title", "")
            if self._is_blocked(ch_id, ch_name, blocked_ids, blocked_names):
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
                is_preferred_tier1=ch_id in tier1_channel_ids,
                is_preferred_tier2=ch_id in tier2_channel_ids or ch_name.lower() in tier2_name_lower,
                is_blocked=False,
            )
            candidates.append(candidate)

        filter_notes = []
        if duration_filtered:
            filter_notes.append(f"{duration_filtered} too short/long")
        if shorts_aspect_filtered:
            filter_notes.append(f"{shorts_aspect_filtered} ~9:16 shorts-shaped")
        if blocked_count:
            filter_notes.append(f"{blocked_count} from blocked channels")
        filter_text = f" (removed {', '.join(filter_notes)})" if filter_notes else ""
        await _emit("check", f"{len(candidates)} usable videos ready for transcript analysis{filter_text}")

        return candidates[:max_candidates]

    async def search_for_shot(
        self, shot: BRollShot, segment: Segment,
        job_id: str | None = None, on_progress=None,
        script_context: ScriptContext | None = None,
    ) -> list[CandidateVideo]:
        """Search for a single B-roll shot using the shot's own queries and key_terms.

        Enhancements:
        - Parallel tier-1 channel + open YouTube search
        - Multilingual queries for Indian topics
        - preferred_source_type modifiers
        - 7-day search cache
        - Exclusion context as soft deprioritization
        """
        async def _emit(icon: str, text: str, depth: int = 2):
            if on_progress:
                try:
                    await on_progress(icon, text, depth)
                except Exception:
                    pass

        configured_per_query: int = self._get("youtube_results_per_query") or 8
        max_candidates: int = self._get("max_candidates_per_shot") or 12
        min_duration: int = int(self._get("min_video_duration_sec") or DEFAULTS["min_video_duration_sec"])
        max_duration: int = int(self._get("max_video_duration_sec") or DEFAULTS["max_video_duration_sec"])

        all_video_ids: list[str] = []
        search_metadata: dict[str, dict] = {}

        def _collect(results: list[dict]) -> list[str]:
            ids = []
            for r in results:
                vid = r.get("video_id", "")
                if vid:
                    ids.append(vid)
                    if vid not in search_metadata:
                        search_metadata[vid] = r
            return ids

        queries = [
            contextualize_query(q, script_context) if script_context else q
            for q in shot.search_queries
        ]
        if not queries:
            queries = [
                contextualize_query(q, script_context) if script_context else q
                for q in segment.search_queries[:2]
            ]

        # Add preferred_source_type modifier to one query
        pst = getattr(shot, 'preferred_source_type', '') or ''
        if pst and pst in SOURCE_TYPE_MODIFIERS:
            modifiers = SOURCE_TYPE_MODIFIERS[pst]
            if queries:
                queries.append(f"{queries[0]} {modifiers[0]}")

        # Add multilingual queries for Indian topics
        if _is_indian_topic(script_context) and queries:
            multilingual = _generate_multilingual_queries(queries[0], script_context)
            queries.extend(multilingual[1:])

        results_per_query = min(configured_per_query, max(3, 20 // max(len(queries), 1)))

        short_need = shot.visual_need[:50]
        await _emit("search", f"    Shot: \"{short_need}\" — searching {len(queries)} queries ({results_per_query} results each)", depth=3)

        # Parallel: tier-1 channel search + open YouTube search
        tier1_ids, tier2_ids = self._build_preferred_channel_ids()
        tier1_list = list(tier1_ids)

        async def _tier1_search():
            if not tier1_list:
                return []
            query_text = " ".join(shot.key_terms[:3]) if shot.key_terms else queries[0] if queries else ""
            return await self._search_tier1_channels_full(
                tier1_list[:5], query_text, results_per_query, job_id, "ytdlp_only", None,
            )

        async def _open_search():
            return await self._search_youtube_primary_full(
                queries, results_per_query, job_id, "ytdlp_only", None,
            )

        tier1_task = asyncio.create_task(_tier1_search())
        open_task = asyncio.create_task(_open_search())
        tier1_results, yt_results = await asyncio.gather(tier1_task, open_task)

        all_video_ids.extend(_collect(tier1_results))
        all_video_ids.extend(_collect(yt_results))

        unique_ids = list(dict.fromkeys(all_video_ids))
        if not unique_ids:
            await _emit("alert", f"    No videos found for shot: \"{short_need}\"", depth=3)
            return []

        video_details = [search_metadata[vid] for vid in unique_ids if vid in search_metadata]
        ids_missing = [vid for vid in unique_ids if vid not in search_metadata]
        if ids_missing:
            extra = await _dispatch_video_details(ids_missing, job_id=job_id)
            video_details.extend(extra)

        blocked_ids = self._build_blocked_channel_ids()
        blocked_names = self._build_blocked_name_set()
        t1_ids, t2_ids = self._build_preferred_channel_ids()
        old_tier2_names = self._get("preferred_channels_tier2") or []
        t2_name_lower = {name.lower() for name in old_tier2_names}

        # Build exclusion keywords for soft deprioritization (not hard filtering)
        exclusion_words = set()
        if script_context and script_context.exclusion_context:
            for word in script_context.exclusion_context.lower().replace(",", " ").split():
                word = word.strip(".,;:!?\"'")
                if len(word) > 3 and word not in {"about", "this", "that", "with", "from", "they", "their", "these", "those", "have", "been", "will", "would", "could"}:
                    exclusion_words.add(word)

        candidates: list[CandidateVideo] = []
        seen_ids: set[str] = set()
        shorts_aspect_filtered = 0

        for v in video_details:
            vid = v.get("video_id", "")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            duration = v.get("duration_seconds") or v.get("video_duration_seconds") or 0
            if duration < min_duration or duration > max_duration:
                continue

            w = int(v.get("width") or 0)
            h = int(v.get("height") or 0)
            if self._should_exclude_shorts_9_16_aspect(w, h):
                shorts_aspect_filtered += 1
                continue

            ch_id = v.get("channel_id", "")
            ch_name = v.get("channel_name", "")
            video_title = v.get("title") or v.get("video_title", "")
            if self._is_blocked(ch_id, ch_name, blocked_ids, blocked_names):
                continue

            candidates.append(CandidateVideo(
                video_id=vid,
                video_url=f"https://www.youtube.com/watch?v={vid}",
                video_title=video_title,
                channel_name=ch_name,
                channel_id=ch_id,
                channel_subscribers=v.get("channel_subscribers") or 0,
                thumbnail_url=v.get("thumbnail_url", ""),
                video_duration_seconds=duration,
                published_at=v.get("published_at", ""),
                view_count=v.get("view_count") or 0,
                is_preferred_tier1=ch_id in t1_ids,
                is_preferred_tier2=ch_id in t2_ids or ch_name.lower() in t2_name_lower,
                is_blocked=False,
            ))

        # Soft deprioritization: move exclusion-matching candidates to the end, don't remove
        if exclusion_words:
            def _exclusion_score(c: CandidateVideo) -> int:
                title_lower = c.video_title.lower()
                return sum(1 for w in exclusion_words if w in title_lower)

            candidates.sort(key=lambda c: (_exclusion_score(c), -c.view_count))

        aspect_note = f", {shorts_aspect_filtered} ~9:16 excluded" if shorts_aspect_filtered else ""
        await _emit("check", f"    {len(candidates)} candidates for \"{short_need}\"{aspect_note}", depth=3)
        return candidates[:max_candidates]

    async def search_batch(
        self,
        segments: list[Segment],
        job_id: str | None = None,
        progress_callback=None,
        on_activity=None,
        script_context: ScriptContext | None = None,
    ) -> dict[str, list[CandidateVideo]]:
        t1_ids, _ = self._build_preferred_channel_ids()
        old_tier1 = self._get("preferred_channels_tier1") or []
        has_preferred = len(t1_ids) > 0 or len(old_tier1) > 0
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
                        script_context=script_context,
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
            cache_key = _cached_search_key(q, max_results)
            cached = _get_cached_search(cache_key)
            if cached is not None:
                all_results.extend(cached)
                if emit:
                    await emit("check", f"→ {len(cached)} cached results for \"{q[:50]}\"", 3)
                continue
            try:
                if emit:
                    await emit("terminal", f"▸ yt-dlp \"ytsearch{max_results}:{q}\" --dump-json --flat-playlist", 3)
                results = await _dispatch_search(
                    query=q, max_results=max_results, job_id=job_id, backend=backend,
                )
                found = [r for r in results if r.get("video_id")]
                _set_cached_search(cache_key, found)
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
