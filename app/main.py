import asyncio
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.models.schemas import (
    JobCreateRequest, JobListResponse, JobStatus,
    FeedbackRequest, SettingsUpdateRequest, BulkSettingsUpdateRequest,
    ChannelResolveRequest, SettingsResponse, HealthResponse,
    LibrarySearchResponse, AgentPollRequest, AgentResultRequest,
    ProjectCreateRequest, ProjectListResponse, ProjectResponse, ProjectSummary,
)
from app.background import run_pipeline, get_job_progress
from app.services.storage import get_storage
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


_usage_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _usage_task
    await _cleanup_stale_jobs()
    _usage_task = asyncio.create_task(_usage_recalc_loop())
    yield
    if _usage_task:
        _usage_task.cancel()


async def _usage_recalc_loop():
    """Recalculate usage stats every hour."""
    from app.services.usage_service import get_usage_service
    await asyncio.sleep(5)
    while True:
        try:
            svc = get_usage_service()
            await svc.recalculate()
            logger.info("Hourly usage recalculation complete")
        except Exception:
            logger.exception("Usage recalculation failed")
        await asyncio.sleep(3600)


async def _cleanup_stale_jobs():
    """Mark any 'processing' jobs as failed on startup — they were killed by a deploy/restart."""
    try:
        storage = get_storage()
        jobs = await storage.list_jobs(limit=100)
        stale = [j for j in jobs if j.status == JobStatus.PROCESSING]
        for job in stale:
            logger.warning("Cleaning up stale job %s (was processing when server restarted)", job.job_id)
            await storage.update_job_status(
                job.job_id, JobStatus.FAILED,
                completed_at=datetime.utcnow().isoformat(),
            )
        if stale:
            logger.info("Cleaned up %d stale processing jobs", len(stale))
    except Exception:
        logger.exception("Failed to clean up stale jobs on startup")


app = FastAPI(
    title="B-Roll Scout API",
    version="0.2.0",
    description="AI-powered B-roll intelligence for video editors",
    lifespan=lifespan,
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
    db_status = "not_configured"
    try:
        storage = get_storage()
        await storage.list_jobs(limit=1)
        db_status = "connected"
    except Exception:
        db_status = "error"
    return HealthResponse(status="ok", db=db_status, version="0.2.0")


_running_tasks: dict[str, asyncio.Task] = {}


@app.post("/api/v1/jobs")
async def create_job(
    body: JobCreateRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    job_id = str(uuid.uuid4())
    storage = get_storage()

    project_id = body.project_id
    title = body.title.strip() if body.title else ""
    category = body.category

    if not project_id and title:
        project_id = str(uuid.uuid4())
        await storage.create_project(project_id, title, category=category)
    elif project_id:
        existing = await storage.get_project(project_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Project not found")
        if not category:
            category = existing.get("category")

    task = asyncio.create_task(run_pipeline(
        job_id, body.script, body.editor_id,
        enable_gemini_expansion=body.enable_gemini_expansion,
        project_id=project_id,
        title=title,
        category=category,
    ))
    _running_tasks[job_id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(job_id, None))

    return {
        "job_id": job_id,
        "project_id": project_id,
        "title": title,
        "category": category,
        "status": "processing",
        "estimated_time_seconds": 120,
    }


@app.post("/api/v1/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    task = _running_tasks.get(job_id)
    if not task:
        storage = get_storage()
        job = await storage.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job_id": job_id, "status": job.status.value, "cancelled": False,
                "message": "Job already finished"}

    task.cancel()
    return {"job_id": job_id, "status": "cancelled", "cancelled": True}


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
        elif progress.get("stage") == "cancelled":
            status = "cancelled"
        elif progress.get("stage") == "failed":
            status = "failed"
        return {"job_id": job_id, "status": status, "progress": progress}

    storage = get_storage()
    job = await storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    stored_log = await storage.get_activity_log(job_id)
    return {
        "job_id": job_id,
        "status": job.status.value,
        "progress": {
            "stage": "completed" if job.status == JobStatus.COMPLETE else job.status.value,
            "percent_complete": 100 if job.status == JobStatus.COMPLETE else 0,
            "message": "Complete" if job.status == JobStatus.COMPLETE else job.status.value,
            "activity_log": stored_log,
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


@app.post("/api/v1/settings/channels/resolve-bulk")
async def resolve_channels_bulk(
    body: dict,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    channel_ids = body.get("channel_ids", [])
    service = get_settings_service()
    results = {}
    for cid in channel_ids:
        try:
            resolved = await service.resolve_channel(cid)
            if resolved:
                results[cid] = resolved.model_dump() if hasattr(resolved, 'model_dump') else {
                    "channel_id": resolved.channel_id,
                    "channel_name": resolved.channel_name,
                    "subscribers": resolved.subscribers,
                    "thumbnail_url": resolved.thumbnail_url,
                }
        except Exception:
            pass
    return {"channels": results}


@app.post("/api/v1/settings/channels/resolve-names")
async def resolve_channels_by_name(
    body: dict,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    names = body.get("names", [])
    service = get_settings_service()
    results = await service.resolve_channels_by_name(names)
    return {
        "channels": {
            name: r.model_dump() if hasattr(r, 'model_dump') else {
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "subscribers": r.subscribers,
                "thumbnail_url": r.thumbnail_url,
            }
            for name, r in results.items()
        }
    }


# --- Project Endpoints ---


@app.get("/api/v1/projects")
async def list_projects(
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    projects = await storage.list_projects(limit=limit)
    return ProjectListResponse(projects=projects)


@app.post("/api/v1/projects")
async def create_project(
    body: ProjectCreateRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    project_id = str(uuid.uuid4())
    await storage.create_project(project_id, body.title.strip(), category=body.category)
    return {"project_id": project_id, "title": body.title.strip(), "category": body.category}


@app.get("/api/v1/projects/{project_id}")
async def get_project(
    project_id: str,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    proj = await storage.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    jobs = await storage.list_jobs(limit=100)
    project_jobs = [j for j in jobs if j.project_id == project_id]
    project_jobs.sort(key=lambda j: j.created_at, reverse=True)

    return ProjectResponse(
        project_id=proj.get("project_id", ""),
        title=proj.get("title", ""),
        created_at=proj.get("created_at", ""),
        updated_at=proj.get("updated_at", ""),
        job_count=len(project_jobs),
        total_clips=sum(j.result_count for j in project_jobs),
        category=proj.get("category"),
        jobs=project_jobs,
    )


@app.put("/api/v1/projects/{project_id}")
async def rename_project(
    project_id: str,
    body: ProjectCreateRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    ok = await storage.rename_project(project_id, body.title.strip())
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to rename project")
    return {"status": "ok", "project_id": project_id, "title": body.title.strip()}


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(
    project_id: str,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    ok = await storage.delete_project(project_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete project")
    return {"status": "ok"}


# --- Usage / Cost Endpoints ---

from app.services.usage_service import get_usage_service


@app.get("/api/v1/usage")
async def get_usage(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    svc = get_usage_service()
    return await svc.get_all_usage()


@app.post("/api/v1/usage/recalculate")
async def recalculate_usage(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    svc = get_usage_service()
    totals = await svc.recalculate()
    return {"status": "ok", "totals": totals}


# --- Local yt-dlp Agent Endpoints ---

from app.utils import agent_queue


@app.post("/api/v1/agent/poll")
async def agent_poll(body: AgentPollRequest):
    tasks = await agent_queue.poll_tasks(body.agent_id, max_tasks=3)
    return {"tasks": tasks}


@app.post("/api/v1/agent/result")
async def agent_result(body: AgentResultRequest):
    ok = await agent_queue.submit_result(body.task_id, body.status, body.result)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already completed")
    return {"ok": True}


@app.get("/api/v1/agent/status")
async def agent_status():
    status = await agent_queue.get_queue_status()
    return status


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
