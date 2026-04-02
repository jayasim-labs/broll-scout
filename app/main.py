import asyncio
import os
import uuid
import logging

from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models.schemas import (
    JobCreateRequest, JobResponse, JobListResponse, JobSummary, JobStatus,
    FeedbackRequest, SettingsUpdateRequest, BulkSettingsUpdateRequest,
    ChannelResolveRequest, ChannelResolution, SettingsResponse, HealthResponse,
    LibrarySearchResponse,
)
from app.background import run_pipeline, get_job_progress
from app.services.storage import get_storage
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)

app = FastAPI(
    title="B-Roll Scout API",
    version="0.1.0",
    description="AI-powered B-roll intelligence for video editors",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _verify_key(x_api_key: str | None) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    settings = get_settings()
    on_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    explicit_aws = bool(settings.aws_access_key_id and settings.aws_secret_access_key)
    db_status = "not_configured"
    if on_lambda or explicit_aws:
        try:
            storage = get_storage()
            await storage.list_jobs(limit=1)
            db_status = "connected"
        except Exception:
            db_status = "error"
    return HealthResponse(status="ok", db=db_status, version="0.1.0")


_running_tasks: dict[str, asyncio.Task] = {}


@app.post("/api/v1/jobs")
async def create_job(
    body: JobCreateRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    job_id = str(uuid.uuid4())

    task = asyncio.create_task(run_pipeline(job_id, body.script, body.editor_id))
    _running_tasks[job_id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(job_id, None))

    return {
        "job_id": job_id,
        "status": "processing",
        "estimated_time_seconds": 120,
    }


@app.get("/api/v1/jobs/{job_id}")
async def get_job(
    job_id: str,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    job = await storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/v1/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    progress = get_job_progress(job_id)
    if progress:
        status = "processing"
        if progress.get("stage") == "completed":
            status = "complete"
        elif progress.get("stage") == "failed":
            status = "failed"
        return {"job_id": job_id, "status": status, "progress": progress}

    storage = get_storage()
    job = await storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job.status.value,
        "progress": {
            "stage": "completed" if job.status == JobStatus.COMPLETE else job.status.value,
            "percent_complete": 100 if job.status == JobStatus.COMPLETE else 0,
            "message": "Complete" if job.status == JobStatus.COMPLETE else job.status.value,
            "activity_log": [],
        },
    }


@app.get("/api/v1/jobs")
async def list_jobs(
    limit: int = Query(default=30, ge=1, le=100),
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    jobs = await storage.list_jobs(limit=limit)
    return JobListResponse(jobs=jobs)


@app.post("/api/v1/results/{result_id}/feedback")
async def submit_feedback(
    result_id: str,
    body: FeedbackRequest,
    job_id: str = Query(...),
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    await storage.store_feedback(
        job_id=job_id,
        result_id=result_id,
        rating=body.rating,
        clip_used=body.clip_used,
        notes=body.notes,
    )
    return {"status": "ok"}


@app.get("/api/v1/library/search")
async def search_library(
    topic: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    min_rating: int | None = Query(default=None, ge=1, le=5),
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    results = await storage.search_library(topic, date_from, min_rating)
    return LibrarySearchResponse(results=results, total_count=len(results))


@app.get("/api/v1/settings")
async def get_all_settings(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    settings = await service.get_all_settings()
    return SettingsResponse(settings=settings)


@app.put("/api/v1/settings")
async def update_setting(
    body: SettingsUpdateRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    ok = await service.update_setting(body.setting_key, body.setting_value)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid setting")
    return {"status": "ok"}


@app.put("/api/v1/settings/bulk")
async def bulk_update_settings(
    body: BulkSettingsUpdateRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    count = await service.bulk_update_settings(body.settings)
    return {"status": "ok", "updated": count}


@app.post("/api/v1/settings/reset")
async def reset_settings(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    ok = await service.reset_to_defaults()
    return {"status": "ok" if ok else "error"}


@app.post("/api/v1/settings/channels/resolve")
async def resolve_channel(
    body: ChannelResolveRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    result = await service.resolve_channel(body.channel_url)
    if not result:
        raise HTTPException(status_code=404, detail="Channel not found")
    return result


try:
    from mangum import Mangum
    handler = Mangum(app, lifespan="off")
except ImportError:
    handler = None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
