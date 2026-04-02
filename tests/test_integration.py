"""
Integration tests for B-Roll Scout critical flows.

These tests verify the full data path:
  Frontend API routes → Backend FastAPI → Pipeline services → DynamoDB → Results display

Run with:  python -m pytest tests/test_integration.py -v
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SCRIPT = (
    "இது ஒரு சோதனை ஸ்கிரிப்ட். "
    "தொழில்நுட்பத்தின் வரலாறு மிகவும் சுவாரஸ்யமானது. "
    "கணினிகள் பெரிய மெயின்ஃபிரேம்களிலிருந்து தனிப்பட்ட கணினிகளாக மாறியது குறிப்பிடத்தக்கது. "
    "சமூகத்தில் அதன் தாக்கம் மிகப் பெரியது என்று சொல்லலாம்."
)

MOCK_SEGMENTS = [
    {
        "segment_id": "seg_001",
        "title": "Test Scene",
        "summary": "A test segment about technology",
        "visual_need": "footage of computers",
        "emotional_tone": "informative",
        "key_terms": ["technology", "computers"],
        "search_queries": ["technology documentary", "computers explainer"],
        "estimated_duration_seconds": 60,
    }
]

MOCK_YOUTUBE_SEARCH_RESULT = [
    {
        "video_id": "dQw4w9WgXcQ",
        "title": "Test Documentary",
        "channel_id": "UCtest123456789012345",
        "channel_title": "Test Channel",
        "published_at": "2025-01-01T00:00:00Z",
        "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    }
]

MOCK_VIDEO_DETAILS = [
    {
        "video_id": "dQw4w9WgXcQ",
        "title": "Test Documentary",
        "channel_id": "UCtest123456789012345",
        "channel_name": "Test Channel",
        "channel_subscribers": 50000,
        "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "duration_seconds": 600,
        "published_at": "2025-01-01T00:00:00Z",
        "view_count": 100000,
        "description": "A documentary about technology",
    }
]

MOCK_CHANNEL_STATS = {
    "UCtest123456789012345": {
        "subscriber_count": 50000,
        "video_count": 100,
        "channel_name": "Test Channel",
        "thumbnail_url": "",
    }
}

MOCK_TRANSCRIPT = """
0:00 Welcome to this documentary about technology
0:15 The evolution of computers has been remarkable
0:30 From large mainframes to personal computers
0:45 The impact on society cannot be overstated
1:00 Let's explore how this technology shapes our world
"""


# ---------------------------------------------------------------------------
# 1. YouTube utility — quota detection
# ---------------------------------------------------------------------------

class TestYouTubeQuotaHandling:

    def test_quota_flag_starts_false(self):
        from app.utils.youtube import is_quota_exhausted, reset_quota_flag
        reset_quota_flag()
        assert not is_quota_exhausted()

    @pytest.mark.asyncio
    async def test_quota_flag_set_on_403(self):
        from app.utils.youtube import (
            _request_with_retry, is_quota_exhausted, reset_quota_flag,
        )
        reset_quota_flag()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.content = b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'
        mock_response.json.return_value = {
            "error": {"errors": [{"reason": "quotaExceeded"}]}
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await _request_with_retry(mock_client, "https://example.com", {})

        assert result is None
        assert is_quota_exhausted()
        reset_quota_flag()

    @pytest.mark.asyncio
    async def test_subsequent_calls_skipped_after_quota(self):
        from app.utils.youtube import (
            _request_with_retry, is_quota_exhausted, reset_quota_flag,
        )
        import app.utils.youtube as yt_mod
        yt_mod._quota_exhausted = True

        mock_client = AsyncMock()
        result = await _request_with_retry(mock_client, "https://example.com", {})

        assert result is None
        mock_client.get.assert_not_called()
        reset_quota_flag()


# ---------------------------------------------------------------------------
# 2. ISO 8601 duration parsing
# ---------------------------------------------------------------------------

class TestDurationParsing:

    def test_standard_duration(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("PT1H2M30S") == 3750

    def test_minutes_only(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("PT10M") == 600

    def test_seconds_only(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("PT45S") == 45

    def test_empty_string(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("") == 0

    def test_none(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration(None) == 0


# ---------------------------------------------------------------------------
# 3. Searcher — blocked channel filtering
# ---------------------------------------------------------------------------

class TestSearcherBlocking:

    def test_blocked_set_includes_all_categories(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={
            "blocked_networks": ["CNN", "BBC"],
            "blocked_studios": ["Disney"],
            "blocked_sports": ["FIFA"],
            "custom_block_rules": "MyBlockedChannel\nAnotherOne",
        })
        blocked = svc._build_blocked_set()
        assert "cnn" in blocked
        assert "bbc" in blocked
        assert "disney" in blocked
        assert "fifa" in blocked
        assert "myblockedchannel" in blocked
        assert "anotherone" in blocked

    def test_empty_block_list(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={
            "blocked_networks": [],
            "blocked_studios": [],
            "blocked_sports": [],
        })
        blocked = svc._build_blocked_set()
        assert len(blocked) == 0


# ---------------------------------------------------------------------------
# 4. Searcher reads pipeline settings not DEFAULTS
# ---------------------------------------------------------------------------

class TestSearcherUsesSettings:

    def test_get_reads_pipeline_first(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={
            "max_candidates_per_segment": 99,
        })
        assert svc._get("max_candidates_per_segment") == 99

    def test_get_falls_back_to_defaults(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={})
        val = svc._get("max_candidates_per_segment")
        from app.config import DEFAULTS
        assert val == DEFAULTS["max_candidates_per_segment"]


# ---------------------------------------------------------------------------
# 5. Matcher reads pipeline settings
# ---------------------------------------------------------------------------

class TestMatcherUsesSettings:

    def test_get_reads_pipeline_first(self):
        from app.services.matcher import MatcherService
        svc = MatcherService(pipeline_settings={
            "timestamp_model": "gpt-4o",
            "special_instructions": "Prefer aerial shots",
        })
        assert svc._get("timestamp_model") == "gpt-4o"
        assert svc._get("special_instructions") == "Prefer aerial shots"

    def test_get_falls_back(self):
        from app.services.matcher import MatcherService
        svc = MatcherService(pipeline_settings={})
        assert svc._get("timestamp_model") == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# 6. Ranker — scoring and filtering
# ---------------------------------------------------------------------------

class TestRanker:

    def test_rank_empty_candidates(self):
        from app.services.ranker import RankerService
        from app.models.schemas import Segment
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="Test", summary="Test",
            visual_need="test", emotional_tone="calm",
            key_terms=["test"], search_queries=["test"],
        )
        results = ranker.rank_and_filter([], seg)
        assert results == []

    def test_blocked_candidates_filtered_when_alternatives_exist(self):
        from app.services.ranker import RankerService
        from app.models.schemas import (
            Segment, CandidateVideo, MatchResult, TranscriptSource,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="Test", summary="Test",
            visual_need="test", emotional_tone="calm",
            key_terms=["test"], search_queries=["test"],
        )
        blocked_cand = CandidateVideo(
            video_id="v1", video_url="https://youtube.com/watch?v=v1",
            video_title="CNN Report", channel_name="CNN",
            channel_id="UC_cnn", channel_subscribers=1000000,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=100000,
            is_blocked=True,
        )
        good_cand = CandidateVideo(
            video_id="v2", video_url="https://youtube.com/watch?v=v2",
            video_title="Documentary", channel_name="Good Channel",
            channel_id="UC_good", channel_subscribers=50000,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=50000,
            is_blocked=False,
        )
        match_blocked = MatchResult(
            start_time_seconds=30, end_time_seconds=90,
            confidence_score=0.9, source_flag=TranscriptSource.YOUTUBE_AUTO,
            context_match_valid=True,
        )
        match_good = MatchResult(
            start_time_seconds=60, end_time_seconds=120,
            confidence_score=0.8, source_flag=TranscriptSource.YOUTUBE_AUTO,
            context_match_valid=True,
        )
        results = ranker.rank_and_filter(
            [(blocked_cand, match_blocked), (good_cand, match_good)], seg
        )
        video_ids = [r.video_id for r in results]
        assert "v1" not in video_ids
        assert "v2" in video_ids

    def test_deduplication_keeps_best_segment(self):
        from app.services.ranker import RankerService
        from app.models.schemas import RankedResult, TranscriptSource

        ranker = RankerService()

        common = dict(
            video_url="url", video_title="V1", channel_name="Ch",
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z",
            source_flag=TranscriptSource.YOUTUBE_AUTO,
        )
        r1 = RankedResult(
            result_id="res_001_001", segment_id="seg_001",
            video_id="v1", relevance_score=0.9, confidence_score=0.8,
            **common,
        )
        r2_dup = RankedResult(
            result_id="res_002_001", segment_id="seg_002",
            video_id="v1", relevance_score=0.5, confidence_score=0.8,
            **common,
        )
        r2_unique = RankedResult(
            result_id="res_002_002", segment_id="seg_002",
            video_id="v2", relevance_score=0.7, confidence_score=0.8,
            **common,
        )

        input_data = {"seg_001": [r1], "seg_002": [r2_dup, r2_unique]}
        deduped = ranker.deduplicate_across_segments(input_data)

        seg1_ids = [r.video_id for r in deduped["seg_001"]]
        seg2_ids = [r.video_id for r in deduped["seg_002"]]
        assert "v1" in seg1_ids
        assert "v1" not in seg2_ids
        assert "v2" in seg2_ids


# ---------------------------------------------------------------------------
# 7. Full search_for_segment with mocked YouTube APIs
# ---------------------------------------------------------------------------

class TestSearchForSegment:

    @pytest.mark.asyncio
    async def test_search_returns_candidates_with_mocked_apis(self):
        from app.services.searcher import SearcherService
        from app.models.schemas import Segment
        from app.utils.youtube import reset_quota_flag
        reset_quota_flag()

        seg = Segment(
            segment_id="seg_001", title="Technology Evolution",
            summary="History of computing", visual_need="computer footage",
            emotional_tone="informative",
            key_terms=["technology", "computers"],
            search_queries=["technology documentary", "computers explainer"],
        )

        with patch("app.services.searcher.search_channel_videos", new_callable=AsyncMock) as mock_ch, \
             patch("app.services.searcher.search_videos", new_callable=AsyncMock) as mock_sv, \
             patch("app.services.searcher.get_video_details", new_callable=AsyncMock) as mock_vd, \
             patch("app.services.searcher.get_channel_stats", new_callable=AsyncMock) as mock_cs:

            mock_ch.return_value = MOCK_YOUTUBE_SEARCH_RESULT
            mock_sv.return_value = MOCK_YOUTUBE_SEARCH_RESULT
            mock_vd.return_value = MOCK_VIDEO_DETAILS
            mock_cs.return_value = MOCK_CHANNEL_STATS

            svc = SearcherService(pipeline_settings={
                "preferred_channels_tier1": ["UCtest123456789012345"],
                "preferred_channels_tier2": [],
                "blocked_networks": [],
                "blocked_studios": [],
                "blocked_sports": [],
                "youtube_results_per_query": 5,
                "max_candidates_per_segment": 12,
                "min_video_duration_sec": 120,
                "max_video_duration_sec": 5400,
                "gemini_expanded_queries": 0,
            })

            candidates = await svc.search_for_segment(seg, job_id="test-job")

            assert len(candidates) >= 1
            assert candidates[0].video_id == "dQw4w9WgXcQ"
            assert candidates[0].video_title == "Test Documentary"
            assert not candidates[0].is_blocked

    @pytest.mark.asyncio
    async def test_search_skips_when_quota_exhausted(self):
        from app.services.searcher import SearcherService
        from app.models.schemas import Segment
        import app.utils.youtube as yt_mod

        yt_mod._quota_exhausted = True

        seg = Segment(
            segment_id="seg_001", title="Test", summary="Test",
            visual_need="test", emotional_tone="calm",
            key_terms=["test"], search_queries=["test"],
        )

        svc = SearcherService(pipeline_settings={
            "preferred_channels_tier1": [],
            "preferred_channels_tier2": [],
            "blocked_networks": [],
            "blocked_studios": [],
            "blocked_sports": [],
        })

        candidates = await svc.search_for_segment(seg, job_id="test-job")
        assert candidates == []
        yt_mod._quota_exhausted = False


# ---------------------------------------------------------------------------
# 8. FastAPI endpoint tests
# ---------------------------------------------------------------------------

class TestFastAPIEndpoints:

    @pytest.fixture
    def client(self):
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "db" in data
            assert "version" in data

    @pytest.mark.asyncio
    async def test_create_job_returns_job_id(self, client):
        with patch("app.main.run_pipeline", new_callable=AsyncMock) as mock_pipe:
            mock_pipe.return_value = None
            async with client as c:
                resp = await c.post("/api/v1/jobs", json={
                    "script": SAMPLE_SCRIPT,
                    "editor_id": "test_editor",
                })
                assert resp.status_code == 200
                data = resp.json()
                assert "job_id" in data
                assert data["status"] == "processing"

    @pytest.mark.asyncio
    async def test_get_nonexistent_job_returns_404(self, client):
        async with client as c:
            resp = await c.get("/api/v1/jobs/nonexistent-job-id")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_status_nonexistent_returns_404(self, client):
        async with client as c:
            resp = await c.get("/api/v1/jobs/nonexistent-job-id/status")
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 9. Progress tracking (in-memory)
# ---------------------------------------------------------------------------

class TestProgressTracking:

    def test_set_and_get_progress(self):
        from app.background import _set_progress, get_job_progress, _progress
        _progress.clear()

        _set_progress("test-job", "searching", 50, "Searching...")
        p = get_job_progress("test-job")

        assert p is not None
        assert p["stage"] == "searching"
        assert p["percent_complete"] == 50
        assert p["message"] == "Searching..."
        _progress.clear()

    def test_activity_log_appended(self):
        from app.background import _log_activity, get_job_progress, _progress
        _progress.clear()
        _progress["test-job"] = {"activity_log": []}

        _log_activity("test-job", "search", "Looking for videos")
        _log_activity("test-job", "check", "Found 5 videos")

        p = get_job_progress("test-job")
        assert len(p["activity_log"]) == 2
        assert p["activity_log"][0]["icon"] == "search"
        assert p["activity_log"][1]["text"] == "Found 5 videos"
        _progress.clear()

    def test_activity_log_capped_at_100(self):
        from app.background import _log_activity, get_job_progress, _progress
        _progress.clear()
        _progress["test-job"] = {"activity_log": []}

        for i in range(120):
            _log_activity("test-job", "info", f"Entry {i}")

        p = get_job_progress("test-job")
        assert len(p["activity_log"]) == 100
        assert p["activity_log"][-1]["text"] == "Entry 119"
        _progress.clear()
