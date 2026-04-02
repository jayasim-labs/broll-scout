#!/usr/bin/env python3
"""
B-Roll Scout CLI Agent — standalone alternative to the browser-based agent.

Polls the EC2 server for pending yt-dlp tasks, executes them locally,
and posts results back.  Useful for:
  - Running on a dedicated always-on machine
  - Power users who prefer a terminal
  - Multiple editors sharing one agent

Usage:
  python broll_agent.py                          # defaults to broll.jayasim.com
  python broll_agent.py --server http://localhost:8000  # local dev

Prerequisites:
  pip install requests yt-dlp
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("broll-agent")

YTDLP_TIMEOUT = 45
POLL_INTERVAL = 2


def main():
    parser = argparse.ArgumentParser(description="B-Roll Scout CLI Agent")
    parser.add_argument(
        "--server", default="https://broll.jayasim.com",
        help="B-Roll Scout server URL",
    )
    parser.add_argument(
        "--agent-id", default="cli-agent",
        help="Identifier for this agent instance",
    )
    args = parser.parse_args()

    base = args.server.rstrip("/")
    agent_id = args.agent_id

    # Verify yt-dlp is installed
    try:
        ver = subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, timeout=5,
        )
        log.info("yt-dlp version: %s", ver.stdout.strip())
    except FileNotFoundError:
        log.error("yt-dlp not found — install with: pip install yt-dlp")
        sys.exit(1)

    log.info("Connecting to %s as agent '%s'", base, agent_id)
    log.info("Press Ctrl+C to stop")

    while True:
        try:
            resp = requests.post(
                f"{base}/api/v1/agent/poll",
                json={"agent_id": agent_id},
                timeout=10,
            )
            resp.raise_for_status()
            tasks = resp.json().get("tasks", [])

            if not tasks:
                time.sleep(POLL_INTERVAL)
                continue

            for task in tasks:
                task_id = task["task_id"]
                task_type = task["task_type"]
                payload = task["payload"]

                log.info("Task %s: %s", task_id[:8], task_type)

                try:
                    if task_type == "search":
                        result = ytdlp_search(payload["query"], payload.get("max_results", 10))
                    elif task_type == "channel_search":
                        result = ytdlp_channel_search(
                            payload["channel_id"], payload["query"], payload.get("max_results", 5),
                        )
                    elif task_type == "video_details":
                        result = ytdlp_video_details(payload["video_ids"])
                    else:
                        log.warning("Unknown task type: %s", task_type)
                        result = []

                    status = "completed"
                    log.info("  → %d results", len(result))
                except Exception as e:
                    log.error("  → failed: %s", e)
                    result = []
                    status = "failed"

                requests.post(
                    f"{base}/api/v1/agent/result",
                    json={"task_id": task_id, "status": status, "result": result},
                    timeout=10,
                )

        except requests.ConnectionError:
            log.warning("Cannot reach %s — retrying in 5s", base)
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.error("Unexpected error: %s", e)
            time.sleep(5)


# ---------------------------------------------------------------------------
# yt-dlp wrappers (identical to companion.py)
# ---------------------------------------------------------------------------

def ytdlp_search(query, max_results=10):
    cmd = ["yt-dlp", f"ytsearch{max_results}:{query}",
           "--dump-json", "--no-download", "--no-warnings", "--flat-playlist"]
    return _run(cmd)


def ytdlp_channel_search(channel_id, query, max_results=5):
    url = f"https://www.youtube.com/channel/{channel_id}/search?query={query}"
    cmd = ["yt-dlp", url, "--dump-json", "--no-download", "--no-warnings",
           "--flat-playlist", "--playlist-end", str(max_results)]
    return _run(cmd)


def ytdlp_video_details(video_ids):
    results = []
    for vid in video_ids:
        cmd = ["yt-dlp", f"https://www.youtube.com/watch?v={vid}",
               "--dump-json", "--no-download", "--no-warnings"]
        results.extend(_run(cmd))
    return results


def _run(cmd):
    results = []
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=YTDLP_TIMEOUT)
        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(_normalize(data))
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp timed out")
    except FileNotFoundError:
        log.error("yt-dlp not found")
    except Exception as e:
        log.error("yt-dlp error: %s", e)
    return results


def _normalize(data):
    upload_date = data.get("upload_date", "")
    iso_date = ""
    if upload_date and len(upload_date) == 8:
        iso_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}T00:00:00Z"
    vid = data.get("id", "")
    return {
        "video_id": vid,
        "title": data.get("title", ""),
        "video_title": data.get("title", ""),
        "video_url": f"https://www.youtube.com/watch?v={vid}",
        "channel_name": data.get("channel") or data.get("uploader") or "",
        "channel_id": data.get("channel_id") or "",
        "channel_subscribers": data.get("channel_follower_count"),
        "thumbnail_url": data.get("thumbnail") or f"https://img.youtube.com/vi/{vid}/mqdefault.jpg",
        "duration_seconds": data.get("duration") or 0,
        "video_duration_seconds": data.get("duration") or 0,
        "published_at": iso_date,
        "view_count": data.get("view_count") or 0,
        "description": data.get("description", "")[:500],
    }


if __name__ == "__main__":
    main()
