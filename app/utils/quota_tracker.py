"""
Tracks YouTube API quota state. YouTube Data API v3 resets its 10,000-unit
daily quota at midnight Pacific Time.
"""

import logging
import threading
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_PACIFIC = timezone(timedelta(hours=-7))


class QuotaTracker:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quota_exhausted = False
        self._exhausted_at: datetime | None = None
        self._api_units_used = 0
        self._ytdlp_searches = 0
        self._ytdlp_detail_lookups = 0
        self._used_api = False
        self._used_ytdlp = False

    @property
    def is_quota_exhausted(self) -> bool:
        with self._lock:
            if not self._quota_exhausted:
                return False
            if self._exhausted_at is None:
                return True
            now_pacific = datetime.now(_PACIFIC)
            exhausted_pacific = self._exhausted_at.astimezone(_PACIFIC)
            if now_pacific.date() > exhausted_pacific.date():
                self._quota_exhausted = False
                self._exhausted_at = None
                logger.info("YouTube API quota auto-reset (new Pacific day)")
                return False
            return True

    def mark_quota_exhausted(self) -> None:
        with self._lock:
            if not self._quota_exhausted:
                self._quota_exhausted = True
                self._exhausted_at = datetime.now(timezone.utc)
                logger.warning("YouTube API quota marked as exhausted")

    def track_api_call(self, units: int) -> None:
        with self._lock:
            self._api_units_used += units
            self._used_api = True

    def track_ytdlp_search(self) -> None:
        with self._lock:
            self._ytdlp_searches += 1
            self._used_ytdlp = True

    def track_ytdlp_details(self, count: int = 1) -> None:
        with self._lock:
            self._ytdlp_detail_lookups += count
            self._used_ytdlp = True

    def _search_mode_unlocked(self) -> str:
        if self._used_api and self._used_ytdlp:
            return "hybrid"
        if self._used_ytdlp:
            return "ytdlp"
        return "youtube_api"

    @property
    def search_mode(self) -> str:
        with self._lock:
            return self._search_mode_unlocked()

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "quota_exhausted": self._quota_exhausted,
                "api_units_used": self._api_units_used,
                "ytdlp_searches_via_agent": self._ytdlp_searches,
                "ytdlp_detail_lookups_via_agent": self._ytdlp_detail_lookups,
                "search_mode": self._search_mode_unlocked(),
            }

    def reset_for_job(self) -> None:
        with self._lock:
            self._api_units_used = 0
            self._ytdlp_searches = 0
            self._ytdlp_detail_lookups = 0
            self._used_api = False
            self._used_ytdlp = False


_instance: QuotaTracker | None = None


def get_quota_tracker() -> QuotaTracker:
    global _instance
    if _instance is None:
        _instance = QuotaTracker()
    return _instance
