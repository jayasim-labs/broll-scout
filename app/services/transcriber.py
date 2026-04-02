import logging

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

from app.config import get_settings, DEFAULTS
from app.models.schemas import Transcript, TranscriptSource
from app.utils.cost_tracker import get_cost_tracker
from app.utils import agent_queue

logger = logging.getLogger(__name__)

_ytt_api = YouTubeTranscriptApi()


class TranscriberService:
    """Fetches transcripts via cache -> YouTube captions -> agent -> Whisper flag cascade."""

    def __init__(self):
        self.settings = get_settings()

    async def get_transcript(
        self,
        video_id: str,
        video_duration_seconds: int = 0,
        job_id: str | None = None,
    ) -> Transcript:
        """Attempt to get a transcript: cache -> direct YouTube -> agent (local companion) -> Whisper flag."""
        from app.services.storage import get_storage
        storage = get_storage()

        no_transcript = Transcript(
            video_id=video_id,
            transcript_text=None,
            transcript_source=TranscriptSource.NONE,
            video_duration_seconds=video_duration_seconds,
        )

        try:
            cached = await storage.get_transcript(video_id)
            if cached and cached.transcript_text:
                logger.info("Transcript cache hit for %s", video_id)
                cached.transcript_source = TranscriptSource.CACHED
                return cached
        except Exception:
            logger.exception("Cache lookup failed for %s", video_id)

        try:
            transcript_data, source = self._fetch_youtube_captions(video_id)
            if transcript_data is not None:
                await storage.store_transcript(
                    video_id=video_id,
                    transcript_text=transcript_data,
                    source=source,
                    language="en",
                    duration=video_duration_seconds,
                )
                return Transcript(
                    video_id=video_id,
                    transcript_text=transcript_data,
                    transcript_source=source,
                    language="en",
                    video_duration_seconds=video_duration_seconds,
                )
        except (TranscriptsDisabled, NoTranscriptFound):
            logger.info("No YouTube captions available for %s (direct)", video_id)
        except Exception:
            logger.info("Direct YouTube caption fetch failed for %s — trying agent", video_id)

        # Fallback: fetch transcript via local companion agent
        try:
            logger.info("Trying agent transcript for %s", video_id)
            agent_result = await self._fetch_via_agent(video_id)
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
            else:
                logger.info("Agent transcript returned nothing for %s", video_id)
        except Exception:
            logger.info("Agent transcript fetch failed for %s", video_id)

        # Last resort: Whisper transcription via local companion
        max_whisper_duration = DEFAULTS.get("whisper_max_video_duration_min", 60) * 60
        effective_duration = video_duration_seconds or 300  # assume 5 min if unknown
        if effective_duration <= max_whisper_duration:
            try:
                logger.info("Trying Whisper for %s (%ds video)", video_id, effective_duration)
                whisper_result = await self._whisper_via_agent(video_id, effective_duration)
                if whisper_result:
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
                    logger.info("Whisper transcript stored for %s", video_id)
                    return Transcript(
                        video_id=video_id,
                        transcript_text=whisper_result["text"],
                        transcript_source=TranscriptSource.WHISPER,
                        language="en",
                        video_duration_seconds=video_duration_seconds,
                    )
                else:
                    logger.info("Whisper returned no transcript for %s", video_id)
            except Exception:
                logger.exception("Whisper transcription failed for %s", video_id)
        else:
            logger.info("Skipping Whisper for %s — duration %ds exceeds max %ds", video_id, effective_duration, max_whisper_duration)

        logger.warning("All transcript sources exhausted for %s", video_id)
        return no_transcript

    async def _fetch_via_agent(self, video_id: str) -> dict | None:
        """Ask the local companion to fetch the transcript."""
        if not agent_queue.is_agent_available():
            logger.info("No agent available for transcript fetch of %s", video_id)
            return None

        task_id = await agent_queue.create_task("transcript", {
            "video_id": video_id,
            "languages": ["en"],
        })
        results = await agent_queue.wait_for_result(task_id, timeout=90)
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
        }
        source = source_map.get(source_str, TranscriptSource.YOUTUBE_MANUAL)
        return {"text": transcript_text, "source": source}

    async def _whisper_via_agent(self, video_id: str, duration_seconds: int) -> dict | None:
        """Ask the local companion to download audio and run Whisper transcription."""
        if not agent_queue.is_agent_available():
            logger.info("No agent available for Whisper transcription of %s", video_id)
            return None

        max_dur_min = DEFAULTS.get("whisper_max_video_duration_min", 60)
        task_id = await agent_queue.create_task("whisper", {
            "video_id": video_id,
            "max_duration_min": max_dur_min,
        })
        timeout = min(600, max(180, duration_seconds + 120))
        results = await agent_queue.wait_for_result(task_id, timeout=timeout)
        if not results:
            return None
        data = results[0]
        transcript_text = data.get("transcript")
        if not transcript_text:
            return None
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

    def _fetch_youtube_captions(
        self, video_id: str
    ) -> tuple[str | None, TranscriptSource]:
        """Try YouTube captions: manual English -> auto English -> manual any language."""
        try:
            fetched = _ytt_api.fetch(video_id, languages=["en"])
            return self._format_entries(fetched.to_raw_data()), TranscriptSource.YOUTUBE_MANUAL
        except Exception:
            pass

        transcript_list = _ytt_api.list(video_id)

        try:
            manual_en = transcript_list.find_manually_created_transcript(["en"])
            entries = manual_en.fetch()
            return self._format_entries(entries.to_raw_data()), TranscriptSource.YOUTUBE_MANUAL
        except Exception:
            pass

        try:
            auto_en = transcript_list.find_generated_transcript(["en"])
            entries = auto_en.fetch()
            return self._format_entries(entries.to_raw_data()), TranscriptSource.YOUTUBE_AUTO
        except Exception:
            pass

        for transcript in transcript_list:
            if not transcript.is_generated:
                try:
                    entries = transcript.fetch()
                    return self._format_entries(entries.to_raw_data()), TranscriptSource.YOUTUBE_MANUAL
                except Exception:
                    continue

        return None, TranscriptSource.NONE

    @staticmethod
    def _format_entries(entries: list[dict]) -> str:
        lines = []
        for entry in entries:
            total_seconds = int(entry.get("start", 0))
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            text = entry.get("text", "").strip()
            if text:
                lines.append(f"{minutes}:{seconds:02d} {text}")
        return "\n".join(lines)
