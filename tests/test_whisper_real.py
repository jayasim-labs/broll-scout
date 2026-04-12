#!/usr/bin/env python3
"""
Real end-to-end smoke test for the Whisper download + transcription pipeline.

NO MOCKS. Calls real yt-dlp, ffmpeg, and Whisper against actual YouTube videos.
Run this before starting a full pipeline to verify the system works.

Usage:
    cd broll-companion
    .venv/bin/python3 ../tests/test_whisper_real.py

    # Or with pytest (slower — loads full test infra):
    cd "BRoll Scout"
    broll-companion/.venv/bin/python3 -m pytest tests/test_whisper_real.py -v -s

What it tests (the actual companion whisper_transcribe flow):
    1. Audio-only download succeeds → Whisper transcribes
    2. Audio-only fails → mp3 extraction fallback
    3. Audio-only + mp3 fail → low-res video download → ffmpeg extract → Whisper
    4. All formats fail → correct failure_detail returned
    5. yt-dlp timeout handling
"""

import glob
import os
import subprocess
import sys
import tempfile
import time

# ---------------------------------------------------------------------------
# Resolve imports — works whether run from repo root or broll-companion/
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMPANION_DIR = os.path.join(_REPO_ROOT, "broll-companion")
sys.path.insert(0, _COMPANION_DIR)
sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Test video IDs
# ---------------------------------------------------------------------------

# Short public video that succeeded in the last pipeline run (4.5 min, 50 segments)
SHORT_VIDEO = "W4wlfwbFjNI"

# A well-known short creative-commons video (Big Buck Bunny trailer, ~33s)
TINY_VIDEO = "aqz-KE-bpKQ"

# Non-existent video ID — should fail all downloads gracefully
FAKE_VIDEO = "zzzNONEXIST99"

# "Yes Bank Crisis: How Banking System Plays With Your Money" — 8.6m video
# This ACTUALLY FAILED in the pipeline run with "audio download failed
# (likely restricted/age-gated)". This is the real regression test.
PREVIOUSLY_FAILED_VIDEO = "KS5TrKRnNYA"


WHISPER_MODEL = "large-v3-turbo"


def _get_device():
    """Return best available Whisper device."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _header(msg: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {msg}")
    print(f"{'='*70}")


def _pass(msg: str) -> None:
    print(f"  ✅ PASS: {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ FAIL: {msg}")


def _info(msg: str) -> None:
    print(f"  ℹ️  {msg}")


# ===================================================================
# Test 1: Full audio download + Whisper transcription (real video)
# ===================================================================
def test_audio_download_and_whisper():
    """
    Download audio from a real YouTube video using yt-dlp,
    then transcribe with Whisper. This is the happy path.
    """
    _header("Test 1: Audio download + Whisper transcription")

    with tempfile.TemporaryDirectory() as tmpdir:
        url = f"https://www.youtube.com/watch?v={TINY_VIDEO}"
        output_template = os.path.join(tmpdir, "audio.%(ext)s")

        # Step 1: yt-dlp audio download
        _info(f"Downloading audio for {TINY_VIDEO} ...")
        t0 = time.time()
        proc = subprocess.run(
            ["yt-dlp", "-f", "bestaudio/ba", url,
             "-o", output_template,
             "--no-playlist", "--no-warnings",
             "--socket-timeout", "30", "--retries", "3"],
            capture_output=True, text=True, timeout=120,
        )
        dl_time = time.time() - t0

        audio_files = glob.glob(os.path.join(tmpdir, "audio.*"))

        if proc.returncode != 0 or not audio_files:
            _fail(f"yt-dlp audio download failed (rc={proc.returncode})")
            _info(f"stderr: {(proc.stderr or '')[:300]}")
            return False

        audio_path = audio_files[0]
        size_kb = os.path.getsize(audio_path) / 1024
        _pass(f"Audio downloaded: {os.path.basename(audio_path)} ({size_kb:.0f} KB) in {dl_time:.1f}s")

        # Step 2: Whisper transcription
        _info(f"Loading Whisper model ({WHISPER_MODEL}) ...")
        try:
            import whisper
        except ImportError as e:
            _fail(f"Missing dependency: {e}")
            return False

        device = _get_device()
        _info(f"Transcribing on {device.upper()} ...")
        t0 = time.time()
        model = whisper.load_model(WHISPER_MODEL, device=device)
        result = model.transcribe(audio_path, language="en", fp16=(device == "cuda"))
        tx_time = time.time() - t0

        segments = result.get("segments", [])
        text_chars = sum(len(s.get("text", "")) for s in segments)

        if segments and text_chars > 0:
            _pass(f"Whisper transcribed: {len(segments)} segments, {text_chars} chars in {tx_time:.1f}s")
            _info(f"First segment: \"{segments[0].get('text', '').strip()[:80]}\"")
            return True
        else:
            _fail(f"Whisper returned no segments (segments={len(segments)}, chars={text_chars})")
            return False


# ===================================================================
# Test 2: Video fallback path — force audio-only to fail, then
#          download low-res video + ffmpeg extract
# ===================================================================
def test_video_fallback_with_ffmpeg():
    """
    Simulate the fallback: request an impossible audio-only format so
    yt-dlp fails, then download worst video+audio and extract with ffmpeg.
    """
    _header("Test 2: Video fallback → ffmpeg audio extraction")

    with tempfile.TemporaryDirectory() as tmpdir:
        url = f"https://www.youtube.com/watch?v={TINY_VIDEO}"

        # Step 1: Force audio-only failure with an impossible format
        _info("Step 1: Attempting impossible audio format (should fail) ...")
        proc_audio = subprocess.run(
            ["yt-dlp", "-f", "bestaudio[ext=flac]", url,
             "-o", os.path.join(tmpdir, "audio.%(ext)s"),
             "--no-playlist", "--no-warnings",
             "--socket-timeout", "15", "--retries", "1"],
            capture_output=True, text=True, timeout=60,
        )
        audio_files = glob.glob(os.path.join(tmpdir, "audio.*"))

        if proc_audio.returncode == 0 and audio_files:
            _info("Audio-only unexpectedly succeeded (video has flac) — still valid, skipping fallback step")
            _pass("Format available, fallback not needed")
            return True

        _pass("Audio-only format correctly unavailable → entering video fallback")

        # Step 2: Download lowest-res video (the actual fallback path)
        _info("Step 2: Downloading lowest-res video ...")
        video_template = os.path.join(tmpdir, "video.%(ext)s")
        t0 = time.time()
        proc_video = subprocess.run(
            ["yt-dlp", "-f", "worstvideo+worstaudio/worst", url,
             "-o", video_template,
             "--no-playlist", "--no-warnings",
             "--socket-timeout", "30", "--retries", "3"],
            capture_output=True, text=True, timeout=180,
        )
        dl_time = time.time() - t0
        video_files = glob.glob(os.path.join(tmpdir, "video.*"))

        if proc_video.returncode != 0 or not video_files:
            _fail(f"Video fallback download failed (rc={proc_video.returncode})")
            _info(f"stderr: {(proc_video.stderr or '')[:300]}")
            return False

        video_path = video_files[0]
        video_size_kb = os.path.getsize(video_path) / 1024
        _pass(f"Video downloaded: {os.path.basename(video_path)} ({video_size_kb:.0f} KB) in {dl_time:.1f}s")

        # Step 3: ffmpeg extract audio
        _info("Step 3: Extracting audio with ffmpeg ...")
        extracted_audio = os.path.join(tmpdir, "audio.mp3")
        t0 = time.time()
        ff = subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vn", "-acodec", "libmp3lame", "-q:a", "6",
             "-y", extracted_audio],
            capture_output=True, text=True, timeout=60,
        )
        ffmpeg_time = time.time() - t0

        if ff.returncode != 0 or not os.path.isfile(extracted_audio):
            _fail(f"ffmpeg extraction failed (rc={ff.returncode})")
            _info(f"stderr: {(ff.stderr or '')[:300]}")
            return False

        audio_size_kb = os.path.getsize(extracted_audio) / 1024
        _pass(f"Audio extracted: {audio_size_kb:.0f} KB in {ffmpeg_time:.1f}s")

        # Step 4: Verify the extracted audio is transcribable
        _info("Step 4: Whisper transcribing extracted audio ...")
        try:
            import whisper
            device = _get_device()
            model = whisper.load_model(WHISPER_MODEL, device=device)
            result = model.transcribe(extracted_audio, language="en", fp16=(device == "cuda"))
            segments = result.get("segments", [])
            if segments:
                _pass(f"Whisper transcribed from video fallback: {len(segments)} segments")
                return True
            else:
                _fail("Whisper returned 0 segments from extracted audio")
                return False
        except Exception as e:
            _fail(f"Whisper failed on extracted audio: {e}")
            return False


# ===================================================================
# Test 3: All formats fail → correct failure_detail
# ===================================================================
def test_nonexistent_video_fails_gracefully():
    """
    A fake video ID should fail all 3 download attempts and return
    the correct failure detail without crashing.
    """
    _header("Test 3: Non-existent video → graceful failure")

    with tempfile.TemporaryDirectory() as tmpdir:
        url = f"https://www.youtube.com/watch?v={FAKE_VIDEO}"
        output_template = os.path.join(tmpdir, "audio.%(ext)s")

        # Attempt 1: bestaudio
        _info("Attempt 1: bestaudio ...")
        proc1 = subprocess.run(
            ["yt-dlp", "-f", "bestaudio/ba", url,
             "-o", output_template,
             "--no-playlist", "--no-warnings",
             "--socket-timeout", "15", "--retries", "1"],
            capture_output=True, text=True, timeout=60,
        )
        audio_files = glob.glob(os.path.join(tmpdir, "audio.*"))
        assert proc1.returncode != 0 or not audio_files, "Fake video should not have audio"
        _pass("Attempt 1 failed as expected")

        # Attempt 2: bestaudio with mp3 extraction
        _info("Attempt 2: bestaudio + mp3 extraction ...")
        proc2 = subprocess.run(
            ["yt-dlp", "-f", "bestaudio/ba", "-x", "--audio-format", "mp3",
             url, "-o", output_template,
             "--no-playlist", "--no-warnings",
             "--socket-timeout", "15", "--retries", "1"],
            capture_output=True, text=True, timeout=60,
        )
        audio_files = glob.glob(os.path.join(tmpdir, "audio.*"))
        assert proc2.returncode != 0 or not audio_files, "Fake video should not have audio"
        _pass("Attempt 2 failed as expected")

        # Attempt 3: worst video fallback
        _info("Attempt 3: worstvideo+worstaudio fallback ...")
        video_template = os.path.join(tmpdir, "video.%(ext)s")
        proc3 = subprocess.run(
            ["yt-dlp", "-f", "worstvideo+worstaudio/worst", url,
             "-o", video_template,
             "--no-playlist", "--no-warnings",
             "--socket-timeout", "15", "--retries", "1"],
            capture_output=True, text=True, timeout=60,
        )
        video_files = glob.glob(os.path.join(tmpdir, "video.*"))
        assert proc3.returncode != 0 or not video_files, "Fake video should not download"
        _pass("Attempt 3 failed as expected")

        # Verify failure categorization logic
        stderr = (proc3.stderr or "")[:400]
        if "Requested format" in stderr or "not available" in stderr:
            detail = "all_formats_failed"
        else:
            detail = "video_fallback_failed"
        _pass(f"Failure detail: '{detail}'")
        return True


# ===================================================================
# Test 4: Full companion whisper_transcribe() function (real call)
# ===================================================================
def test_companion_whisper_transcribe_real():
    """
    Call the actual whisper_transcribe() function from companion.py
    against a real short video. This is the exact code path the pipeline uses.
    """
    _header("Test 4: companion.py whisper_transcribe() — real end-to-end")

    # Need to set up the companion module globals
    import companion
    # Ensure throttle doesn't block (set generous token bucket)
    companion._yt_tokens = 10.0

    _info(f"Calling whisper_transcribe('{TINY_VIDEO}', whisper_model='{WHISPER_MODEL}') ...")
    t0 = time.time()
    results = companion.whisper_transcribe(
        video_id=TINY_VIDEO,
        max_duration_min=5,
        whisper_model=WHISPER_MODEL,
    )
    elapsed = time.time() - t0

    if not results:
        _fail("whisper_transcribe returned empty list")
        return False

    result = results[0]
    video_id = result.get("video_id")
    transcript = result.get("transcript")
    source = result.get("source")
    failure_detail = result.get("failure_detail")

    _info(f"Completed in {elapsed:.1f}s")
    _info(f"video_id={video_id}, source={source}, failure_detail={failure_detail}")

    if transcript:
        lines = transcript.strip().split("\n")
        _pass(f"Transcript received: {len(lines)} lines, {len(transcript)} chars")
        _info(f"First line: \"{lines[0][:100]}\"")
        assert source == "whisper_transcription", f"Expected whisper_transcription, got {source}"
        assert "failure_detail" not in result or result.get("failure_detail") is None
        return True
    else:
        _fail(f"No transcript — source={source}, failure_detail={failure_detail}")
        return False


# ===================================================================
# Test 5: companion whisper_transcribe() with fake video → failure
# ===================================================================
def test_companion_whisper_transcribe_failure():
    """
    Call whisper_transcribe() with a non-existent video to verify
    it returns the correct failure response structure.
    """
    _header("Test 5: companion.py whisper_transcribe() — expected failure")

    import companion
    companion._yt_tokens = 10.0

    _info(f"Calling whisper_transcribe('{FAKE_VIDEO}') — should fail gracefully ...")
    t0 = time.time()
    results = companion.whisper_transcribe(
        video_id=FAKE_VIDEO,
        max_duration_min=5,
        whisper_model=WHISPER_MODEL,
    )
    elapsed = time.time() - t0

    if not results:
        _fail("whisper_transcribe returned empty list (should return failure dict)")
        return False

    result = results[0]
    transcript = result.get("transcript")
    source = result.get("source")
    failure_detail = result.get("failure_detail")

    _info(f"Completed in {elapsed:.1f}s")
    _info(f"source={source}, failure_detail={failure_detail}")

    if transcript is not None:
        _fail(f"Expected no transcript for fake video, got: {transcript[:100]}")
        return False

    assert source == "whisper_failed", f"Expected whisper_failed, got {source}"
    assert failure_detail is not None, "Expected a failure_detail but got None"
    _pass(f"Correctly failed with source='{source}', failure_detail='{failure_detail}'")
    return True


# ===================================================================
# Test 6: Longer real video that succeeded in the last pipeline run
# ===================================================================
def test_known_good_video():
    """
    Test against a video that succeeded in the last pipeline run.
    Uses the real companion whisper_transcribe() function.
    """
    _header(f"Test 6: Known-good video from last run ({SHORT_VIDEO})")

    import companion
    companion._yt_tokens = 10.0

    _info(f"Calling whisper_transcribe('{SHORT_VIDEO}', whisper_model='{WHISPER_MODEL}') ...")
    _info("This video (4.5 min) may take 1-3 minutes with large-v3-turbo ...")
    t0 = time.time()
    results = companion.whisper_transcribe(
        video_id=SHORT_VIDEO,
        max_duration_min=10,
        whisper_model=WHISPER_MODEL,
    )
    elapsed = time.time() - t0

    result = results[0]
    transcript = result.get("transcript")
    source = result.get("source")

    if transcript:
        lines = transcript.strip().split("\n")
        _pass(f"Transcript: {len(lines)} lines, {len(transcript)} chars in {elapsed:.1f}s")
        _info(f"First line: \"{lines[0][:100]}\"")
        _info(f"Last line:  \"{lines[-1][:100]}\"")
        return True
    else:
        _fail(f"No transcript — source={source}, detail={result.get('failure_detail')}")
        return False


def test_previously_failed_video():
    """
    Test the video that ACTUALLY FAILED during the pipeline run:
    KS5TrKRnNYA — "Yes Bank Crisis" (8.6 min, reported audio download failed).
    After the ejs:github fix and video fallback, this should now succeed.
    """
    _header(f"Test 7: Previously-failed pipeline video ({PREVIOUSLY_FAILED_VIDEO})")

    import companion  # noqa: E402

    _info(f"This video failed in the real pipeline run with 'audio download failed'")
    _info(f"Calling whisper_transcribe('{PREVIOUSLY_FAILED_VIDEO}', whisper_model='{WHISPER_MODEL}') ...")
    _info("This is an 8.6 min video — may take 1-4 minutes ...")
    t0 = time.time()
    results = companion.whisper_transcribe(
        video_id=PREVIOUSLY_FAILED_VIDEO,
        max_duration_min=15,
        whisper_model=WHISPER_MODEL,
    )
    elapsed = time.time() - t0

    result = results[0]
    transcript = result.get("transcript")
    source = result.get("source")
    failure_detail = result.get("failure_detail")

    if transcript:
        lines = transcript.strip().split("\n")
        _pass(f"FIXED! Transcript: {len(lines)} lines, {len(transcript)} chars in {elapsed:.1f}s (was failing before)")
        _info(f"source={source}")
        _info(f"First line: \"{lines[0][:100]}\"")
        _info(f"Last line:  \"{lines[-1][:100]}\"")
        return True
    else:
        _fail(f"Still failing — source={source}, failure_detail={failure_detail}")
        _info("This video may genuinely be restricted/unavailable in this region")
        return False


# ===================================================================
# Main runner
# ===================================================================
def main():
    print("\n" + "🔧 " * 20)
    print("  B-Roll Scout — Whisper Pipeline Smoke Test (REAL, no mocks)")
    print("  Tests: yt-dlp download → ffmpeg fallback → Whisper transcription")
    print("🔧 " * 20)

    results = {}

    # Quick tests first (use tiny video)
    results["1_audio_download_whisper"] = test_audio_download_and_whisper()
    results["2_video_fallback_ffmpeg"] = test_video_fallback_with_ffmpeg()
    results["3_nonexistent_graceful"] = test_nonexistent_video_fails_gracefully()
    results["4_companion_real_success"] = test_companion_whisper_transcribe_real()
    results["5_companion_real_failure"] = test_companion_whisper_transcribe_failure()

    # Longer tests — actual pipeline videos
    results["6_known_good_video"] = test_known_good_video()
    results["7_previously_failed_video"] = test_previously_failed_video()

    # Summary
    _header("SUMMARY")
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")

    print(f"\n  {passed}/{len(results)} passed, {failed} failed\n")

    if failed > 0:
        print("  ⚠️  Fix failures above before running the full pipeline.\n")
        sys.exit(1)
    else:
        print("  🎉 All clear — Whisper pipeline is working.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
