import logging

from app.config import get_settings, DEFAULTS
from app.models.schemas import Transcript, TranscriptSource
from app.utils.cost_tracker import get_cost_tracker
from app.utils import agent_queue

logger = logging.getLogger(__name__)


class TranscriberService:
    """Fetches transcripts via cache -> companion captions -> Whisper."""

    def __init__(self, pipeline_settings: dict | None = None):
        self.settings = get_settings()
        self._pipeline = pipeline_settings or {}

    def _get(self, key: str, fallback=None):
        if key in self._pipeline:
            return self._pipeline[key]
        return DEFAULTS.get(key, fallback)

    async def get_transcript(
        self,
        video_id: str,
        video_duration_seconds: int = 0,
        job_id: str | None = None,
        on_whisper_start=None,
    ) -> Transcript:
        """Try cache -> companion captions -> Whisper, sequentially.

        Simple and synchronous — caller gets back a complete result every time.
        """
        from app.services.storage import get_storage
        storage = get_storage()

        no_transcript = Transcript(
            video_id=video_id,
            transcript_text=None,
            transcript_source=TranscriptSource.NONE,
            video_duration_seconds=video_duration_seconds,
        )

        # 1. Cache lookup
        try:
            cached = await storage.get_transcript(video_id)
            if cached and cached.transcript_text:
                logger.info("Transcript cache hit for %s", video_id)
                cached.transcript_source = TranscriptSource.CACHED
                return cached
        except Exception:
            logger.exception("Cache lookup failed for %s", video_id)

        # 2. Companion captions (YouTube captions via residential IP)
        if agent_queue.is_agent_available():
            try:
                agent_result = await self._fetch_captions_via_agent(video_id, job_id=job_id)
                if agent_result:
                    await storage.store_transcript(
                        video_id=video_id,
                        transcript_text=agent_result["text"],
                        source=agent_result["source"],
                        language="en",
                        duration=video_duration_seconds,
                    )
                    return Transcript(
                        video_id=video_id,
                        transcript_text=agent_result["text"],
                        transcript_source=agent_result["source"],
                        language="en",
                        video_duration_seconds=video_duration_seconds,
                    )
            except Exception:
                logger.info("[transcript] Agent captions failed for %s", video_id)

        # 3. Whisper transcription via companion
        max_whisper_duration = self._get("whisper_max_video_duration_min", 60) * 60
        effective_duration = video_duration_seconds or 300
        if effective_duration > max_whisper_duration:
            logger.info("[transcript] Skipping Whisper for %s — duration %ds > max %ds",
                        video_id, effective_duration, max_whisper_duration)
            return no_transcript

        if not agent_queue.is_agent_available():
            logger.warning("[transcript] No agent available for Whisper on %s", video_id)
            no_transcript.whisper_attempted = True
            no_transcript.whisper_failure_reason = "no_agent"
            return no_transcript

        try:
            if on_whisper_start:
                try:
                    await on_whisper_start(video_id, effective_duration)
                except Exception:
                    pass

            whisper_result = await self._whisper_via_agent(
                video_id, effective_duration, job_id=job_id,
            )
            if whisper_result and whisper_result.get("text"):
                await storage.store_transcript(
                    video_id=video_id,
                    transcript_text=whisper_result["text"],
                    source=TranscriptSource.WHISPER,
                    language="en",
                    duration=video_duration_seconds,
                )
                if job_id:
                    whisper_min = round(effective_duration / 60, 1)
                    costs = get_cost_tracker().get_job_costs(job_id)
                    if costs:
                        costs.add_whisper(whisper_min)
                return Transcript(
                    video_id=video_id,
                    transcript_text=whisper_result["text"],
                    transcript_source=TranscriptSource.WHISPER,
                    language="en",
                    video_duration_seconds=video_duration_seconds,
                    whisper_attempted=True,
                )
            else:
                reason = whisper_result.get("failure_reason", "unknown") if whisper_result else "unknown"
                no_transcript.whisper_attempted = True
                no_transcript.whisper_failure_reason = reason
        except Exception:
            logger.exception("[transcript] Whisper exception for %s", video_id)
            no_transcript.whisper_attempted = True

        return no_transcript

    async def _fetch_captions_via_agent(
        self, video_id: str, job_id: str | None = None,
    ) -> dict | None:
        """Ask the companion to fetch YouTube captions."""
        task_id = await agent_queue.create_task("transcript", {
            "video_id": video_id,
            "languages": ["en"],
        }, job_id=job_id)
        results = await agent_queue.wait_for_result(task_id, timeout=300)
        if not results:
            return None
        data = results[0]
        transcript_text = data.get("transcript")
        if not transcript_text:
            return None

        source_str = data.get("source", "youtube_captions")
        source_map = {
            "youtube_captions": TranscriptSource.YOUTUBE_MANUAL,
            "youtube_auto_captions": TranscriptSource.YOUTUBE_AUTO,
            "youtube_captions_ytdlp": TranscriptSource.YOUTUBE_MANUAL,
            "youtube_auto_captions_ytdlp": TranscriptSource.YOUTUBE_AUTO,
        }
        source = source_map.get(source_str, TranscriptSource.YOUTUBE_MANUAL)
        return {"text": transcript_text, "source": source}

    async def _whisper_via_agent(
        self, video_id: str, duration_seconds: int, job_id: str | None = None,
    ) -> dict | None:
        """Ask the companion to download audio and run Whisper.

        Returns dict with 'text' on success, or 'failure_reason' on failure.
        No timeout calculation needed — the companion does the work and we just wait.
        """
        max_dur_min = self._get("whisper_max_video_duration_min", 60)
        whisper_model = self._get("whisper_model", "large-v3-turbo")
        task_id = await agent_queue.create_task("whisper", {
            "video_id": video_id,
            "max_duration_min": max_dur_min,
            "whisper_model": whisper_model,
        }, job_id=job_id)

        # Generous per-task timeout: the companion processes sequentially so
        # this task may queue behind others. 4 hours is a safe upper bound —
        # in practice even a 60-min video takes ~10 min on GPU.
        timeout = 4 * 3600
        results = await agent_queue.wait_for_result(task_id, timeout=timeout)
        if not results:
            logger.warning("[whisper] Task timed out or empty for %s", video_id)
            return {"failure_reason": "timeout"}
        data = results[0]
        transcript_text = data.get("transcript")
        if not transcript_text:
            failure_detail = data.get("failure_detail", "audio_download_failed")
            return {"failure_reason": failure_detail}
        return {"text": transcript_text}

    async def store_whisper_result(
        self,
        video_id: str,
        transcript_text: str,
        language: str = "en",
        duration: int = 0,
    ) -> Transcript:
        """Store a Whisper transcript submitted by the client."""
        from app.services.storage import get_storage
        storage = get_storage()

        await storage.store_transcript(
            video_id=video_id,
            transcript_text=transcript_text,
            source=TranscriptSource.WHISPER,
            language=language,
            duration=duration,
        )

        return Transcript(
            video_id=video_id,
            transcript_text=transcript_text,
            transcript_source=TranscriptSource.WHISPER,
            language=language,
            video_duration_seconds=duration,
        )
