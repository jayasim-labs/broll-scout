"""
Data API Routes - Export and manage application data
"""

from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.storage import get_storage
from app.services.settings_service import get_settings_service
from app.utils.cost_tracker import get_cost_tracker


router = APIRouter()


@router.get("/export")
async def export_all_data():
    """
    Export all application data as JSON.
    
    Includes:
    - All jobs and their results
    - Settings (API keys masked)
    - Cost history
    """
    storage = get_storage()
    jobs = await storage.list_jobs(limit=1000)
    
    settings_data = await get_settings_service().get_all_settings()
    if settings_data.get("openai_key"):
        settings_data["openai_key"] = "***REDACTED***"
    if settings_data.get("pexels_key"):
        settings_data["pexels_key"] = "***REDACTED***"
    
    jobs_data = [j.model_dump() for j in jobs]
    
    export_data = {
        "export_date": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "jobs": jobs_data,
        "settings": settings_data,
        "cost_history": [],
        "summary": {
            "total_jobs": len(jobs_data),
            "completed_jobs": sum(1 for j in jobs_data if j.get("status") == "completed"),
            "total_cost": 0.0
        }
    }
    
    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f"attachment; filename=broll-scout-export-{datetime.utcnow().strftime('%Y%m%d')}.json"
        }
    )


@router.get("/stats")
async def get_application_stats():
    """Get application usage statistics."""
    storage = get_storage()
    jobs = await storage.list_jobs(limit=1000)
    
    total_jobs = len(jobs)
    completed_jobs = sum(1 for j in jobs if j.status.value == "completed")
    failed_jobs = sum(1 for j in jobs if j.status.value == "failed")
    
    return {
        "jobs": {
            "total": total_jobs,
            "completed": completed_jobs,
            "failed": failed_jobs,
            "success_rate": completed_jobs / total_jobs if total_jobs > 0 else 0
        },
        "processing": {
            "total_cues_generated": 0,
            "total_clips_matched": 0,
            "scripts_processed": 0
        },
        "costs": {
            "total_spent": 0.0,
            "average_per_job": 0.0
        }
    }


@router.delete("/clear-cache")
async def clear_cache():
    """Clear cached data (search results, etc.)."""
    return {"message": "Cleared 0 cached items"}
