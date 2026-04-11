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
import glob as globmod_top
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
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

# ---------------------------------------------------------------------------
# YouTube request throttle — limits yt-dlp calls to avoid bot detection.
# Token-bucket: allows short bursts but enforces avg rate over time.
# ---------------------------------------------------------------------------
import random as _random

_YT_RATE_LIMIT = float(os.environ.get("BROLL_YT_RATE_LIMIT", "2.5"))  # req/sec avg
_YT_BURST_SIZE = int(os.environ.get("BROLL_YT_BURST_SIZE", "4"))       # max burst
_yt_tokens = float(_YT_BURST_SIZE)
_yt_last_refill = time.time()
_yt_lock = threading.Lock()


def _yt_throttle() -> None:
    """Block until a token is available. Adds small jitter to look human."""
    global _yt_tokens, _yt_last_refill
    while True:
        with _yt_lock:
            now = time.time()
            elapsed = now - _yt_last_refill
            _yt_tokens = min(_YT_BURST_SIZE, _yt_tokens + elapsed * _YT_RATE_LIMIT)
            _yt_last_refill = now
            if _yt_tokens >= 1.0:
                _yt_tokens -= 1.0
                break
        time.sleep(0.1 + _random.uniform(0.05, 0.2))

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
MATCHER_MODELS = {
    "qwen3:8b": {
        "ollama_name": "qwen3:8b",
        "display_name": "Qwen3 8B (Fast, Default)",
        "num_ctx": 32768,
        "num_predict": 512,
        "min_vram_gb": 6,
        "pull_size_gb": 5,
    },
    "gemma4:26b": {
        "ollama_name": "gemma4:26b",
        "display_name": "Gemma 4 26B MoE (Quality)",
        "num_ctx": 262144,
        "num_predict": 1024,
        "min_vram_gb": 18,
        "pull_size_gb": 18,
        "min_ollama_version": "0.20.0",
    },
    "gemma4:e4b": {
        "ollama_name": "gemma4:e4b",
        "display_name": "Gemma 4 E4B (Balanced)",
        "num_ctx": 131072,
        "num_predict": 1024,
        "min_vram_gb": 10,
        "pull_size_gb": 9.6,
        "min_ollama_version": "0.20.0",
    },
    "qwen3:4b": {
        "ollama_name": "qwen3:4b",
        "display_name": "Qwen3 4B (Faster, Less Accurate)",
        "num_ctx": 32768,
        "num_predict": 512,
        "min_vram_gb": 3,
        "pull_size_gb": 2.5,
    },
    "llama3.3:8b": {
        "ollama_name": "llama3.3:8b",
        "display_name": "Llama 3.3 8B",
        "num_ctx": 32768,
        "num_predict": 512,
        "min_vram_gb": 5,
        "pull_size_gb": 4.7,
    },
}

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


def _is_model_installed(model_key: str, installed_names: list[str]) -> bool:
    """Check if a model from MATCHER_MODELS is installed by matching its base name prefix."""
    base_name = model_key.split(":")[0]
    return any(base_name in m for m in installed_names)


def ensure_model_loaded() -> bool:
    """Check if the current MATCHER_MODEL is pulled locally, then warm it up."""
    global _ollama_model_ready
    if not _ollama_available or not _ollama_server_ready:
        return False
    try:
        models = ollama_client.list()
        model_names = [m.model for m in models.models]
        if _is_model_installed(MATCHER_MODEL, model_names):
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


_cleanup_done = False

def _unload_model(model_name: str) -> bool:
    """Unload a single model from GPU via Ollama REST API (no Python client needed)."""
    import urllib.request
    try:
        body = json.dumps({
            "model": model_name,
            "keep_alive": 0,
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        log.info("Unloaded %s from GPU memory", model_name)
        return True
    except Exception as e:
        log.warning("Failed to unload %s: %s", model_name, e)
        return False


def _cleanup():
    """Unload ALL loaded models from GPU memory and stop Ollama if we started it."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    if _ollama_available:
        import urllib.request

        # Query what's actually loaded in GPU
        loaded_models: list[str] = []
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as resp:
                ps_data = json.loads(resp.read())
                loaded_models = [m["name"] for m in ps_data.get("models", [])]
        except Exception as e:
            log.debug("Could not query /api/ps: %s", e)

        # Also include active model + all installed models from registry
        must_unload = set(loaded_models)
        must_unload.add(MATCHER_MODEL)
        installed_names: list[str] = []
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
                tags_data = json.loads(resp.read())
                installed_names = [m["name"] for m in tags_data.get("models", [])]
        except Exception:
            pass
        for cfg in MATCHER_MODELS.values():
            name = cfg["ollama_name"]
            if any(name in iname for iname in installed_names):
                must_unload.add(name)

        log.info("Cleanup: unloading models from GPU: %s", ", ".join(sorted(must_unload)))
        for model_name in must_unload:
            _unload_model(model_name)

    # Stop Ollama server if we started it
    if _ollama_process and _ollama_process.poll() is None:
        log.info("Stopping Ollama server (PID: %d)...", _ollama_process.pid)
        _ollama_process.terminate()
        try:
            _ollama_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _ollama_process.kill()

    # Clean up cookie jar temp directory
    if _cookie_jar_dir:
        try:
            _cookie_jar_dir.cleanup()
        except Exception:
            pass

    log.info("Companion cleanup complete.")

atexit.register(_cleanup)

def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM — clean up and exit."""
    sig_name = signal.Signals(signum).name
    log.info("Received %s — shutting down...", sig_name)
    _cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ---------------------------------------------------------------------------
# Browser cookie configuration
# ---------------------------------------------------------------------------
COOKIE_BROWSER = os.environ.get("BROLL_COOKIE_BROWSER", "none")
_cookie_args: list[str] = []
_cookie_status: str = "untested"
_cookie_jar_path: str | None = None
_cookie_jar_dir: tempfile.TemporaryDirectory | None = None


def _detect_cookie_support() -> None:
    """Test if yt-dlp can read cookies from the configured browser and export a cookie jar."""
    global _cookie_args, _cookie_status, _cookie_jar_path, _cookie_jar_dir

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
            _export_cookie_jar()
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


def _export_cookie_jar() -> None:
    """Export browser cookies to a Netscape cookie jar file via yt-dlp.

    This jar is used by fetch_transcript's yt-dlp fallback so that
    subtitle requests are authenticated (avoids YouTube bot detection).
    """
    global _cookie_jar_path, _cookie_jar_dir
    try:
        _cookie_jar_dir = tempfile.TemporaryDirectory(prefix="broll_cookies_")
        jar_path = os.path.join(_cookie_jar_dir.name, "cookies.txt")
        proc = subprocess.run(
            ["yt-dlp", "--cookies-from-browser", COOKIE_BROWSER,
             "--cookies", jar_path,
             "--skip-download", "--no-warnings",
             "--flat-playlist", "--playlist-end", "1",
             "ytsearch1:test"],
            capture_output=True, text=True, timeout=30,
        )
        if os.path.isfile(jar_path) and os.path.getsize(jar_path) > 100:
            _cookie_jar_path = jar_path
            log.info("Cookie jar exported to %s (%d bytes)", jar_path, os.path.getsize(jar_path))
        else:
            log.warning("Cookie jar export produced no usable file")
    except Exception as e:
        log.warning("Cookie jar export failed: %s — yt-dlp fallback will use --cookies-from-browser directly", e)


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
    result["matcher_models"] = list(MATCHER_MODELS.keys())

    return jsonify(result)


@app.route("/models/status")
def models_status():
    """Return installation/readiness status for all registered matcher models."""
    ollama_running = False
    installed_names: list[str] = []
    ollama_version = "unknown"

    try:
        proc = subprocess.run(
            ["ollama", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        import re as _re
        _ver_match = _re.search(r'(\d+\.\d+\.\d+)', proc.stdout)
        ollama_version = _ver_match.group(1) if _ver_match else proc.stdout.strip()
    except Exception:
        pass

    try:
        models = ollama_client.list()
        installed_names = [m.model for m in models.models]
        ollama_running = True
    except Exception:
        pass

    def _version_tuple(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except (ValueError, AttributeError):
            return (0, 0, 0)

    model_statuses = {}
    for key, cfg in MATCHER_MODELS.items():
        installed = _is_model_installed(key, installed_names)
        min_ver = cfg.get("min_ollama_version")
        ollama_outdated = (
            min_ver is not None
            and ollama_version != "unknown"
            and _version_tuple(ollama_version) < _version_tuple(min_ver)
        )
        model_statuses[key] = {
            "installed": installed,
            "ready": installed and ollama_running,
            "display_name": cfg["display_name"],
            "min_vram_gb": cfg["min_vram_gb"],
            "pull_size_gb": cfg["pull_size_gb"],
            "ollama_outdated": ollama_outdated,
            "min_ollama_version": min_ver,
        }

    return jsonify({
        "ollama_version": ollama_version,
        "ollama_running": ollama_running,
        "active_model": MATCHER_MODEL,
        "models": model_statuses,
    })


@app.route("/models/pull", methods=["POST"])
def models_pull():
    """Pull (download) a model from the MATCHER_MODELS registry via Ollama."""
    data = request.json or {}
    model_key = data.get("model", "")

    if model_key not in MATCHER_MODELS:
        return jsonify({
            "error": f"Unknown model: {model_key}. Available: {list(MATCHER_MODELS.keys())}",
        }), 400

    model_name = MATCHER_MODELS[model_key]["ollama_name"]
    log.info("Pulling model %s — this may take a while...", model_name)

    try:
        proc = subprocess.run(
            ["ollama", "pull", model_name],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode == 0:
            log.info("Model %s pulled successfully", model_name)
            return jsonify({"status": "ok", "model": model_key})
        else:
            stderr = proc.stderr[:500]
            log.warning("Model pull failed for %s: %s", model_name, stderr)
            if "412" in stderr or "newer version" in stderr.lower():
                return jsonify({
                    "error": "Ollama is outdated. Update it at https://ollama.com/download then retry.",
                    "update_required": True,
                }), 500
            return jsonify({"error": stderr or "Pull failed"}), 500
    except subprocess.TimeoutExpired:
        log.warning("Model pull timed out for %s (30 min limit)", model_name)
        return jsonify({"error": "Pull timed out (30 min limit)"}), 500
    except FileNotFoundError:
        return jsonify({"error": "ollama binary not found"}), 500
    except Exception as e:
        log.error("Model pull error for %s: %s", model_name, e)
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/models/switch", methods=["POST"])
def models_switch():
    """Switch the active matcher model. Unloads old model, warms new one."""
    global MATCHER_MODEL, _ollama_model_ready
    data = request.json or {}
    new_model = data.get("model", "")

    if new_model not in MATCHER_MODELS:
        return jsonify({
            "error": f"Unknown model: {new_model}. Available: {list(MATCHER_MODELS.keys())}",
        }), 400

    try:
        models = ollama_client.list()
        installed_names = [m.model for m in models.models]
    except Exception:
        return jsonify({"error": "Cannot reach Ollama server"}), 503

    if not _is_model_installed(new_model, installed_names):
        return jsonify({
            "error": f"Model {new_model} is not installed. Pull it first via /models/pull.",
        }), 400

    old_model = MATCHER_MODEL
    MATCHER_MODEL = new_model

    try:
        ollama_client.chat(
            model=old_model,
            messages=[{"role": "user", "content": ""}],
            keep_alive=0,
        )
        log.info("Unloaded previous model %s from memory", old_model)
    except Exception:
        log.debug("Could not unload previous model %s (may already be unloaded)", old_model)

    try:
        ollama_client.chat(
            model=new_model,
            messages=[{"role": "user", "content": "hello"}],
            keep_alive=-1,
        )
        _ollama_model_ready = True
        log.info("Switched to model %s and warmed up", new_model)
    except Exception as e:
        _ollama_model_ready = True
        log.warning("Model %s switched but warm-up failed: %s — first call will be slower", new_model, e)

    return jsonify({"status": "ok", "active_model": MATCHER_MODEL})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """View or change cookie browser at runtime."""
    global COOKIE_BROWSER, _cookie_args, _cookie_status, _cookie_jar_path, _cookie_jar_dir

    if request.method == "POST":
        body = request.json or {}
        new_browser = body.get("cookie_browser", "").strip()
        if new_browser:
            COOKIE_BROWSER = new_browser  # noqa: F841 — intentional global reassign
            _cookie_args = []
            _cookie_status = "untested"
            if _cookie_jar_dir:
                _cookie_jar_dir.cleanup()
                _cookie_jar_dir = None
            _cookie_jar_path = None
            _detect_cookie_support()
        return jsonify({"cookie_browser": COOKIE_BROWSER, "cookie_status": _cookie_status})

    return jsonify({
        "cookie_browser": COOKIE_BROWSER,
        "cookie_status": _cookie_status,
        "cookie_jar_active": _cookie_jar_path is not None,
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
    if not video_ids:
        return []
    # Batch: pass all URLs in a single yt-dlp invocation
    urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]
    cmd = ["yt-dlp", *urls, "--dump-json", "--no-download", "--no-warnings"]
    results = _run_ytdlp(cmd, timeout=max(YTDLP_TIMEOUT, len(video_ids) * 8))
    return results


def _run_ytdlp(cmd: list[str], timeout: int | None = None, throttle: bool = True) -> list[dict]:
    if throttle:
        _yt_throttle()
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
    """Fetch transcript: try youtube-transcript-api first, fall back to yt-dlp with cookies."""
    languages = languages or ["en"]

    # --- Attempt 1: youtube-transcript-api (fast, no auth needed for most videos) ---
    try:
        result = _fetch_transcript_api(video_id, languages)
        if result and result[0].get("transcript"):
            return result
        log.info("youtube-transcript-api returned no transcript for %s — trying yt-dlp fallback", video_id)
    except Exception as e:
        log.info("youtube-transcript-api failed for %s: %s — trying yt-dlp fallback", video_id, e)

    # --- Attempt 2: yt-dlp with cookies (authenticated, bypasses bot detection) ---
    if _cookie_args:
        try:
            result = _fetch_transcript_ytdlp(video_id, languages)
            if result and result[0].get("transcript"):
                return result
        except Exception as e:
            log.warning("yt-dlp subtitle fallback failed for %s: %s", video_id, e)

    return [{"video_id": video_id, "transcript": None, "source": "no_transcript"}]


def _fetch_transcript_api(video_id: str, languages: list[str]) -> list[dict]:
    """Primary transcript fetcher using youtube-transcript-api."""
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

    return [{"video_id": video_id, "transcript": _format_entries(entries), "source": source}]


def _fetch_transcript_ytdlp(video_id: str, languages: list[str]) -> list[dict]:
    """Fallback transcript fetcher using yt-dlp with browser cookies.

    Uses your authenticated YouTube session so requests aren't treated as bot traffic.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    lang_str = ",".join(languages)

    with tempfile.TemporaryDirectory(prefix="broll_subs_") as tmpdir:
        sub_file_base = os.path.join(tmpdir, "subs")
        cmd = [
            "yt-dlp", *_cookie_args, url,
            "--write-subs", "--write-auto-subs",
            "--sub-langs", lang_str,
            "--sub-format", "json3",
            "--skip-download",
            "--no-playlist", "--no-warnings",
            "-o", sub_file_base,
        ]

        _yt_throttle()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            log.warning("yt-dlp subtitle download failed for %s (rc=%d): %s",
                        video_id, proc.returncode, proc.stderr[:200].strip())
            cmd_vtt = [
                "yt-dlp", *_cookie_args, url,
                "--write-subs", "--write-auto-subs",
                "--sub-langs", lang_str,
                "--sub-format", "vtt",
                "--skip-download",
                "--no-playlist", "--no-warnings",
                "-o", sub_file_base,
            ]
            _yt_throttle()
            proc = subprocess.run(cmd_vtt, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                return [{"video_id": video_id, "transcript": None, "source": "no_transcript"}]

        sub_files = globmod_top.glob(os.path.join(tmpdir, "subs*"))
        if not sub_files:
            return [{"video_id": video_id, "transcript": None, "source": "no_transcript"}]

        sub_path = sub_files[0]
        source = "youtube_auto_captions" if ".auto." in os.path.basename(sub_path) else "youtube_captions"
        source += "_ytdlp"

        if sub_path.endswith(".json3"):
            return _parse_json3_subs(video_id, sub_path, source)
        else:
            return _parse_vtt_subs(video_id, sub_path, source)


def _parse_json3_subs(video_id: str, path: str, source: str) -> list[dict]:
    """Parse YouTube json3 subtitle format into our timestamped transcript format."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events", [])
    lines = []
    for ev in events:
        start_ms = ev.get("tStartMs", 0)
        duration_ms = ev.get("dDurationMs", 0)
        segs = ev.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs).strip()
        text = text.replace("\n", " ")
        if not text:
            continue
        start = start_ms // 1000
        end = (start_ms + duration_ms) // 1000
        s_min, s_sec = start // 60, start % 60
        e_min, e_sec = end // 60, end % 60
        lines.append(f"[{s_min}:{s_sec:02d} → {e_min}:{e_sec:02d}] {text}")

    transcript = "\n".join(lines)
    if transcript:
        log.info("yt-dlp json3 subtitles: %d lines for %s", len(lines), video_id)
    return [{"video_id": video_id, "transcript": transcript or None, "source": source}]


def _parse_vtt_subs(video_id: str, path: str, source: str) -> list[dict]:
    """Parse WebVTT subtitle format into our timestamped transcript format."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    timestamp_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})\.\d+ --> (\d{2}):(\d{2}):(\d{2})\.\d+"
    )
    lines = []
    current_start = current_end = None
    text_buf: list[str] = []

    for line in content.split("\n"):
        m = timestamp_re.match(line.strip())
        if m:
            if text_buf and current_start is not None:
                text = " ".join(text_buf).strip()
                if text and text not in ("", "&nbsp;"):
                    lines.append(f"[{current_start // 60}:{current_start % 60:02d} → {current_end // 60}:{current_end % 60:02d}] {text}")
                text_buf = []
            h1, m1, s1, h2, m2, s2 = (int(x) for x in m.groups())
            current_start = h1 * 3600 + m1 * 60 + s1
            current_end = h2 * 3600 + m2 * 60 + s2
        elif line.strip() and not line.strip().isdigit() and "WEBVTT" not in line:
            clean = re.sub(r"<[^>]+>", "", line.strip())
            if clean:
                text_buf.append(clean)

    if text_buf and current_start is not None:
        text = " ".join(text_buf).strip()
        if text:
            lines.append(f"[{current_start // 60}:{current_start % 60:02d} → {current_end // 60}:{current_end % 60:02d}] {text}")

    seen = set()
    deduped = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)

    transcript = "\n".join(deduped)
    if transcript:
        log.info("yt-dlp vtt subtitles: %d lines for %s", len(deduped), video_id)
    return [{"video_id": video_id, "transcript": transcript or None, "source": source}]


def _format_entries(entries: list[dict]) -> str:
    """Format raw transcript entries into timestamped text."""
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
    return "\n".join(lines)


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
            _yt_throttle()
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

    model_config = MATCHER_MODELS.get(model, {})
    num_ctx = model_config.get("num_ctx", 32768)
    num_predict = model_config.get("num_predict", 512)

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

    messages = [
        {"role": "system", "content": "You are the Viral B-Roll Extractor. Return JSON only. /nothink"},
        {"role": "user", "content": prompt},
    ]
    opts = {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict}
    prompt_words = len(prompt.split())

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        start_t = time.time()
        try:
            response = ollama_client.chat(
                model=model, messages=messages, format=schema,
                options=opts, keep_alive=-1,
            )
            elapsed_ms = int((time.time() - start_t) * 1000)
            content = response.get("message", {}).get("content", "").strip()

            if not content:
                log.warning("Ollama returned empty response in %dms (model=%s, attempt=%d/%d, prompt=%d words)",
                            elapsed_ms, model, attempt, max_attempts, prompt_words)
                if attempt < max_attempts:
                    continue
                return {
                    "start_time_seconds": 0, "end_time_seconds": 0,
                    "excerpt": "", "confidence_score": 0.0,
                    "relevance_note": "Model returned empty response after retries",
                    "the_hook": "", "matcher_source": "local_empty",
                }

            result = json.loads(content)
            result["matcher_source"] = "local"
            result["matcher_model"] = model
            result["matcher_latency_ms"] = elapsed_ms
            log.info("Local match done in %dms (model=%s, confidence=%.2f, prompt=%d words%s)",
                     elapsed_ms, model, result.get("confidence_score", 0), prompt_words,
                     f", retry={attempt}" if attempt > 1 else "")
            return result

        except json.JSONDecodeError as e:
            elapsed_ms = int((time.time() - start_t) * 1000)
            raw = response.get("message", {}).get("content", "")[:200] if 'response' in dir() else ""
            log.warning("Ollama returned invalid JSON in %dms (model=%s, attempt=%d/%d): %s | raw: %s",
                        elapsed_ms, model, attempt, max_attempts, e, raw)
            if attempt < max_attempts:
                continue
            return {
                "start_time_seconds": 0, "end_time_seconds": 0,
                "excerpt": "", "confidence_score": 0.0,
                "relevance_note": f"Model returned invalid JSON: {str(e)[:80]}",
                "the_hook": "", "matcher_source": "local_error",
            }

        except Exception as e:
            elapsed_ms = int((time.time() - start_t) * 1000)
            log.error("Ollama match failed after %dms (model=%s, attempt=%d/%d): %s",
                      elapsed_ms, model, attempt, max_attempts, e)
            if attempt < max_attempts:
                continue
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
        "--concurrent-fragments", "4",
        "--no-playlist", "--no-warnings",
        "-o", output_path,
    ]

    log.info("Clip download: %s [%s – %s]", video_id, start_ts, end_ts)
    _yt_throttle()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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
        return {"status": "error", "message": "Download timed out (5 min limit)"}
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
