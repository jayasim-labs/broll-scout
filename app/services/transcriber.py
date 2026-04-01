import logging

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

from app.config import get_settings, DEFAULTS
from app.models.schemas import Transcript, TranscriptSource
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)


class TranscriberService:
    """Fetches transcripts via cache -> YouTube captions -> Whisper flag cascade."""

    def __init__(self):
        self.settings = get_settings()

    async def get_transcript(
        self,
        video_id: str,
        video_duration_seconds: int = 0,
        job_id: str | None = None,
    ) -> Transcript:
        """Attempt to get a transcript following the cascade: cache -> YouTube -> Whisper flag."""
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
            logger.info("No YouTube captions available for %s", video_id)
        except Exception:
            logger.exception("YouTube caption fetch failed for %s", video_id)

        whisper_max_seconds = DEFAULTS.get("whisper_max_video_duration_min", 60) * 60
        if 0 < video_duration_seconds <= whisper_max_seconds:
            return Transcript(
                video_id=video_id,
                transcript_text=None,
                transcript_source=TranscriptSource.NONE,
                video_duration_seconds=video_duration_seconds,
            )

        return no_transcript

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
            entries = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
            return self._format_entries(entries), TranscriptSource.YOUTUBE_MANUAL
        except Exception:
            pass

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        try:
            manual_en = transcript_list.find_manually_created_transcript(["en"])
            entries = manual_en.fetch()
            return self._format_entries(entries), TranscriptSource.YOUTUBE_MANUAL
        except Exception:
            pass

        try:
            auto_en = transcript_list.find_generated_transcript(["en"])
            entries = auto_en.fetch()
            return self._format_entries(entries), TranscriptSource.YOUTUBE_AUTO
        except Exception:
            pass

        for transcript in transcript_list:
            if not transcript.is_generated:
                try:
                    entries = transcript.fetch()
                    return self._format_entries(entries), TranscriptSource.YOUTUBE_MANUAL
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
