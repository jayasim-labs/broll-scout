import re
import asyncio
import logging
from typing import List, Dict, Any, Optional

import httpx

from app.config import get_settings, DEFAULTS
from app.utils.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 30.0
MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = {500, 503}


class YouTubeQuotaExceeded(Exception):
    """Raised when the YouTube Data API returns a 403 quotaExceeded error."""


def _get_batch_size() -> int:
    return DEFAULTS.get("youtube_api_batch_size", 50)


def parse_iso8601_duration(duration: str) -> int:
    """Parse ISO 8601 duration (e.g. PT1H2M30S) to seconds."""
    match = re.match(
        r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration or ""
    )
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


async def _request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Make an HTTP request with retries. Raises YouTubeQuotaExceeded on quota errors."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url, params=params)

            if resp.status_code == 403:
                body = resp.json() if resp.content else {}
                errors = body.get("error", {}).get("errors", [])
                reasons = [e.get("reason", "") for e in errors]
                if any(r in ("quotaExceeded", "dailyLimitExceeded") for r in reasons):
                    raise YouTubeQuotaExceeded("YouTube API daily quota exceeded")
                logger.warning("YouTube API 403: %s", reasons)
                return {}

            if resp.status_code in RETRYABLE_STATUS_CODES:
                wait = (2 ** attempt)
                logger.warning(
                    "YouTube API %d on attempt %d/%d, retrying in %ds",
                    resp.status_code, attempt + 1, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except YouTubeQuotaExceeded:
            raise
        except httpx.TimeoutException:
            wait = (2 ** attempt)
            logger.warning(
                "YouTube API timeout on attempt %d/%d, retrying in %ds",
                attempt + 1, MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)
        except httpx.HTTPStatusError as exc:
            logger.error("YouTube API HTTP error: %s", exc)
            return {}
        except Exception as exc:
            logger.error("YouTube API unexpected error: %s", exc)
            return {}

    logger.error("YouTube API request failed after %d attempts", MAX_RETRIES)
    return {}


def _parse_search_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []
    for item in items:
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumb = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url", "")
        )
        results.append({
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
            "thumbnail_url": thumb,
        })
    return results


async def search_videos(
    query: str,
    max_results: int = 5,
    api_key: str = "",
    job_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search YouTube. Raises YouTubeQuotaExceeded if quota is hit."""
    key = api_key or get_settings().youtube_api_key
    if not key:
        logger.error("No YouTube API key configured")
        return []

    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": max_results,
        "key": key,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _request_with_retry(client, f"{BASE_URL}/search", params)

    if job_id:
        get_cost_tracker().track_youtube_search(job_id)

    if not data:
        return []

    return _parse_search_items(data.get("items", []))


async def search_channel_videos(
    channel_id: str,
    query: str,
    max_results: int = 5,
    api_key: str = "",
    job_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search within a channel. Raises YouTubeQuotaExceeded if quota is hit."""
    key = api_key or get_settings().youtube_api_key
    if not key:
        logger.error("No YouTube API key configured")
        return []

    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "channelId": channel_id,
        "maxResults": max_results,
        "key": key,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _request_with_retry(client, f"{BASE_URL}/search", params)

    if job_id:
        get_cost_tracker().track_youtube_search(job_id)

    if not data:
        return []

    return _parse_search_items(data.get("items", []))


async def get_video_details(
    video_ids: List[str],
    api_key: str = "",
    job_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch detailed info. Raises YouTubeQuotaExceeded if quota is hit."""
    key = api_key or get_settings().youtube_api_key
    if not key:
        logger.error("No YouTube API key configured")
        return []

    if not video_ids:
        return []

    batch_size = _get_batch_size()
    results: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for i in range(0, len(video_ids), batch_size):
            chunk = video_ids[i : i + batch_size]
            params = {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(chunk),
                "key": key,
            }

            data = await _request_with_retry(client, f"{BASE_URL}/videos", params)

            if job_id:
                get_cost_tracker().track_youtube_details(job_id, count=len(chunk))

            if not data:
                continue

            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                content = item.get("contentDetails", {})
                thumbnails = snippet.get("thumbnails", {})
                thumb = (
                    thumbnails.get("high", {}).get("url")
                    or thumbnails.get("medium", {}).get("url")
                    or thumbnails.get("default", {}).get("url", "")
                )
                results.append({
                    "video_id": item.get("id", ""),
                    "title": snippet.get("title", ""),
                    "channel_id": snippet.get("channelId", ""),
                    "channel_name": snippet.get("channelTitle", ""),
                    "channel_subscribers": 0,
                    "thumbnail_url": thumb,
                    "duration_seconds": parse_iso8601_duration(
                        content.get("duration", "")
                    ),
                    "published_at": snippet.get("publishedAt", ""),
                    "view_count": int(stats.get("viewCount", 0)),
                    "description": snippet.get("description", ""),
                })

    return results


async def get_channel_stats(
    channel_ids: List[str],
    api_key: str = "",
    job_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch channel statistics. Raises YouTubeQuotaExceeded if quota is hit."""
    key = api_key or get_settings().youtube_api_key
    if not key:
        logger.error("No YouTube API key configured")
        return {}

    if not channel_ids:
        return {}

    batch_size = _get_batch_size()
    results: Dict[str, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for i in range(0, len(channel_ids), batch_size):
            chunk = channel_ids[i : i + batch_size]
            params = {
                "part": "statistics,snippet",
                "id": ",".join(chunk),
                "key": key,
            }

            data = await _request_with_retry(client, f"{BASE_URL}/channels", params)

            if not data:
                continue

            for item in data.get("items", []):
                cid = item.get("id", "")
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                thumbnails = snippet.get("thumbnails", {})
                thumb = (
                    thumbnails.get("default", {}).get("url", "")
                )
                results[cid] = {
                    "subscriber_count": int(stats.get("subscriberCount", 0)),
                    "video_count": int(stats.get("videoCount", 0)),
                    "channel_name": snippet.get("title", ""),
                    "thumbnail_url": thumb,
                }

    return results
