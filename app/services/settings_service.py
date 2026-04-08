import asyncio
import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

import boto3
import httpx
from botocore.exceptions import ClientError

from app.config import get_settings, DEFAULTS
from app.models.schemas import ChannelResolution, ChannelEntry
from app.utils import agent_queue

logger = logging.getLogger(__name__)


def _format_sub_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count) if count else "N/A"


class SettingsService:
    """Settings CRUD, defaults, and YouTube channel resolution."""

    def __init__(self):
        settings = get_settings()
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )
        self.prefix = settings.dynamodb_table_prefix
        self.youtube_api_key = settings.youtube_api_key
        self._channel_cache: Dict[str, ChannelResolution] = {}

    def _table(self, name: str):
        return self.dynamodb.Table(f"{self.prefix}{name}")

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def get_all_settings(self) -> Dict[str, Any]:
        result = dict(DEFAULTS)
        try:
            resp = await self._run(self._table("settings").scan)
            for item in resp.get("Items", []):
                key = item.get("setting_key")
                value = item.get("setting_value")
                if key and value is not None:
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    result[key] = value
        except ClientError:
            logger.exception("Failed to fetch settings")
        return result

    async def get_setting(self, key: str) -> Any:
        try:
            resp = await self._run(
                self._table("settings").get_item,
                Key={"setting_key": key},
            )
            if "Item" in resp:
                value = resp["Item"].get("setting_value")
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        return value
                return value
        except ClientError:
            logger.exception("Failed to fetch setting %s", key)
        return DEFAULTS.get(key)

    async def update_setting(
        self, key: str, value: Any, editor_id: str = "system"
    ) -> bool:
        if not self._validate_setting(key, value):
            logger.warning("Invalid setting value for %s", key)
            return False
        try:
            stored = value
            if isinstance(value, (list, dict)):
                stored = json.dumps(value)
            await self._run(
                self._table("settings").put_item,
                Item={
                    "setting_key": key,
                    "setting_value": stored,
                    "updated_at": datetime.utcnow().isoformat(),
                    "updated_by": editor_id,
                },
            )
            return True
        except ClientError:
            logger.exception("Failed to update setting %s", key)
            return False

    async def bulk_update_settings(
        self, settings_dict: Dict[str, Any], editor_id: str = "system"
    ) -> int:
        count = 0
        for key, value in settings_dict.items():
            if await self.update_setting(key, value, editor_id):
                count += 1
        return count

    async def reset_to_defaults(self) -> bool:
        try:
            resp = await self._run(self._table("settings").scan)
            for item in resp.get("Items", []):
                key = item.get("setting_key")
                if key:
                    await self._run(
                        self._table("settings").delete_item,
                        Key={"setting_key": key},
                    )
            return True
        except ClientError:
            logger.exception("Failed to reset settings")
            return False

    async def resolve_channel(self, channel_url: str) -> Optional[ChannelResolution]:
        channel_id = self._extract_channel_id(channel_url)
        if not channel_id:
            return None

        if channel_id in self._channel_cache:
            return self._channel_cache[channel_id]

        cached = await self._get_cached_channel(channel_id)
        if cached:
            self._channel_cache[channel_id] = cached
            return cached

        resolution = None

        if self.youtube_api_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/channels",
                        params={
                            "part": "snippet,statistics",
                            "id": channel_id,
                            "key": self.youtube_api_key,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                items = data.get("items", [])
                if items:
                    item = items[0]
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    resolution = ChannelResolution(
                        channel_id=channel_id,
                        channel_name=snippet.get("title", ""),
                        subscribers=int(stats.get("subscriberCount", 0)),
                        thumbnail_url=snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                    )
            except Exception:
                logger.warning("YouTube API failed for %s, trying HTML fallback", channel_id)

        if not resolution:
            resolution = await self._resolve_channel_from_html(channel_id)

        if resolution:
            self._channel_cache[channel_id] = resolution
            await self._cache_channel(resolution)
        return resolution

    async def get_preferred_channels(self) -> Tuple[List[ChannelResolution], List[ChannelResolution]]:
        tier1_ids = await self.get_setting("preferred_channels_tier1") or []
        tier2_names = await self.get_setting("preferred_channels_tier2") or []

        tier1 = []
        for cid in tier1_ids:
            resolved = await self.resolve_channel(cid)
            if resolved:
                tier1.append(resolved)

        tier2 = [
            ChannelResolution(channel_id="", channel_name=name)
            for name in tier2_names
        ]
        return tier1, tier2

    async def get_blocked_sources(self) -> Dict[str, Any]:
        return {
            "networks": await self.get_setting("blocked_networks") or [],
            "studios": await self.get_setting("blocked_studios") or [],
            "sports": await self.get_setting("blocked_sports") or [],
            "custom_rules": await self.get_setting("custom_block_rules") or "",
        }

    # ── Channel sources CRUD ──────────────────────────────────────────────

    async def get_channel_sources(self) -> List[dict]:
        raw = await self.get_setting("channel_sources")
        if isinstance(raw, list):
            return raw
        return []

    async def add_channel_source(self, entry: dict) -> bool:
        sources = await self.get_channel_sources()
        if any(s.get("channel_id") == entry.get("channel_id") and s.get("tier") == entry.get("tier") for s in sources):
            return False
        entry.setdefault("added_at", datetime.utcnow().isoformat())
        sources.append(entry)
        return await self.update_setting("channel_sources", sources)

    async def remove_channel_source(self, channel_id: str) -> bool:
        sources = await self.get_channel_sources()
        filtered = [s for s in sources if s.get("channel_id") != channel_id]
        if len(filtered) == len(sources):
            return False
        return await self.update_setting("channel_sources", filtered)

    async def get_channel_sources_grouped(self) -> Dict[str, List[dict]]:
        sources = await self.get_channel_sources()
        groups: Dict[str, List[dict]] = {
            "blocked-news": [], "blocked-studio": [], "blocked-sports": [],
            "preferred-tier1": [], "preferred-tier2": [],
        }
        for s in sources:
            tier = s.get("tier", "")
            cat = s.get("category", "")
            if tier == "blocked":
                key = f"blocked-{cat}"
            elif tier in ("tier1", "tier2"):
                key = f"preferred-{tier}"
            else:
                continue
            if key in groups:
                groups[key].append(s)
        return groups

    # ── Channel resolution (URL/handle/ID/search → full metadata) ───────

    async def resolve_channel_input(self, input_str: str) -> List[dict]:
        input_str = input_str.strip()
        if not input_str:
            return []

        match = re.match(r'https?://(?:www\.)?youtube\.com/channel/(UC[\w-]{22})', input_str)
        if match:
            return await self._resolve_by_id(match.group(1))

        match = re.match(r'https?://(?:www\.)?youtube\.com/@([\w.-]+)', input_str)
        if match:
            return await self._resolve_by_handle(match.group(1))

        match = re.match(r'https?://(?:www\.)?youtube\.com/(?:c|user)/([\w-]+)', input_str)
        if match:
            return await self._search_channels(match.group(1))

        if re.match(r'^UC[\w-]{22}$', input_str):
            return await self._resolve_by_id(input_str)

        if input_str.startswith('@'):
            return await self._resolve_by_handle(input_str[1:])

        return await self._search_channels(input_str)

    async def _resolve_by_id(self, channel_id: str) -> List[dict]:
        if self.youtube_api_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/channels",
                        params={"part": "snippet,statistics", "id": channel_id, "key": self.youtube_api_key},
                    )
                    resp.raise_for_status()
                    items = resp.json().get("items", [])
                    if items:
                        return [self._parse_yt_channel(items[0])]
            except Exception:
                logger.warning("YouTube API failed for channel ID %s", channel_id)

        return await self._resolve_via_companion("resolve_channel", {"channel_id": channel_id})

    async def _resolve_by_handle(self, handle: str) -> List[dict]:
        if self.youtube_api_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/channels",
                        params={"part": "snippet,statistics", "forHandle": handle, "key": self.youtube_api_key},
                    )
                    resp.raise_for_status()
                    items = resp.json().get("items", [])
                    if items:
                        return [self._parse_yt_channel(items[0])]
            except Exception:
                logger.warning("YouTube API failed for handle @%s", handle)

        return await self._resolve_via_companion("resolve_channel", {"handle": handle})

    async def _search_channels(self, query: str) -> List[dict]:
        if self.youtube_api_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    search_resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/search",
                        params={
                            "part": "snippet", "q": query, "type": "channel",
                            "maxResults": 10, "key": self.youtube_api_key,
                        },
                    )
                    search_resp.raise_for_status()
                    channel_ids = [
                        item["id"]["channelId"]
                        for item in search_resp.json().get("items", [])
                        if "channelId" in item.get("id", {})
                    ]
                    if not channel_ids:
                        return []

                    details_resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/channels",
                        params={
                            "part": "snippet,statistics",
                            "id": ",".join(channel_ids),
                            "key": self.youtube_api_key,
                        },
                    )
                    details_resp.raise_for_status()
                    return [self._parse_yt_channel(item) for item in details_resp.json().get("items", [])]
            except Exception:
                logger.warning("YouTube API search failed for '%s'", query)

        return await self._resolve_via_companion("search_channels", {"query": query, "max_results": 10})

    async def _resolve_via_companion(self, task_type: str, payload: dict) -> List[dict]:
        if not agent_queue.is_agent_available():
            return []
        try:
            task_id = await agent_queue.create_task(task_type, payload)
            result = await agent_queue.wait_for_result(task_id, timeout=30)
            if isinstance(result, list):
                return result
        except Exception:
            logger.warning("Companion channel resolution failed for %s", task_type)
        return []

    @staticmethod
    def _parse_yt_channel(item: dict) -> dict:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        sub_count = int(stats.get("subscriberCount", 0))
        return {
            "channel_id": item.get("id", ""),
            "channel_name": snippet.get("title", ""),
            "channel_url": f"https://www.youtube.com/channel/{item.get('id', '')}",
            "channel_handle": snippet.get("customUrl", ""),
            "thumbnail_url": (
                snippet.get("thumbnails", {}).get("medium", {}).get("url", "")
                or snippet.get("thumbnails", {}).get("default", {}).get("url", "")
            ),
            "subscriber_count": sub_count,
            "subscriber_display": _format_sub_count(sub_count),
            "video_count": int(stats.get("videoCount", 0)),
            "description": (snippet.get("description", ""))[:200],
        }

    # ── Migration from old format ────────────────────────────────────────

    async def migrate_channel_settings_if_needed(self) -> int:
        existing = await self.get_channel_sources()
        if existing:
            return 0

        old_keys = [
            ("blocked_networks", "news", "blocked"),
            ("blocked_studios", "studio", "blocked"),
            ("blocked_sports", "sports", "blocked"),
            ("preferred_channels_tier1", "archive", "tier1"),
            ("preferred_channels_tier2", "documentary", "tier2"),
        ]

        new_entries: List[dict] = []
        for setting_key, category, tier in old_keys:
            old_values = await self.get_setting(setting_key)
            if not old_values or not isinstance(old_values, list):
                continue
            for value in old_values:
                if not value:
                    continue
                resolved = await self.resolve_channel_input(str(value))
                if resolved:
                    best = resolved[0]
                    best["category"] = category
                    best["tier"] = tier
                    best["added_at"] = datetime.utcnow().isoformat()
                    best["added_by"] = "migration"
                    new_entries.append(best)
                else:
                    new_entries.append({
                        "channel_id": value if value.startswith("UC") else "",
                        "channel_name": value if not value.startswith("UC") else "",
                        "channel_url": "",
                        "channel_handle": "",
                        "thumbnail_url": "",
                        "subscriber_count": 0,
                        "subscriber_display": "N/A",
                        "category": category,
                        "tier": tier,
                        "added_at": datetime.utcnow().isoformat(),
                        "added_by": "migration",
                    })

        if new_entries:
            await self.update_setting("channel_sources", new_entries)
            logger.info("Migrated %d channel entries to channel_sources", len(new_entries))

        return len(new_entries)

    # Legacy default before min duration was raised to 120s (keep in sync with git history).
    _LEGACY_MIN_VIDEO_DURATION_SEC = 30

    async def migrate_min_video_duration_to_current_default(self, force: bool = False) -> bool:
        """Align DynamoDB ``min_video_duration_sec`` with :data:`app.config.DEFAULTS`.

        - With ``force=False`` (e.g. on API startup): only updates when the stored
          value is still the old default (30), so custom values are preserved.
        - With ``force=True``: always writes the current code default (use with care).

        Returns True if a row was written.
        """
        target = int(DEFAULTS["min_video_duration_sec"])
        if force:
            return await self.update_setting(
                "min_video_duration_sec", target, editor_id="system_default_sync",
            )

        try:
            resp = await self._run(
                self._table("settings").get_item,
                Key={"setting_key": "min_video_duration_sec"},
            )
            item = resp.get("Item")
            if item is None:
                return False
            raw = item.get("setting_value")
            if isinstance(raw, str):
                try:
                    current = int(json.loads(raw))
                except (json.JSONDecodeError, TypeError, ValueError):
                    try:
                        current = int(raw)
                    except (TypeError, ValueError):
                        return False
            elif isinstance(raw, Decimal):
                current = int(raw)
            else:
                try:
                    current = int(raw)
                except (TypeError, ValueError):
                    return False

            if current == self._LEGACY_MIN_VIDEO_DURATION_SEC and current != target:
                ok = await self.update_setting(
                    "min_video_duration_sec", target, editor_id="system_default_sync",
                )
                if ok:
                    logger.info(
                        "Updated min_video_duration_sec in DynamoDB: %s -> %s",
                        current, target,
                    )
                return ok
        except ClientError:
            logger.exception("migrate_min_video_duration_to_current_default: DynamoDB read failed")
        except Exception:
            logger.exception("migrate_min_video_duration_to_current_default failed")
        return False

    def _validate_setting(self, key: str, value: Any) -> bool:
        weight_keys = {
            "weight_ai_confidence", "weight_fit_score",
            "weight_viral_score",
            "weight_channel_authority", "weight_caption_quality", "weight_recency",
            "weight_context_relevance",
        }
        if key in weight_keys:
            try:
                v = float(value)
                return 0.0 <= v <= 1.0
            except (TypeError, ValueError):
                return False

        if key == "confidence_threshold":
            try:
                v = float(value)
                return 0.1 <= v <= 0.9
            except (TypeError, ValueError):
                return False

        if key == "preferred_channels_tier1" and isinstance(value, list):
            for ch in value:
                if isinstance(ch, str) and not ch.startswith("UC"):
                    return False

        if key == "min_video_duration_sec":
            try:
                v = int(value)
                return 30 <= v <= 600
            except (TypeError, ValueError):
                return False

        if key == "filter_9_16_shorts":
            return value is True or value is False

        if key == "shorts_9_16_aspect_tolerance":
            try:
                v = float(value)
                return 0.02 <= v <= 0.15
            except (TypeError, ValueError):
                return False

        return True

    @staticmethod
    def _extract_channel_id(url: str) -> Optional[str]:
        if url.startswith("UC") and len(url) == 24:
            return url
        match = re.search(r"/channel/(UC[a-zA-Z0-9_-]{22})", url)
        if match:
            return match.group(1)
        return None

    async def resolve_channels_by_name(self, names: list[str]) -> dict[str, ChannelResolution]:
        """Look up cached channels by name (for Tier 2 display)."""
        results: dict[str, ChannelResolution] = {}
        try:
            resp = await self._run(self._table("channel_cache").scan)
            items = resp.get("Items", [])
            name_map: list[tuple[str, dict]] = []
            for item in items:
                raw = item.get("channel_name", "").lower()
                name_map.append((raw, item))
            for name in names:
                key = name.lower()
                key_compact = key.replace(" ", "")
                for cached_name, item in name_map:
                    cached_compact = cached_name.replace(" ", "")
                    if (key in cached_name or cached_name in key
                            or key_compact in cached_compact
                            or cached_compact in key_compact):
                        results[name] = ChannelResolution(
                            channel_id=item.get("channel_id", ""),
                            channel_name=item.get("channel_name", ""),
                            subscribers=int(item.get("subscribers", 0)),
                            thumbnail_url=item.get("thumbnail_url", ""),
                        )
                        break
        except ClientError:
            logger.warning("Failed to scan channel cache for name lookup")
        return results

    async def _resolve_channel_from_html(self, channel_id: str) -> Optional[ChannelResolution]:
        """Fallback: scrape channel name and avatar from the YouTube channel page."""
        url = f"https://www.youtube.com/channel/{channel_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                html = resp.text

            avatar_url = ""
            m = re.search(r'<link rel="image_src" href="([^"]+)"', html)
            if m:
                avatar_url = m.group(1).split("=")[0] + "=s176-c-k-c0x00ffffff-no-rj"

            channel_name = ""
            m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            if m:
                channel_name = m.group(1)
            if not channel_name:
                m = re.search(r'"name"\s*:\s*"([^"]+)"', html)
                if m:
                    channel_name = m.group(1)

            if not channel_name:
                return None

            return ChannelResolution(
                channel_id=channel_id,
                channel_name=channel_name,
                subscribers=0,
                thumbnail_url=avatar_url,
            )
        except Exception:
            logger.warning("HTML fallback failed for channel %s", channel_id)
            return None

    async def _get_cached_channel(self, channel_id: str) -> Optional[ChannelResolution]:
        try:
            resp = await self._run(
                self._table("channel_cache").get_item,
                Key={"channel_id": channel_id},
            )
            if "Item" not in resp:
                return None
            item = resp["Item"]
            return ChannelResolution(
                channel_id=channel_id,
                channel_name=item.get("channel_name", ""),
                subscribers=int(item.get("subscribers", 0)),
                thumbnail_url=item.get("thumbnail_url", ""),
            )
        except ClientError:
            return None

    async def _cache_channel(self, resolution: ChannelResolution) -> None:
        try:
            await self._run(
                self._table("channel_cache").put_item,
                Item={
                    "channel_id": resolution.channel_id,
                    "channel_name": resolution.channel_name,
                    "subscribers": resolution.subscribers,
                    "thumbnail_url": resolution.thumbnail_url,
                    "cached_at": datetime.utcnow().isoformat(),
                },
            )
        except ClientError:
            logger.warning("Failed to cache channel %s", resolution.channel_id)


_settings_service: Optional[SettingsService] = None


def get_settings_service() -> SettingsService:
    global _settings_service
    if _settings_service is None:
        _settings_service = SettingsService()
    return _settings_service
