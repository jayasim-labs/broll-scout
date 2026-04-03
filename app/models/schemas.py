from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TranscriptSource(str, Enum):
    CACHED = "cached_transcript"
    YOUTUBE_MANUAL = "youtube_captions"
    YOUTUBE_AUTO = "youtube_auto_captions"
    WHISPER = "whisper_transcription"
    NONE = "no_transcript"


VALID_CATEGORIES = [
    "history", "mystery", "current_affairs", "science",
    "finance", "ai_tech", "geo_politics", "societal_issues", "sports",
]


class JobCreateRequest(BaseModel):
    script: str = Field(..., min_length=100)
    title: str = Field(default="")
    project_id: Optional[str] = Field(default=None)
    editor_id: str = Field(default="default_editor")
    enable_gemini_expansion: bool = Field(default=False)
    category: Optional[str] = Field(default=None)

    @field_validator("script")
    @classmethod
    def validate_script(cls, v: str) -> str:
        if len(v.strip()) < 100:
            raise ValueError("Script must be at least 100 characters")
        return v.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if v and v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {v}")
        return v or None


class ProjectSummary(BaseModel):
    project_id: str
    title: str
    created_at: str
    updated_at: str
    job_count: int = 0
    total_clips: int = 0
    category: Optional[str] = None


class ProjectResponse(BaseModel):
    project_id: str
    title: str
    created_at: str
    updated_at: str
    job_count: int = 0
    total_clips: int = 0
    category: Optional[str] = None
    jobs: List["JobSummary"] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    clip_used: bool = Field(default=False)
    notes: Optional[str] = Field(default=None, max_length=1000)


class SettingsUpdateRequest(BaseModel):
    setting_key: str
    setting_value: Any


class BulkSettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


class ChannelResolveRequest(BaseModel):
    channel_url: str


class ScriptContext(BaseModel):
    """Top-level context extracted from the entire script by GPT-4o."""
    script_topic: str = ""
    script_domain: str = ""
    geographic_scope: str = ""
    temporal_scope: str = ""
    exclusion_context: str = ""


class Segment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    segment_id: str = Field(..., pattern=r"^seg_\d{3}$")
    title: str
    summary: str
    visual_need: str
    emotional_tone: str
    key_terms: List[str] = Field(default_factory=list)
    search_queries: List[str] = Field(default_factory=list)
    estimated_duration_seconds: int = Field(default=60, ge=10)
    context_anchor: str = Field(default="")
    negative_keywords: List[str] = Field(default_factory=list)

    @field_validator("segment_id")
    @classmethod
    def validate_segment_id(cls, v: str) -> str:
        import re
        if not re.match(r"^seg_\d{3}$", v):
            raise ValueError("segment_id must match format seg_XXX")
        return v


class CandidateVideo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    video_id: str
    video_url: str
    video_title: str
    channel_name: str
    channel_id: str
    channel_subscribers: int = 0
    thumbnail_url: str
    video_duration_seconds: int
    published_at: str
    view_count: int = 0
    is_preferred_tier1: bool = False
    is_preferred_tier2: bool = False
    is_blocked: bool = False


class MatchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_time_seconds: Optional[int] = None
    end_time_seconds: Optional[int] = None
    transcript_excerpt: Optional[str] = None
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance_note: Optional[str] = None
    the_hook: Optional[str] = None
    source_flag: TranscriptSource = TranscriptSource.NONE
    context_match_valid: bool = True
    context_match: bool = True
    context_mismatch_reason: Optional[str] = None


class RankedResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    result_id: str
    segment_id: str
    video_id: str
    video_url: str
    video_title: str
    channel_name: str
    channel_subscribers: int = 0
    thumbnail_url: str
    video_duration_seconds: int
    published_at: str
    view_count: int = 0
    start_time_seconds: Optional[int] = None
    end_time_seconds: Optional[int] = None
    clip_url: Optional[str] = None
    transcript_excerpt: Optional[str] = None
    the_hook: Optional[str] = None
    relevance_note: Optional[str] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_flag: TranscriptSource = TranscriptSource.NONE
    context_match: bool = True
    context_mismatch_reason: Optional[str] = None
    editor_rating: Optional[int] = Field(default=None, ge=1, le=5)
    clip_used: bool = False
    editor_notes: Optional[str] = None


class Transcript(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    video_id: str
    transcript_text: Optional[str] = None
    transcript_source: TranscriptSource
    language: str = "en"
    video_duration_seconds: int = 0
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class APICosts(BaseModel):
    openai_calls: int = 0
    openai_mini_calls: int = 0
    openai_input_tokens: int = 0
    openai_output_tokens: int = 0
    whisper_minutes: float = 0.0
    whisper_calls: int = 0
    youtube_api_units: int = 0
    google_cse_calls: int = 0
    gemini_calls: int = 0
    ytdlp_searches: int = 0
    ytdlp_detail_lookups: int = 0
    estimated_cost_usd: float = 0.0


class SegmentWithResults(Segment):
    results: List[RankedResult] = Field(default_factory=list)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    status: JobStatus
    created_at: str
    completed_at: Optional[str] = None
    processing_time_seconds: Optional[float] = None
    script_duration_minutes: int = 0
    total_segments: int = 0
    total_results: int = 0
    minimum_results_met: bool = True
    api_costs: APICosts = Field(default_factory=APICosts)
    segments: List[SegmentWithResults] = Field(default_factory=list)
    english_translation: Optional[str] = None
    project_id: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    script_context: Optional[ScriptContext] = None
    activity_log: List[dict] = Field(default_factory=list)


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    segment_count: int = 0
    result_count: int = 0
    project_id: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: List[JobSummary] = Field(default_factory=list)


class ProjectCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: Optional[str] = Field(default=None)


class ProjectListResponse(BaseModel):
    projects: List[ProjectSummary] = Field(default_factory=list)


class ChannelResolution(BaseModel):
    channel_id: str
    channel_name: str
    subscribers: int = 0
    thumbnail_url: str = ""


class SettingsResponse(BaseModel):
    settings: Dict[str, Any]


class HealthResponse(BaseModel):
    status: str = "ok"
    db: str = "connected"
    version: str = "0.1.0"


class LibrarySearchParams(BaseModel):
    topic: Optional[str] = None
    date_from: Optional[str] = None
    min_rating: Optional[int] = Field(default=None, ge=1, le=5)


class LibrarySearchResponse(BaseModel):
    results: List[RankedResult] = Field(default_factory=list)
    total_count: int = 0


class AgentPollRequest(BaseModel):
    agent_id: str = Field(default="browser-agent")


class AgentResultRequest(BaseModel):
    task_id: str
    status: str = Field(default="completed")
    result: List[Dict[str, Any]] = Field(default_factory=list)


ProjectResponse.model_rebuild()
