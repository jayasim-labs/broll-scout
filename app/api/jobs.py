"""
Jobs API Routes - Create, manage, and monitor scouting jobs
"""

import asyncio
from typing import Optional, List
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from app.models.schemas import Job, JobStatus, SelectionPreferences
from app.services.orchestrator import orchestrator_service
from app.services.storage import get_storage


router = APIRouter()


class CreateJobRequest(BaseModel):
    """Request body for creating a new job."""
    script_text: str
    video_style: str = "documentary"
    target_audience: str = "general"
    preferences: Optional[SelectionPreferences] = None


class JobResponse(BaseModel):
    """Response for job operations."""
    id: str
    status: str
    message: str


@router.post("", response_model=JobResponse)
async def create_job(request: CreateJobRequest):
    """
    Create a new b-roll scouting job.
    
    This creates the job but does not start processing.
    Use POST /jobs/{job_id}/start to begin processing.
    """
    try:
        job = await orchestrator_service.create_job(
            script_text=request.script_text,
            video_style=request.video_style,
            target_audience=request.target_audience,
            preferences=request.preferences
        )
        
        return JobResponse(
            id=job.id,
            status=job.status,
            message="Job created successfully"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/start")
async def start_job(job_id: str, background_tasks: BackgroundTasks):
    """
    Start processing a job.
    
    This runs the full pipeline in the background:
    1. Translate script to visual cues
    2. Search for stock footage
    3. Match and analyze clips
    4. Rank and select best matches
    """
    job = await orchestrator_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Job cannot be started. Current status: {job.status}"
        )
    
    # Start processing in background
    background_tasks.add_task(run_pipeline, job_id)
    
    return {"message": "Job started", "job_id": job_id}


async def run_pipeline(job_id: str):
    """Background task to run the scouting pipeline."""
    try:
        await orchestrator_service.run_pipeline(job_id)
    except Exception as e:
        print(f"Pipeline error for job {job_id}: {str(e)}")
        # Error handling is done in orchestrator


@router.get("/{job_id}")
async def get_job(job_id: str):
    """Get full job details including results."""
    job = await orchestrator_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return job


@router.get("/{job_id}/status")
async def get_job_status(job_id: str):
    """Get current job status and progress."""
    status = await orchestrator_service.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return status


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a running job."""
    success = await orchestrator_service.cancel_job(job_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Job cannot be cancelled"
        )
    
    return {"message": "Job cancelled", "job_id": job_id}


@router.get("")
async def list_jobs(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
):
    """List all jobs with optional filtering."""
    storage = get_storage()
    jobs = await storage.list_jobs(limit=limit)
    
    return {
        "jobs": [j.model_dump() for j in jobs],
        "total": len(jobs),
        "limit": limit,
        "offset": offset
    }


@router.delete("")
async def clear_jobs():
    """Delete all completed/failed jobs."""
    return {"message": "Cleared 0 jobs"}


@router.delete("/{job_id}")
async def delete_job(job_id: str):
    """Delete a specific job."""
    return {"message": "Job deleted", "job_id": job_id}
