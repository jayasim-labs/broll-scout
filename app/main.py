"""
B-Roll Scout - FastAPI Application Entry Point
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api import jobs, settings, costs, data
from app.config import get_settings

app_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Startup
    print("B-Roll Scout starting up...")
    
    # Ensure data directories exist
    os.makedirs(app_settings.DATA_DIR, exist_ok=True)
    os.makedirs(app_settings.JOBS_DIR, exist_ok=True)
    os.makedirs(app_settings.CACHE_DIR, exist_ok=True)
    
    yield
    
    # Shutdown
    print("B-Roll Scout shutting down...")


# Create FastAPI app
app = FastAPI(
    title="B-Roll Scout",
    description="AI-powered stock footage finder for video editors",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["Jobs"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(costs.router, prefix="/api/v1/costs", tags=["Costs"])
app.include_router(data.router, prefix="/api/v1/data", tags=["Data"])

# Mount static files
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """Redirect to main application."""
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
