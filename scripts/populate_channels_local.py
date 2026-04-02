#!/usr/bin/env python3
"""
Populate channel_cache in DynamoDB using yt-dlp (runs locally, no API quota needed).
Uses yt-dlp to fetch channel metadata, then stores in DynamoDB.

Usage:
    python scripts/populate_channels_local.py
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import boto3
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import get_settings, DEFAULTS

settings = get_settings()
TABLE_PREFIX = settings.dynamodb_table_prefix
REGION = settings.aws_region

dynamodb_kwargs: dict = {"region_name": REGION}
if settings.aws_access_key_id:
    dynamodb_kwargs["aws_access_key_id"] = settings.aws_access_key_id
    dynamodb_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
dynamodb = boto3.resource("dynamodb", **dynamodb_kwargs)
table = dynamodb.Table(f"{TABLE_PREFIX}channel_cache")


def fetch_channel_avatar(channel_id: str) -> str:
    """Fetch the actual channel avatar URL by scraping the YouTube channel page."""
    url = f"https://www.youtube.com/channel/{channel_id}"
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=10.0, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept-Language": "en-US,en;q=0.9",
        })
        m = re.search(r'<link rel="image_src" href="([^"]+)"', resp.text)
        if m:
            avatar = m.group(1)
            return avatar.split("=")[0] + "=s176-c-k-c0x00ffffff-no-rj"
    except Exception:
        pass
    return ""


def fetch_channel_via_ytdlp(channel_id: str) -> dict | None:
    url = f"https://www.youtube.com/channel/{channel_id}"
    cmd = [
        "yt-dlp", url,
        "--dump-json", "--no-download", "--no-warnings",
        "--playlist-end", "1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                avatar_url = fetch_channel_avatar(channel_id)
                return {
                    "channel_id": channel_id,
                    "channel_name": data.get("channel") or data.get("uploader") or channel_id,
                    "subscribers": data.get("channel_follower_count") or 0,
                    "thumbnail_url": avatar_url,
                }
            except json.JSONDecodeError:
                continue
    except Exception as e:
        print(f"    yt-dlp error: {e}")
    return None


def search_channel_by_name(name: str) -> dict | None:
    cmd = [
        "yt-dlp", f"ytsearch1:{name} channel",
        "--dump-json", "--no-download", "--no-warnings",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                channel_id = data.get("channel_id", "")
                if channel_id:
                    return fetch_channel_via_ytdlp(channel_id) or {
                        "channel_id": channel_id,
                        "channel_name": data.get("channel") or data.get("uploader") or name,
                        "subscribers": data.get("channel_follower_count") or 0,
                        "thumbnail_url": f"https://img.youtube.com/vi/{data.get('id', '')}/default.jpg",
                    }
            except json.JSONDecodeError:
                continue
    except Exception as e:
        print(f"    yt-dlp search error: {e}")
    return None


def store_channel(info: dict) -> None:
    table.put_item(Item={
        "channel_id": info["channel_id"],
        "channel_name": info["channel_name"],
        "subscribers": info["subscribers"],
        "thumbnail_url": info["thumbnail_url"],
        "cached_at": datetime.now(timezone.utc).isoformat(),
    })


def main():
    tier1_ids = DEFAULTS.get("preferred_channels_tier1", [])
    tier2_names = DEFAULTS.get("preferred_channels_tier2", [])

    print(f"Populating channel cache for {len(tier1_ids)} Tier 1 + {len(tier2_names)} Tier 2 channels")
    print("Using yt-dlp (local, no API quota)\n")

    print("=== Tier 1 Channels (by ID) ===")
    for cid in tier1_ids:
        info = fetch_channel_via_ytdlp(cid)
        if info:
            store_channel(info)
            subs = f"{info['subscribers']:,}" if info['subscribers'] else "?"
            print(f"  OK  {cid} -> {info['channel_name']} ({subs} subs)")
        else:
            print(f"  --  {cid} -> could not fetch via yt-dlp")

    print(f"\n=== Tier 2 Channels (by name) ===")
    for name in tier2_names:
        info = search_channel_by_name(name)
        if info:
            store_channel(info)
            subs = f"{info['subscribers']:,}" if info['subscribers'] else "?"
            print(f"  OK  \"{name}\" -> {info['channel_name']} [{info['channel_id']}] ({subs} subs)")
        else:
            print(f"  --  \"{name}\" -> not found")

    print("\nDone!")


if __name__ == "__main__":
    main()
