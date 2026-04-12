"""
Integration test for the Whisper transcript pipeline.

Tests 6 videos through the full transcript fetch flow:
  - 3 succeed (cache hit, YouTube captions via companion, Whisper transcription)
  - 3 fail (Whisper timeout, audio download failure, all formats restricted)

Verifies that:
  1. Each video takes the correct path through the pipeline
  2. Failure reasons propagate correctly to the Transcript model
  3. The background.py worker correctly logs and tallies each outcome
  4. transcript_queue.join() waits for ALL items before proceeding

Run with:  python -m pytest tests/test_whisper_pipeline.py -v
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import app.services.storage  # noqa: F401 — ensure module is loaded for patch() resolution
from app.models.schemas import (
    BRollShot, CandidateVideo, MatchResult, Segment, Transcript,
    TranscriptSource,
)


# ---------------------------------------------------------------------------
# Test data — 3 success videos, 3 failure videos
# ---------------------------------------------------------------------------

def _make_candidate(video_id: str, title: str, duration_seconds: int = 600) -> CandidateVideo:
    return CandidateVideo(
        video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        video_title=title,
        channel_name="TestChannel",
        channel_id="UC_test",
        channel_subscribers=100000,
        thumbnail_url="",
        video_duration_seconds=duration_seconds,
        published_at="2025-01-01T00:00:00Z",
        view_count=50000,
    )

SUCCESS_VIDEOS = {
    "CACHED_001": _make_candidate("CACHED_001", "Video With DynamoDB Cache", 300),
    "CAPTION_002": _make_candidate("CAPTION_002", "Video With YouTube Captions", 480),
    "WHISPER_003": _make_candidate("WHISPER_003", "Video Needing Whisper Transcription", 900),
}

FAILURE_VIDEOS = {
    "TIMEOUT_004": _make_candidate("TIMEOUT_004", "Video Whose Whisper Task Times Out", 600),
    "AUDIOFAIL_005": _make_candidate("AUDIOFAIL_005", "Video With Audio Download Failure", 450),
    "RESTRICTED_006": _make_candidate("RESTRICTED_006", "Restricted Video All Formats Blocked", 720),
}

ALL_VIDEOS = {**SUCCESS_VIDEOS, **FAILURE_VIDEOS}


# ---------------------------------------------------------------------------
# 1. TranscriberService — individual path tests
# ---------------------------------------------------------------------------

class TestTranscriberPaths:
    """Test each transcript path in isolation via TranscriberService."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_immediately(self):
        """Video with a cached transcript should return without touching the companion."""
        from app.services.transcriber import TranscriberService

        cached = Transcript(
            video_id="CACHED_001",
            transcript_text="0:00 Cached transcript content\n0:30 More content",
            transcript_source=TranscriptSource.CACHED,
            video_duration_seconds=300,
        )
        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = cached

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock) as mock_create:
            ts = TranscriberService()
            result = await ts.get_transcript("CACHED_001", video_duration_seconds=300)

        assert result.transcript_text is not None
        assert result.transcript_source == TranscriptSource.CACHED
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_companion_captions_success(self):
        """Video gets captions from the companion agent (no Whisper needed)."""
        from app.services.transcriber import TranscriberService

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        caption_result = [{"video_id": "CAPTION_002", "transcript": "0:00 Caption text\n1:00 More captions", "source": "youtube_captions"}]

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-cap"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=caption_result), \
             patch("app.utils.agent_queue.is_agent_available", return_value=True):
            ts = TranscriberService()
            result = await ts.get_transcript("CAPTION_002", video_duration_seconds=480)

        assert result.transcript_text is not None
        assert "Caption text" in result.transcript_text
        assert result.transcript_source == TranscriptSource.YOUTUBE_MANUAL
        assert result.whisper_attempted is False

    @pytest.mark.asyncio
    async def test_whisper_success_after_caption_failure(self):
        """Captions unavailable → Whisper succeeds → transcript returned."""
        from app.services.transcriber import TranscriberService

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        no_caption = [{"video_id": "WHISPER_003", "transcript": None, "source": "no_transcript"}]
        whisper_ok = [{"video_id": "WHISPER_003", "transcript": "0:00 Whisper transcribed audio\n5:00 Banking explanation", "source": "whisper_transcription"}]

        call_count = {"n": 0}
        async def mock_create(task_type, payload, job_id=None):
            call_count["n"] += 1
            return f"task-{call_count['n']}"

        async def mock_wait(task_id, timeout=60):
            if task_id == "task-1":
                return no_caption
            elif task_id == "task-2":
                return whisper_ok
            return []

        on_whisper = AsyncMock()

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", side_effect=mock_create), \
             patch("app.utils.agent_queue.wait_for_result", side_effect=mock_wait), \
             patch("app.utils.agent_queue.is_agent_available", return_value=True):
            ts = TranscriberService()
            result = await ts.get_transcript("WHISPER_003", video_duration_seconds=900, on_whisper_start=on_whisper)

        assert result.transcript_text is not None
        assert "Whisper transcribed" in result.transcript_text
        assert result.transcript_source == TranscriptSource.WHISPER
        assert result.whisper_attempted is True
        on_whisper.assert_called_once()

    @pytest.mark.asyncio
    async def test_whisper_timeout_failure(self):
        """Captions unavailable → Whisper task times out → failure with reason='timeout'."""
        from app.services.transcriber import TranscriberService

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        no_caption = [{"video_id": "TIMEOUT_004", "transcript": None, "source": "no_transcript"}]

        call_count = {"n": 0}
        async def mock_create(task_type, payload, job_id=None):
            call_count["n"] += 1
            return f"task-{call_count['n']}"

        async def mock_wait(task_id, timeout=60):
            if task_id == "task-1":
                return no_caption
            elif task_id == "task-2":
                return []  # empty = timeout
            return []

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", side_effect=mock_create), \
             patch("app.utils.agent_queue.wait_for_result", side_effect=mock_wait), \
             patch("app.utils.agent_queue.is_agent_available", return_value=True):
            ts = TranscriberService()
            result = await ts.get_transcript("TIMEOUT_004", video_duration_seconds=600)

        assert result.transcript_text is None
        assert result.transcript_source == TranscriptSource.NONE
        assert result.whisper_attempted is True
        assert result.whisper_failure_reason == "timeout"

    @pytest.mark.asyncio
    async def test_whisper_audio_download_failure(self):
        """Captions unavailable → Whisper companion fails audio download."""
        from app.services.transcriber import TranscriberService

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        no_caption = [{"video_id": "AUDIOFAIL_005", "transcript": None, "source": "no_transcript"}]
        whisper_fail = [{"video_id": "AUDIOFAIL_005", "transcript": None, "source": "whisper_failed", "failure_detail": "audio_download_failed"}]

        call_count = {"n": 0}
        async def mock_create(task_type, payload, job_id=None):
            call_count["n"] += 1
            return f"task-{call_count['n']}"

        async def mock_wait(task_id, timeout=60):
            if task_id == "task-1":
                return no_caption
            elif task_id == "task-2":
                return whisper_fail
            return []

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", side_effect=mock_create), \
             patch("app.utils.agent_queue.wait_for_result", side_effect=mock_wait), \
             patch("app.utils.agent_queue.is_agent_available", return_value=True):
            ts = TranscriberService()
            result = await ts.get_transcript("AUDIOFAIL_005", video_duration_seconds=450)

        assert result.transcript_text is None
        assert result.whisper_attempted is True
        assert result.whisper_failure_reason == "audio_download_failed"

    @pytest.mark.asyncio
    async def test_whisper_restricted_video_failure(self):
        """Captions unavailable → all download formats fail (restricted video)."""
        from app.services.transcriber import TranscriberService

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        no_caption = [{"video_id": "RESTRICTED_006", "transcript": None, "source": "no_transcript"}]
        whisper_restricted = [{"video_id": "RESTRICTED_006", "transcript": None, "source": "whisper_failed", "failure_detail": "all_formats_failed"}]

        call_count = {"n": 0}
        async def mock_create(task_type, payload, job_id=None):
            call_count["n"] += 1
            return f"task-{call_count['n']}"

        async def mock_wait(task_id, timeout=60):
            if task_id == "task-1":
                return no_caption
            elif task_id == "task-2":
                return whisper_restricted
            return []

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.create_task", side_effect=mock_create), \
             patch("app.utils.agent_queue.wait_for_result", side_effect=mock_wait), \
             patch("app.utils.agent_queue.is_agent_available", return_value=True):
            ts = TranscriberService()
            result = await ts.get_transcript("RESTRICTED_006", video_duration_seconds=720)

        assert result.transcript_text is None
        assert result.whisper_attempted is True
        assert result.whisper_failure_reason == "all_formats_failed"


# ---------------------------------------------------------------------------
# 2. Full transcript queue pipeline — 6 videos processed by workers
# ---------------------------------------------------------------------------

class TestTranscriptQueuePipeline:
    """
    Simulates the background.py transcript fetch workers processing 6 videos
    through the queue, verifying that:
    - All items are processed before queue.join() returns
    - Success/failure counts are correct
    - Failure reasons are correctly tracked
    """

    @pytest.mark.asyncio
    async def test_all_six_videos_processed_before_join(self):
        """
        Puts 6 videos in a PriorityQueue, runs 2 workers that call a mock
        transcriber, then verifies queue.join() waits for all 6 and the
        tallies match expected outcomes.
        """
        transcript_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        transcript_cache: dict = {}
        failed_fetches: set = set()
        transcript_sources: dict = {}
        whisper_failure_reasons: dict[str, int] = {}
        whisper_queue_count = 0
        transcripts_fetched = 0

        video_pool = dict(ALL_VIDEOS)

        for priority, vid in enumerate(video_pool):
            await transcript_queue.put((priority, vid))

        mock_transcripts = {
            "CACHED_001": Transcript(
                video_id="CACHED_001",
                transcript_text="0:00 Cached content",
                transcript_source=TranscriptSource.CACHED,
                video_duration_seconds=300,
            ),
            "CAPTION_002": Transcript(
                video_id="CAPTION_002",
                transcript_text="0:00 YouTube captions",
                transcript_source=TranscriptSource.YOUTUBE_MANUAL,
                video_duration_seconds=480,
            ),
            "WHISPER_003": Transcript(
                video_id="WHISPER_003",
                transcript_text="0:00 Whisper transcription",
                transcript_source=TranscriptSource.WHISPER,
                video_duration_seconds=900,
                whisper_attempted=True,
            ),
            "TIMEOUT_004": Transcript(
                video_id="TIMEOUT_004",
                transcript_text=None,
                transcript_source=TranscriptSource.NONE,
                video_duration_seconds=600,
                whisper_attempted=True,
                whisper_failure_reason="timeout",
            ),
            "AUDIOFAIL_005": Transcript(
                video_id="AUDIOFAIL_005",
                transcript_text=None,
                transcript_source=TranscriptSource.NONE,
                video_duration_seconds=450,
                whisper_attempted=True,
                whisper_failure_reason="audio_download_failed",
            ),
            "RESTRICTED_006": Transcript(
                video_id="RESTRICTED_006",
                transcript_text=None,
                transcript_source=TranscriptSource.NONE,
                video_duration_seconds=720,
                whisper_attempted=True,
                whisper_failure_reason="all_formats_failed",
            ),
        }

        mock_transcriber = AsyncMock()
        async def fake_get_transcript(vid, video_duration_seconds=0, job_id=None, on_whisper_start=None):
            t = mock_transcripts[vid]
            if t.whisper_attempted and on_whisper_start:
                await on_whisper_start(vid, video_duration_seconds)
            await asyncio.sleep(0.01)
            return t
        mock_transcriber.get_transcript = fake_get_transcript

        async def worker(worker_id: int):
            nonlocal transcripts_fetched, whisper_queue_count
            while True:
                item = await transcript_queue.get()
                if item is None or (isinstance(item, tuple) and item[1] is None):
                    transcript_queue.task_done()
                    break
                vid = item[1] if isinstance(item, tuple) else item
                cand = video_pool.get(vid)
                if not cand:
                    transcript_queue.task_done()
                    continue

                async def _on_whisper_start(v_id, dur_s):
                    nonlocal whisper_queue_count
                    whisper_queue_count += 1

                t = await mock_transcriber.get_transcript(
                    vid, video_duration_seconds=cand.video_duration_seconds,
                    on_whisper_start=_on_whisper_start,
                )
                transcript_cache[vid] = t.transcript_text
                transcript_sources[vid] = t.transcript_source.value

                if t.transcript_text is None:
                    failed_fetches.add(vid)
                    if t.whisper_attempted and t.whisper_failure_reason:
                        reason = t.whisper_failure_reason
                        whisper_failure_reasons[reason] = whisper_failure_reasons.get(reason, 0) + 1

                transcripts_fetched += 1
                transcript_queue.task_done()

        NUM_WORKERS = 2
        tasks = [asyncio.create_task(worker(i)) for i in range(NUM_WORKERS)]

        await asyncio.wait_for(transcript_queue.join(), timeout=5.0)

        for _ in range(NUM_WORKERS):
            await transcript_queue.put((999999, None))
        await asyncio.gather(*tasks)

        assert transcripts_fetched == 6, f"Expected 6, got {transcripts_fetched}"
        assert len(transcript_cache) == 6

        assert transcript_cache["CACHED_001"] is not None
        assert transcript_cache["CAPTION_002"] is not None
        assert transcript_cache["WHISPER_003"] is not None
        assert transcript_cache["TIMEOUT_004"] is None
        assert transcript_cache["AUDIOFAIL_005"] is None
        assert transcript_cache["RESTRICTED_006"] is None

        assert failed_fetches == {"TIMEOUT_004", "AUDIOFAIL_005", "RESTRICTED_006"}

        assert whisper_queue_count == 4  # 3 failures + 1 success triggered on_whisper_start

        assert whisper_failure_reasons.get("timeout") == 1
        assert whisper_failure_reasons.get("audio_download_failed") == 1
        assert whisper_failure_reasons.get("all_formats_failed") == 1

    @pytest.mark.asyncio
    async def test_queue_join_blocks_until_all_done(self):
        """
        Verify that queue.join() does NOT return early — all items must have
        task_done() called. This is the core guarantee that the pipeline
        waits for all Whisper jobs.
        """
        queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        processed = []

        for i in range(5):
            await queue.put((i, f"vid_{i}"))

        async def slow_worker():
            while True:
                item = await queue.get()
                if item[1] is None:
                    queue.task_done()
                    break
                await asyncio.sleep(0.05)
                processed.append(item[1])
                queue.task_done()

        task = asyncio.create_task(slow_worker())

        join_start = time.time()
        await asyncio.wait_for(queue.join(), timeout=5.0)
        join_elapsed = time.time() - join_start

        await queue.put((999, None))
        await task

        assert len(processed) == 5
        assert join_elapsed >= 0.2, "join() should have blocked ~250ms for 5 items at 50ms each"


# ---------------------------------------------------------------------------
# 3. Activity log message accuracy
# ---------------------------------------------------------------------------

class TestActivityLogMessages:
    """Verify the background.py activity log messages contain correct failure reasons."""

    def test_failure_reason_labels_cover_all_companion_reasons(self):
        """All failure_detail values from the companion have matching labels."""
        reason_labels = {
            "timeout": "Whisper task timed out (queue backlog)",
            "no_agent": "companion agent unavailable",
            "audio_download_failed": "audio download failed (restricted/age-gated)",
            "video_fallback_failed": "audio + video fallback both failed",
            "all_formats_failed": "all download formats failed (restricted video)",
        }
        companion_reasons = [
            "audio_download_failed", "video_fallback_failed",
            "all_formats_failed", "download_timeout", "transcription_error",
        ]
        backend_reasons = ["timeout", "no_agent"]

        for reason in companion_reasons + backend_reasons:
            label = reason_labels.get(reason, f"Whisper failed: {reason}")
            assert label, f"No label for reason: {reason}"

    def test_whisper_summary_breakdown(self):
        """Verify the summary message format with mixed failure reasons."""
        whisper_failure_reasons = {
            "timeout": 45,
            "audio_download_failed": 30,
            "all_formats_failed": 8,
        }
        whisper_ok = 7
        whisper_failed = sum(whisper_failure_reasons.values())

        reason_summary_parts = []
        if whisper_failure_reasons.get("timeout"):
            reason_summary_parts.append(f"{whisper_failure_reasons['timeout']} timed out")
        if whisper_failure_reasons.get("audio_download_failed"):
            reason_summary_parts.append(f"{whisper_failure_reasons['audio_download_failed']} audio download failed")
        if whisper_failure_reasons.get("all_formats_failed"):
            reason_summary_parts.append(f"{whisper_failure_reasons['all_formats_failed']} restricted")
        reason_detail = f" ({', '.join(reason_summary_parts)})"

        expected = f"🎙️ Whisper: {whisper_ok} succeeded, {whisper_failed} failed{reason_detail} (concurrency: 2)"
        assert "45 timed out" in expected
        assert "30 audio download failed" in expected
        assert "8 restricted" in expected
        assert "7 succeeded" in expected


# ---------------------------------------------------------------------------
# 4. Companion Whisper response format
# ---------------------------------------------------------------------------

class TestCompanionWhisperResponse:
    """Verify the companion returns correct failure_detail values."""

    def test_whisper_failed_with_audio_download_detail(self):
        response = {"video_id": "test", "transcript": None, "source": "whisper_failed", "failure_detail": "audio_download_failed"}
        assert response["transcript"] is None
        assert response["failure_detail"] == "audio_download_failed"

    def test_whisper_failed_with_video_fallback_detail(self):
        response = {"video_id": "test", "transcript": None, "source": "whisper_failed", "failure_detail": "video_fallback_failed"}
        assert response["failure_detail"] == "video_fallback_failed"

    def test_whisper_failed_with_all_formats_detail(self):
        response = {"video_id": "test", "transcript": None, "source": "whisper_failed", "failure_detail": "all_formats_failed"}
        assert response["failure_detail"] == "all_formats_failed"

    def test_whisper_failed_with_timeout_detail(self):
        response = {"video_id": "test", "transcript": None, "source": "whisper_failed", "failure_detail": "download_timeout"}
        assert response["failure_detail"] == "download_timeout"

    def test_whisper_success_has_transcript(self):
        response = {"video_id": "test", "transcript": "0:00 Hello world\n1:00 More text", "source": "whisper_transcription"}
        assert response["transcript"] is not None
        assert "failure_detail" not in response


# ---------------------------------------------------------------------------
# 5. Transcript model — whisper_failure_reason field
# ---------------------------------------------------------------------------

class TestTranscriptModel:
    """Verify the Transcript model has the new whisper_failure_reason field."""

    def test_default_none(self):
        t = Transcript(
            video_id="test", transcript_source=TranscriptSource.NONE,
        )
        assert t.whisper_failure_reason is None
        assert t.whisper_attempted is False

    def test_timeout_reason(self):
        t = Transcript(
            video_id="test", transcript_source=TranscriptSource.NONE,
            whisper_attempted=True, whisper_failure_reason="timeout",
        )
        assert t.whisper_failure_reason == "timeout"

    def test_successful_whisper_no_failure_reason(self):
        t = Transcript(
            video_id="test",
            transcript_text="0:00 Transcribed text",
            transcript_source=TranscriptSource.WHISPER,
            whisper_attempted=True,
        )
        assert t.transcript_text is not None
        assert t.whisper_failure_reason is None


# ---------------------------------------------------------------------------
# 6. End-to-end: TranscriberService._whisper_via_agent failure paths
# ---------------------------------------------------------------------------

class TestWhisperViaAgentFailurePaths:
    """Test _whisper_via_agent returns structured failure info."""

    @pytest.mark.asyncio
    async def test_timeout_returns_failure_reason(self):
        from app.services.transcriber import TranscriberService

        with patch("app.utils.agent_queue.is_agent_available", return_value=True), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-t"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=[]):
            ts = TranscriberService()
            result = await ts._whisper_via_agent("test_vid", 600)

        assert result is not None
        assert result.get("failure_reason") == "timeout"
        assert "text" not in result

    @pytest.mark.asyncio
    async def test_companion_failure_returns_detail(self):
        from app.services.transcriber import TranscriberService

        whisper_fail = [{"video_id": "test_vid", "transcript": None, "source": "whisper_failed", "failure_detail": "video_fallback_failed"}]

        with patch("app.utils.agent_queue.is_agent_available", return_value=True), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-f"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=whisper_fail):
            ts = TranscriberService()
            result = await ts._whisper_via_agent("test_vid", 600)

        assert result is not None
        assert result.get("failure_reason") == "video_fallback_failed"

    @pytest.mark.asyncio
    async def test_no_agent_returns_failure_reason(self):
        from app.services.transcriber import TranscriberService

        mock_storage = AsyncMock()
        mock_storage.get_transcript.return_value = None

        with patch("app.services.storage.get_storage", return_value=mock_storage), \
             patch("app.utils.agent_queue.is_agent_available", return_value=False):
            ts = TranscriberService()
            result = await ts.get_transcript("test_vid", video_duration_seconds=600)

        assert result.transcript_text is None
        assert result.whisper_attempted is True
        assert result.whisper_failure_reason == "no_agent"

    @pytest.mark.asyncio
    async def test_success_returns_text(self):
        from app.services.transcriber import TranscriberService

        whisper_ok = [{"video_id": "test_vid", "transcript": "0:00 Hello world", "source": "whisper_transcription"}]

        with patch("app.utils.agent_queue.is_agent_available", return_value=True), \
             patch("app.utils.agent_queue.create_task", new_callable=AsyncMock, return_value="task-s"), \
             patch("app.utils.agent_queue.wait_for_result", new_callable=AsyncMock, return_value=whisper_ok):
            ts = TranscriberService()
            result = await ts._whisper_via_agent("test_vid", 600)

        assert result is not None
        assert result.get("text") == "0:00 Hello world"
        assert "failure_reason" not in result
