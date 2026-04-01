"""
Costs API Routes - Track and manage API usage costs
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Query

from app.utils.cost_tracker import get_cost_tracker
from app.services.settings_service import get_settings_service


router = APIRouter()


@router.get("/monthly")
async def get_monthly_costs():
    """Get current month's cost summary."""
    now = datetime.utcnow()
    start_of_month = datetime(now.year, now.month, 1)
    
    settings_data = await get_settings_service().get_all_settings()
    budget = settings_data.get("monthly_budget", 50.0)
    
    return {
        "total_cost": 0.0,
        "budget": budget,
        "percent_used": 0.0,
        "remaining": budget,
        "transaction_count": 0,
        "period": {
            "start": start_of_month.isoformat(),
            "end": now.isoformat()
        }
    }


@router.get("/breakdown")
async def get_cost_breakdown(
    days: int = Query(default=30, ge=1, le=365)
):
    """Get cost breakdown by service for the specified period."""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    return {
        "by_service": {},
        "by_day": {},
        "total": 0.0,
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "days": days
        }
    }


@router.get("/history")
async def get_cost_history(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Get detailed cost transaction history."""
    return {
        "costs": [],
        "total": 0,
        "limit": limit,
        "offset": offset
    }


@router.get("/job/{job_id}")
async def get_job_costs(job_id: str):
    """Get cost breakdown for a specific job."""
    tracker = get_cost_tracker()
    costs = tracker.get_job_costs(job_id)
    
    return {
        "job_id": job_id,
        "total_cost": costs.calculate_cost() if costs else 0.0,
        "breakdown": costs.to_dict() if costs else {}
    }


@router.get("/estimate")
async def estimate_cost(
    word_count: int = Query(ge=1),
    video_style: str = "documentary"
):
    """Estimate cost for a script based on word count."""
    # Rough estimates based on typical usage
    estimated_cues = max(1, word_count // 50)  # ~1 cue per 50 words
    
    # Translation cost (GPT-4o-mini)
    input_tokens = word_count * 1.5  # ~1.5 tokens per word
    output_tokens = estimated_cues * 200  # ~200 tokens per cue
    translation_cost = (input_tokens * 0.00015 + output_tokens * 0.0006) / 1000
    
    # Matching cost (per cue analysis)
    matching_cost = estimated_cues * 0.002
    
    # Search is free (Pexels)
    search_cost = 0
    
    total = translation_cost + matching_cost + search_cost
    
    return {
        "word_count": word_count,
        "estimated_cues": estimated_cues,
        "breakdown": {
            "translation": round(translation_cost, 4),
            "matching": round(matching_cost, 4),
            "search": search_cost
        },
        "total_estimate": round(total, 4),
        "note": "Actual costs may vary based on script complexity"
    }
