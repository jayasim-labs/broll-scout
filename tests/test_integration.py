"""
Integration tests for B-Roll Scout critical flows.

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


# ---------------------------------------------------------------------------
# 1. QuotaTracker
# ---------------------------------------------------------------------------

class TestQuotaTracker:

    def test_starts_not_exhausted(self):
        from app.utils.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        assert not qt.is_quota_exhausted

    def test_mark_exhausted(self):
        from app.utils.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        qt.mark_quota_exhausted()
        assert qt.is_quota_exhausted

    def test_track_api_call(self):
        from app.utils.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        qt.track_api_call(100)
        assert qt.stats["api_units_used"] == 100
        assert qt.search_mode == "youtube_api"

    def test_track_ytdlp(self):
        from app.utils.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        qt.track_ytdlp_search()
        qt.track_ytdlp_details(3)
        s = qt.stats
        assert s["ytdlp_searches_via_agent"] == 1
        assert s["ytdlp_detail_lookups_via_agent"] == 3
        assert qt.search_mode == "ytdlp"

    def test_hybrid_mode(self):
        from app.utils.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        qt.track_api_call(100)
        qt.track_ytdlp_search()
        assert qt.search_mode == "hybrid"

    def test_reset_for_job(self):
        from app.utils.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        qt.track_api_call(500)
        qt.track_ytdlp_search()
        qt.reset_for_job()
        s = qt.stats
        assert s["api_units_used"] == 0
        assert s["ytdlp_searches_via_agent"] == 0


# ---------------------------------------------------------------------------
# 2. YouTubeQuotaExceeded exception
# ---------------------------------------------------------------------------

class TestYouTubeQuotaExceeded:

    @pytest.mark.asyncio
    async def test_quota_403_raises(self):
        from app.utils.youtube import _request_with_retry, YouTubeQuotaExceeded

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.content = b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'
        mock_response.json.return_value = {
            "error": {"errors": [{"reason": "quotaExceeded"}]}
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(YouTubeQuotaExceeded):
            await _request_with_retry(mock_client, "https://example.com", {})

    @pytest.mark.asyncio
    async def test_normal_403_returns_empty(self):
        from app.utils.youtube import _request_with_retry

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.content = b'{"error":{"errors":[{"reason":"forbidden"}]}}'
        mock_response.json.return_value = {
            "error": {"errors": [{"reason": "forbidden"}]}
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await _request_with_retry(mock_client, "https://example.com", {})
        assert result == {}


# ---------------------------------------------------------------------------
# 3. Duration parsing
# ---------------------------------------------------------------------------

class TestDurationParsing:

    def test_standard(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("PT1H2M30S") == 3750

    def test_minutes_only(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("PT10M") == 600

    def test_empty(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration("") == 0

    def test_none(self):
        from app.utils.youtube import parse_iso8601_duration
        assert parse_iso8601_duration(None) == 0


# ---------------------------------------------------------------------------
# 4. Agent queue
# ---------------------------------------------------------------------------

class TestAgentQueue:

    @pytest.mark.asyncio
    async def test_create_and_poll(self):
        from app.utils import agent_queue
        task_id = await agent_queue.create_task("search", {"query": "test", "max_results": 5})
        assert task_id

        tasks = await agent_queue.poll_tasks("test-agent")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == task_id
        assert tasks[0]["task_type"] == "search"

        # Clean up
        await agent_queue.submit_result(task_id, "completed", [{"video_id": "abc"}])

    @pytest.mark.asyncio
    async def test_submit_result_signals_event(self):
        from app.utils import agent_queue
        task_id = await agent_queue.create_task("search", {"query": "test"})
        await agent_queue.poll_tasks("test-agent")

        async def submit_after_delay():
            await asyncio.sleep(0.1)
            await agent_queue.submit_result(task_id, "completed", [{"video_id": "v1"}])

        asyncio.create_task(submit_after_delay())
        results = await agent_queue.wait_for_result(task_id, timeout=5)
        assert len(results) == 1
        assert results[0]["video_id"] == "v1"

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        from app.utils import agent_queue
        task_id = await agent_queue.create_task("search", {"query": "test"})
        results = await agent_queue.wait_for_result(task_id, timeout=0.1)
        assert results == []

    @pytest.mark.asyncio
    async def test_queue_status(self):
        from app.utils import agent_queue
        status = await agent_queue.get_queue_status()
        assert "pending_tasks" in status
        assert "agents_active" in status


# ---------------------------------------------------------------------------
# 5. Searcher blocked channel filtering
# ---------------------------------------------------------------------------

class TestSearcherBlocking:

    def test_blocked_set(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={
            "blocked_networks": ["CNN", "BBC"],
            "blocked_studios": ["Disney"],
            "blocked_sports": ["FIFA"],
            "custom_block_rules": "MyBlockedChannel\nAnotherOne",
        })
        blocked = svc._build_blocked_set()
        assert "cnn" in blocked
        assert "disney" in blocked
        assert "myblockedchannel" in blocked


# ---------------------------------------------------------------------------
# 6. Searcher reads pipeline settings
# ---------------------------------------------------------------------------

class TestSearcherUsesSettings:

    def test_get_reads_pipeline_first(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={
            "max_candidates_per_segment": 99,
        })
        assert svc._get("max_candidates_per_segment") == 99

    def test_backend_defaults_to_auto(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={})
        assert svc._backend() == "auto"

    def test_backend_from_settings(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={"search_backend": "ytdlp_only"})
        assert svc._backend() == "ytdlp_only"


# ---------------------------------------------------------------------------
# 7. Matcher reads pipeline settings
# ---------------------------------------------------------------------------

class TestMatcherUsesSettings:

    def test_get_reads_pipeline_first(self):
        from app.services.matcher import MatcherService
        svc = MatcherService(pipeline_settings={"timestamp_model": "gpt-4o"})
        assert svc._get("timestamp_model") == "gpt-4o"

    def test_get_falls_back(self):
        from app.services.matcher import MatcherService
        svc = MatcherService(pipeline_settings={})
        assert svc._get("timestamp_model") == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# 8. Ranker
# ---------------------------------------------------------------------------

class TestRanker:

    def test_rank_empty(self):
        from app.services.ranker import RankerService
        from app.models.schemas import Segment
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="calm",
            key_terms=["test"], search_queries=["test"],
        )
        assert ranker.rank_and_filter([], seg) == []

    def test_blocked_filtered_when_alternatives(self):
        from app.services.ranker import RankerService
        from app.models.schemas import (
            Segment, CandidateVideo, MatchResult, TranscriptSource,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="calm",
            key_terms=["test"], search_queries=["test"],
        )
        blocked = CandidateVideo(
            video_id="v1", video_url="u", video_title="CNN Report",
            channel_name="CNN", channel_id="c1", channel_subscribers=1000000,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=100000, is_blocked=True,
        )
        good = CandidateVideo(
            video_id="v2", video_url="u", video_title="Doc",
            channel_name="Good", channel_id="c2", channel_subscribers=50000,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=50000, is_blocked=False,
        )
        m1 = MatchResult(start_time_seconds=30, end_time_seconds=90,
                         confidence_score=0.9, source_flag=TranscriptSource.YOUTUBE_AUTO,
                         context_match_valid=True)
        m2 = MatchResult(start_time_seconds=60, end_time_seconds=120,
                         confidence_score=0.8, source_flag=TranscriptSource.YOUTUBE_AUTO,
                         context_match_valid=True)
        results = ranker.rank_and_filter([(blocked, m1), (good, m2)], seg)
        ids = [r.video_id for r in results]
        assert "v1" not in ids
        assert "v2" in ids

    def test_channel_authority_handles_none_subscribers(self):
        from app.services.ranker import RankerService
        from app.models.schemas import CandidateVideo
        ranker = RankerService()
        cand = CandidateVideo(
            video_id="v1", video_url="u", video_title="T",
            channel_name="Ch", channel_id="c1", channel_subscribers=0,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=100,
        )
        score = ranker._channel_authority(cand)
        assert score == 0.4

    def test_dedup_keeps_best_segment(self):
        from app.services.ranker import RankerService
        from app.models.schemas import RankedResult, TranscriptSource
        ranker = RankerService()
        common = dict(
            video_url="url", video_title="V1", channel_name="Ch",
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z",
            source_flag=TranscriptSource.YOUTUBE_AUTO,
        )
        r1 = RankedResult(result_id="r1", segment_id="seg_001", video_id="v1",
                          relevance_score=0.9, confidence_score=0.8, **common)
        r2_dup = RankedResult(result_id="r2", segment_id="seg_002", video_id="v1",
                              relevance_score=0.5, confidence_score=0.8, **common)
        r2_unique = RankedResult(result_id="r3", segment_id="seg_002", video_id="v2",
                                 relevance_score=0.7, confidence_score=0.8, **common)
        deduped = ranker.deduplicate_across_segments({
            "seg_001": [r1], "seg_002": [r2_dup, r2_unique],
        })
        assert "v1" in [r.video_id for r in deduped["seg_001"]]
        assert "v1" not in [r.video_id for r in deduped["seg_002"]]
        assert "v2" in [r.video_id for r in deduped["seg_002"]]


# ---------------------------------------------------------------------------
# 9. Search dispatchers
# ---------------------------------------------------------------------------

class TestSearchDispatchers:

    @pytest.mark.asyncio
    async def test_auto_uses_api_when_quota_ok(self):
        from app.services.searcher import _dispatch_search
        from app.utils.quota_tracker import QuotaTracker

        mock_qt = QuotaTracker()
        with patch("app.services.searcher.get_quota_tracker", return_value=mock_qt), \
             patch("app.services.searcher.search_videos", new_callable=AsyncMock) as mock_sv:
            mock_sv.return_value = MOCK_YOUTUBE_SEARCH_RESULT
            results = await _dispatch_search("test", max_results=5, backend="auto")
            assert len(results) == 1
            mock_sv.assert_called_once()
            assert mock_qt.stats["api_units_used"] == 100

    @pytest.mark.asyncio
    async def test_ytdlp_only_uses_agent(self):
        from app.services.searcher import _dispatch_search
        from app.utils.quota_tracker import QuotaTracker

        mock_qt = QuotaTracker()
        with patch("app.services.searcher.get_quota_tracker", return_value=mock_qt), \
             patch("app.services.searcher._search_via_agent", new_callable=AsyncMock) as mock_ag:
            mock_ag.return_value = [{"video_id": "abc"}]
            results = await _dispatch_search("test", max_results=5, backend="ytdlp_only")
            assert len(results) == 1
            mock_ag.assert_called_once()
            assert mock_qt.stats["ytdlp_searches_via_agent"] == 1

    @pytest.mark.asyncio
    async def test_auto_falls_back_on_quota_exceeded(self):
        from app.services.searcher import _dispatch_search
        from app.utils.youtube import YouTubeQuotaExceeded
        from app.utils.quota_tracker import QuotaTracker

        mock_qt = QuotaTracker()
        with patch("app.services.searcher.get_quota_tracker", return_value=mock_qt), \
             patch("app.services.searcher.search_videos", new_callable=AsyncMock) as mock_sv, \
             patch("app.services.searcher._search_via_agent", new_callable=AsyncMock) as mock_ag:
            mock_sv.side_effect = YouTubeQuotaExceeded("quota exceeded")
            mock_ag.return_value = [{"video_id": "fallback"}]

            results = await _dispatch_search("test", max_results=5, backend="auto")
            assert len(results) == 1
            assert results[0]["video_id"] == "fallback"
            assert mock_qt.is_quota_exhausted
            assert mock_qt.stats["ytdlp_searches_via_agent"] == 1


# ---------------------------------------------------------------------------
# 10. Search with mocked APIs
# ---------------------------------------------------------------------------

class TestSearchForSegment:

    @pytest.mark.asyncio
    async def test_search_returns_candidates(self):
        from app.services.searcher import SearcherService
        from app.models.schemas import Segment

        seg = Segment(
            segment_id="seg_001", title="Technology Evolution",
            summary="History of computing", visual_need="computer footage",
            emotional_tone="informative",
            key_terms=["technology", "computers"],
            search_queries=["technology documentary", "computers explainer"],
        )

        with patch("app.services.searcher._dispatch_channel_search", new_callable=AsyncMock) as mock_ch, \
             patch("app.services.searcher._dispatch_search", new_callable=AsyncMock) as mock_sv, \
             patch("app.services.searcher._dispatch_video_details", new_callable=AsyncMock) as mock_vd, \
             patch("app.services.searcher._dispatch_channel_stats", new_callable=AsyncMock) as mock_cs:

            mock_ch.return_value = MOCK_YOUTUBE_SEARCH_RESULT
            mock_sv.return_value = MOCK_YOUTUBE_SEARCH_RESULT
            mock_vd.return_value = MOCK_VIDEO_DETAILS
            mock_cs.return_value = MOCK_CHANNEL_STATS

            svc = SearcherService(pipeline_settings={
                "preferred_channels_tier1": ["UCtest123456789012345"],
                "preferred_channels_tier2": [],
                "blocked_networks": [], "blocked_studios": [], "blocked_sports": [],
                "youtube_results_per_query": 5,
                "max_candidates_per_segment": 12,
                "min_video_duration_sec": 120,
                "max_video_duration_sec": 5400,
                "gemini_expanded_queries": 0,
                "search_backend": "auto",
            })

            candidates = await svc.search_for_segment(seg, job_id="test-job")
            assert len(candidates) >= 1
            assert candidates[0].video_id == "dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# 11. FastAPI endpoints
# ---------------------------------------------------------------------------

class TestFastAPIEndpoints:

    @pytest.fixture
    def client(self):
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_health(self, client):
        async with client as c:
            resp = await c.get("/api/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_create_job(self, client):
        with patch("app.main.run_pipeline", new_callable=AsyncMock) as mock_pipe:
            mock_pipe.return_value = None
            async with client as c:
                resp = await c.post("/api/v1/jobs", json={
                    "script": SAMPLE_SCRIPT,
                    "editor_id": "test_editor",
                })
                assert resp.status_code == 200
                assert "job_id" in resp.json()

    @pytest.mark.asyncio
    async def test_nonexistent_job_404(self, client):
        async with client as c:
            resp = await c.get("/api/v1/jobs/nonexistent-id")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_agent_poll(self, client):
        async with client as c:
            resp = await c.post("/api/v1/agent/poll", json={"agent_id": "test"})
            assert resp.status_code == 200
            assert "tasks" in resp.json()

    @pytest.mark.asyncio
    async def test_agent_status(self, client):
        async with client as c:
            resp = await c.get("/api/v1/agent/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "pending_tasks" in data
            assert "agents_active" in data

    @pytest.mark.asyncio
    async def test_agent_result_unknown_task(self, client):
        async with client as c:
            resp = await c.post("/api/v1/agent/result", json={
                "task_id": "nonexistent",
                "status": "completed",
                "result": [],
            })
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 12. Progress tracking
# ---------------------------------------------------------------------------

class TestProgressTracking:

    def test_set_and_get(self):
        from app.background import _set_progress, get_job_progress, _progress
        _progress.clear()
        _set_progress("test-job", "searching", 50, "Searching...")
        p = get_job_progress("test-job")
        assert p["stage"] == "searching"
        assert p["percent_complete"] == 50
        _progress.clear()

    def test_activity_log(self):
        from app.background import _log_activity, get_job_progress, _progress
        _progress.clear()
        _progress["test-job"] = {"activity_log": []}
        _log_activity("test-job", "search", "Looking for videos")
        _log_activity("test-job", "check", "Found 5 videos")
        p = get_job_progress("test-job")
        assert len(p["activity_log"]) == 2
        _progress.clear()

    def test_activity_log_capped(self):
        from app.background import _log_activity, get_job_progress, _progress
        _progress.clear()
        _progress["test-job"] = {"activity_log": []}
        for i in range(120):
            _log_activity("test-job", "info", f"Entry {i}")
        p = get_job_progress("test-job")
        assert len(p["activity_log"]) == 100
        _progress.clear()
