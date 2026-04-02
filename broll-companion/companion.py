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
from concurrent.futures import ThreadPoolExecutor

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

YTDLP_TIMEOUT = 60
_executor = ThreadPoolExecutor(max_workers=4)


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
    elif task_type == "transcript":
        results = fetch_transcript(payload["video_id"], payload.get("languages", ["en"]))
    elif task_type == "whisper":
        results = whisper_transcribe(payload["video_id"], payload.get("max_duration_min", 60))
    elif task_type == "clip":
        result = clip_download(
            payload["video_id"],
            payload.get("start_seconds", 0),
            payload.get("end_seconds", 60),
            payload.get("output_dir"),
        )
        return jsonify(result)
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


def fetch_transcript(video_id: str, languages: list[str] | None = None) -> list[dict]:
    """Fetch transcript using youtube-transcript-api (runs locally, not blocked by YouTube)."""
    languages = languages or ["en"]
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()

        try:
            fetched = api.fetch(video_id, languages=languages)
            entries = fetched.to_raw_data()
            source = "youtube_captions"
        except Exception:
            transcript_list = api.list(video_id)
            try:
                manual = transcript_list.find_manually_created_transcript(languages)
                entries = manual.fetch().to_raw_data()
                source = "youtube_captions"
            except Exception:
                try:
                    auto = transcript_list.find_generated_transcript(languages)
                    entries = auto.fetch().to_raw_data()
                    source = "youtube_auto_captions"
                except Exception:
                    for t in transcript_list:
                        if not t.is_generated:
                            entries = t.fetch().to_raw_data()
                            source = "youtube_captions"
                            break
                    else:
                        return [{"video_id": video_id, "transcript": None, "source": "no_transcript"}]

        lines = []
        for entry in entries:
            total_seconds = int(entry.get("start", 0))
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            text = entry.get("text", "").strip()
            if text:
                lines.append(f"{minutes}:{seconds:02d} {text}")

        return [{"video_id": video_id, "transcript": "\n".join(lines), "source": source}]

    except Exception as e:
        log.warning("Transcript fetch failed for %s: %s", video_id, e)
        return [{"video_id": video_id, "transcript": None, "source": "no_transcript"}]


def whisper_transcribe(video_id: str, max_duration_min: int = 60) -> list[dict]:
    """Download audio via yt-dlp, transcribe with OpenAI Whisper locally."""
    import tempfile
    import os

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")
        cmd = [
            "yt-dlp", url,
            "-x", "--audio-format", "mp3",
            "--no-playlist", "--no-warnings",
            "-o", audio_path,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0 or not os.path.exists(audio_path):
                log.warning("yt-dlp audio download failed for %s: %s", video_id, proc.stderr[:300])
                return [{"video_id": video_id, "transcript": None, "source": "whisper_failed"}]
        except subprocess.TimeoutExpired:
            log.warning("yt-dlp audio download timed out for %s", video_id)
            return [{"video_id": video_id, "transcript": None, "source": "whisper_failed"}]

        try:
            import whisper
        except ImportError:
            log.error("openai-whisper not installed. Run: pip install openai-whisper")
            return [{"video_id": video_id, "transcript": None, "source": "whisper_not_installed"}]

        try:
            model = whisper.load_model("base")
            result = model.transcribe(audio_path, language="en")
        except Exception as e:
            log.error("Whisper transcription failed for %s: %s", video_id, e)
            return [{"video_id": video_id, "transcript": None, "source": "whisper_failed"}]

        segments = result.get("segments", [])
        lines = []
        for seg in segments:
            total_seconds = int(seg.get("start", 0))
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            text = seg.get("text", "").strip()
            if text:
                lines.append(f"{minutes}:{seconds:02d} {text}")

        transcript_text = "\n".join(lines)
        return [{"video_id": video_id, "transcript": transcript_text, "source": "whisper_transcription"}]


def clip_download(video_id: str, start_seconds: int, end_seconds: int, output_dir: str | None = None) -> dict:
    """Download a clipped section of a YouTube video using yt-dlp --download-sections."""
    import os

    if output_dir is None:
        output_dir = os.path.join(os.path.expanduser("~"), "Downloads", "BRoll Clips")
    os.makedirs(output_dir, exist_ok=True)

    start_ts = _seconds_to_hms(start_seconds)
    end_ts = _seconds_to_hms(end_seconds)

    filename = f"clip_{video_id}_{start_seconds}-{end_seconds}.mp4"
    output_path = os.path.join(output_dir, filename)

    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp", url,
        "--download-sections", f"*{start_ts}-{end_ts}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-warnings",
        "-o", output_path,
    ]

    log.info("Clip download: %s [%s – %s]", video_id, start_ts, end_ts)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode == 0 and os.path.exists(output_path):
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            log.info("Clip saved: %s (%.1f MB)", output_path, file_size_mb)
            return {
                "status": "ok",
                "file_path": output_path,
                "file_name": filename,
                "file_size_mb": round(file_size_mb, 2),
                "duration_seconds": end_seconds - start_seconds,
            }
        else:
            log.warning("Clip download failed for %s: %s", video_id, proc.stderr[:500])
            return {"status": "error", "message": proc.stderr[:500] or "Download failed"}
    except subprocess.TimeoutExpired:
        log.warning("Clip download timed out for %s", video_id)
        return {"status": "error", "message": "Download timed out (3 min limit)"}
    except Exception as e:
        log.error("Clip download error: %s", e)
        return {"status": "error", "message": str(e)}


def _seconds_to_hms(total_seconds: int) -> str:
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


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
    app.run(host="127.0.0.1", port=port, threaded=True)
