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
    ChannelResolveRequest, ChannelAddRequest, ChannelRemoveRequest,
    SettingsResponse, HealthResponse,
    LibrarySearchResponse, AgentPollRequest, AgentResultRequest,
    ProjectCreateRequest, ProjectListResponse, ProjectResponse, ProjectSummary,
    DeepSearchRequest, AddToJobRequest, FindSimilarRequest, RecategorizeRequest,
    ExpandShotRequest, BRollShot, ResumeRequest,
)
from app.background import run_pipeline, get_job_progress
from app.services.storage import get_storage
from app.services.settings_service import get_settings_service
from app.utils import agent_queue

logger = logging.getLogger(__name__)


_usage_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _usage_task
    await _cleanup_stale_jobs()
    try:
        await get_settings_service().migrate_min_video_duration_to_current_default()
    except Exception:
        logger.exception("min_video_duration_sec DynamoDB migration skipped")
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


@app.post("/api/v1/jobs/estimate")
async def estimate_pipeline(
    body: JobCreateRequest,
    x_api_key: str | None = Header(default=None),
):
    """Return a pipeline plan preview without running anything."""
    _verify_key(x_api_key)

    service = get_settings_service()
    pipeline_cfg = await service.get_all_settings()

    word_count = len(body.script.split())
    script_duration = max(1, round(word_count / 100))

    # Segment estimation: matches _build_segment_guidance in translator.py
    min_segments = max(2, round(script_duration / 2))
    if script_duration <= 3:
        est_segments = max(min_segments, round(script_duration * 1.2))
        script_type = "Short Form"
    elif script_duration <= 8:
        est_segments = max(min_segments, round(script_duration * 0.7))
        script_type = "Medium Form"
    elif script_duration <= 20:
        est_segments = max(min_segments, round(script_duration * 0.65))
        script_type = "Long Form"
    else:
        est_segments = max(min_segments, round(script_duration * 0.6))
        script_type = "Long Form"

    # Shot estimation: GPT typically gives 2-3 shots/segment for documentary
    # ~15% of segments are host-on-camera (broll_count=0)
    active_segments = round(est_segments * 0.85)
    no_broll_segments = est_segments - active_segments
    avg_shots_per_segment = 2.5 if script_duration >= 10 else 2.0
    est_shots = round(active_segments * avg_shots_per_segment)

    queries_per_shot = 3
    est_queries = est_shots * queries_per_shot

    # YouTube search estimate
    yt_results_per_query = pipeline_cfg.get("youtube_results_per_query", 12)
    max_cands_per_shot = pipeline_cfg.get("max_candidates_per_shot", 20)
    est_unique_videos = min(est_shots * max_cands_per_shot, est_queries * yt_results_per_query // 2)

    # Time estimate: 12s per shot + 80s cooldown per batch of 4
    num_batches = max(1, est_shots // 4)
    search_sec = est_shots * 12 + num_batches * 80
    transcript_sec = est_unique_videos * 8
    matching_sec = est_unique_videos * 3
    total_sec = 60 + search_sec + transcript_sec + matching_sec  # 60s for translation
    est_time_min_low = max(1, total_sec // 60)
    est_time_min_high = max(est_time_min_low + 2, round(total_sec * 1.4 / 60))

    # Cost estimate: GPT-4o translation
    # ~$0.005/1K input tokens, ~$0.015/1K output tokens
    # Input: script (~0.75 tokens/word) + system prompt (~2K tokens)
    # Output: segments JSON (~3x input tokens)
    input_tokens = round(word_count * 0.75 + 2000)
    output_tokens = round(input_tokens * 2.5)
    translation_model = pipeline_cfg.get("translation_model", "gpt-4o")
    if "gpt-4o-mini" in translation_model:
        cost_per_1k_in, cost_per_1k_out = 0.00015, 0.0006
    else:
        cost_per_1k_in, cost_per_1k_out = 0.0025, 0.01
    translation_cost = (input_tokens / 1000) * cost_per_1k_in + (output_tokens / 1000) * cost_per_1k_out

    # Matching cost: gpt-4o-mini for timestamp matching
    matching_cost = est_unique_videos * 0.0003
    total_cost = translation_cost + matching_cost

    return {
        "script_type": script_type,
        "word_count": word_count,
        "est_duration_minutes": script_duration,
        "est_segments": est_segments,
        "est_no_broll_segments": no_broll_segments,
        "est_active_segments": active_segments,
        "est_shots": est_shots,
        "est_queries": est_queries,
        "est_youtube_searches": est_queries,
        "est_videos_to_match": est_unique_videos,
        "est_pipeline_time_min": est_time_min_low,
        "est_pipeline_time_max": est_time_min_high,
        "est_cost_usd": round(total_cost, 3),
        "config": {
            "translation_model": translation_model,
            "matcher_model": pipeline_cfg.get("matcher_model", "qwen3:8b"),
            "matcher_backend": pipeline_cfg.get("matcher_backend", "auto"),
            "youtube_results_per_query": yt_results_per_query,
            "max_candidates_per_shot": max_cands_per_shot,
            "whisper_model": pipeline_cfg.get("whisper_model", "large-v3-turbo"),
            "enable_gemini_expansion": body.enable_gemini_expansion,
        },
    }


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
                "message": "Job already finished",
                "cancelled_agent_task_ids": []}

    cancelled_agent_task_ids = await agent_queue.cancel_tasks_for_job(job_id)
    task.cancel()
    return {
        "job_id": job_id,
        "status": "cancelled",
        "cancelled": True,
        "cancelled_agent_task_ids": cancelled_agent_task_ids,
    }


CHECKPOINT_ORDER = ["segmented", "searched", "matched", "completed"]


@app.post("/api/v1/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    body: ResumeRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    job = await storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job_id in _running_tasks:
        raise HTTPException(status_code=409, detail="Job is currently running")

    if job.status == JobStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Job is currently processing")

    cp = getattr(job, "pipeline_checkpoint", None)
    if cp == "completed":
        raise HTTPException(status_code=400, detail="Job is already completed — nothing to resume")

    required_checkpoint = "searched" if body.from_stage == "transcripts" else "matched"
    if not cp:
        raise HTTPException(status_code=400, detail="Job has no checkpoint — cannot resume (must run full pipeline)")
    cp_idx = CHECKPOINT_ORDER.index(cp) if cp in CHECKPOINT_ORDER else -1
    req_idx = CHECKPOINT_ORDER.index(required_checkpoint)
    if cp_idx < req_idx:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume from '{body.from_stage}': job checkpoint is '{cp}' but needs at least '{required_checkpoint}'",
        )

    task = asyncio.create_task(run_pipeline(
        job_id,
        resume_from=body.from_stage,
        project_id=job.project_id,
        title=job.title,
        category=job.category,
    ))
    _running_tasks[job_id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(job_id, None))

    return {
        "job_id": job_id,
        "status": "processing",
        "resumed_from": body.from_stage,
        "message": f"Resuming from {body.from_stage}",
    }


@app.post("/api/v1/jobs/{job_id}/segments/{segment_id}/expand-shots")
async def expand_segment_shots(
    job_id: str,
    segment_id: str,
    body: ExpandShotRequest,
    x_api_key: str | None = Header(default=None),
):
    """Editor-driven: generate additional B-roll shots for a specific segment."""
    _verify_key(x_api_key)
    storage = get_storage()
    job = await storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    target_seg = None
    for seg in job.segments:
        if seg.segment_id == segment_id:
            target_seg = seg
            break
    if not target_seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    from app.services.expand_shots import expand_shots_for_segment
    asyncio.create_task(
        expand_shots_for_segment(job_id, target_seg, body.count, job.script_context)
    )
    return {
        "job_id": job_id,
        "segment_id": segment_id,
        "message": f"Generating {body.count} new shot(s) for \"{target_seg.title}\"",
    }


@app.get("/api/v1/jobs/{job_id}/segments/{segment_id}/expand-progress")
async def get_expand_progress(
    job_id: str,
    segment_id: str,
    x_api_key: str | None = Header(default=None),
):
    """Get real-time progress of an expand-shots operation."""
    _verify_key(x_api_key)
    from app.services.expand_shots import get_expand_progress as _get_progress
    progress = _get_progress(job_id, segment_id)
    if not progress:
        return {"phase": "unknown", "log": []}
    return progress


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


@app.delete("/api/v1/jobs/{job_id}")
async def delete_job(
    job_id: str,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    stats = await storage.hard_delete_job(job_id)
    return {"status": "ok", "deleted": stats}


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
    q: str | None = Query(default=None),
    mode: str = Query(default="metadata"),
    categories: str | None = Query(default=None),
    min_rating: int | None = Query(default=None, ge=1, le=5),
    min_views: int | None = Query(default=None),
    used: str | None = Query(default=None),
    sort: str = Query(default="relevance"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    return await svc.search(
        q=q, categories=categories, min_rating=min_rating,
        min_views=min_views, used=used, sort=sort,
        page=page, per_page=per_page,
    )


@app.get("/api/v1/library/stats")
async def library_stats(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    return await svc.get_stats()


@app.post("/api/v1/library/deep-search")
async def library_deep_search(
    body: DeepSearchRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    clips = await svc.deep_search(body.query, body.max_results)
    stats = await svc.get_stats()
    return LibrarySearchResponse(total=len(clips), results=clips, stats=stats)


@app.post("/api/v1/library/add-to-job")
async def library_add_to_job(
    body: AddToJobRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    ok = await svc.add_to_job(body.job_id, body.result_id, body.job_id, body.segment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Source clip not found")
    return {"status": "ok"}


@app.post("/api/v1/library/find-similar")
async def library_find_similar(
    body: FindSimilarRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    clips = await svc.find_similar(body.job_id, body.result_id)
    return {"results": [c.model_dump() for c in clips]}


@app.post("/api/v1/library/re-categorize")
async def library_recategorize(
    body: RecategorizeRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    ok = await svc.recategorize(
        body.job_id, body.result_id,
        categories=body.categories or None,
        add=body.add or None,
        remove=body.remove or None,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to recategorize")
    return {"status": "ok"}


@app.get("/api/v1/library/categories")
async def library_categories(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    from app.services.library import get_library_service
    svc = get_library_service()
    stats = await svc.get_stats()
    return {"categories": [c.model_dump() for c in stats.top_categories]}


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


@app.post("/api/v1/settings/resolve-channel")
async def resolve_channel(
    body: ChannelResolveRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    channels = await service.resolve_channel_input(body.input)
    return {"resolved": len(channels) > 0, "channels": channels}


@app.post("/api/v1/settings/channels/add")
async def add_channel_source(
    body: ChannelAddRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    ok = await service.add_channel_source(body.model_dump())
    if not ok:
        raise HTTPException(status_code=409, detail="Channel already exists in this tier")
    return {"status": "ok"}


@app.post("/api/v1/settings/channels/remove")
async def remove_channel_source(
    body: ChannelRemoveRequest,
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    ok = await service.remove_channel_source(body.channel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"status": "ok"}


@app.get("/api/v1/settings/channels")
async def get_channel_sources(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    groups = await service.get_channel_sources_grouped()
    return {"groups": groups}


@app.post("/api/v1/settings/channels/resolve")
async def resolve_channel_legacy(
    body: dict,
    x_api_key: str | None = Header(default=None),
):
    """Legacy endpoint for backward compatibility."""
    _verify_key(x_api_key)
    channel_url = body.get("channel_url", "")
    service = get_settings_service()
    result = await service.resolve_channel(channel_url)
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


@app.post("/api/v1/settings/migrate-channels")
async def migrate_channels(
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    service = get_settings_service()
    count = await service.migrate_channel_settings_if_needed()
    return {"status": "ok", "migrated": count}


# --- Project Endpoints ---


@app.get("/api/v1/projects")
async def list_projects(
    limit: int = Query(default=200, ge=1, le=500),
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

    project_jobs = await storage.list_jobs_for_project(project_id)

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


@app.get("/api/v1/projects/{project_id}/jobs")
async def list_project_jobs(
    project_id: str,
    x_api_key: str | None = Header(default=None),
):
    """Lightweight endpoint: returns only jobs for a single project."""
    _verify_key(x_api_key)
    storage = get_storage()
    project_jobs = await storage.list_jobs_for_project(project_id)
    return JobListResponse(jobs=project_jobs)


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
    hard: bool = Query(default=False),
    x_api_key: str | None = Header(default=None),
):
    _verify_key(x_api_key)
    storage = get_storage()
    if hard:
        stats = await storage.hard_delete_project(project_id)
        return {"status": "ok", "deleted": stats}
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


@app.post("/api/v1/agent/poll")
async def agent_poll(body: AgentPollRequest):
    if body.heartbeat_only:
        await agent_queue.heartbeat(body.agent_id, job_id=body.job_id)
        return {"tasks": []}
    tasks = await agent_queue.poll_tasks(body.agent_id, max_tasks=3, job_id=body.job_id)
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
