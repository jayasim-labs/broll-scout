#!/usr/bin/env python3
"""
B-Roll Scout Companion — runs on the editor's local machine.

Listens on localhost:9876 and executes yt-dlp commands on behalf of the
EC2-hosted B-Roll Scout application.  The browser frontend relays tasks
between the EC2 server and this companion via localhost.

Start:  python companion.py
Stop:   Ctrl+C  (or quit from system tray)

Prerequisites:
  pip install flask flask-cors
  brew install yt-dlp   # or: pip install yt-dlp
"""

import json
import logging
import subprocess
import sys

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=[
    "https://broll.jayasim.com",
    "http://localhost:3000",
    "http://localhost:3001",
])

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("companion")

YTDLP_TIMEOUT = 45


@app.route("/health")
def health():
    try:
        proc = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return jsonify({
            "status": "ok",
            "ytdlp_version": proc.stdout.strip(),
        })
    except FileNotFoundError:
        return jsonify({
            "status": "error",
            "message": "yt-dlp not installed. Run: pip install yt-dlp",
        }), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/execute", methods=["POST"])
def execute():
    task = request.json or {}
    task_type = task.get("task_type")
    payload = task.get("payload", {})

    log.info("Task: %s  payload keys: %s", task_type, list(payload.keys()))

    if task_type == "search":
        results = ytdlp_search(payload["query"], payload.get("max_results", 10))
    elif task_type == "channel_search":
        results = ytdlp_channel_search(
            payload["channel_id"], payload["query"], payload.get("max_results", 5),
        )
    elif task_type == "video_details":
        results = ytdlp_video_details(payload["video_ids"])
    else:
        return jsonify({"error": f"Unknown task type: {task_type}"}), 400

    log.info("Returning %d results for %s", len(results), task_type)
    return jsonify({"results": results})


# ---------------------------------------------------------------------------
# yt-dlp wrappers
# ---------------------------------------------------------------------------

def ytdlp_search(query: str, max_results: int = 10) -> list[dict]:
    cmd = [
        "yt-dlp", f"ytsearch{max_results}:{query}",
        "--dump-json", "--no-download", "--no-warnings", "--flat-playlist",
    ]
    return _run_ytdlp(cmd)


def ytdlp_channel_search(channel_id: str, query: str, max_results: int = 5) -> list[dict]:
    url = f"https://www.youtube.com/channel/{channel_id}/search?query={query}"
    cmd = [
        "yt-dlp", url,
        "--dump-json", "--no-download", "--no-warnings",
        "--flat-playlist", "--playlist-end", str(max_results),
    ]
    return _run_ytdlp(cmd)


def ytdlp_video_details(video_ids: list[str]) -> list[dict]:
    results = []
    for vid in video_ids:
        cmd = [
            "yt-dlp", f"https://www.youtube.com/watch?v={vid}",
            "--dump-json", "--no-download", "--no-warnings",
        ]
        r = _run_ytdlp(cmd)
        results.extend(r)
    return results


def _run_ytdlp(cmd: list[str]) -> list[dict]:
    results = []
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=YTDLP_TIMEOUT,
        )
        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(_normalize(data))
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp timed out after %ds: %s", YTDLP_TIMEOUT, " ".join(cmd[:4]))
    except FileNotFoundError:
        log.error("yt-dlp not found — install with: pip install yt-dlp")
    except Exception as e:
        log.error("yt-dlp error: %s", e)
    return results


def _normalize(data: dict) -> dict:
    """Convert yt-dlp JSON to the same dict format the YouTube API returns."""
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
        "thumbnail_url": (
            data.get("thumbnail")
            or f"https://img.youtube.com/vi/{vid}/mqdefault.jpg"
        ),
        "duration_seconds": data.get("duration") or 0,
        "video_duration_seconds": data.get("duration") or 0,
        "published_at": iso_date,
        "view_count": data.get("view_count") or 0,
        "description": data.get("description", "")[:500],
    }


if __name__ == "__main__":
    port = 9876
    log.info("B-Roll Scout Companion starting on http://127.0.0.1:%d", port)
    log.info("Keep this running while using broll.jayasim.com")
    app.run(host="127.0.0.1", port=port)
