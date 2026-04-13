import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.models.schemas import (
    APICosts, JobResponse, JobStatus, JobSummary, ProjectSummary, RankedResult,
    ScriptContext, Segment, SegmentWithResults, Transcript, TranscriptSource,
)

logger = logging.getLogger(__name__)


def _to_dynamo(val: Any) -> Any:
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, dict):
        return {k: _to_dynamo(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_to_dynamo(item) for item in val]
    return val


def _from_dynamo_float(val: Any) -> float:
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


class StorageService:
    """All DynamoDB read/write operations."""

    def __init__(self):
        settings = get_settings()
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )
        self.prefix = settings.dynamodb_table_prefix

    def _table(self, name: str):
        return self.dynamodb.Table(f"{self.prefix}{name}")

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def _scan_all(self, table_name: str, **scan_kwargs) -> list[dict]:
        """Paginated DynamoDB scan that reads all items across partitions."""
        table = self._table(table_name)
        items: list[dict] = []
        while True:
            resp = await self._run(table.scan, **scan_kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    async def create_job(
        self, job_id: str, script_hash: str,
        editor_id: str = "default_editor", script_language: str = "ta",
        project_id: Optional[str] = None, title: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = {
            "job_id": job_id,
            "script_hash": script_hash,
            "script_language": script_language,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "segment_count": 0,
            "result_count": 0,
            "status": JobStatus.PROCESSING.value,
            "processing_time_seconds": None,
            "api_costs": {},
            "editor_id": editor_id,
            "english_translation": None,
            "project_id": project_id,
            "title": title,
            "category": category,
        }
        try:
            await self._run(self._table("jobs").put_item, Item=item)
        except ClientError:
            logger.exception("Failed to create job %s", job_id)
        return item

    async def update_job_status(self, job_id: str, status: JobStatus, **kwargs) -> None:
        parts = ["#st = :st"]
        names = {"#st": "status"}
        values: Dict[str, Any] = {":st": status.value}

        for param in (
            "completed_at", "processing_time_seconds", "api_costs",
            "segment_count", "result_count", "english_translation",
            "script_duration_minutes", "coverage_assessment", "warnings",
            "activity_log", "script_context",
            "pipeline_checkpoint", "checkpoint_at",
        ):
            if param in kwargs:
                safe = param.replace("_", "")
                parts.append(f"#{safe} = :{safe}")
                names[f"#{safe}"] = param
                val = kwargs[param]
                if isinstance(val, dict):
                    val = {k: _to_dynamo(v) for k, v in val.items()}
                values[f":{safe}"] = _to_dynamo(val)

        try:
            await self._run(
                self._table("jobs").update_item,
                Key={"job_id": job_id},
                UpdateExpression="SET " + ", ".join(parts),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError:
            logger.exception("Failed to update job %s", job_id)

    async def update_pipeline_checkpoint(self, job_id: str, checkpoint: str) -> None:
        """Update the job's pipeline checkpoint and timestamp."""
        try:
            await self._run(
                self._table("jobs").update_item,
                Key={"job_id": job_id},
                UpdateExpression="SET pipeline_checkpoint = :cp, checkpoint_at = :ts",
                ExpressionAttributeValues={
                    ":cp": checkpoint,
                    ":ts": datetime.utcnow().isoformat(),
                },
            )
        except ClientError:
            logger.exception("Failed to update checkpoint for %s", job_id)

    async def save_shot_candidates(
        self, job_id: str, segment_id: str, shot_id: str, candidates: List[dict],
    ) -> None:
        """Update a specific shot's candidates within the segment item."""
        try:
            seg_resp = await self._run(
                self._table("segments").get_item,
                Key={"job_id": job_id, "segment_id": segment_id},
            )
            item = seg_resp.get("Item")
            if not item:
                logger.warning("Segment %s/%s not found for candidate save", job_id, segment_id)
                return
            shots = item.get("broll_shots", [])
            for shot in shots:
                if shot.get("shot_id") == shot_id:
                    shot["candidates"] = _to_dynamo(candidates)
                    break
            await self._run(
                self._table("segments").update_item,
                Key={"job_id": job_id, "segment_id": segment_id},
                UpdateExpression="SET broll_shots = :bs",
                ExpressionAttributeValues={":bs": shots},
            )
        except ClientError:
            logger.exception("Failed to save candidates for %s/%s/%s", job_id, segment_id, shot_id)

    async def get_segments_with_candidates(self, job_id: str) -> List[dict]:
        """Load all segments for a job, including candidate data on each shot."""
        try:
            seg_resp = await self._run(
                self._table("segments").query,
                KeyConditionExpression=boto3.dynamodb.conditions.Key("job_id").eq(job_id),
            )
            return seg_resp.get("Items", [])
        except ClientError:
            logger.exception("Failed to load segments for resume %s", job_id)
            return []

    async def delete_job_results(self, job_id: str) -> None:
        """Delete all result items for a job (used before resume to avoid stale data)."""
        try:
            res_resp = await self._run(
                self._table("results").query,
                KeyConditionExpression=boto3.dynamodb.conditions.Key("job_id").eq(job_id),
                ProjectionExpression="job_id, result_id",
            )
            items = res_resp.get("Items", [])
            if items:
                table = self._table("results")
                for i in range(0, len(items), 25):
                    batch = items[i:i + 25]
                    with table.batch_writer() as writer:
                        for item in batch:
                            writer.delete_item(Key={"job_id": item["job_id"], "result_id": item["result_id"]})
                logger.info("Deleted %d results for job %s before resume", len(items), job_id)
        except ClientError:
            logger.exception("Failed to delete results for %s", job_id)

    async def flush_activity_log(self, job_id: str, activity_log: list) -> None:
        """Persist activity log entries to the job record incrementally."""
        try:
            await self._run(
                self._table("jobs").update_item,
                Key={"job_id": job_id},
                UpdateExpression="SET activity_log = :al",
                ExpressionAttributeValues={":al": _to_dynamo(activity_log)},
            )
        except ClientError:
            logger.exception("Failed to flush activity log for %s", job_id)

    async def get_activity_log(self, job_id: str) -> list:
        try:
            resp = await self._run(
                self._table("jobs").get_item,
                Key={"job_id": job_id},
                ProjectionExpression="activity_log",
            )
            return resp.get("Item", {}).get("activity_log", [])
        except ClientError:
            logger.exception("Failed to get activity log for %s", job_id)
            return []

    async def get_job(self, job_id: str) -> Optional[JobResponse]:
        try:
            job_resp = await self._run(
                self._table("jobs").get_item, Key={"job_id": job_id}
            )
            if "Item" not in job_resp:
                return None

            item = job_resp["Item"]

            seg_resp = await self._run(
                self._table("segments").query,
                KeyConditionExpression=boto3.dynamodb.conditions.Key("job_id").eq(job_id),
            )
            segments = seg_resp.get("Items", [])

            res_resp = await self._run(
                self._table("results").query,
                KeyConditionExpression=boto3.dynamodb.conditions.Key("job_id").eq(job_id),
            )
            results = res_resp.get("Items", [])

            results_by_seg: Dict[str, List[Dict]] = {}
            for r in results:
                sid = r.get("segment_id", "")
                results_by_seg.setdefault(sid, []).append(r)

            segments_with_results = []
            for seg in segments:
                seg_results = results_by_seg.get(seg.get("segment_id", ""), [])
                ranked = [
                    RankedResult(
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
                        context_mismatch_reason=r.get("context_mismatch_reason"),
                        editor_rating=r.get("editor_rating"),
                        clip_used=r.get("clip_used", False),
                        editor_notes=r.get("editor_notes"),
                    )
                    for r in seg_results
                ]
                from app.models.schemas import BRollShot
                broll_shots_raw = seg.get("broll_shots", [])
                broll_shots = [BRollShot(**s) for s in broll_shots_raw] if broll_shots_raw else []

                segments_with_results.append(SegmentWithResults(
                    segment_id=seg.get("segment_id", "seg_001"),
                    title=seg.get("title", ""),
                    summary=seg.get("summary", ""),
                    visual_need=seg.get("visual_need", ""),
                    emotional_tone=seg.get("emotional_tone", ""),
                    key_terms=seg.get("key_terms", []),
                    search_queries=seg.get("search_queries", []),
                    estimated_duration_seconds=int(seg.get("estimated_duration_seconds", 60)),
                    context_anchor=seg.get("context_anchor", ""),
                    negative_keywords=seg.get("negative_keywords", []),
                    broll_count=int(seg.get("broll_count", 1)),
                    broll_shots=broll_shots,
                    broll_note=seg.get("broll_note"),
                    results=ranked,
                ))

            costs_data = item.get("api_costs", {})
            costs_data = {k: _from_dynamo_float(v) if isinstance(v, (Decimal, float)) else v
                         for k, v in costs_data.items()} if costs_data else {}

            ctx_raw = item.get("script_context")
            script_ctx = ScriptContext(**ctx_raw) if ctx_raw and isinstance(ctx_raw, dict) else None

            coverage_raw = item.get("coverage_assessment")
            coverage = None
            if coverage_raw and isinstance(coverage_raw, dict):
                from app.models.schemas import CoverageAssessment
                coverage = CoverageAssessment(
                    shots_per_minute=_from_dynamo_float(coverage_raw.get("shots_per_minute", 0)),
                    clips_found=int(coverage_raw.get("clips_found", 0)),
                    total_shots=int(coverage_raw.get("total_shots", 0)),
                    longest_no_broll_gap_seconds=int(coverage_raw.get("longest_no_broll_gap_seconds", 0)),
                    longest_no_broll_gap_segments=coverage_raw.get("longest_no_broll_gap_segments", []),
                    note=coverage_raw.get("note", ""),
                    warnings_count=int(coverage_raw.get("warnings_count", 0)),
                )

            warnings_raw = item.get("warnings", [])
            from app.models.schemas import ShotWarning
            shot_warnings = [
                ShotWarning(**w) for w in warnings_raw if isinstance(w, dict)
            ]

            computed_total_shots = sum(s.broll_count for s in segments_with_results)
            computed_no_broll = sum(1 for s in segments_with_results if s.broll_count == 0)

            return JobResponse(
                job_id=job_id,
                status=JobStatus(item.get("status", "processing")),
                created_at=item.get("created_at", ""),
                completed_at=item.get("completed_at"),
                processing_time_seconds=_from_dynamo_float(item.get("processing_time_seconds")) if item.get("processing_time_seconds") else None,
                script_duration_minutes=int(item.get("script_duration_minutes", 0)),
                total_segments=len(segments),
                total_shots=computed_total_shots,
                total_results=len(results),
                segments_with_no_broll=computed_no_broll,
                coverage_assessment=coverage,
                warnings=shot_warnings,
                api_costs=APICosts(**{k: (int(v) if isinstance(v, int) else _from_dynamo_float(v)) for k, v in costs_data.items()}) if costs_data else APICosts(),
                segments=segments_with_results,
                english_translation=item.get("english_translation"),
                project_id=item.get("project_id"),
                title=item.get("title"),
                category=item.get("category"),
                script_context=script_ctx,
                activity_log=item.get("activity_log", []),
                pipeline_checkpoint=item.get("pipeline_checkpoint"),
                checkpoint_at=item.get("checkpoint_at"),
            )
        except ClientError:
            logger.exception("Failed to get job %s", job_id)
            return None

    async def list_jobs(self, limit: int = 50) -> List[JobSummary]:
        try:
            items = await self._scan_all(
                "jobs",
                ProjectionExpression="job_id, #st, created_at, segment_count, result_count, project_id, title, category, pipeline_checkpoint",
                ExpressionAttributeNames={"#st": "status"},
            )
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return [
                JobSummary(
                    job_id=i.get("job_id", ""),
                    status=JobStatus(i.get("status", "processing")),
                    created_at=i.get("created_at", ""),
                    segment_count=int(i.get("segment_count", 0)),
                    result_count=int(i.get("result_count", 0)),
                    project_id=i.get("project_id"),
                    title=i.get("title"),
                    category=i.get("category"),
                    pipeline_checkpoint=i.get("pipeline_checkpoint"),
                )
                for i in items[:limit]
            ]
        except ClientError:
            logger.exception("Failed to list jobs")
            return []

    async def list_jobs_for_project(self, project_id: str) -> List[JobSummary]:
        """Return all jobs belonging to a specific project, newest first."""
        try:
            items = await self._scan_all(
                "jobs",
                FilterExpression=boto3.dynamodb.conditions.Attr("project_id").eq(project_id),
                ProjectionExpression="job_id, #st, created_at, segment_count, result_count, project_id, title, category, pipeline_checkpoint",
                ExpressionAttributeNames={"#st": "status"},
            )
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return [
                JobSummary(
                    job_id=i.get("job_id", ""),
                    status=JobStatus(i.get("status", "processing")),
                    created_at=i.get("created_at", ""),
                    segment_count=int(i.get("segment_count", 0)),
                    result_count=int(i.get("result_count", 0)),
                    project_id=i.get("project_id"),
                    title=i.get("title"),
                    category=i.get("category"),
                    pipeline_checkpoint=i.get("pipeline_checkpoint"),
                )
                for i in items
            ]
        except ClientError:
            logger.exception("Failed to list jobs for project %s", project_id)
            return []

    async def store_segments(self, job_id: str, segments: List[Segment]) -> None:
        if not segments:
            return
        try:
            table = self._table("segments")
            for i in range(0, len(segments), 25):
                batch = segments[i:i + 25]
                with table.batch_writer() as writer:
                    for seg in batch:
                        item = {
                            "job_id": job_id,
                            "segment_id": seg.segment_id,
                            "title": seg.title,
                            "summary": seg.summary,
                            "visual_need": seg.visual_need,
                            "emotional_tone": seg.emotional_tone,
                            "key_terms": seg.key_terms,
                            "search_queries": seg.search_queries,
                            "estimated_duration_seconds": seg.estimated_duration_seconds,
                            "broll_count": seg.broll_count,
                        }
                        if seg.context_anchor:
                            item["context_anchor"] = seg.context_anchor
                        if seg.negative_keywords:
                            item["negative_keywords"] = seg.negative_keywords
                        if seg.broll_shots:
                            item["broll_shots"] = [s.model_dump() for s in seg.broll_shots]
                        if seg.broll_note:
                            item["broll_note"] = seg.broll_note
                        writer.put_item(Item=item)
        except ClientError:
            logger.exception("Failed to store segments for %s", job_id)

    async def store_results(
        self, job_id: str, results: List[RankedResult],
        category: Optional[str] = None,
        categories: Optional[List[str]] = None,
    ) -> None:
        if not results:
            return
        try:
            table = self._table("results")
            for i in range(0, len(results), 25):
                batch = results[i:i + 25]
                with table.batch_writer() as writer:
                    for r in batch:
                        item = {
                            "job_id": job_id,
                            "result_id": r.result_id,
                            "segment_id": r.segment_id,
                            "shot_id": r.shot_id,
                            "shot_visual_need": r.shot_visual_need,
                            "video_id": r.video_id,
                            "video_url": r.video_url,
                            "video_title": r.video_title,
                            "channel_name": r.channel_name,
                            "channel_subscribers": r.channel_subscribers,
                            "thumbnail_url": r.thumbnail_url,
                            "video_duration_seconds": r.video_duration_seconds,
                            "published_at": r.published_at,
                            "view_count": r.view_count,
                            "start_time_seconds": r.start_time_seconds,
                            "end_time_seconds": r.end_time_seconds,
                            "clip_url": r.clip_url,
                            "transcript_excerpt": r.transcript_excerpt,
                            "the_hook": r.the_hook,
                            "relevance_note": r.relevance_note,
                            "relevance_score": str(r.relevance_score),
                            "confidence_score": str(r.confidence_score),
                            "source_flag": r.source_flag.value,
                            "context_match": r.context_match,
                            "editor_rating": r.editor_rating,
                            "clip_used": r.clip_used,
                            "editor_notes": r.editor_notes,
                        }
                        if r.context_mismatch_reason:
                            item["context_mismatch_reason"] = r.context_mismatch_reason
                        if categories:
                            item["categories"] = categories
                        elif category:
                            item["categories"] = [category]
                        writer.put_item(Item=item)
        except ClientError:
            logger.exception("Failed to store results for %s", job_id)

    async def get_transcript(self, video_id: str) -> Optional[Transcript]:
        try:
            resp = await self._run(
                self._table("transcripts").get_item,
                Key={"video_id": video_id},
                ConsistentRead=True,
            )
            if "Item" not in resp:
                return None
            item = resp["Item"]
            return Transcript(
                video_id=video_id,
                transcript_text=item.get("transcript_text"),
                transcript_source=TranscriptSource(item.get("transcript_source", "no_transcript")),
                language=item.get("language", "en"),
                video_duration_seconds=int(item.get("video_duration_seconds", 0)),
                created_at=item.get("created_at", ""),
            )
        except ClientError:
            logger.exception("Failed to get transcript for %s", video_id)
            return None

    async def store_transcript(
        self, video_id: str, transcript_text: str,
        source: TranscriptSource, language: str = "en", duration: int = 0,
    ) -> None:
        try:
            await self._run(
                self._table("transcripts").put_item,
                Item={
                    "video_id": video_id,
                    "transcript_text": transcript_text,
                    "transcript_source": source.value,
                    "language": language,
                    "video_duration_seconds": duration,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        except ClientError:
            logger.exception("Failed to store transcript for %s", video_id)

    async def store_feedback(
        self, job_id: str, result_id: str,
        rating: int, clip_used: bool, notes: Optional[str] = None,
    ) -> None:
        try:
            await self._run(
                self._table("results").update_item,
                Key={"job_id": job_id, "result_id": result_id},
                UpdateExpression="SET editor_rating = :r, clip_used = :u, editor_notes = :n",
                ExpressionAttributeValues={":r": rating, ":u": clip_used, ":n": notes},
            )
            await self._run(
                self._table("feedback").put_item,
                Item={
                    "result_id": result_id,
                    "editor_rating": rating,
                    "clip_used": clip_used,
                    "notes": notes,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        except ClientError:
            logger.exception("Failed to store feedback for %s", result_id)

    async def search_library(
        self, topic: Optional[str] = None,
        date_from: Optional[str] = None,
        min_rating: Optional[int] = None,
    ) -> List[RankedResult]:
        try:
            scan_kwargs: Dict[str, Any] = {}
            filter_parts = []
            values: Dict[str, Any] = {}

            if topic:
                filter_parts.append("contains(video_title, :topic)")
                values[":topic"] = topic
            if min_rating:
                filter_parts.append("editor_rating >= :rating")
                values[":rating"] = min_rating

            if filter_parts:
                scan_kwargs["FilterExpression"] = " AND ".join(filter_parts)
                scan_kwargs["ExpressionAttributeValues"] = values

            items = await self._scan_all("results", **scan_kwargs)

            return [
                RankedResult(
                    result_id=i.get("result_id", ""),
                    segment_id=i.get("segment_id", ""),
                    video_id=i.get("video_id", ""),
                    video_url=i.get("video_url", ""),
                    video_title=i.get("video_title", ""),
                    channel_name=i.get("channel_name", ""),
                    channel_subscribers=int(i.get("channel_subscribers", 0)),
                    thumbnail_url=i.get("thumbnail_url", ""),
                    video_duration_seconds=int(i.get("video_duration_seconds", 0)),
                    published_at=i.get("published_at", ""),
                    view_count=int(i.get("view_count", 0)),
                    start_time_seconds=i.get("start_time_seconds"),
                    end_time_seconds=i.get("end_time_seconds"),
                    clip_url=i.get("clip_url"),
                    transcript_excerpt=i.get("transcript_excerpt"),
                    the_hook=i.get("the_hook"),
                    relevance_note=i.get("relevance_note"),
                    relevance_score=_from_dynamo_float(i.get("relevance_score", 0)),
                    confidence_score=_from_dynamo_float(i.get("confidence_score", 0)),
                    source_flag=TranscriptSource(i.get("source_flag", "no_transcript")),
                    context_match=i.get("context_match", True),
                    context_mismatch_reason=i.get("context_mismatch_reason"),
                    editor_rating=i.get("editor_rating"),
                    clip_used=i.get("clip_used", False),
                )
                for i in items
            ]
        except ClientError:
            logger.exception("Failed to search library")
            return []


    # ─── Project CRUD ───

    async def create_project(
        self, project_id: str, title: str, category: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        item: Dict[str, Any] = {
            "project_id": project_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "job_count": 0,
            "total_clips": 0,
        }
        if category:
            item["category"] = category
        try:
            await self._run(self._table("projects").put_item, Item=item)
        except ClientError:
            logger.exception("Failed to create project %s", project_id)
        return item

    async def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._run(
                self._table("projects").get_item,
                Key={"project_id": project_id},
            )
            return resp.get("Item")
        except ClientError:
            logger.exception("Failed to get project %s", project_id)
            return None

    async def list_projects(self, limit: int = 200) -> List[ProjectSummary]:
        try:
            items = await self._scan_all("projects")
            items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

            project_ids = [i.get("project_id", "") for i in items[:limit] if i.get("project_id")]
            live_counts: Dict[str, tuple] = {}
            if project_ids:
                try:
                    job_items = await self._scan_all(
                        "jobs",
                        FilterExpression=boto3.dynamodb.conditions.Attr("project_id").is_in(project_ids),
                        ProjectionExpression="project_id, result_count",
                    )
                    for j in job_items:
                        pid = j.get("project_id", "")
                        if pid:
                            prev = live_counts.get(pid, (0, 0))
                            live_counts[pid] = (prev[0] + 1, prev[1] + int(j.get("result_count", 0)))
                except ClientError:
                    logger.warning("Failed to compute live job counts, using cached values")

            return [
                ProjectSummary(
                    project_id=i.get("project_id", ""),
                    title=i.get("title", ""),
                    created_at=i.get("created_at", ""),
                    updated_at=i.get("updated_at", ""),
                    job_count=live_counts.get(i.get("project_id", ""), (0, 0))[0] or int(i.get("job_count", 0)),
                    total_clips=live_counts.get(i.get("project_id", ""), (0, 0))[1] or int(i.get("total_clips", 0)),
                    category=i.get("category"),
                )
                for i in items[:limit]
            ]
        except ClientError:
            logger.exception("Failed to list projects")
            return []

    async def update_project_stats(self, project_id: str) -> None:
        """Recalculate job_count and total_clips for a project by scanning jobs."""
        try:
            items = await self._scan_all(
                "jobs",
                FilterExpression=boto3.dynamodb.conditions.Attr("project_id").eq(project_id),
                ProjectionExpression="job_id, result_count, #st",
                ExpressionAttributeNames={"#st": "status"},
            )
            job_count = len(items)
            total_clips = sum(int(i.get("result_count", 0)) for i in items)

            await self._run(
                self._table("projects").update_item,
                Key={"project_id": project_id},
                UpdateExpression="SET job_count = :jc, total_clips = :tc, updated_at = :ua",
                ExpressionAttributeValues={
                    ":jc": job_count,
                    ":tc": total_clips,
                    ":ua": datetime.utcnow().isoformat(),
                },
            )
        except ClientError:
            logger.exception("Failed to update project stats for %s", project_id)

    async def delete_project(self, project_id: str) -> bool:
        try:
            await self._run(
                self._table("projects").delete_item,
                Key={"project_id": project_id},
            )
            return True
        except ClientError:
            logger.exception("Failed to delete project %s", project_id)
            return False

    async def rename_project(self, project_id: str, new_title: str) -> bool:
        try:
            await self._run(
                self._table("projects").update_item,
                Key={"project_id": project_id},
                UpdateExpression="SET title = :t, updated_at = :ua",
                ExpressionAttributeValues={
                    ":t": new_title,
                    ":ua": datetime.utcnow().isoformat(),
                },
            )
            return True
        except ClientError:
            logger.exception("Failed to rename project %s", project_id)
            return False

    async def store_audit_log(self, job_id: str, records: list[dict]) -> None:
        """Store context audit decisions for pattern analysis."""
        table = self._table("audit_log")
        timestamp = datetime.utcnow().isoformat()
        for rec in records:
            item = {
                "job_id": job_id,
                "result_id": rec.get("result_id", "unknown"),
                "audited_at": timestamp,
                **rec,
            }
            try:
                await self._run(table.put_item, Item=item)
            except Exception:
                logger.debug("Failed to store audit record for %s", rec.get("result_id"))

    # ── Search cache (DynamoDB with TTL) ──────────────────────────────

    async def get_search_cache(self, cache_key: str) -> Optional[list[dict]]:
        """Retrieve cached search results. Returns None on miss or expiry."""
        import json as _json
        try:
            resp = await self._run(
                self._table("search_cache").get_item,
                Key={"cache_key": cache_key},
            )
            item = resp.get("Item")
            if not item:
                return None
            return _json.loads(item["results"])
        except Exception:
            logger.debug("Search cache read error for %s", cache_key[:60], exc_info=True)
            return None

    async def put_search_cache(self, cache_key: str, results: list[dict], ttl_seconds: int = 7 * 24 * 3600) -> None:
        """Store search results with a DynamoDB TTL for auto-expiry."""
        import json as _json
        import time as _time
        try:
            await self._run(
                self._table("search_cache").put_item,
                Item={
                    "cache_key": cache_key,
                    "results": _json.dumps(results),
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": int(_time.time()) + ttl_seconds,
                },
            )
        except Exception:
            logger.debug("Search cache write error for %s", cache_key[:60], exc_info=True)


_storage: Optional[StorageService] = None


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
