#!/usr/bin/env python3
"""
One-time script: Populate the DynamoDB channel_cache table with channel info
for all Tier 1 and Tier 2 channels defined in config.py.

Uses the YouTube Data API v3 to fetch channel name, subscriber count, and
thumbnail for each channel ID, then stores them in DynamoDB.

For Tier 2 channels (names only), it searches YouTube to find the channel ID
first, then fetches full info.

Usage:
    python scripts/populate_channels.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import boto3
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "broll_")
REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

if not YOUTUBE_API_KEY:
    try:
        from app.config import get_settings
        settings = get_settings()
        YOUTUBE_API_KEY = settings.youtube_api_key
        TABLE_PREFIX = settings.dynamodb_table_prefix
        REGION = settings.aws_region
        AWS_ACCESS_KEY_ID = settings.aws_access_key_id
        AWS_SECRET_ACCESS_KEY = settings.aws_secret_access_key
    except Exception:
        pass

from app.config import DEFAULTS

dynamodb_kwargs: dict = {"region_name": REGION}
if AWS_ACCESS_KEY_ID:
    dynamodb_kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
    dynamodb_kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
dynamodb = boto3.resource("dynamodb", **dynamodb_kwargs)
table = dynamodb.Table(f"{TABLE_PREFIX}channel_cache")


async def fetch_channel_by_id(client: httpx.AsyncClient, channel_id: str) -> dict | None:
    resp = await client.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={
            "part": "snippet,statistics",
            "id": channel_id,
            "key": YOUTUBE_API_KEY,
        },
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return None
    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "channel_id": channel_id,
        "channel_name": snippet.get("title", ""),
        "subscribers": int(stats.get("subscriberCount", 0)),
        "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
    }


async def search_channel_by_name(client: httpx.AsyncClient, name: str) -> dict | None:
    resp = await client.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "type": "channel",
            "q": name,
            "maxResults": 1,
            "key": YOUTUBE_API_KEY,
        },
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return None
    channel_id = items[0].get("snippet", {}).get("channelId") or items[0].get("id", {}).get("channelId", "")
    if not channel_id:
        return None
    return await fetch_channel_by_id(client, channel_id)


def store_channel(info: dict) -> None:
    table.put_item(Item={
        "channel_id": info["channel_id"],
        "channel_name": info["channel_name"],
        "subscribers": info["subscribers"],
        "thumbnail_url": info["thumbnail_url"],
        "cached_at": datetime.utcnow().isoformat(),
    })


async def main():
    if not YOUTUBE_API_KEY:
        print("ERROR: YOUTUBE_API_KEY not set in .env")
        sys.exit(1)

    tier1_ids = DEFAULTS.get("preferred_channels_tier1", [])
    tier2_names = DEFAULTS.get("preferred_channels_tier2", [])

    print(f"Populating channel cache for {len(tier1_ids)} Tier 1 + {len(tier2_names)} Tier 2 channels\n")

    async with httpx.AsyncClient(timeout=15.0) as client:
        print("=== Tier 1 Channels (by ID) ===")
        for cid in tier1_ids:
            try:
                info = await fetch_channel_by_id(client, cid)
                if info:
                    store_channel(info)
                    subs = f"{info['subscribers']:,}" if info['subscribers'] else "?"
                    print(f"  OK  {cid} → {info['channel_name']} ({subs} subs)")
                else:
                    print(f"  --  {cid} → not found on YouTube")
            except Exception as e:
                print(f"  ERR {cid} → {e}")

        print(f"\n=== Tier 2 Channels (by name) ===")
        for name in tier2_names:
            try:
                info = await search_channel_by_name(client, name)
                if info:
                    store_channel(info)
                    subs = f"{info['subscribers']:,}" if info['subscribers'] else "?"
                    print(f"  OK  \"{name}\" → {info['channel_name']} [{info['channel_id']}] ({subs} subs)")
                else:
                    print(f"  --  \"{name}\" → not found on YouTube")
            except Exception as e:
                print(f"  ERR \"{name}\" → {e}")

    print("\nDone! Channel cache populated.")


if __name__ == "__main__":
    asyncio.run(main())
