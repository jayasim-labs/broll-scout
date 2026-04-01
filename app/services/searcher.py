import asyncio
import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import get_settings, DEFAULTS
from app.models.schemas import Segment, CandidateVideo
from app.utils.youtube import (
    search_videos,
    search_channel_videos,
    get_video_details,
    get_channel_stats,
)
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)

GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


class SearcherService:
    """Searches YouTube, Google CSE, and Gemini for candidate B-roll videos."""

    def __init__(self):
        self._settings = get_settings()
        self._cost_tracker = get_cost_tracker()

    def _get_default(self, key: str):
        return DEFAULTS.get(key)

    def _build_blocked_set(self) -> set[str]:
        blocked: list[str] = []
        blocked.extend(self._get_default("blocked_networks") or [])
        blocked.extend(self._get_default("blocked_studios") or [])
        blocked.extend(self._get_default("blocked_sports") or [])
        custom = self._get_default("custom_block_rules") or ""
        if custom:
            blocked.extend(line.strip() for line in custom.split("\n") if line.strip())
        return {name.lower() for name in blocked}

    async def search_for_segment(
        self, segment: Segment, job_id: str | None = None
    ) -> list[CandidateVideo]:
        tier1_ids: list[str] = self._get_default("preferred_channels_tier1") or []
        tier2_names: list[str] = self._get_default("preferred_channels_tier2") or []
        results_per_query: int = self._get_default("youtube_results_per_query") or 5
        max_candidates: int = self._get_default("max_candidates_per_segment") or 12
        min_duration: int = self._get_default("min_video_duration_sec") or 120
        max_duration: int = self._get_default("max_video_duration_sec") or 5400

        all_video_ids: list[str] = []
        search_metadata: dict[str, dict] = {}

        query_text = " ".join(segment.key_terms)

        # (a) Preferred Channel Search (Tier 1)
        tier1_video_ids = await self._search_tier1_channels(
            tier1_ids, query_text, results_per_query, job_id
        )
        all_video_ids.extend(tier1_video_ids)

        # (b) YouTube Data API (Primary)
        yt_video_ids = await self._search_youtube_primary(
            segment.search_queries, results_per_query, job_id
        )
        all_video_ids.extend(yt_video_ids)

        # (c) Google Custom Search API (Secondary)
        cse_video_ids = await self._search_google_cse(segment.key_terms, job_id)
        for vid in cse_video_ids:
            if vid not in all_video_ids:
                all_video_ids.append(vid)

        # (d) Gemini Query Expansion
        initial_titles = list({m.get("title", "") for m in search_metadata.values() if m.get("title")})
        expanded_ids = await self._gemini_expand_and_search(
            segment.summary, initial_titles, results_per_query, job_id
        )
        for vid in expanded_ids:
            if vid not in all_video_ids:
                all_video_ids.append(vid)

        # (e) Batch Video Details
        unique_ids = list(dict.fromkeys(all_video_ids))
        if not unique_ids:
            return []

        video_details = await get_video_details(unique_ids, job_id=job_id)
        channel_ids = list({v["channel_id"] for v in video_details if v.get("channel_id")})
        channel_stats = await get_channel_stats(channel_ids, job_id=job_id) if channel_ids else {}

        # (f) Build CandidateVideo objects
        tier1_set = set(tier1_ids)
        tier2_lower = {name.lower() for name in tier2_names}
        blocked_set = self._build_blocked_set()

        candidates: list[CandidateVideo] = []
        seen_ids: set[str] = set()

        for v in video_details:
            vid = v.get("video_id", "")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            duration = v.get("duration_seconds", 0)
            if duration < min_duration or duration > max_duration:
                continue

            ch_id = v.get("channel_id", "")
            ch_name = v.get("channel_name", "")
            ch_stats = channel_stats.get(ch_id, {})
            subscribers = ch_stats.get("subscriber_count", v.get("channel_subscribers", 0))

            is_blocked = ch_name.lower() in blocked_set
            if is_blocked:
                continue

            candidate = CandidateVideo(
                video_id=vid,
                video_url=f"https://www.youtube.com/watch?v={vid}",
                video_title=v.get("title", ""),
                channel_name=ch_name,
                channel_id=ch_id,
                channel_subscribers=subscribers,
                thumbnail_url=v.get("thumbnail_url", ""),
                video_duration_seconds=duration,
                published_at=v.get("published_at", ""),
                view_count=v.get("view_count", 0),
                is_preferred_tier1=ch_id in tier1_set,
                is_preferred_tier2=ch_name.lower() in tier2_lower,
                is_blocked=False,
            )
            candidates.append(candidate)

        # (g) Limit to max_candidates_per_segment
        return candidates[:max_candidates]

    async def search_batch(
        self,
        segments: list[Segment],
        job_id: str | None = None,
        progress_callback=None,
    ) -> dict[str, list[CandidateVideo]]:
        max_concurrent: int = self._get_default("max_concurrent_segments") or 5
        semaphore = asyncio.Semaphore(max_concurrent)
        results: dict[str, list[CandidateVideo]] = {}
        total = len(segments)
        completed = 0
        lock = asyncio.Lock()

        async def _process(seg: Segment):
            nonlocal completed
            async with semaphore:
                try:
                    candidates = await self.search_for_segment(seg, job_id=job_id)
                except Exception:
                    logger.exception("Failed to search segment %s", seg.segment_id)
                    candidates = []
                results[seg.segment_id] = candidates
                async with lock:
                    completed += 1
                    if progress_callback:
                        try:
                            await progress_callback(
                                completed, total, f"Searched {completed}/{total} segments"
                            )
                        except Exception:
                            pass

        await asyncio.gather(*[_process(seg) for seg in segments])
        return results

    # ── Tier 1 channel search ───────────────────────────────────────────

    async def _search_tier1_channels(
        self,
        channel_ids: list[str],
        query: str,
        max_results: int,
        job_id: str | None,
    ) -> list[str]:
        video_ids: list[str] = []
        for ch_id in channel_ids:
            try:
                results = await search_channel_videos(
                    channel_id=ch_id,
                    query=query,
                    max_results=max_results,
                    job_id=job_id,
                )
                video_ids.extend(r["video_id"] for r in results if r.get("video_id"))
            except Exception:
                logger.warning("Tier 1 channel search failed for %s", ch_id)
        return video_ids

    # ── YouTube primary search ──────────────────────────────────────────

    async def _search_youtube_primary(
        self,
        queries: list[str],
        max_results: int,
        job_id: str | None,
    ) -> list[str]:
        video_ids: list[str] = []
        for q in queries:
            try:
                results = await search_videos(
                    query=q, max_results=max_results, job_id=job_id
                )
                video_ids.extend(r["video_id"] for r in results if r.get("video_id"))
            except Exception:
                logger.warning("YouTube search failed for query: %s", q)
        return video_ids

    # ── Google CSE search ───────────────────────────────────────────────

    async def _search_google_cse(
        self, key_terms: list[str], job_id: str | None
    ) -> list[str]:
        api_key = self._settings.google_search_api_key
        cx = self._settings.google_search_cx
        if not api_key or not cx:
            return []

        queries = []
        for term in key_terms:
            queries.append(f"{term} documentary YouTube")
            queries.append(f"{term} explainer video")

        video_ids: list[str] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for q in queries:
                try:
                    resp = await client.get(
                        GOOGLE_CSE_URL,
                        params={"key": api_key, "cx": cx, "q": q},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if job_id:
                        self._cost_tracker.track_google_cse(job_id)
                    for item in data.get("items", []):
                        vid = self._extract_youtube_id(item.get("link", ""))
                        if vid and vid not in video_ids:
                            video_ids.append(vid)
                except Exception:
                    logger.warning("Google CSE search failed for query: %s", q)
        return video_ids

    @staticmethod
    def _extract_youtube_id(url: str) -> str | None:
        try:
            parsed = urlparse(url)
            if "youtube.com" in parsed.netloc:
                qs = parse_qs(parsed.query)
                vid = qs.get("v", [None])[0]
                if vid:
                    return vid
            if "youtu.be" in parsed.netloc:
                return parsed.path.lstrip("/").split("/")[0] or None
        except Exception:
            pass
        return None

    # ── Gemini query expansion ──────────────────────────────────────────

    async def _gemini_expand_and_search(
        self,
        summary: str,
        initial_titles: list[str],
        max_results: int,
        job_id: str | None,
    ) -> list[str]:
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

        video_ids: list[str] = []
        for q in expanded_queries:
            try:
                results = await search_videos(
                    query=q, max_results=max_results, job_id=job_id
                )
                video_ids.extend(r["video_id"] for r in results if r.get("video_id"))
            except Exception:
                logger.warning("Expanded query search failed for: %s", q)
        return video_ids

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
