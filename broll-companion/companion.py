#!/usr/bin/env python3
"""
B-Roll Scout Companion — runs on the editor's local machine.

Listens on localhost:9876 and executes yt-dlp commands on behalf of the
EC2-hosted B-Roll Scout application.  The browser frontend relays tasks
between the EC2 server and this companion via localhost.

Start:  python companion.py
Stop:   Ctrl+C  (or quit from system tray)

Prerequisites:
  pip install flask flask-cors ollama
  brew install yt-dlp   # or: pip install yt-dlp
  ollama pull qwen3:8b
"""

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify
from flask_cors import CORS

try:
    import ollama as ollama_client
    _ollama_available = True
except ImportError:
    _ollama_available = False

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

_abort_lock = threading.Lock()
_task_aborts: dict[str, threading.Event] = {}


def _register_abort(task_id: str) -> threading.Event:
    ev = threading.Event()
    with _abort_lock:
        _task_aborts[task_id] = ev
    return ev


def _unregister_abort(task_id: str) -> None:
    with _abort_lock:
        _task_aborts.pop(task_id, None)

# ---------------------------------------------------------------------------
# Ollama (local LLM) configuration
# ---------------------------------------------------------------------------
MATCHER_MODEL = os.environ.get("BROLL_MATCHER_MODEL", "qwen3:8b")
_ollama_process = None
_ollama_server_ready = False
_ollama_model_ready = False


def ensure_ollama_running() -> bool:
    """Start Ollama server if not already running. Called on companion startup."""
    global _ollama_process, _ollama_server_ready
    if not _ollama_available:
        log.warning("ollama Python package not installed — local matching disabled")
        return False
    try:
        ollama_client.list()
        _ollama_server_ready = True
        log.info("Ollama server already running (for parallel matching, restart with OLLAMA_NUM_PARALLEL=3)")
        return True
    except Exception:
        pass

    try:
        log.info("Starting Ollama server (OLLAMA_NUM_PARALLEL=3)...")
        env = os.environ.copy()
        env["OLLAMA_NUM_PARALLEL"] = "3"
        _ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        for _ in range(30):
            time.sleep(1)
            try:
                ollama_client.list()
                _ollama_server_ready = True
                log.info("Ollama server started (PID: %d)", _ollama_process.pid)
                return True
            except Exception:
                continue
        log.warning("Ollama server did not start within 30 seconds")
        return False
    except FileNotFoundError:
        log.warning("Ollama binary not found — install from https://ollama.com")
        return False
    except Exception as e:
        log.warning("Failed to start Ollama server: %s", e)
        return False


def ensure_model_loaded() -> bool:
    """Check if the matcher model is pulled locally, then warm it up."""
    global _ollama_model_ready
    if not _ollama_available or not _ollama_server_ready:
        return False
    try:
        models = ollama_client.list()
        model_names = [m.model for m in models.models]
        base_name = MATCHER_MODEL.split(":")[0]
        if any(base_name in m for m in model_names):
            _ollama_model_ready = True
            log.info("Model %s found — warming up (loading into GPU memory)...", MATCHER_MODEL)
            try:
                ollama_client.chat(
                    model=MATCHER_MODEL,
                    messages=[{"role": "user", "content": "hello"}],
                    keep_alive=-1,
                )
                log.info("Model %s warm — loaded in GPU memory (keep_alive=-1)", MATCHER_MODEL)
            except Exception as e:
                log.warning("Model warm-up failed: %s — first match call will be slower", e)
            return True
        log.warning("Model %s not found. Run: ollama pull %s", MATCHER_MODEL, MATCHER_MODEL)
        return False
    except Exception:
        return False


@atexit.register
def _cleanup_ollama():
    """Stop Ollama server if we started it."""
    if _ollama_process and _ollama_process.poll() is None:
        log.info("Stopping Ollama server (PID: %d)...", _ollama_process.pid)
        _ollama_process.terminate()

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
    result = {"status": "ok", "cookie_status": _cookie_status}

    # yt-dlp
    try:
        proc = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        result["ytdlp_version"] = proc.stdout.strip()
        result["ytdlp_ok"] = True
    except FileNotFoundError:
        result["ytdlp_ok"] = False
    except Exception:
        result["ytdlp_ok"] = False

    # ffmpeg
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=5,
        )
        first_line = proc.stdout.split("\n")[0] if proc.stdout else ""
        result["ffmpeg_version"] = first_line.split(" ")[2] if "version" in first_line else first_line[:40]
        result["ffmpeg_ok"] = True
    except FileNotFoundError:
        result["ffmpeg_ok"] = False
    except Exception:
        result["ffmpeg_ok"] = False

    # Whisper
    try:
        import whisper as _whisper_check  # noqa: F811
        result["whisper_ok"] = True
    except ImportError:
        result["whisper_ok"] = False

    # Ollama + model
    result["ollama_available"] = _ollama_available
    result["ollama_server"] = "running" if _ollama_server_ready else "not running"
    result["matcher_model"] = MATCHER_MODEL
    result["model_loaded"] = _ollama_model_ready

    return jsonify(result)


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


@app.route("/abort_task", methods=["POST"])
def abort_task():
    """Signal an in-flight task (e.g. Whisper) to stop at the next checkpoint."""
    data = request.json or {}
    tid = data.get("task_id")
    if not tid:
        return jsonify({"error": "task_id required"}), 400
    tid = str(tid)
    with _abort_lock:
        ev = _task_aborts.get(tid)
    if ev:
        ev.set()
        log.info("Abort requested for task %s", tid[:8])
    return jsonify({"ok": True})


@app.route("/execute", methods=["POST"])
def execute():
    task = request.json or {}
    task_type = task.get("task_type")
    payload = task.get("payload", {})
    task_id = task.get("task_id")

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
        ev = _register_abort(str(task_id)) if task_id else None
        try:
            results = whisper_transcribe(
                payload["video_id"],
                payload.get("max_duration_min", 60),
                abort_event=ev,
            )
        finally:
            if task_id:
                _unregister_abort(str(task_id))
    elif task_type == "match_timestamp":
        result = ollama_match_timestamp(payload)
        return jsonify({"results": [result]})
    elif task_type == "lightweight_llm":
        result = ollama_lightweight_llm(payload)
        return jsonify({"results": [result]})
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


def whisper_transcribe(
    video_id: str,
    max_duration_min: int = 60,
    abort_event: threading.Event | None = None,
) -> list[dict]:
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

        if abort_event and abort_event.is_set():
            log.info("Whisper: aborted before transcribe for %s", video_id)
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


def ollama_match_timestamp(payload: dict) -> dict:
    """Run timestamp matching locally using Ollama."""
    if not _ollama_available or not _ollama_server_ready or not _ollama_model_ready:
        return {
            "start_time_seconds": 0, "end_time_seconds": 0,
            "excerpt": "", "confidence_score": 0.0,
            "relevance_note": "Local model not available",
            "the_hook": "", "matcher_source": "local_unavailable",
        }

    model = payload.get("model", MATCHER_MODEL)
    prompt = payload.get("prompt", "")

    schema = {
        "type": "object",
        "properties": {
            "start_time_seconds": {"type": "integer"},
            "end_time_seconds": {"type": "integer"},
            "excerpt": {"type": "string"},
            "confidence_score": {"type": "number"},
            "relevance_note": {"type": "string"},
            "the_hook": {"type": "string"},
        },
        "required": [
            "start_time_seconds", "end_time_seconds",
            "excerpt", "confidence_score",
            "relevance_note", "the_hook",
        ],
    }

    start_t = time.time()
    try:
        response = ollama_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": "You are the Viral B-Roll Extractor. Return JSON only. /nothink"},
                {"role": "user", "content": prompt},
            ],
            format=schema,
            options={
                "temperature": 0,
                "num_ctx": 32768,
                "num_predict": 512,
            },
            keep_alive=-1,
        )
        elapsed_ms = int((time.time() - start_t) * 1000)
        result = json.loads(response["message"]["content"])
        result["matcher_source"] = "local"
        result["matcher_model"] = model
        result["matcher_latency_ms"] = elapsed_ms
        prompt_words = len(prompt.split())
        log.info("Local match done in %dms (model=%s, confidence=%.2f, prompt=%d words)",
                 elapsed_ms, model, result.get("confidence_score", 0), prompt_words)
        return result
    except Exception as e:
        elapsed_ms = int((time.time() - start_t) * 1000)
        log.error("Ollama match failed after %dms: %s", elapsed_ms, e)
        return {
            "start_time_seconds": 0, "end_time_seconds": 0,
            "excerpt": "", "confidence_score": 0.0,
            "relevance_note": f"Local model error: {str(e)[:100]}",
            "the_hook": "", "matcher_source": "local_error",
        }


def ollama_lightweight_llm(payload: dict) -> dict:
    """Run a lightweight JSON-returning prompt through Ollama (for query generation, shot ideation, etc.)."""
    if not _ollama_available or not _ollama_server_ready or not _ollama_model_ready:
        return {"error": "local_unavailable", "result": None}

    model = payload.get("model", MATCHER_MODEL)
    prompt = payload.get("prompt", "")
    system_prompt = payload.get("system_prompt", "Return valid JSON only. /nothink")

    start_t = time.time()
    try:
        response = ollama_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            format="json",
            options={
                "temperature": 0.7,
                "num_ctx": 4096,
                "num_predict": 1024,
            },
            keep_alive=-1,
        )
        elapsed_ms = int((time.time() - start_t) * 1000)
        raw = response["message"]["content"]
        parsed = json.loads(raw)
        log.info("Lightweight LLM done in %dms (model=%s, output_keys=%s)",
                 elapsed_ms, model, list(parsed.keys()) if isinstance(parsed, dict) else "non-dict")
        return {"error": None, "result": parsed, "elapsed_ms": elapsed_ms}
    except Exception as e:
        elapsed_ms = int((time.time() - start_t) * 1000)
        log.error("Lightweight LLM failed after %dms: %s", elapsed_ms, e)
        return {"error": str(e)[:200], "result": None, "elapsed_ms": elapsed_ms}


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

    log.info("Matcher model: %s", MATCHER_MODEL)
    ollama_ok = ensure_ollama_running()
    if ollama_ok:
        model_ok = ensure_model_loaded()
        if model_ok:
            log.info("Local LLM ready — timestamp matching will use %s ($0 cost)", MATCHER_MODEL)
        else:
            log.warning("Model not pulled — run: ollama pull %s", MATCHER_MODEL)
            log.warning("Timestamp matching will fall back to GPT-4o-mini via API")
    else:
        log.warning("Ollama not available — timestamp matching will use GPT-4o-mini via API")

    log.info("CORS allowed origins: %s", _default_cors)
    log.info("Keep this running while your B-Roll Scout tab is open in the browser")
    app.run(host="127.0.0.1", port=port, threaded=True)
