import asyncio
import json
import logging
import re
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

import boto3
import httpx
from botocore.exceptions import ClientError

from app.config import get_settings, DEFAULTS
from app.models.schemas import ChannelResolution

logger = logging.getLogger(__name__)


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

        if not self.youtube_api_key:
            return None

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
            if not items:
                return None

            item = items[0]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})

            resolution = ChannelResolution(
                channel_id=channel_id,
                channel_name=snippet.get("title", ""),
                subscribers=int(stats.get("subscriberCount", 0)),
                thumbnail_url=snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            )

            self._channel_cache[channel_id] = resolution
            await self._cache_channel(resolution)
            return resolution
        except Exception:
            logger.exception("Failed to resolve channel %s", channel_id)
            return None

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

    def _validate_setting(self, key: str, value: Any) -> bool:
        weight_keys = {
            "weight_keyword_density", "weight_viral_score",
            "weight_channel_authority", "weight_caption_quality", "weight_recency",
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
        return True

    @staticmethod
    def _extract_channel_id(url: str) -> Optional[str]:
        if url.startswith("UC") and len(url) == 24:
            return url
        match = re.search(r"/channel/(UC[a-zA-Z0-9_-]{22})", url)
        if match:
            return match.group(1)
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
