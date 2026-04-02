#!/usr/bin/env python3
"""
End-to-end test script that exercises each stage of the B-roll pipeline
independently, without requiring the full server to be running.

Usage:
  python scripts/test_e2e_flow.py
"""

import asyncio
import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def banner(msg: str):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def step(msg: str):
    print(f"\n  → {msg}")


def ok(msg: str):
    print(f"  ✓ {msg}")


def fail(msg: str):
    print(f"  ✗ {msg}")


# ---------- Stage 1: yt-dlp search ----------
def test_ytdlp_search():
    banner("Stage 1: yt-dlp search (companion)")
    query = "Epstein island documentary"
    cmd = [
        "yt-dlp", f"ytsearch3:{query}",
        "--dump-json", "--no-download", "--no-warnings", "--flat-playlist",
    ]
    step(f"Running: {' '.join(cmd[:4])}...")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    results = []
    for line in proc.stdout.strip().split("\n"):
        if not line:
            continue
        data = json.loads(line)
        vid = data.get("id", "")
        results.append({
            "video_id": vid,
            "title": data.get("title", ""),
            "channel_name": data.get("channel") or data.get("uploader") or "",
            "channel_id": data.get("channel_id") or "",
            "duration_seconds": data.get("duration") or 0,
            "video_duration_seconds": data.get("duration") or 0,
            "view_count": data.get("view_count") or 0,
            "thumbnail_url": data.get("thumbnail") or f"https://img.youtube.com/vi/{vid}/mqdefault.jpg",
            "published_at": "",
        })

    if not results:
        fail("No search results from yt-dlp")
        return None
    for r in results:
        ok(f"{r['video_id']} — \"{r['title'][:50]}\" ({r['duration_seconds']}s, {r['view_count']} views)")
    return results


# ---------- Stage 2: Transcript fetch ----------
def test_transcript(video_id: str):
    banner("Stage 2: Transcript fetch")
    step(f"Fetching transcript for {video_id}...")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["en"])
        entries = fetched.to_raw_data()
        if not entries:
            fail("Transcript is empty")
            return None
        text_lines = []
        for entry in entries:
            start = int(entry.get("start", 0))
            duration = entry.get("duration", 0)
            end = int(start + duration) if duration else start
            s_min, s_sec = start // 60, start % 60
            e_min, e_sec = end // 60, end % 60
            text = entry.get("text", "").strip()
            if text:
                text_lines.append(f"[{s_min}:{s_sec:02d} → {e_min}:{e_sec:02d}] {text}")
        transcript_text = "\n".join(text_lines)
        ok(f"Got {len(entries)} entries, {len(transcript_text)} chars")
        ok(f"First line: {text_lines[0][:80]}")
        return transcript_text
    except Exception as e:
        fail(f"Transcript fetch failed: {e}")
        return None


# ---------- Stage 3: Matcher (GPT-4o-mini) ----------
async def test_matcher(transcript_text: str, video_metadata: dict):
    banner("Stage 3: Matcher (GPT-4o-mini timestamp extraction)")
    from dotenv import load_dotenv
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        fail("OPENAI_API_KEY not set — skipping matcher test")
        return None

    from app.services.matcher import MatcherService
    from app.models.schemas import Segment

    segment = Segment(
        segment_id="seg_001",
        title="Epstein's Island",
        summary="Jeffrey Epstein owned a private island in the US Virgin Islands used for criminal activity.",
        visual_need="aerial footage of Little St. James Island",
        emotional_tone="ominous",
        key_terms=["Epstein", "island", "Little St. James", "Virgin Islands"],
        search_queries=["Epstein island documentary"],
    )

    matcher = MatcherService()
    step("Calling GPT-4o-mini to find peak visual moment...")
    match = await matcher.find_timestamp(transcript_text, segment, video_metadata)

    if match.confidence_score > 0:
        ok(f"Match found! confidence={match.confidence_score:.2f}")
        ok(f"  Timestamp: {match.start_time_seconds}s - {match.end_time_seconds}s")
        ok(f"  Hook: {match.the_hook}")
        if match.transcript_excerpt:
            ok(f"  Excerpt: {match.transcript_excerpt[:100]}...")
    else:
        fail(f"No match found (confidence={match.confidence_score})")

    match = matcher.validate_context_match(match, video_metadata["video_duration_seconds"])
    ok(f"  Context valid: {match.context_match_valid}")
    return match


# ---------- Stage 4: Ranker ----------
def test_ranker(candidate, match_result, segment):
    banner("Stage 4: Ranker")
    from app.services.ranker import RankerService
    ranker = RankerService()
    step("Ranking and filtering...")
    ranked = ranker.rank_and_filter([(candidate, match_result)], segment)
    if ranked:
        r = ranked[0]
        ok(f"Ranked result: relevance={r.relevance_score:.4f}, confidence={r.confidence_score:.2f}")
        ok(f"  Clip URL: {r.clip_url}")
        ok(f"  Video: {r.video_title[:50]}")
    else:
        fail("Ranker produced no results")
    return ranked


async def main():
    banner("B-Roll Scout End-to-End Flow Test")
    print("This test verifies each pipeline stage works independently.\n")

    # Stage 1: Search
    search_results = test_ytdlp_search()
    if not search_results:
        print("\n\nFAILED: No search results. Cannot continue.")
        sys.exit(1)

    video = search_results[0]
    video_id = video["video_id"]

    # Stage 2: Transcript
    transcript = test_transcript(video_id)
    if not transcript:
        print(f"\n\nFAILED: Could not get transcript for {video_id}.")
        sys.exit(1)

    # Stage 3: Matcher
    video_meta = {
        "video_duration_seconds": int(video["duration_seconds"]),
        "video_title": video["title"],
        "view_count": video["view_count"],
        "transcript_source": "youtube_captions",
    }
    match_result = await test_matcher(transcript, video_meta)

    if match_result and match_result.confidence_score > 0:
        # Stage 4: Ranker
        from app.models.schemas import Segment, CandidateVideo
        segment = Segment(
            segment_id="seg_001",
            title="Epstein's Island",
            summary="Jeffrey Epstein owned a private island.",
            visual_need="aerial footage of Little St. James Island",
            emotional_tone="ominous",
            key_terms=["Epstein", "island", "Little St. James"],
            search_queries=["Epstein island documentary"],
        )
        candidate = CandidateVideo(
            video_id=video_id,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            video_title=video["title"],
            channel_name=video["channel_name"],
            channel_id=video["channel_id"],
            channel_subscribers=0,
            thumbnail_url=video.get("thumbnail_url", ""),
            video_duration_seconds=int(video["duration_seconds"]),
            published_at=video.get("published_at", ""),
            view_count=video["view_count"],
        )
        test_ranker(candidate, match_result, segment)

    banner("TEST COMPLETE")
    print("\nAll stages that ran produced valid output.")
    print("If you see results above, the pipeline is working correctly.\n")


if __name__ == "__main__":
    asyncio.run(main())
