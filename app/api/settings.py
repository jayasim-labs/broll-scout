"""
Settings API Routes - Manage application settings and API keys
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

from app.services.settings_service import get_settings_service
from app.config import get_settings


router = APIRouter()


class SettingsUpdate(BaseModel):
    """Request body for updating settings."""
    openaiKey: Optional[str] = None
    pexelsKey: Optional[str] = None
    defaultStyle: Optional[str] = None
    defaultAudience: Optional[str] = None
    defaultQuality: Optional[str] = None
    resultsPerCue: Optional[str] = None
    monthlyBudget: Optional[float] = None
    budgetAlerts: Optional[bool] = None
    alertThreshold: Optional[str] = None


class TestKeyRequest(BaseModel):
    """Request body for testing API keys."""
    provider: str
    key: str


class TestKeyResponse(BaseModel):
    """Response for API key test."""
    valid: bool
    error: Optional[str] = None


@router.get("")
async def get_settings():
    """Get current application settings (API keys masked)."""
    settings_data = await get_settings_service().get_all_settings()
    
    # Mask API keys for security
    if settings_data.get("openai_key"):
        key = settings_data["openai_key"]
        settings_data["openai_key"] = f"{key[:7]}...{key[-4:]}" if len(key) > 11 else "***"
    
    if settings_data.get("pexels_key"):
        key = settings_data["pexels_key"]
        settings_data["pexels_key"] = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"
    
    return settings_data


@router.put("")
async def update_settings(request: SettingsUpdate):
    """Update application settings."""
    update_data = {}
    
    if request.openaiKey:
        update_data["openai_key"] = request.openaiKey
    if request.pexelsKey:
        update_data["pexels_key"] = request.pexelsKey
    if request.defaultStyle:
        update_data["default_style"] = request.defaultStyle
    if request.defaultAudience:
        update_data["default_audience"] = request.defaultAudience
    if request.defaultQuality:
        update_data["default_quality"] = request.defaultQuality
    if request.resultsPerCue:
        update_data["results_per_cue"] = int(request.resultsPerCue)
    if request.monthlyBudget is not None:
        update_data["monthly_budget"] = request.monthlyBudget
    if request.budgetAlerts is not None:
        update_data["budget_alerts"] = request.budgetAlerts
    if request.alertThreshold:
        update_data["alert_threshold"] = int(request.alertThreshold)
    
    await get_settings_service().bulk_update_settings(update_data)
    
    return {"message": "Settings updated"}


@router.post("/test-key", response_model=TestKeyResponse)
async def test_api_key(request: TestKeyRequest):
    """Test if an API key is valid."""
    try:
        if request.provider == "openai":
            return await test_openai_key(request.key)
        elif request.provider == "pexels":
            return await test_pexels_key(request.key)
        else:
            raise HTTPException(status_code=400, detail="Unknown provider")
    except Exception as e:
        return TestKeyResponse(valid=False, error=str(e))


async def test_openai_key(key: str) -> TestKeyResponse:
    """Test OpenAI API key validity."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"}
            )
            
            if response.status_code == 200:
                return TestKeyResponse(valid=True)
            elif response.status_code == 401:
                return TestKeyResponse(valid=False, error="Invalid API key")
            else:
                return TestKeyResponse(
                    valid=False,
                    error=f"API error: {response.status_code}"
                )
    except Exception as e:
        return TestKeyResponse(valid=False, error=str(e))


async def test_pexels_key(key: str) -> TestKeyResponse:
    """Test Pexels API key validity."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.pexels.com/v1/search?query=test&per_page=1",
                headers={"Authorization": key}
            )
            
            if response.status_code == 200:
                return TestKeyResponse(valid=True)
            elif response.status_code == 401:
                return TestKeyResponse(valid=False, error="Invalid API key")
            else:
                return TestKeyResponse(
                    valid=False,
                    error=f"API error: {response.status_code}"
                )
    except Exception as e:
        return TestKeyResponse(valid=False, error=str(e))


@router.post("/reset")
async def reset_settings():
    """Reset all settings to defaults (keeps API keys)."""
    await get_settings_service().reset_to_defaults()
    return {"message": "Settings reset to defaults"}
