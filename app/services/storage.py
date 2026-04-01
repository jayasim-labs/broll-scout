"""
B-Roll Scout - Storage Service
All DynamoDB read/write operations. Every module accesses the database through this service.
"""

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import asyncio
from functools import partial

from app.config import get_settings, DEFAULTS
from app.models.schemas import (
    Segment, RankedResult, Transcript, JobResponse, JobSummary,
    SegmentWithResults, APICosts, JobStatus, TranscriptSource
)

logger = logging.getLogger(__name__)


class StorageService:
    """DynamoDB storage operations for B-Roll Scout."""
    
    def __init__(self):
        settings = get_settings()
        self.dynamodb = boto3.resource(
            'dynamodb',
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key
        )
        self.prefix = settings.dynamodb_table_prefix
        
    def _table(self, name: str):
        """Get a DynamoDB table by name with prefix."""
        return self.dynamodb.Table(f"{self.prefix}{name}")
    
    # =========================================================================
    # Jobs Table Operations
    # =========================================================================
    
    async def create_job(
        self,
        job_id: str,
        script_hash: str,
        editor_id: str = "default_editor",
        script_language: str = "ta"
    ) -> Dict[str, Any]:
        """Create a new job record with processing status."""
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
            "english_translation": None
        }
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(self._table("jobs").put_item, Item=item)
            )
            return item
        except ClientError as e:
            logger.error(f"Failed to create job {job_id}: {e}")
            raise
    
    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        **kwargs
    ) -> None:
        """Update job status and optional fields."""
        update_expr_parts = ["#status = :status"]
        expr_names = {"#status": "status"}
        expr_values = {":status": status.value}
        
        field_mapping = {
            "completed_at": "completed_at",
            "processing_time_seconds": "processing_time_seconds",
            "api_costs": "api_costs",
            "segment_count": "segment_count",
            "result_count": "result_count",
            "english_translation": "english_translation",
            "script_duration_minutes": "script_duration_minutes"
        }
        
        for param, field in field_mapping.items():
            if param in kwargs:
                update_expr_parts.append(f"#{param} = :{param}")
                expr_names[f"#{param}"] = field
                expr_values[f":{param}"] = kwargs[param]
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(
                    self._table("jobs").update_item,
                    Key={"job_id": job_id},
                    UpdateExpression="SET " + ", ".join(update_expr_parts),
                    ExpressionAttributeNames=expr_names,
                    ExpressionAttributeValues=expr_values
                )
            )
        except ClientError as e:
            logger.error(f"Failed to update job {job_id}: {e}")
            # Don't raise - job updates should not block pipeline
    
    async def get_job(self, job_id: str) -> Optional[JobResponse]:
        """Get full job with segments and results."""
        try:
            loop = asyncio.get_event_loop()
            
            # Get job record
            job_response = await loop.run_in_executor(
                None,
                partial(self._table("jobs").get_item, Key={"job_id": job_id})
            )
            
            if "Item" not in job_response:
                return None
            
            job_item = job_response["Item"]
            
            # Get segments
            segments_response = await loop.run_in_executor(
                None,
                partial(
                    self._table("segments").query,
                    KeyConditionExpression=Key("job_id").eq(job_id)
                )
            )
            segments = segments_response.get("Items", [])
            
            # Get results
            results_response = await loop.run_in_executor(
                None,
                partial(
                    self._table("results").query,
                    KeyConditionExpression=Key("job_id").eq(job_id)
                )
            )
            results = results_response.get("Items", [])
            
            # Group results by segment
            results_by_segment: Dict[str, List[Dict]] = {}
            for result in results:
                seg_id = result.get("segment_id", "")
                if seg_id not in results_by_segment:
                    results_by_segment[seg_id] = []
                results_by_segment[seg_id].append(result)
            
            # Build response
            segments_with_results = []
            for seg in segments:
                seg_results = results_by_segment.get(seg.get("segment_id", ""), [])
                ranked_results = [
                    RankedResult(
                        result_id=r.get("result_id", ""),
                        segment_id=r.get("segment_id", ""),
                        video_id=r.get("video_id", ""),
                        video_url=r.get("video_url", ""),
                        video_title=r.get("video_title", ""),
                        channel_name=r.get("channel_name", ""),
                        channel_subscribers=r.get("channel_subscribers", 0),
                        thumbnail_url=r.get("thumbnail_url", ""),
                        video_duration_seconds=r.get("video_duration_seconds", 0),
                        published_at=r.get("published_at", ""),
                        view_count=r.get("view_count", 0),
                        start_time_seconds=r.get("start_time_seconds"),
                        end_time_seconds=r.get("end_time_seconds"),
                        clip_url=r.get("clip_url"),
                        transcript_excerpt=r.get("transcript_excerpt"),
                        the_hook=r.get("the_hook"),
                        relevance_score=float(r.get("relevance_score", 0)),
                        confidence_score=float(r.get("confidence_score", 0)),
                        source_flag=TranscriptSource(r.get("source_flag", "no_transcript")),
                        editor_rating=r.get("editor_rating"),
                        clip_used=r.get("clip_used", False),
                        editor_notes=r.get("editor_notes")
                    )
                    for r in seg_results
                ]
                
                segments_with_results.append(
                    SegmentWithResults(
                        segment_id=seg.get("segment_id", ""),
                        title=seg.get("title", ""),
                        summary=seg.get("summary", ""),
                        visual_need=seg.get("visual_need", ""),
                        emotional_tone=seg.get("emotional_tone", ""),
                        key_terms=seg.get("key_terms", []),
                        search_queries=seg.get("search_queries", []),
                        estimated_duration_seconds=seg.get("estimated_duration_seconds", 60),
                        results=ranked_results
                    )
                )
            
            api_costs_data = job_item.get("api_costs", {})
            
            return JobResponse(
                job_id=job_id,
                status=JobStatus(job_item.get("status", "processing")),
                created_at=job_item.get("created_at", ""),
                completed_at=job_item.get("completed_at"),
                processing_time_seconds=job_item.get("processing_time_seconds"),
                script_duration_minutes=job_item.get("script_duration_minutes", 0),
                total_segments=len(segments),
                total_results=len(results),
                minimum_results_met=len(results) >= job_item.get("script_duration_minutes", 0),
                api_costs=APICosts(**api_costs_data) if api_costs_data else APICosts(),
                segments=segments_with_results,
                english_translation=job_item.get("english_translation")
            )
            
        except ClientError as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None
    
    async def list_jobs(self, limit: int = 30) -> List[JobSummary]:
        """List recent jobs for the sidebar."""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                partial(
                    self._table("jobs").scan,
                    Limit=limit,
                    ProjectionExpression="job_id, #status, created_at, segment_count, result_count",
                    ExpressionAttributeNames={"#status": "status"}
                )
            )
            
            items = response.get("Items", [])
            # Sort by created_at descending
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            
            return [
                JobSummary(
                    job_id=item.get("job_id", ""),
                    status=JobStatus(item.get("status", "processing")),
                    created_at=item.get("created_at", ""),
                    segment_count=item.get("segment_count", 0),
                    result_count=item.get("result_count", 0)
                )
                for item in items[:limit]
            ]
            
        except ClientError as e:
            logger.error(f"Failed to list jobs: {e}")
            return []
    
    # =========================================================================
    # Segments Table Operations
    # =========================================================================
    
    async def store_segments(self, job_id: str, segments: List[Segment]) -> None:
        """Batch write all segments for a job."""
        if not segments:
            return
            
        try:
            table = self._table("segments")
            loop = asyncio.get_event_loop()
            
            # DynamoDB batch write supports max 25 items per batch
            for i in range(0, len(segments), 25):
                batch = segments[i:i+25]
                
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
                            "estimated_duration_seconds": seg.estimated_duration_seconds
                        }
                        await loop.run_in_executor(None, writer.put_item, item)
                        
        except ClientError as e:
            logger.error(f"Failed to store segments for job {job_id}: {e}")
    
    # =========================================================================
    # Results Table Operations
    # =========================================================================
    
    async def store_results(self, job_id: str, results: List[RankedResult]) -> None:
        """Batch write all results for a job."""
        if not results:
            return
            
        try:
            table = self._table("results")
            loop = asyncio.get_event_loop()
            
            for i in range(0, len(results), 25):
                batch = results[i:i+25]
                
                with table.batch_writer() as writer:
                    for res in batch:
                        item = {
                            "job_id": job_id,
                            "result_id": res.result_id,
                            "segment_id": res.segment_id,
                            "video_id": res.video_id,
                            "video_url": res.video_url,
                            "video_title": res.video_title,
                            "channel_name": res.channel_name,
                            "channel_subscribers": res.channel_subscribers,
                            "thumbnail_url": res.thumbnail_url,
                            "video_duration_seconds": res.video_duration_seconds,
                            "published_at": res.published_at,
                            "view_count": res.view_count,
                            "start_time_seconds": res.start_time_seconds,
                            "end_time_seconds": res.end_time_seconds,
                            "clip_url": res.clip_url,
                            "transcript_excerpt": res.transcript_excerpt,
                            "the_hook": res.the_hook,
                            "relevance_score": str(res.relevance_score),  # DynamoDB doesn't like floats
                            "confidence_score": str(res.confidence_score),
                            "source_flag": res.source_flag.value,
                            "editor_rating": res.editor_rating,
                            "clip_used": res.clip_used,
                            "editor_notes": res.editor_notes
                        }
                        await loop.run_in_executor(None, writer.put_item, item)
                        
        except ClientError as e:
            logger.error(f"Failed to store results for job {job_id}: {e}")
    
    async def update_result_feedback(
        self,
        job_id: str,
        result_id: str,
        rating: int,
        clip_used: bool,
        notes: Optional[str] = None
    ) -> None:
        """Update feedback on a result."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(
                    self._table("results").update_item,
                    Key={"job_id": job_id, "result_id": result_id},
                    UpdateExpression="SET editor_rating = :rating, clip_used = :used, editor_notes = :notes",
                    ExpressionAttributeValues={
                        ":rating": rating,
                        ":used": clip_used,
                        ":notes": notes
                    }
                )
            )
            
            # Also write to feedback table for cross-job analysis
            await self._store_feedback(result_id, rating, clip_used, notes)
            
        except ClientError as e:
            logger.error(f"Failed to update feedback for result {result_id}: {e}")
    
    async def _store_feedback(
        self,
        result_id: str,
        rating: int,
        clip_used: bool,
        notes: Optional[str]
    ) -> None:
        """Store feedback in the feedback table."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(
                    self._table("feedback").put_item,
                    Item={
                        "result_id": result_id,
                        "editor_rating": rating,
                        "clip_used": clip_used,
                        "notes": notes,
                        "created_at": datetime.utcnow().isoformat()
                    }
                )
            )
        except ClientError as e:
            logger.error(f"Failed to store feedback: {e}")
    
    # =========================================================================
    # Transcripts Table Operations
    # =========================================================================
    
    async def get_transcript(self, video_id: str) -> Optional[Transcript]:
        """Check transcript cache for a video."""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                partial(
                    self._table("transcripts").get_item,
                    Key={"video_id": video_id},
                    ConsistentRead=True  # Avoid cache misses on recent writes
                )
            )
            
            if "Item" not in response:
                return None
                
            item = response["Item"]
            return Transcript(
                video_id=video_id,
                transcript_text=item.get("transcript_text"),
                transcript_source=TranscriptSource(item.get("transcript_source", "no_transcript")),
                language=item.get("language", "en"),
                video_duration_seconds=item.get("video_duration_seconds", 0),
                created_at=item.get("created_at", "")
            )
            
        except ClientError as e:
            logger.error(f"Failed to get transcript for {video_id}: {e}")
            return None
    
    async def store_transcript(
        self,
        video_id: str,
        transcript_text: str,
        source: TranscriptSource,
        language: str = "en",
        duration: int = 0
    ) -> None:
        """Cache a transcript."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(
                    self._table("transcripts").put_item,
                    Item={
                        "video_id": video_id,
                        "transcript_text": transcript_text,
                        "transcript_source": source.value,
                        "language": language,
                        "video_duration_seconds": duration,
                        "created_at": datetime.utcnow().isoformat()
                    }
                )
            )
        except ClientError as e:
            logger.error(f"Failed to store transcript for {video_id}: {e}")
    
    # =========================================================================
    # Library Search
    # =========================================================================
    
    async def search_library(
        self,
        topic: Optional[str] = None,
        date_from: Optional[str] = None,
        min_rating: Optional[int] = None
    ) -> List[RankedResult]:
        """Search across all past results."""
        try:
            loop = asyncio.get_event_loop()
            
            # Build filter expression
            filter_parts = []
            expr_values = {}
            
            if topic:
                filter_parts.append("contains(video_title, :topic)")
                expr_values[":topic"] = topic
                
            if min_rating:
                filter_parts.append("editor_rating >= :rating")
                expr_values[":rating"] = min_rating
            
            scan_kwargs = {"Limit": 100}
            if filter_parts:
                scan_kwargs["FilterExpression"] = " AND ".join(filter_parts)
                scan_kwargs["ExpressionAttributeValues"] = expr_values
            
            response = await loop.run_in_executor(
                None,
                partial(self._table("results").scan, **scan_kwargs)
            )
            
            results = []
            for item in response.get("Items", []):
                results.append(
                    RankedResult(
                        result_id=item.get("result_id", ""),
                        segment_id=item.get("segment_id", ""),
                        video_id=item.get("video_id", ""),
                        video_url=item.get("video_url", ""),
                        video_title=item.get("video_title", ""),
                        channel_name=item.get("channel_name", ""),
                        channel_subscribers=item.get("channel_subscribers", 0),
                        thumbnail_url=item.get("thumbnail_url", ""),
                        video_duration_seconds=item.get("video_duration_seconds", 0),
                        published_at=item.get("published_at", ""),
                        view_count=item.get("view_count", 0),
                        start_time_seconds=item.get("start_time_seconds"),
                        end_time_seconds=item.get("end_time_seconds"),
                        clip_url=item.get("clip_url"),
                        transcript_excerpt=item.get("transcript_excerpt"),
                        the_hook=item.get("the_hook"),
                        relevance_score=float(item.get("relevance_score", 0)),
                        confidence_score=float(item.get("confidence_score", 0)),
                        source_flag=TranscriptSource(item.get("source_flag", "no_transcript")),
                        editor_rating=item.get("editor_rating"),
                        clip_used=item.get("clip_used", False)
                    )
                )
            
            return results
            
        except ClientError as e:
            logger.error(f"Failed to search library: {e}")
            return []


# Singleton instance
_storage: Optional[StorageService] = None


def get_storage() -> StorageService:
    """Get the storage service singleton."""
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
