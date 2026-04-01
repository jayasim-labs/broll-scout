"""
Orchestrator Service - Coordinates the full b-roll scouting pipeline
Manages job state, progress tracking, and service coordination
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from enum import Enum

from app.models.schemas import (
    Job, JobStatus, JobProgress, VisualCue, StockClip,
    TranslationResult, SearchResult, MatchResult, RankedSelection,
    SelectionPreferences, PipelineResult
)
from app.services.translator import translator_service
from app.services.searcher import searcher_service
from app.services.matcher import matcher_service
from app.services.ranker import ranker_service
from app.utils.cost_tracker import get_cost_tracker


class PipelineStage(str, Enum):
    """Pipeline stages for tracking progress."""
    INITIALIZED = "initialized"
    TRANSLATING = "translating"
    SEARCHING = "searching"
    MATCHING = "matching"
    RANKING = "ranking"
    COMPLETED = "completed"
    FAILED = "failed"


class OrchestratorService:
    """Orchestrates the complete b-roll scouting pipeline."""
    
    def __init__(self):
        self.cost_tracker = get_cost_tracker()
        self._active_jobs: Dict[str, Job] = {}
        self._progress_callbacks: Dict[str, Callable] = {}
    
    async def create_job(
        self,
        script_text: str,
        video_style: str = "documentary",
        target_audience: str = "general",
        preferences: Optional[SelectionPreferences] = None
    ) -> Job:
        """
        Create a new scouting job.
        
        Args:
            script_text: The raw script to process
            video_style: Style of the video
            target_audience: Target audience
            preferences: Selection preferences
            
        Returns:
            Created Job object
        """
        job_id = str(uuid.uuid4())
        
        job = Job(
            id=job_id,
            status=JobStatus.PENDING,
            stage=PipelineStage.INITIALIZED,
            script_text=script_text,
            video_style=video_style,
            target_audience=target_audience,
            preferences=preferences or SelectionPreferences(),
            created_at=datetime.utcnow().isoformat(),
            progress=JobProgress(
                current_stage=PipelineStage.INITIALIZED,
                percent_complete=0,
                message="Job created"
            )
        )
        
        self._active_jobs[job_id] = job
        
        return job
    
    async def run_pipeline(
        self,
        job_id: str,
        progress_callback: Optional[Callable] = None
    ) -> PipelineResult:
        """
        Run the complete scouting pipeline for a job.
        
        Args:
            job_id: The job ID to process
            progress_callback: Optional callback for progress updates
            
        Returns:
            PipelineResult with all outputs
        """
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")
        
        if progress_callback:
            self._progress_callbacks[job_id] = progress_callback
        
        try:
            # Update job status
            await self._update_job_status(job, JobStatus.PROCESSING, PipelineStage.TRANSLATING)
            
            # Stage 1: Translate script to visual cues
            await self._update_progress(job, 5, "Analyzing script...")
            translation_result = await translator_service.translate_script(
                script_text=job.script_text,
                video_style=job.video_style,
                target_audience=job.target_audience,
                job_id=job_id
            )
            job.visual_cues = translation_result.visual_cues
            await self._update_progress(job, 20, f"Generated {len(job.visual_cues)} visual cues")
            
            # Stage 2: Search for stock footage
            await self._update_job_status(job, JobStatus.PROCESSING, PipelineStage.SEARCHING)
            await self._update_progress(job, 25, "Searching for stock footage...")
            
            async def search_progress(current, total, message):
                pct = 25 + int((current / total) * 30)
                await self._update_progress(job, pct, message)
            
            search_results = await searcher_service.search_batch(
                cues=job.visual_cues,
                max_results_per_query=5,
                job_id=job_id,
                progress_callback=search_progress
            )
            job.search_results = search_results
            total_clips = sum(len(r.clips) for r in search_results)
            await self._update_progress(job, 55, f"Found {total_clips} potential clips")
            
            # Stage 3: Match clips to cues
            await self._update_job_status(job, JobStatus.PROCESSING, PipelineStage.MATCHING)
            await self._update_progress(job, 60, "Analyzing clip relevance...")
            
            match_results = await matcher_service.match_search_results(
                search_results=search_results,
                cues=job.visual_cues,
                top_k_per_cue=5,
                job_id=job_id
            )
            job.match_results = match_results
            await self._update_progress(job, 80, "Clips analyzed")
            
            # Stage 4: Rank and select
            await self._update_job_status(job, JobStatus.PROCESSING, PipelineStage.RANKING)
            await self._update_progress(job, 85, "Ranking and selecting best matches...")
            
            selections = ranker_service.rank_matches(
                match_results=match_results,
                cues=job.visual_cues,
                preferences=job.preferences
            )
            job.selections = selections
            
            # Get summary
            summary = ranker_service.get_selection_summary(selections, job.visual_cues)
            job.summary = summary
            
            job_costs = self.cost_tracker.get_job_costs(job_id)
            total_cost = job_costs.calculate_cost() if job_costs else 0.0
            job.total_cost = total_cost
            
            # Complete
            await self._update_job_status(job, JobStatus.COMPLETED, PipelineStage.COMPLETED)
            await self._update_progress(job, 100, "Pipeline completed successfully")
            job.completed_at = datetime.utcnow().isoformat()
            
            self._active_jobs[job_id] = job
            
            return PipelineResult(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                visual_cues=job.visual_cues,
                selections=selections,
                summary=summary,
                total_cost=total_cost
            )
            
        except Exception as e:
            # Handle failure
            job.status = JobStatus.FAILED
            job.stage = PipelineStage.FAILED
            job.error = str(e)
            self._active_jobs[job_id] = job
            await self._update_progress(job, -1, f"Pipeline failed: {str(e)}")
            raise
        
        finally:
            # Cleanup
            if job_id in self._progress_callbacks:
                del self._progress_callbacks[job_id]
    
    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        # Check active jobs first
        if job_id in self._active_jobs:
            return self._active_jobs[job_id]
        
        return None
    
    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get current job status and progress."""
        job = await self.get_job(job_id)
        if not job:
            return None
        
        return {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress.dict() if job.progress else None,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "error": job.error
        }
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        job = await self.get_job(job_id)
        if not job:
            return False
        
        if job.status in [JobStatus.COMPLETED, JobStatus.FAILED]:
            return False
        
        job.status = JobStatus.FAILED
        job.error = "Job cancelled by user"
        self._active_jobs[job.id] = job
        
        return True
    
    async def _update_job_status(
        self,
        job: Job,
        status: JobStatus,
        stage: PipelineStage
    ):
        """Update job status and stage."""
        job.status = status
        job.stage = stage
        self._active_jobs[job.id] = job
    
    async def _update_progress(
        self,
        job: Job,
        percent: int,
        message: str
    ):
        """Update job progress and notify callback."""
        job.progress = JobProgress(
            current_stage=job.stage,
            percent_complete=max(0, min(100, percent)),
            message=message
        )
        
        # Notify callback if registered
        callback = self._progress_callbacks.get(job.id)
        if callback:
            try:
                await callback(job.progress)
            except Exception:
                pass  # Don't fail on callback errors
        
        self._active_jobs[job.id] = job


# Singleton instance
orchestrator_service = OrchestratorService()
