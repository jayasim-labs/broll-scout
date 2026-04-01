"""
B-Roll Scout - Settings Service
CRUD operations for settings table, default management, and YouTube channel resolution.
"""

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import logging
import asyncio
from functools import partial
import re

from app.config import get_settings, DEFAULTS
from app.models.schemas import ChannelResolution

logger = logging.getLogger(__name__)


class SettingsService:
    """Settings management service."""
    
    def __init__(self):
        settings = get_settings()
        self.dynamodb = boto3.resource(
            'dynamodb',
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key
        )
        self.prefix = settings.dynamodb_table_prefix
        self.youtube_api_key = settings.youtube_api_key
        self._channel_cache: Dict[str, ChannelResolution] = {}
    
    def _table(self, name: str):
        """Get a DynamoDB table by name with prefix."""
        return self.dynamodb.Table(f"{self.prefix}{name}")
    
    async def get_all_settings(self) -> Dict[str, Any]:
        """
        Get all settings merged with defaults.
        DynamoDB values override defaults.
        """
        # Start with defaults
        result = dict(DEFAULTS)
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self._table("settings").scan
            )
            
            # Override with database values
            for item in response.get("Items", []):
                key = item.get("setting_key")
                value = item.get("setting_value")
                if key and value is not None:
                    # Parse JSON strings for complex types
                    if isinstance(value, str):
                        try:
                            import json
                            value = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            pass  # Keep as string
                    result[key] = value
                    
        except ClientError as e:
            logger.error(f"Failed to fetch settings: {e}")
        
        return result
    
    async def get_setting(self, key: str) -> Any:
        """Get a single setting value, or its default."""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                partial(
                    self._table("settings").get_item,
                    Key={"setting_key": key}
                )
            )
            
            if "Item" in response:
                value = response["Item"].get("setting_value")
                if isinstance(value, str):
                    try:
                        import json
                        return json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        return value
                return value
                
        except ClientError as e:
            logger.error(f"Failed to fetch setting {key}: {e}")
        
        # Return default
        return DEFAULTS.get(key)
    
    async def update_setting(
        self,
        key: str,
        value: Any,
        editor_id: str = "system"
    ) -> bool:
        """Update a single setting."""
        # Validate the setting
        if not self._validate_setting(key, value):
            logger.warning(f"Invalid setting value for {key}")
            return False
        
        try:
            import json
            loop = asyncio.get_event_loop()
            
            # Serialize complex types
            stored_value = value
            if isinstance(value, (list, dict)):
                stored_value = json.dumps(value)
            
            await loop.run_in_executor(
                None,
                partial(
                    self._table("settings").put_item,
                    Item={
                        "setting_key": key,
                        "setting_value": stored_value,
                        "updated_at": datetime.utcnow().isoformat(),
                        "updated_by": editor_id
                    }
                )
            )
            return True
            
        except ClientError as e:
            logger.error(f"Failed to update setting {key}: {e}")
            return False
    
    async def bulk_update_settings(
        self,
        settings_dict: Dict[str, Any],
        editor_id: str = "system"
    ) -> int:
        """Bulk update multiple settings. Returns count of successful updates."""
        success_count = 0
        for key, value in settings_dict.items():
            if await self.update_setting(key, value, editor_id):
                success_count += 1
        return success_count
    
    async def reset_to_defaults(self) -> bool:
        """Delete all custom settings, reverting to hardcoded defaults."""
        try:
            loop = asyncio.get_event_loop()
            
            # Scan all settings
            response = await loop.run_in_executor(
                None,
                self._table("settings").scan
            )
            
            # Delete each one
            for item in response.get("Items", []):
                key = item.get("setting_key")
                if key:
                    await loop.run_in_executor(
                        None,
                        partial(
                            self._table("settings").delete_item,
                            Key={"setting_key": key}
                        )
                    )
            
            return True
            
        except ClientError as e:
            logger.error(f"Failed to reset settings: {e}")
            return False
    
    def _validate_setting(self, key: str, value: Any) -> bool:
        """Validate setting values before writing."""
        # Weight parameters must be 0.0-1.0
        weight_keys = [
            "weight_keyword_density", "weight_viral_score",
            "weight_channel_authority", "weight_caption_quality", "weight_recency"
        ]
        if key in weight_keys:
            try:
                v = float(value)
                return 0.0 <= v <= 1.0
            except (TypeError, ValueError):
                return False
        
        # Confidence threshold must be 0.1-0.9
        if key == "confidence_threshold":
            try:
                v = float(value)
                return 0.1 <= v <= 0.9
            except (TypeError, ValueError):
                return False
        
        # Channel IDs must match UC... format (for tier1)
        if key == "preferred_channels_tier1" and isinstance(value, list):
            for ch in value:
                if isinstance(ch, str) and not ch.startswith("UC"):
                    return False
        
        return True
    
    # =========================================================================
    # YouTube Channel Resolution
    # =========================================================================
    
    async def resolve_channel(self, channel_url: str) -> Optional[ChannelResolution]:
        """
        Extract channel ID from URL, call YouTube API to fetch metadata.
        Results are cached.
        """
        # Extract channel ID from URL
        channel_id = self._extract_channel_id(channel_url)
        if not channel_id:
            logger.warning(f"Could not extract channel ID from {channel_url}")
            return None
        
        # Check cache
        if channel_id in self._channel_cache:
            return self._channel_cache[channel_id]
        
        # Check DynamoDB cache
        cached = await self._get_cached_channel(channel_id)
        if cached:
            self._channel_cache[channel_id] = cached
            return cached
        
        # Fetch from YouTube API
        try:
            from googleapiclient.discovery import build
            
            loop = asyncio.get_event_loop()
            youtube = build('youtube', 'v3', developerKey=self.youtube_api_key)
            
            response = await loop.run_in_executor(
                None,
                lambda: youtube.channels().list(
                    part='snippet,statistics',
                    id=channel_id
                ).execute()
            )
            
            items = response.get('items', [])
            if not items:
                return None
            
            item = items[0]
            snippet = item.get('snippet', {})
            stats = item.get('statistics', {})
            
            resolution = ChannelResolution(
                channel_id=channel_id,
                channel_name=snippet.get('title', ''),
                subscribers=int(stats.get('subscriberCount', 0)),
                thumbnail_url=snippet.get('thumbnails', {}).get('default', {}).get('url', '')
            )
            
            # Cache in memory and DynamoDB
            self._channel_cache[channel_id] = resolution
            await self._cache_channel(resolution)
            
            return resolution
            
        except Exception as e:
            logger.error(f"Failed to resolve channel {channel_id}: {e}")
            return None
    
    def _extract_channel_id(self, url: str) -> Optional[str]:
        """Extract channel ID from various YouTube URL formats."""
        # Direct channel ID
        if url.startswith("UC") and len(url) == 24:
            return url
        
        # /channel/UC... format
        match = re.search(r'/channel/(UC[a-zA-Z0-9_-]{22})', url)
        if match:
            return match.group(1)
        
        # /c/... or /user/... format - would need API lookup
        # For MVP, just handle direct channel IDs
        
        return None
    
    async def _get_cached_channel(self, channel_id: str) -> Optional[ChannelResolution]:
        """Get cached channel metadata from DynamoDB."""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                partial(
                    self._table("channel_cache").get_item,
                    Key={"channel_id": channel_id}
                )
            )
            
            if "Item" not in response:
                return None
            
            item = response["Item"]
            return ChannelResolution(
                channel_id=channel_id,
                channel_name=item.get("channel_name", ""),
                subscribers=item.get("subscribers", 0),
                thumbnail_url=item.get("thumbnail_url", "")
            )
            
        except ClientError:
            return None
    
    async def _cache_channel(self, resolution: ChannelResolution) -> None:
        """Cache channel metadata in DynamoDB."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(
                    self._table("channel_cache").put_item,
                    Item={
                        "channel_id": resolution.channel_id,
                        "channel_name": resolution.channel_name,
                        "subscribers": resolution.subscribers,
                        "thumbnail_url": resolution.thumbnail_url,
                        "cached_at": datetime.utcnow().isoformat()
                    }
                )
            )
        except ClientError as e:
            logger.warning(f"Failed to cache channel: {e}")
    
    async def get_preferred_channels(self) -> Tuple[List[ChannelResolution], List[ChannelResolution]]:
        """Get Tier 1 and Tier 2 channel lists with resolved names."""
        tier1_ids = await self.get_setting("preferred_channels_tier1") or []
        tier2_names = await self.get_setting("preferred_channels_tier2") or []
        
        tier1_resolved = []
        for ch_id in tier1_ids:
            resolved = await self.resolve_channel(ch_id)
            if resolved:
                tier1_resolved.append(resolved)
        
        # Tier 2 are stored by name, not ID
        tier2_resolved = [
            ChannelResolution(
                channel_id="",
                channel_name=name,
                subscribers=0,
                thumbnail_url=""
            )
            for name in tier2_names
        ]
        
        return tier1_resolved, tier2_resolved
    
    async def get_blocked_sources(self) -> Dict[str, List[str]]:
        """Get all blocked source lists."""
        return {
            "networks": await self.get_setting("blocked_networks") or [],
            "studios": await self.get_setting("blocked_studios") or [],
            "sports": await self.get_setting("blocked_sports") or [],
            "custom_rules": await self.get_setting("custom_block_rules") or ""
        }


# Singleton instance
_settings_service: Optional[SettingsService] = None


def get_settings_service() -> SettingsService:
    """Get the settings service singleton."""
    global _settings_service
    if _settings_service is None:
        _settings_service = SettingsService()
    return _settings_service
