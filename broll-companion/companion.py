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
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
_default_cors = [
    "https://broll.jayasim.com",
    "http://localhost:3000",
    "http://localhost:3001",
]
_extra = os.environ.get("BROLL_CORS_ORIGINS", "").strip()
if _extra:
    _default_cors.extend(
        o.strip() for o in _extra.split(",") if o.strip()
    )
CORS(app, origins=_default_cors)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("companion")

YTDLP_TIMEOUT = 60
_executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# Browser cookie configuration
# ---------------------------------------------------------------------------
COOKIE_BROWSER = os.environ.get("BROLL_COOKIE_BROWSER", "none")
_cookie_args: list[str] = []
_cookie_status: str = "untested"


def _detect_cookie_support() -> None:
    """Test if yt-dlp can read cookies from the configured browser."""
    global _cookie_args, _cookie_status

    if COOKIE_BROWSER.lower() == "none":
        _cookie_status = "disabled"
        log.info("Cookie extraction disabled (BROLL_COOKIE_BROWSER=none)")
        return

    try:
        proc = subprocess.run(
            ["yt-dlp", "--cookies-from-browser", COOKIE_BROWSER,
             "--dump-json", "--no-download", "--no-warnings",
             "--flat-playlist", "--playlist-end", "1",
             "ytsearch1:test"],
            capture_output=True, text=True, timeout=20,
        )
        stderr_upper = proc.stderr.upper()[:300]
        cookie_error = any(kw in stderr_upper for kw in [
            "COULD NOT FIND COOKIE", "COOKIE", "KEYRING", "DECRYPT",
            "NO SUITABLE COOKIE",
        ])
        if not cookie_error:
            _cookie_args = ["--cookies-from-browser", COOKIE_BROWSER]
            _cookie_status = f"active ({COOKIE_BROWSER})"
            log.info("Cookie extraction enabled: --cookies-from-browser %s", COOKIE_BROWSER)
        else:
            _cookie_status = f"failed ({COOKIE_BROWSER})"
            log.warning(
                "Cookie extraction from %s failed: %s. "
                "Running without cookies. Set BROLL_COOKIE_BROWSER=none to silence this.",
                COOKIE_BROWSER, proc.stderr[:200].strip(),
            )
    except subprocess.TimeoutExpired:
        _cookie_status = "timeout"
        log.warning("Cookie detection timed out — running without cookies")
    except FileNotFoundError:
        _cookie_status = "no yt-dlp"
        log.error("yt-dlp not found during cookie detection")
    except Exception as e:
        _cookie_status = f"error: {e}"
        log.warning("Cookie detection error: %s — running without cookies", e)


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
            "cookie_status": _cookie_status,
        })
    except FileNotFoundError:
        return jsonify({
            "status": "error",
            "message": "yt-dlp not installed. Run: pip install yt-dlp",
        }), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """View or change cookie browser at runtime."""
    global COOKIE_BROWSER, _cookie_args, _cookie_status

    if request.method == "POST":
        body = request.json or {}
        new_browser = body.get("cookie_browser", "").strip()
        if new_browser:
            COOKIE_BROWSER = new_browser  # noqa: F841 — intentional global reassign
            _cookie_args = []
            _cookie_status = "untested"
            _detect_cookie_support()
        return jsonify({"cookie_browser": COOKIE_BROWSER, "cookie_status": _cookie_status})

    return jsonify({
        "cookie_browser": COOKIE_BROWSER,
        "cookie_status": _cookie_status,
        "cookie_args": _cookie_args,
    })


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


def _run_ytdlp(cmd: list[str], timeout: int | None = None) -> list[dict]:
    full_cmd = cmd[:1] + _cookie_args + cmd[1:]
    results = []
    t = timeout or YTDLP_TIMEOUT
    try:
        proc = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=t,
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
        log.warning("yt-dlp timed out after %ds: %s", t, " ".join(full_cmd[:4]))
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
            start = int(entry.get("start", 0))
            duration = entry.get("duration", 0)
            end = int(start + duration) if duration else start
            s_min, s_sec = start // 60, start % 60
            e_min, e_sec = end // 60, end % 60
            text = entry.get("text", "").strip()
            if text:
                lines.append(f"[{s_min}:{s_sec:02d} → {e_min}:{e_sec:02d}] {text}")

        return [{"video_id": video_id, "transcript": "\n".join(lines), "source": source}]

    except Exception as e:
        log.warning("Transcript fetch failed for %s: %s", video_id, e)
        return [{"video_id": video_id, "transcript": None, "source": "no_transcript"}]


def whisper_transcribe(video_id: str, max_duration_min: int = 60) -> list[dict]:
    """Download audio via yt-dlp, transcribe with OpenAI Whisper locally."""
    import tempfile
    import os
    import glob as globmod

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "audio.%(ext)s")
        cmd = [
            "yt-dlp", *_cookie_args, url,
            "-x", "--audio-format", "mp3",
            "--no-playlist", "--no-warnings",
            "-o", output_template,
        ]
        try:
            log.info("Whisper: downloading audio for %s", video_id)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            audio_files = globmod.glob(os.path.join(tmpdir, "audio.*"))
            if proc.returncode != 0 or not audio_files:
                log.warning("yt-dlp audio download failed for %s (rc=%d): %s", video_id, proc.returncode, proc.stderr[:300])
                return [{"video_id": video_id, "transcript": None, "source": "whisper_failed"}]
            audio_path = audio_files[0]
            log.info("Whisper: audio downloaded → %s", os.path.basename(audio_path))
        except subprocess.TimeoutExpired:
            log.warning("yt-dlp audio download timed out for %s", video_id)
            return [{"video_id": video_id, "transcript": None, "source": "whisper_failed"}]

        try:
            import whisper
        except ImportError:
            log.error("openai-whisper not installed. Run: pip install openai-whisper")
            return [{"video_id": video_id, "transcript": None, "source": "whisper_not_installed"}]

        try:
            log.info("Whisper: transcribing %s with base model...", video_id)
            model = whisper.load_model("base")
            result = model.transcribe(audio_path, language="en")
            log.info("Whisper: transcription complete for %s (%d segments)", video_id, len(result.get("segments", [])))
        except Exception as e:
            log.error("Whisper transcription failed for %s: %s", video_id, e)
            return [{"video_id": video_id, "transcript": None, "source": "whisper_failed"}]

        segments = result.get("segments", [])
        lines = []
        for seg in segments:
            start = seg.get("start", 0)
            end = seg.get("end", start)
            s_min, s_sec = int(start) // 60, int(start) % 60
            e_min, e_sec = int(end) // 60, int(end) % 60
            text = seg.get("text", "").strip()
            if text:
                lines.append(f"[{s_min}:{s_sec:02d} → {e_min}:{e_sec:02d}] {text}")

        transcript_text = "\n".join(lines)
        if not transcript_text:
            log.warning("Whisper found no speech in %s (0 segments with text)", video_id)
            return [{"video_id": video_id, "transcript": None, "source": "whisper_no_speech"}]
        log.info("Whisper: %d segments, %d chars for %s", len(lines), len(transcript_text), video_id)
        return [{"video_id": video_id, "transcript": transcript_text, "source": "whisper_transcription"}]


def clip_download(video_id: str, start_seconds: int, end_seconds: int, output_dir: str | None = None) -> dict:
    """Download a clipped section of a YouTube video using yt-dlp --download-sections."""
    import os

    if start_seconds >= end_seconds:
        return {
            "status": "error",
            "message": f"Invalid range: start ({start_seconds}s) must be before end ({end_seconds}s)",
        }

    if output_dir is None:
        output_dir = os.path.join(os.path.expanduser("~"), "Downloads", "BRoll Clips")
    os.makedirs(output_dir, exist_ok=True)

    start_ts = _seconds_to_hms(start_seconds)
    end_ts = _seconds_to_hms(end_seconds)

    filename = f"clip_{video_id}_{start_seconds}-{end_seconds}.mp4"
    output_path = os.path.join(output_dir, filename)

    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp", *_cookie_args, url,
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
        "width": data.get("width") or 0,
        "height": data.get("height") or 0,
    }


if __name__ == "__main__":
    port = 9876
    log.info("B-Roll Scout Companion starting on http://127.0.0.1:%d", port)
    log.info("Detecting browser cookies (default: %s)...", COOKIE_BROWSER)
    _detect_cookie_support()
    log.info("Cookie status: %s", _cookie_status)
    log.info("CORS allowed origins: %s", _default_cors)
    log.info("Keep this running while your B-Roll Scout tab is open in the browser")
    app.run(host="127.0.0.1", port=port, threaded=True)
