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
        assert score == 0.3

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


# ---------------------------------------------------------------------------
# 13. Agent relay flow (critical end-to-end)
# ---------------------------------------------------------------------------

class TestAgentRelayFlow:
    """Tests the full agent task queue relay: create → poll → execute → submit → receive."""

    @pytest.mark.asyncio
    async def test_full_relay_cycle(self):
        """Simulates the browser agent: create task, poll it, submit result, receive it."""
        from app.utils import agent_queue

        task_id = await agent_queue.create_task("search", {
            "query": "Epstein island footage",
            "max_results": 5,
        })

        # Browser agent polls and claims the task
        tasks = await agent_queue.poll_tasks("browser-agent", max_tasks=10)
        assert len(tasks) >= 1
        claimed = next(t for t in tasks if t["task_id"] == task_id)
        assert claimed["task_type"] == "search"
        assert claimed["payload"]["query"] == "Epstein island footage"

        # Browser agent posts result (simulating companion response)
        companion_results = [
            {"video_id": "wBzUZWLqN4k", "title": "Drone Footage of Epstein Island", "duration_seconds": 356},
            {"video_id": "lLes27m1irE", "title": "Complete Epstein Island Drone", "duration_seconds": 13132},
        ]

        async def submit_soon():
            await asyncio.sleep(0.05)
            await agent_queue.submit_result(task_id, "completed", companion_results)

        asyncio.create_task(submit_soon())
        results = await agent_queue.wait_for_result(task_id, timeout=5)

        assert len(results) == 2
        assert results[0]["video_id"] == "wBzUZWLqN4k"
        assert results[1]["video_id"] == "lLes27m1irE"

    @pytest.mark.asyncio
    async def test_multiple_tasks_batched(self):
        """Multiple tasks created concurrently are all claimable in one poll."""
        from app.utils import agent_queue

        ids = []
        for q in ["query A", "query B", "query C"]:
            tid = await agent_queue.create_task("search", {"query": q, "max_results": 3})
            ids.append(tid)

        tasks = await agent_queue.poll_tasks("batch-agent", max_tasks=10)
        claimed_ids = {t["task_id"] for t in tasks}
        for tid in ids:
            assert tid in claimed_ids

        # Clean up
        for tid in ids:
            await agent_queue.submit_result(tid, "completed", [])

    @pytest.mark.asyncio
    async def test_channel_search_task_type(self):
        """Channel search tasks carry the right payload."""
        from app.utils import agent_queue

        task_id = await agent_queue.create_task("channel_search", {
            "channel_id": "UC_5jTJ1XNWcq9FOWX6Q7hCg",
            "query": "documentary footage",
            "max_results": 5,
        })

        tasks = await agent_queue.poll_tasks("ch-agent")
        claimed = next(t for t in tasks if t["task_id"] == task_id)
        assert claimed["task_type"] == "channel_search"
        assert claimed["payload"]["channel_id"] == "UC_5jTJ1XNWcq9FOWX6Q7hCg"

        await agent_queue.submit_result(task_id, "completed", [])

    @pytest.mark.asyncio
    async def test_video_details_task_type(self):
        """Video details tasks carry a list of video IDs."""
        from app.utils import agent_queue

        task_id = await agent_queue.create_task("video_details", {
            "video_ids": ["wBzUZWLqN4k", "lLes27m1irE"],
        })

        tasks = await agent_queue.poll_tasks("details-agent")
        claimed = next(t for t in tasks if t["task_id"] == task_id)
        assert claimed["task_type"] == "video_details"
        assert len(claimed["payload"]["video_ids"]) == 2

        await agent_queue.submit_result(task_id, "completed", [])

    @pytest.mark.asyncio
    async def test_failed_task_returns_empty(self):
        """When agent reports failure, wait_for_result returns empty list."""
        from app.utils import agent_queue

        task_id = await agent_queue.create_task("search", {"query": "fail test"})
        await agent_queue.poll_tasks("fail-agent")

        async def submit_failure():
            await asyncio.sleep(0.05)
            await agent_queue.submit_result(task_id, "failed", [])

        asyncio.create_task(submit_failure())
        results = await agent_queue.wait_for_result(task_id, timeout=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_task_timeout_returns_empty(self):
        """Unclaimed tasks timeout gracefully."""
        from app.utils import agent_queue

        task_id = await agent_queue.create_task("search", {"query": "timeout test"})
        # Nobody polls — task sits unclaimed
        results = await agent_queue.wait_for_result(task_id, timeout=0.2)
        assert results == []

    @pytest.mark.asyncio
    async def test_concurrent_segments_via_dispatchers(self):
        """Two segments searching concurrently via dispatchers both get results."""
        from app.services.searcher import _dispatch_search
        from app.utils.quota_tracker import QuotaTracker
        from app.utils import agent_queue

        mock_qt = QuotaTracker()
        mock_qt.mark_quota_exhausted()

        async def fake_agent_consumer():
            """Simulates the browser agent loop — poll and submit results."""
            for _ in range(20):
                await asyncio.sleep(0.05)
                tasks = await agent_queue.poll_tasks("test-consumer", max_tasks=10)
                for task in tasks:
                    fake_results = [{"video_id": f"vid_{task['payload']['query'][:5]}"}]
                    await agent_queue.submit_result(task["task_id"], "completed", fake_results)

        consumer = asyncio.create_task(fake_agent_consumer())

        with patch("app.services.searcher.get_quota_tracker", return_value=mock_qt):
            r1, r2 = await asyncio.gather(
                _dispatch_search("alpha query", max_results=3, backend="ytdlp_only"),
                _dispatch_search("beta query", max_results=3, backend="ytdlp_only"),
            )

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

        assert len(r1) >= 1
        assert len(r2) >= 1
        assert r1[0]["video_id"] == "vid_alpha"
        assert r2[0]["video_id"] == "vid_beta "


# ---------------------------------------------------------------------------
# 14. Cancel job flow
# ---------------------------------------------------------------------------

class TestCancelJobFlow:

    @pytest.fixture
    def client(self):
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_404(self, client):
        async with client as c:
            resp = await c.post("/api/v1/jobs/nonexistent-uuid/cancel")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_running_job(self, client):
        """Create a job with a slow pipeline, cancel it, verify it stops."""
        from app.main import _running_tasks

        async def slow_pipeline(*args, **kwargs):
            await asyncio.sleep(30)

        with patch("app.main.run_pipeline", side_effect=slow_pipeline):
            async with client as c:
                resp = await c.post("/api/v1/jobs", json={
                    "script": SAMPLE_SCRIPT,
                    "editor_id": "cancel_test",
                })
                assert resp.status_code == 200
                job_id = resp.json()["job_id"]

                # Task should be running
                assert job_id in _running_tasks

                # Cancel it
                cancel_resp = await c.post(f"/api/v1/jobs/{job_id}/cancel")
                assert cancel_resp.status_code == 200
                data = cancel_resp.json()
                assert data["cancelled"] is True

                # Give the task a moment to be cancelled
                await asyncio.sleep(0.2)
                assert job_id not in _running_tasks

    @pytest.mark.asyncio
    async def test_cancel_finished_job(self, client):
        """Cancelling an already-finished job returns cancelled=false."""
        with patch("app.main.run_pipeline", new_callable=AsyncMock) as mock_pipe, \
             patch("app.main.get_storage") as mock_storage_fn:
            mock_pipe.return_value = None

            mock_storage = AsyncMock()
            mock_job = MagicMock()
            mock_job.status.value = "complete"
            mock_storage.get_job.return_value = mock_job
            mock_storage_fn.return_value = mock_storage

            async with client as c:
                resp = await c.post("/api/v1/jobs", json={
                    "script": SAMPLE_SCRIPT,
                    "editor_id": "done_test",
                })
                job_id = resp.json()["job_id"]

                # Wait for mock pipeline to finish
                await asyncio.sleep(0.2)

                cancel_resp = await c.post(f"/api/v1/jobs/{job_id}/cancel")
                assert cancel_resp.status_code == 200
                data = cancel_resp.json()
                assert data["cancelled"] is False


# ---------------------------------------------------------------------------
# 15. JobStatus enum includes cancelled
# ---------------------------------------------------------------------------

class TestJobStatusEnum:

    def test_cancelled_status_exists(self):
        from app.models.schemas import JobStatus
        assert JobStatus.CANCELLED.value == "cancelled"

    def test_all_statuses(self):
        from app.models.schemas import JobStatus
        expected = {"pending", "processing", "complete", "partial", "failed", "cancelled"}
        actual = {s.value for s in JobStatus}
        assert actual == expected


# ---------------------------------------------------------------------------
# 16. Stale job cleanup on startup
# ---------------------------------------------------------------------------

class TestStaleJobCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_marks_processing_as_failed(self):
        from app.main import _cleanup_stale_jobs
        from app.models.schemas import JobStatus, JobSummary

        stale_job = JobSummary(
            job_id="stale-123", status=JobStatus.PROCESSING,
            created_at="2026-04-01T00:00:00Z", segment_count=5, result_count=0,
        )
        complete_job = JobSummary(
            job_id="done-456", status=JobStatus.COMPLETE,
            created_at="2026-04-01T00:00:00Z", segment_count=10, result_count=20,
        )

        mock_storage = AsyncMock()
        mock_storage.list_jobs.return_value = [stale_job, complete_job]

        with patch("app.main.get_storage", return_value=mock_storage):
            await _cleanup_stale_jobs()

        mock_storage.update_job_status.assert_called_once()
        call_args = mock_storage.update_job_status.call_args
        assert call_args[0][0] == "stale-123"
        assert call_args[0][1] == JobStatus.FAILED


# ---------------------------------------------------------------------------
# 17. Transcriber — new youtube-transcript-api v1.2 compatibility
# ---------------------------------------------------------------------------

class TestTranscriberNewAPI:
    """Verify transcriber works with youtube-transcript-api >= 1.2.0."""

    @pytest.mark.asyncio
    async def test_fetch_transcript_uses_new_api(self):
        """The transcriber must use YouTubeTranscriptApi().fetch() not .get_transcript()."""
        from app.services.transcriber import TranscriberService

        mock_fetched = MagicMock()
        mock_fetched.to_raw_data.return_value = [
            {"text": "Hello world", "start": 0.0, "duration": 2.0},
            {"text": "Second line", "start": 2.0, "duration": 3.0},
        ]

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage):
            mock_api.fetch.return_value = mock_fetched

            ts = TranscriberService()
            result = await ts.get_transcript("test_vid")

            mock_api.fetch.assert_called_once_with("test_vid", languages=["en"])
            assert result.transcript_text is not None
            assert "Hello world" in result.transcript_text

    @pytest.mark.asyncio
    async def test_list_transcripts_uses_new_api(self):
        """Fallback path: list() then find_manually_created_transcript() uses new API."""
        from app.services.transcriber import TranscriberService

        mock_fetched = MagicMock()
        mock_fetched.to_raw_data.return_value = [
            {"text": "Manual caption", "start": 10.0, "duration": 5.0},
        ]

        mock_manual = MagicMock()
        mock_manual.fetch.return_value = mock_fetched

        mock_transcript_list = MagicMock()
        mock_transcript_list.find_manually_created_transcript.return_value = mock_manual

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage):
            mock_api.fetch.side_effect = Exception("No English transcript")
            mock_api.list.return_value = mock_transcript_list

            ts = TranscriberService()
            result = await ts.get_transcript("test_vid_2")

            mock_api.list.assert_called_once_with("test_vid_2")
            assert result.transcript_text is not None
            assert "Manual caption" in result.transcript_text


# ---------------------------------------------------------------------------
# 18. Matcher — timestamp extraction produces valid clips
# ---------------------------------------------------------------------------

class TestMatcherClipExtraction:

    @pytest.mark.asyncio
    async def test_matcher_returns_valid_timestamps(self):
        """Matcher should return a non-zero confidence result with valid timestamps."""
        from app.services.matcher import MatcherService
        from app.models.schemas import Segment

        gpt_response = {
            "start_time_seconds": 120,
            "end_time_seconds": 165,
            "excerpt": "This is the relevant section about the topic...",
            "confidence_score": 0.85,
            "relevance_note": "Covers the main topic directly",
            "the_hook": "Dramatic aerial footage of the location",
        }

        segment = Segment(
            segment_id="seg_001",
            title="Test Segment",
            summary="A test segment about history",
            visual_need="historical footage",
            emotional_tone="dramatic",
            key_terms=["history", "documentary"],
            search_queries=["history documentary"],
        )

        with patch.object(MatcherService, "_call_model", return_value=gpt_response):
            matcher = MatcherService()
            result = await matcher.find_timestamp(
                "0:00 This is a test transcript\n2:00 This is the relevant section about the topic",
                segment,
                {"video_duration_seconds": 3600, "video_title": "Test", "view_count": 100000, "transcript_source": "youtube_captions"},
            )

        assert result.confidence_score == 0.85
        assert result.start_time_seconds == 120
        assert result.end_time_seconds == 165
        assert result.the_hook is not None

    @pytest.mark.asyncio
    async def test_matcher_validates_timestamps(self):
        """Timestamps past video duration should be marked invalid."""
        from app.services.matcher import MatcherService
        from app.models.schemas import MatchResult

        matcher = MatcherService()
        match = MatchResult(
            start_time_seconds=4000,
            end_time_seconds=4060,
            confidence_score=0.9,
            context_match_valid=True,
        )
        result = matcher.validate_context_match(match, video_duration_seconds=3600)
        assert result.context_match_valid is False


# ---------------------------------------------------------------------------
# 19. Ranker — full pipeline from candidate to ranked result
# ---------------------------------------------------------------------------

class TestRankerPipeline:

    def test_ranker_produces_clip_url(self):
        """Ranker should produce a RankedResult with a clip_url containing timestamp."""
        from app.services.ranker import RankerService
        from app.models.schemas import CandidateVideo, MatchResult, Segment, TranscriptSource

        candidate = CandidateVideo(
            video_id="test123",
            video_url="https://www.youtube.com/watch?v=test123",
            video_title="Test Documentary",
            channel_name="TestChannel",
            channel_id="UC_test",
            channel_subscribers=500000,
            thumbnail_url="https://img.youtube.com/vi/test123/mqdefault.jpg",
            video_duration_seconds=3600,
            published_at="2025-01-01T00:00:00Z",
            view_count=1000000,
        )
        match = MatchResult(
            start_time_seconds=120,
            end_time_seconds=165,
            transcript_excerpt="history documentary footage of the era",
            confidence_score=0.85,
            relevance_note="Relevant",
            the_hook="Dramatic footage",
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )
        segment = Segment(
            segment_id="seg_001",
            title="Historical Era",
            summary="Documentary about historical events",
            visual_need="archival footage",
            emotional_tone="dramatic",
            key_terms=["history", "documentary", "era"],
            search_queries=["history documentary"],
        )

        ranker = RankerService()
        results = ranker.rank_and_filter([(candidate, match)], segment)

        assert len(results) >= 1
        r = results[0]
        assert r.video_id == "test123"
        assert r.clip_url is not None
        assert "t=120" in r.clip_url
        assert r.relevance_score > 0
        assert r.confidence_score == 0.85

    def test_ranker_filters_low_confidence(self):
        """Very low confidence matches should be filtered when better options exist."""
        from app.services.ranker import RankerService
        from app.models.schemas import CandidateVideo, MatchResult, Segment, TranscriptSource

        def make_candidate(vid_id, views=100000):
            return CandidateVideo(
                video_id=vid_id,
                video_url=f"https://www.youtube.com/watch?v={vid_id}",
                video_title="Test",
                channel_name="Channel",
                channel_id="UC_test",
                thumbnail_url="",
                video_duration_seconds=3600,
                published_at="2025-06-01T00:00:00Z",
                view_count=views,
            )

        good_match = MatchResult(
            start_time_seconds=120, end_time_seconds=180,
            transcript_excerpt="history documentary footage",
            confidence_score=0.9,
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )
        bad_match = MatchResult(
            start_time_seconds=10, end_time_seconds=40,
            transcript_excerpt="unrelated content",
            confidence_score=0.1,
            source_flag=TranscriptSource.YOUTUBE_AUTO,
            context_match_valid=True,
        )

        segment = Segment(
            segment_id="seg_001", title="Test",
            summary="Test", visual_need="Test", emotional_tone="neutral",
            key_terms=["history"], search_queries=["test"],
        )

        ranker = RankerService()
        results = ranker.rank_and_filter(
            [(make_candidate("good"), good_match), (make_candidate("bad"), bad_match)],
            segment,
            settings={"top_results_per_segment": 2, "confidence_threshold": 0.4},
        )
        assert len(results) >= 1
        assert results[0].video_id == "good"


# ---------------------------------------------------------------------------
# 19b. Transcriber — agent fallback when YouTube blocks EC2 IP
# ---------------------------------------------------------------------------

class TestTranscriberAgentFallback:

    @pytest.mark.asyncio
    async def test_falls_back_to_agent_on_request_blocked(self):
        """When direct transcript fetch fails (e.g., YouTube blocks EC2 IP), the
        transcriber should fall back to the local companion agent."""
        from app.services.transcriber import TranscriberService
        from app.models.schemas import TranscriptSource

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        agent_result = [{"video_id": "vid123", "transcript": "0:00 Test transcript\n0:05 More text", "source": "youtube_captions"}]

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-abc"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=agent_result):

            mock_api.fetch.side_effect = Exception("RequestBlocked: YouTube is blocking requests from your IP")
            mock_api.list.side_effect = Exception("RequestBlocked")

            ts = TranscriberService()
            result = await ts.get_transcript("vid123", video_duration_seconds=3600)

            assert result.transcript_text is not None
            assert "Test transcript" in result.transcript_text
            assert result.transcript_source in (TranscriptSource.YOUTUBE_MANUAL, TranscriptSource.YOUTUBE_AUTO)

    @pytest.mark.asyncio
    async def test_agent_fallback_no_transcript_returns_none(self):
        """If the agent also cannot find a transcript, return NONE source."""
        from app.services.transcriber import TranscriberService
        from app.models.schemas import TranscriptSource

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        agent_result = [{"video_id": "vid456", "transcript": None, "source": "no_transcript"}]

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-def"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=agent_result):

            mock_api.fetch.side_effect = Exception("RequestBlocked")
            mock_api.list.side_effect = Exception("RequestBlocked")

            ts = TranscriberService()
            result = await ts.get_transcript("vid456", video_duration_seconds=3600)

            assert result.transcript_text is None
            assert result.transcript_source == TranscriptSource.NONE


# ---------------------------------------------------------------------------
# 19c. Transcriber — Whisper fallback via companion agent
# ---------------------------------------------------------------------------

class TestTranscriberWhisperFallback:

    @pytest.mark.asyncio
    async def test_whisper_fallback_when_no_captions(self):
        """When both direct and agent transcript fetch fail, Whisper should be attempted."""
        from app.services.transcriber import TranscriberService
        from app.models.schemas import TranscriptSource

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        agent_no_transcript = [{"video_id": "vid789", "transcript": None, "source": "no_transcript"}]
        whisper_result = [{"video_id": "vid789", "transcript": "0:00 Whisper transcription\n0:05 More text", "source": "whisper_transcription"}]

        call_count = {"n": 0}
        async def mock_create_task(task_type, payload):
            call_count["n"] += 1
            return f"task-{call_count['n']}"

        async def mock_wait_for_result(task_id, timeout=60):
            if task_id == "task-1":
                return agent_no_transcript
            elif task_id == "task-2":
                return whisper_result
            return []

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", side_effect=mock_create_task), \
             patch("app.utils.agent_queue.wait_for_result", side_effect=mock_wait_for_result):

            mock_api.fetch.side_effect = Exception("RequestBlocked")
            mock_api.list.side_effect = Exception("RequestBlocked")

            ts = TranscriberService()
            result = await ts.get_transcript("vid789", video_duration_seconds=600)

            assert result.transcript_text is not None
            assert "Whisper transcription" in result.transcript_text
            assert result.transcript_source == TranscriptSource.WHISPER

    @pytest.mark.asyncio
    async def test_whisper_skipped_for_long_videos(self):
        """Whisper should be skipped if video duration exceeds max."""
        from app.services.transcriber import TranscriberService
        from app.models.schemas import TranscriptSource

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        agent_no_transcript = [{"video_id": "vid_long", "transcript": None, "source": "no_transcript"}]

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-x"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=agent_no_transcript):

            mock_api.fetch.side_effect = Exception("RequestBlocked")
            mock_api.list.side_effect = Exception("RequestBlocked")

            ts = TranscriberService()
            # 7200s = 120 min, exceeds default 60 min max
            result = await ts.get_transcript("vid_long", video_duration_seconds=7200)

            assert result.transcript_source == TranscriptSource.NONE

    @pytest.mark.asyncio
    async def test_whisper_result_is_cached(self):
        """Whisper transcription results should be stored via storage.store_transcript."""
        from app.services.transcriber import TranscriberService
        from app.models.schemas import TranscriptSource

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        agent_no_transcript = [{"video_id": "vid_cache", "transcript": None, "source": "no_transcript"}]
        whisper_result = [{"video_id": "vid_cache", "transcript": "0:00 Cached text", "source": "whisper_transcription"}]

        call_count = {"n": 0}
        async def mock_create_task(task_type, payload):
            call_count["n"] += 1
            return f"task-{call_count['n']}"

        async def mock_wait_for_result(task_id, timeout=60):
            if task_id == "task-1":
                return agent_no_transcript
            elif task_id == "task-2":
                return whisper_result
            return []

        with patch("app.services.transcriber._ytt_api") as mock_api, \
             patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", side_effect=mock_create_task), \
             patch("app.utils.agent_queue.wait_for_result", side_effect=mock_wait_for_result):

            mock_api.fetch.side_effect = Exception("RequestBlocked")
            mock_api.list.side_effect = Exception("RequestBlocked")

            ts = TranscriberService()
            await ts.get_transcript("vid_cache", video_duration_seconds=600)

            # store_transcript should have been called with Whisper source
            whisper_store_calls = [
                c for c in mock_storage.store_transcript.call_args_list
                if c.kwargs.get("source") == TranscriptSource.WHISPER
                or (len(c.args) > 2 and c.args[2] == TranscriptSource.WHISPER)
            ]
            assert len(whisper_store_calls) >= 1


# ---------------------------------------------------------------------------
# 19d. Gemini expansion toggle
# ---------------------------------------------------------------------------

class TestGeminiExpansionToggle:

    def test_default_gemini_expansion_off(self):
        from app.config import DEFAULTS
        assert DEFAULTS["enable_gemini_expansion"] is False

    def test_searcher_skips_gemini_when_off(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={"enable_gemini_expansion": False})
        assert svc._get("enable_gemini_expansion") is False

    def test_searcher_enables_gemini_when_on(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={"enable_gemini_expansion": True})
        assert svc._get("enable_gemini_expansion") is True

    def test_job_create_request_accepts_gemini_flag(self):
        from app.models.schemas import JobCreateRequest
        req = JobCreateRequest(script="x" * 200, enable_gemini_expansion=True)
        assert req.enable_gemini_expansion is True

    def test_job_create_request_defaults_gemini_off(self):
        from app.models.schemas import JobCreateRequest
        req = JobCreateRequest(script="x" * 200)
        assert req.enable_gemini_expansion is False


# ---------------------------------------------------------------------------
# 20. Full pipeline mock — search -> match -> rank -> clip
# ---------------------------------------------------------------------------

class TestFullPipelineMock:
    """Test the complete pipeline with all stages mocked to verify data flows correctly."""

    @pytest.mark.asyncio
    async def test_pipeline_produces_clips(self):
        """A full pipeline run with mocked services should produce > 0 clips."""
        from app.models.schemas import (
            CandidateVideo, JobStatus, MatchResult, Segment, TranscriptSource, Transcript,
        )
        from app.services.ranker import RankerService

        segments = [Segment(
            segment_id="seg_001",
            title="Test Historical Segment",
            summary="A documentary about historical events.",
            visual_need="archival footage of historical buildings",
            emotional_tone="dramatic",
            key_terms=["history", "buildings", "archival"],
            search_queries=["historical buildings documentary"],
        )]

        candidate = CandidateVideo(
            video_id="vid_abc",
            video_url="https://www.youtube.com/watch?v=vid_abc",
            video_title="Historical Buildings Documentary",
            channel_name="HistoryChannel",
            channel_id="UC_hist",
            channel_subscribers=250000,
            thumbnail_url="https://img.youtube.com/vi/vid_abc/mqdefault.jpg",
            video_duration_seconds=2400,
            published_at="2025-03-01T00:00:00Z",
            view_count=500000,
        )

        transcript = Transcript(
            video_id="vid_abc",
            transcript_text="0:00 Welcome to the documentary\n2:00 Here we see the historical buildings from the era\n4:00 The archival footage shows remarkable detail",
            transcript_source=TranscriptSource.YOUTUBE_MANUAL,
            video_duration_seconds=2400,
        )

        match_result = MatchResult(
            start_time_seconds=120,
            end_time_seconds=180,
            transcript_excerpt="Here we see the historical buildings from the era",
            confidence_score=0.88,
            relevance_note="Direct match to archival footage need",
            the_hook="Stunning archival footage of historical buildings",
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )

        mock_searcher = AsyncMock()
        mock_searcher.search_batch.return_value = {"seg_001": [candidate]}

        mock_transcriber = AsyncMock()
        mock_transcriber.get_transcript.return_value = transcript

        mock_matcher = AsyncMock()
        mock_matcher.find_timestamp.return_value = match_result
        mock_matcher.validate_context_match = MagicMock(return_value=match_result)

        ranker = RankerService()

        candidates_by_seg = await mock_searcher.search_batch(segments, job_id="test-job")
        assert "seg_001" in candidates_by_seg
        assert len(candidates_by_seg["seg_001"]) == 1

        cands = candidates_by_seg["seg_001"]
        matched = []
        for cand in cands:
            t = await mock_transcriber.get_transcript(cand.video_id)
            assert t.transcript_text is not None
            m = await mock_matcher.find_timestamp(t.transcript_text, segments[0], {})
            m = mock_matcher.validate_context_match(m, cand.video_duration_seconds)
            matched.append((cand, m))

        assert len(matched) == 1
        assert matched[0][1].confidence_score > 0

        ranked = ranker.rank_and_filter(matched, segments[0])
        assert len(ranked) >= 1

        r = ranked[0]
        assert r.video_id == "vid_abc"
        assert "t=120" in r.clip_url
        assert r.confidence_score == 0.88
        assert r.relevance_score > 0

    @pytest.mark.asyncio
    async def test_pipeline_no_transcript_still_ranks(self):
        """Even without transcript, the ranker should produce a fallback result."""
        from app.models.schemas import (
            CandidateVideo, MatchResult, Segment, TranscriptSource, Transcript,
        )
        from app.services.ranker import RankerService

        segment = Segment(
            segment_id="seg_002",
            title="Test Segment No Transcript",
            summary="Testing fallback when no transcript available.",
            visual_need="any footage",
            emotional_tone="neutral",
            key_terms=["test"],
            search_queries=["test search"],
        )
        candidate = CandidateVideo(
            video_id="vid_no_tx",
            video_url="https://www.youtube.com/watch?v=vid_no_tx",
            video_title="Video Without Captions",
            channel_name="SomeChannel",
            channel_id="UC_some",
            thumbnail_url="",
            video_duration_seconds=1800,
            published_at="2025-01-01T00:00:00Z",
            view_count=50000,
        )
        match = MatchResult(
            confidence_score=0.0,
            source_flag=TranscriptSource.NONE,
        )

        ranker = RankerService()
        ranked = ranker.rank_and_filter([(candidate, match)], segment)
        assert len(ranked) >= 1
        assert ranked[0].video_id == "vid_no_tx"
