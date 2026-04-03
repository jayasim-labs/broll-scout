"""
Library service — search across all previously discovered clips.

Modes:
  1. Metadata search (fast) — text match on video_title, channel, the_hook, excerpt
  2. Deep search (slow)    — full-text search across cached transcripts + local LLM matching
  3. Similar clip search   — find clips similar to a given result
"""
import asyncio
import logging
import re
from collections import Counter
from typing import Optional

from app.models.schemas import (
    LibraryClip, LibraryCategoryCount, LibrarySearchResponse, LibraryStats,
    RankedResult, TranscriptSource,
)
from app.services.storage import get_storage
from app.utils import agent_queue

logger = logging.getLogger(__name__)


def _from_dynamo_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _result_to_clip(r: dict) -> LibraryClip:
    return LibraryClip(
        result_id=r.get("result_id", ""),
        segment_id=r.get("segment_id", ""),
        shot_id=r.get("shot_id"),
        shot_visual_need=r.get("shot_visual_need"),
        video_id=r.get("video_id", ""),
        video_url=r.get("video_url", ""),
        video_title=r.get("video_title", ""),
        channel_name=r.get("channel_name", ""),
        channel_subscribers=int(r.get("channel_subscribers", 0)),
        thumbnail_url=r.get("thumbnail_url", ""),
        video_duration_seconds=int(r.get("video_duration_seconds", 0)),
        published_at=r.get("published_at", ""),
        view_count=int(r.get("view_count", 0)),
        start_time_seconds=r.get("start_time_seconds"),
        end_time_seconds=r.get("end_time_seconds"),
        clip_url=r.get("clip_url"),
        transcript_excerpt=r.get("transcript_excerpt"),
        the_hook=r.get("the_hook"),
        relevance_note=r.get("relevance_note"),
        relevance_score=_from_dynamo_float(r.get("relevance_score", 0)),
        confidence_score=_from_dynamo_float(r.get("confidence_score", 0)),
        source_flag=TranscriptSource(r.get("source_flag", "no_transcript")),
        context_match=r.get("context_match", True),
        editor_rating=r.get("editor_rating"),
        clip_used=r.get("clip_used", False),
        editor_notes=r.get("editor_notes"),
        category=r.get("category"),
        job_id=r.get("job_id"),
        job_title=r.get("job_title"),
    )


def _matches_query(item: dict, tokens: list[str]) -> int:
    """Count how many query tokens appear in the item's searchable text."""
    searchable = " ".join([
        item.get("video_title", ""),
        item.get("channel_name", ""),
        item.get("the_hook", ""),
        item.get("transcript_excerpt", ""),
        item.get("relevance_note", ""),
    ]).lower()
    return sum(1 for t in tokens if t in searchable)


class LibraryService:

    async def search(
        self,
        q: Optional[str] = None,
        categories: Optional[str] = None,
        min_rating: Optional[int] = None,
        min_views: Optional[int] = None,
        used: Optional[str] = None,
        sort: str = "relevance",
        page: int = 1,
        per_page: int = 50,
    ) -> LibrarySearchResponse:
        storage = get_storage()
        all_items = await self._scan_all_results(storage)

        tokens = [t.lower() for t in (q or "").split() if len(t) >= 2]
        cat_filter = set(c.strip().lower() for c in (categories or "").split(",") if c.strip())

        scored: list[tuple[dict, int]] = []
        for item in all_items:
            if min_rating and (item.get("editor_rating") or 0) < min_rating:
                continue
            if min_views and (int(item.get("view_count", 0)) or 0) < min_views:
                continue
            if used == "used" and not item.get("clip_used"):
                continue
            if used == "unused" and item.get("clip_used"):
                continue
            if cat_filter:
                item_cat = (item.get("category") or "").lower()
                if item_cat and item_cat not in cat_filter:
                    continue

            if tokens:
                hit_count = _matches_query(item, tokens)
                if hit_count == 0:
                    continue
                scored.append((item, hit_count))
            else:
                scored.append((item, 0))

        if sort == "rating":
            scored.sort(key=lambda x: (x[0].get("editor_rating") or 0, x[1]), reverse=True)
        elif sort == "views":
            scored.sort(key=lambda x: int(x[0].get("view_count", 0)), reverse=True)
        elif sort == "recent":
            scored.sort(key=lambda x: x[0].get("published_at", ""), reverse=True)
        elif sort == "added":
            scored.sort(key=lambda x: x[0].get("result_id", ""), reverse=True)
        else:
            scored.sort(key=lambda x: (x[1], _from_dynamo_float(x[0].get("relevance_score", 0))), reverse=True)

        total = len(scored)
        start_idx = (page - 1) * per_page
        page_items = scored[start_idx : start_idx + per_page]

        clips = [_result_to_clip(item) for item, _ in page_items]

        cat_counts = Counter(
            (item.get("category") or "uncategorized").lower()
            for item, _ in scored
        )
        categories_list = [
            LibraryCategoryCount(name=cat, count=count)
            for cat, count in cat_counts.most_common(20)
        ]

        stats = await self.get_stats(storage, all_items)

        return LibrarySearchResponse(
            total=total,
            page=page,
            results=clips,
            stats=stats,
            categories=categories_list,
        )

    async def deep_search(self, query: str, max_results: int = 20) -> list[LibraryClip]:
        """Search full cached transcripts for matches, run local LLM to find timestamps."""
        storage = get_storage()
        tokens = [t.lower() for t in query.split() if len(t) >= 2]
        if not tokens:
            return []

        transcripts = await self._scan_transcripts_with_keywords(storage, tokens)
        if not transcripts:
            return []

        ranked_transcripts = []
        for t in transcripts:
            text_lower = (t.get("transcript_text") or "").lower()
            hits = sum(text_lower.count(tok) for tok in tokens)
            if hits > 0:
                ranked_transcripts.append((t, hits))
        ranked_transcripts.sort(key=lambda x: x[1], reverse=True)
        ranked_transcripts = ranked_transcripts[:max_results]

        results: list[LibraryClip] = []
        for transcript, _ in ranked_transcripts:
            video_id = transcript.get("video_id", "")
            transcript_text = transcript.get("transcript_text", "")
            if not transcript_text:
                continue

            match_result = await self._run_library_match(query, transcript_text)
            if not match_result or match_result.get("confidence_score", 0) < 0.3:
                continue

            start = match_result.get("start_time_seconds", 0)
            clip = LibraryClip(
                result_id=f"lib_{video_id}_{start}",
                segment_id="library",
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                video_title=transcript.get("video_title", video_id),
                channel_name=transcript.get("channel_name", ""),
                thumbnail_url=f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
                video_duration_seconds=int(transcript.get("video_duration_seconds", 0)),
                start_time_seconds=start,
                end_time_seconds=match_result.get("end_time_seconds"),
                clip_url=f"https://www.youtube.com/watch?v={video_id}&t={start}",
                transcript_excerpt=match_result.get("excerpt", ""),
                the_hook=match_result.get("the_hook", ""),
                confidence_score=match_result.get("confidence_score", 0),
                relevance_score=match_result.get("confidence_score", 0),
                source_flag=TranscriptSource(transcript.get("transcript_source", "cached_transcript")),
            )
            results.append(clip)

        results.sort(key=lambda c: c.confidence_score, reverse=True)
        return results[:max_results]

    async def find_similar(self, job_id: str, result_id: str) -> list[LibraryClip]:
        """Find clips similar to a given result by searching its excerpt and hook."""
        storage = get_storage()
        result_item = await self._get_result_item(storage, job_id, result_id)
        if not result_item:
            return []

        search_text = " ".join(filter(None, [
            result_item.get("the_hook", ""),
            result_item.get("transcript_excerpt", ""),
        ]))
        words = re.findall(r'\b\w{4,}\b', search_text.lower())
        unique_words = list(dict.fromkeys(words))[:8]

        if not unique_words:
            return []

        all_items = await self._scan_all_results(storage)
        source_video_id = result_item.get("video_id", "")

        scored: list[tuple[dict, int]] = []
        for item in all_items:
            if item.get("video_id") == source_video_id:
                continue
            hits = _matches_query(item, unique_words)
            if hits >= 2:
                scored.append((item, hits))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [_result_to_clip(item) for item, _ in scored[:10]]

    async def add_to_job(self, job_id: str, result_id: str, source_job_id: str, segment_id: str) -> bool:
        """Copy a library clip into a specific job's results."""
        storage = get_storage()
        source_item = await self._get_result_item(storage, source_job_id, result_id)
        if not source_item:
            return False

        new_result = RankedResult(
            result_id=f"lib_{result_id}",
            segment_id=segment_id,
            video_id=source_item.get("video_id", ""),
            video_url=source_item.get("video_url", ""),
            video_title=source_item.get("video_title", ""),
            channel_name=source_item.get("channel_name", ""),
            channel_subscribers=int(source_item.get("channel_subscribers", 0)),
            thumbnail_url=source_item.get("thumbnail_url", ""),
            video_duration_seconds=int(source_item.get("video_duration_seconds", 0)),
            published_at=source_item.get("published_at", ""),
            view_count=int(source_item.get("view_count", 0)),
            start_time_seconds=source_item.get("start_time_seconds"),
            end_time_seconds=source_item.get("end_time_seconds"),
            clip_url=source_item.get("clip_url"),
            transcript_excerpt=source_item.get("transcript_excerpt"),
            the_hook=source_item.get("the_hook"),
            relevance_note=f"Imported from library (original: {result_id})",
            relevance_score=_from_dynamo_float(source_item.get("relevance_score", 0)),
            confidence_score=_from_dynamo_float(source_item.get("confidence_score", 0)),
            source_flag=TranscriptSource(source_item.get("source_flag", "no_transcript")),
        )

        await storage.store_results(job_id, [new_result])
        return True

    async def recategorize(self, job_id: str, result_id: str, category: str) -> bool:
        storage = get_storage()
        try:
            await storage._run(
                storage._table("results").update_item,
                Key={"job_id": job_id, "result_id": result_id},
                UpdateExpression="SET category = :c",
                ExpressionAttributeValues={":c": category},
            )
            return True
        except Exception:
            logger.exception("Failed to recategorize %s", result_id)
            return False

    async def get_stats(
        self, storage=None, all_items: list[dict] | None = None,
    ) -> LibraryStats:
        if storage is None:
            storage = get_storage()
        if all_items is None:
            all_items = await self._scan_all_results(storage)

        video_ids = set()
        rated_count = 0
        used_count = 0
        channel_counts: Counter = Counter()
        cat_counts: Counter = Counter()

        for item in all_items:
            video_ids.add(item.get("video_id", ""))
            if item.get("editor_rating"):
                rated_count += 1
            if item.get("clip_used"):
                used_count += 1
            ch = item.get("channel_name", "")
            if ch:
                channel_counts[ch] += 1
            cat = item.get("category") or "uncategorized"
            cat_counts[cat.lower()] += 1

        transcript_count = 0
        try:
            resp = await storage._run(storage._table("transcripts").scan, Select="COUNT")
            transcript_count = resp.get("Count", 0)
        except Exception:
            pass

        top_channels = [
            {"name": name, "count": count}
            for name, count in channel_counts.most_common(10)
        ]
        top_categories = [
            LibraryCategoryCount(name=cat, count=count)
            for cat, count in cat_counts.most_common(15)
        ]

        usage_rate = used_count / max(len(all_items), 1)

        return LibraryStats(
            videos_indexed=len(video_ids),
            clips_found=len(all_items),
            transcripts_cached=transcript_count,
            editor_rated=rated_count,
            usage_rate=round(usage_rate, 3),
            top_channels=top_channels,
            top_categories=top_categories,
        )

    async def get_suggestions_for_segment(
        self, segment_key_terms: list[str], exclude_video_ids: set[str], limit: int = 3,
    ) -> list[LibraryClip]:
        """Quick metadata search for library clips matching a segment's key_terms."""
        storage = get_storage()
        all_items = await self._scan_all_results(storage)
        tokens = [t.lower() for t in segment_key_terms if len(t) >= 2]
        if not tokens:
            return []

        scored: list[tuple[dict, int]] = []
        for item in all_items:
            vid = item.get("video_id", "")
            if vid in exclude_video_ids:
                continue
            hits = _matches_query(item, tokens)
            if hits >= 2:
                scored.append((item, hits))

        scored.sort(key=lambda x: (x[1], _from_dynamo_float(x[0].get("relevance_score", 0))), reverse=True)
        return [_result_to_clip(item) for item, _ in scored[:limit]]

    # ── Private helpers ──

    async def _scan_all_results(self, storage) -> list[dict]:
        """Scan all items from the results table. Paginate through entire table."""
        table = storage._table("results")
        items: list[dict] = []
        scan_kwargs: dict = {}
        try:
            while True:
                resp = await storage._run(table.scan, **scan_kwargs)
                items.extend(resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                scan_kwargs["ExclusiveStartKey"] = last_key
        except Exception:
            logger.exception("Failed to scan results table for library")
        return items

    async def _scan_transcripts_with_keywords(
        self, storage, tokens: list[str],
    ) -> list[dict]:
        """Scan transcripts table for rows containing any of the tokens."""
        table = storage._table("transcripts")
        items: list[dict] = []
        try:
            contains_parts = []
            values = {}
            for i, tok in enumerate(tokens[:5]):
                key = f":kw{i}"
                contains_parts.append(f"contains(transcript_text, {key})")
                values[key] = tok

            filter_expr = " OR ".join(contains_parts)
            scan_kwargs = {
                "FilterExpression": filter_expr,
                "ExpressionAttributeValues": values,
                "Limit": 200,
            }
            resp = await storage._run(table.scan, **scan_kwargs)
            items = resp.get("Items", [])
        except Exception:
            logger.exception("Failed to scan transcripts for deep search")
        return items

    async def _run_library_match(self, query: str, transcript_text: str) -> dict | None:
        """Dispatch a library match to the local companion's Ollama model."""
        words = transcript_text.split()
        if len(words) > 12000:
            transcript_text = " ".join(words[:12000])

        prompt = (
            f"Given this video transcript, find the section most relevant to this search query.\n\n"
            f"Search query: {query}\n\n"
            f"Transcript:\n{transcript_text}\n\n"
            "Return JSON only:\n"
            "{\n"
            '  "start_time_seconds": int,\n'
            '  "end_time_seconds": int,\n'
            '  "excerpt": "relevant text (max 200 words)",\n'
            '  "confidence_score": float (0.0-1.0),\n'
            '  "the_hook": "why this moment is visually compelling"\n'
            "}\n\n"
            "If no relevant section exists, return confidence_score: 0.0."
        )

        try:
            task_id = await agent_queue.create_task("match_timestamp", {
                "prompt": prompt,
            })
            result = await agent_queue.wait_for_result(task_id, timeout=60)
            if isinstance(result, dict):
                return result
            if isinstance(result, list) and result:
                return result[0] if isinstance(result[0], dict) else None
        except Exception:
            logger.warning("Library deep search match failed via companion")
        return None

    async def _get_result_item(self, storage, job_id: str, result_id: str) -> dict | None:
        try:
            resp = await storage._run(
                storage._table("results").get_item,
                Key={"job_id": job_id, "result_id": result_id},
            )
            return resp.get("Item")
        except Exception:
            logger.exception("Failed to get result %s/%s", job_id, result_id)
            return None


_library: LibraryService | None = None


def get_library_service() -> LibraryService:
    global _library
    if _library is None:
        _library = LibraryService()
    return _library
