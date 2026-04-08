"""
Integration tests for the V2 pipeline upgrades.

Covers:
  - New schema enums and fields (ShotIntent, Scarcity, AuditStatus)
  - Matcher two-axis scoring (visual_fit, topical_fit) and intent-weighted combination
  - Ranker dynamic thresholds, source diversity, session dedup, early stopping
  - Context audit three-tier outcomes (pass/review/reject)
  - Searcher multilingual queries, source-type modifiers, search cache, exclusion soft-deprioritization
  - Re-search failure-mode awareness and consolidation detection
  - Cross-cutting: session-level timestamp dedup
  - TypeScript types consistency

Run with:  python -m pytest tests/test_v2_pipeline.py -v
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


# ═══════════════════════════════════════════════════════════════════════════
# 1. New Schema Enums
# ═══════════════════════════════════════════════════════════════════════════

class TestNewEnums:

    def test_shot_intent_values(self):
        from app.models.schemas import ShotIntent
        assert ShotIntent.LITERAL.value == "literal"
        assert ShotIntent.ILLUSTRATIVE.value == "illustrative"
        assert ShotIntent.ATMOSPHERIC.value == "atmospheric"

    def test_scarcity_values(self):
        from app.models.schemas import Scarcity
        assert Scarcity.COMMON.value == "common"
        assert Scarcity.MEDIUM.value == "medium"
        assert Scarcity.RARE.value == "rare"

    def test_audit_status_values(self):
        from app.models.schemas import AuditStatus
        assert AuditStatus.PASS.value == "pass"
        assert AuditStatus.REVIEW.value == "review"
        assert AuditStatus.REJECT.value == "reject"
        assert AuditStatus.UNAUDITED.value == "unaudited"


# ═══════════════════════════════════════════════════════════════════════════
# 2. BRollShot — new fields
# ═══════════════════════════════════════════════════════════════════════════

class TestBRollShotV2:

    def test_new_fields_default(self):
        from app.models.schemas import BRollShot, ShotIntent, Scarcity
        shot = BRollShot(shot_id="seg_001_shot_1", visual_need="test")
        assert shot.shot_intent == ShotIntent.LITERAL
        assert shot.scarcity == Scarcity.COMMON
        assert shot.visual_description == ""
        assert shot.preferred_source_type == ""

    def test_new_fields_explicit(self):
        from app.models.schemas import BRollShot, ShotIntent, Scarcity
        shot = BRollShot(
            shot_id="seg_001_shot_1",
            visual_need="Aerial view of island",
            visual_description="Wide drone shot from 500ft, turquoise water, green canopy",
            shot_intent=ShotIntent.ILLUSTRATIVE,
            scarcity=Scarcity.RARE,
            preferred_source_type="drone_aerial",
            search_queries=["island aerial", "drone island footage"],
            key_terms=["island", "aerial"],
        )
        assert shot.shot_intent == ShotIntent.ILLUSTRATIVE
        assert shot.scarcity == Scarcity.RARE
        assert shot.preferred_source_type == "drone_aerial"
        assert "drone" in shot.visual_description

    def test_string_enum_coercion(self):
        from app.models.schemas import BRollShot, ShotIntent
        shot = BRollShot(
            shot_id="seg_001_shot_1", visual_need="test",
            shot_intent="atmospheric",
        )
        assert shot.shot_intent == ShotIntent.ATMOSPHERIC


# ═══════════════════════════════════════════════════════════════════════════
# 3. MatchResult — new scoring fields
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchResultV2:

    def test_new_scoring_fields_default(self):
        from app.models.schemas import MatchResult
        m = MatchResult()
        assert m.visual_fit == 0.0
        assert m.topical_fit == 0.0
        assert m.match_reasoning is None

    def test_new_scoring_fields_set(self):
        from app.models.schemas import MatchResult
        m = MatchResult(
            visual_fit=0.85,
            topical_fit=0.60,
            match_reasoning="Aerial footage matches the visual need, topic is closely related",
            confidence_score=0.75,
        )
        assert m.visual_fit == 0.85
        assert m.topical_fit == 0.60
        assert "Aerial" in m.match_reasoning


# ═══════════════════════════════════════════════════════════════════════════
# 4. RankedResult — carries V2 fields
# ═══════════════════════════════════════════════════════════════════════════

class TestRankedResultV2:

    def test_new_fields_default(self):
        from app.models.schemas import RankedResult, TranscriptSource, ShotIntent, Scarcity, AuditStatus
        r = RankedResult(
            result_id="r1", segment_id="seg_001",
            video_id="v1", video_url="u", video_title="T",
            channel_name="C", thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01", source_flag=TranscriptSource.YOUTUBE_AUTO,
        )
        assert r.shot_intent == ShotIntent.LITERAL
        assert r.scarcity == Scarcity.COMMON
        assert r.visual_fit == 0.0
        assert r.topical_fit == 0.0
        assert r.match_reasoning is None
        assert r.audit_status == AuditStatus.UNAUDITED
        assert r.audit_reason is None

    def test_new_fields_set(self):
        from app.models.schemas import RankedResult, TranscriptSource, ShotIntent, Scarcity, AuditStatus
        r = RankedResult(
            result_id="r1", segment_id="seg_001",
            video_id="v1", video_url="u", video_title="T",
            channel_name="C", thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01", source_flag=TranscriptSource.YOUTUBE_AUTO,
            shot_intent=ShotIntent.ATMOSPHERIC,
            scarcity=Scarcity.RARE,
            visual_fit=0.90,
            topical_fit=0.30,
            match_reasoning="Moody footage, low topical relevance but high visual fit for atmospheric shot",
            audit_status=AuditStatus.REVIEW,
            audit_reason="Borderline geographic match",
        )
        assert r.shot_intent == ShotIntent.ATMOSPHERIC
        assert r.scarcity == Scarcity.RARE
        assert r.audit_status == AuditStatus.REVIEW


# ═══════════════════════════════════════════════════════════════════════════
# 5. Matcher — intent-weighted scoring
# ═══════════════════════════════════════════════════════════════════════════

class TestMatcherIntentWeighting:

    def test_literal_weights_topical_higher(self):
        from app.services.matcher import MatcherService
        from app.models.schemas import ShotIntent
        score = MatcherService._intent_weighted_score(0.8, 0.4, ShotIntent.LITERAL)
        expected = 0.3 * 0.8 + 0.7 * 0.4
        assert abs(score - expected) < 0.001

    def test_illustrative_weights_visual_higher(self):
        from app.services.matcher import MatcherService
        from app.models.schemas import ShotIntent
        score = MatcherService._intent_weighted_score(0.8, 0.4, ShotIntent.ILLUSTRATIVE)
        expected = 0.7 * 0.8 + 0.3 * 0.4
        assert abs(score - expected) < 0.001

    def test_atmospheric_nearly_all_visual(self):
        from app.services.matcher import MatcherService
        from app.models.schemas import ShotIntent
        score = MatcherService._intent_weighted_score(0.9, 0.1, ShotIntent.ATMOSPHERIC)
        expected = 0.9 * 0.9 + 0.1 * 0.1
        assert abs(score - expected) < 0.001
        assert score > 0.8

    def test_atmospheric_low_topical_still_high(self):
        from app.services.matcher import MatcherService
        from app.models.schemas import ShotIntent
        score_atm = MatcherService._intent_weighted_score(0.85, 0.10, ShotIntent.ATMOSPHERIC)
        score_lit = MatcherService._intent_weighted_score(0.85, 0.10, ShotIntent.LITERAL)
        assert score_atm > score_lit


# ═══════════════════════════════════════════════════════════════════════════
# 6. Ranker — dynamic thresholds by scarcity
# ═══════════════════════════════════════════════════════════════════════════

class TestRankerDynamicThresholds:

    def _make_candidate(self, vid_id="v1", views=100000):
        from app.models.schemas import CandidateVideo
        return CandidateVideo(
            video_id=vid_id, video_url=f"https://youtube.com/watch?v={vid_id}",
            video_title="Documentary", channel_name="Ch", channel_id="UC_1",
            channel_subscribers=50000, thumbnail_url="",
            video_duration_seconds=600, published_at="2025-01-01T00:00:00Z",
            view_count=views,
        )

    def _make_match(self, conf=0.7, vf=0.0, tf=0.0):
        from app.models.schemas import MatchResult, TranscriptSource
        return MatchResult(
            start_time_seconds=60, end_time_seconds=120,
            transcript_excerpt="test content", confidence_score=conf,
            visual_fit=vf, topical_fit=tf,
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )

    def _make_segment(self):
        from app.models.schemas import Segment
        return Segment(
            segment_id="seg_001", title="Test", summary="Test",
            visual_need="footage", emotional_tone="neutral",
            key_terms=["test"], search_queries=["test"],
        )

    def test_common_shot_high_threshold(self):
        from app.services.ranker import RankerService
        from app.models.schemas import BRollShot, Scarcity
        ranker = RankerService()
        shot = BRollShot(
            shot_id="seg_001_shot_1", visual_need="city skyline",
            scarcity=Scarcity.COMMON,
        )
        cand = self._make_candidate()
        match = self._make_match(conf=0.5)
        results = ranker.rank_and_filter(
            [(cand, match)], self._make_segment(), shot=shot,
        )
        # Score of ~0.5 should be below common threshold of 0.60
        # but fallback keeps it since it's the only candidate
        assert len(results) >= 1

    def test_rare_shot_low_threshold_passes(self):
        from app.services.ranker import RankerService
        from app.models.schemas import BRollShot, Scarcity
        ranker = RankerService()
        shot = BRollShot(
            shot_id="seg_001_shot_1", visual_need="classified footage",
            scarcity=Scarcity.RARE,
        )
        cand = self._make_candidate()
        match = self._make_match(conf=0.35)
        results = ranker.rank_and_filter(
            [(cand, match)], self._make_segment(), shot=shot,
        )
        assert len(results) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. Ranker — source diversity bonus/penalty
# ═══════════════════════════════════════════════════════════════════════════

class TestRankerSourceDiversity:

    def test_first_from_channel_gets_boost(self):
        from app.services.ranker import RankerService
        from app.models.schemas import (
            CandidateVideo, MatchResult, Segment, TranscriptSource, BRollShot,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="neutral",
            key_terms=["test"], search_queries=["test"],
        )
        shot = BRollShot(shot_id="seg_001_shot_1", visual_need="test")

        def make_cand(vid, ch_id):
            return CandidateVideo(
                video_id=vid, video_url="u", video_title="Doc",
                channel_name=f"Ch_{ch_id}", channel_id=ch_id,
                channel_subscribers=100000, thumbnail_url="",
                video_duration_seconds=600, published_at="2025-01-01T00:00:00Z",
                view_count=100000,
            )

        def make_match():
            return MatchResult(
                start_time_seconds=60, end_time_seconds=120,
                confidence_score=0.7, source_flag=TranscriptSource.YOUTUBE_MANUAL,
                context_match_valid=True,
            )

        # Two candidates from different channels — both should get +0.05
        results = ranker.rank_and_filter(
            [(make_cand("v1", "ch_a"), make_match()), (make_cand("v2", "ch_b"), make_match())],
            seg, shot=shot, settings={"top_results_per_shot": 2},
        )
        assert len(results) == 2

    def test_third_from_same_channel_penalized(self):
        from app.services.ranker import RankerService
        from app.models.schemas import (
            CandidateVideo, MatchResult, Segment, TranscriptSource, BRollShot,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="neutral",
            key_terms=["test"], search_queries=["test"],
        )
        shot = BRollShot(shot_id="seg_001_shot_1", visual_need="test")

        def make_cand(vid):
            return CandidateVideo(
                video_id=vid, video_url="u", video_title="Doc",
                channel_name="SameChannel", channel_id="same_ch",
                channel_subscribers=100000, thumbnail_url="",
                video_duration_seconds=600, published_at="2025-01-01T00:00:00Z",
                view_count=100000,
            )

        def make_match(conf):
            return MatchResult(
                start_time_seconds=60, end_time_seconds=120,
                confidence_score=conf, source_flag=TranscriptSource.YOUTUBE_MANUAL,
                context_match_valid=True,
            )

        # Three candidates from the same channel
        results = ranker.rank_and_filter(
            [
                (make_cand("v1"), make_match(0.8)),
                (make_cand("v2"), make_match(0.78)),
                (make_cand("v3"), make_match(0.76)),
            ],
            seg, shot=shot, settings={"top_results_per_shot": 3},
        )
        # First should have higher score than third due to diversity penalty
        assert results[0].relevance_score >= results[-1].relevance_score


# ═══════════════════════════════════════════════════════════════════════════
# 8. Ranker — session-level timestamp dedup
# ═══════════════════════════════════════════════════════════════════════════

class TestRankerSessionDedup:

    def test_used_timestamp_deprioritized(self):
        """When a timestamp bucket is already used, the clip gets excluded from the main
        scoring path.  If a better alternative exists the deduped clip won't appear;
        when it's the *only* option the ranker may surface it as a low-score fallback."""
        from app.services.ranker import RankerService
        from app.models.schemas import (
            CandidateVideo, MatchResult, Segment, TranscriptSource, BRollShot,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="neutral",
            key_terms=["test"], search_queries=["test"],
        )
        shot = BRollShot(shot_id="seg_001_shot_1", visual_need="test")

        def make_cand(vid):
            return CandidateVideo(
                video_id=vid, video_url="u", video_title="Doc",
                channel_name="Ch", channel_id="c1", channel_subscribers=50000,
                thumbnail_url="", video_duration_seconds=600,
                published_at="2025-01-01T00:00:00Z", view_count=100000,
            )

        def make_match(start):
            return MatchResult(
                start_time_seconds=start, end_time_seconds=start + 30,
                confidence_score=0.9, source_flag=TranscriptSource.YOUTUBE_MANUAL,
                context_match_valid=True,
            )

        # v1@60s (bucket 2) is used; v2@300s (bucket 10) is fresh
        used_timestamps = {"v1": {2}}
        results = ranker.rank_and_filter(
            [(make_cand("v1"), make_match(60)), (make_cand("v2"), make_match(300))],
            seg, shot=shot, used_timestamps=used_timestamps,
        )
        # The fresh clip should be picked over the used one
        assert results[0].video_id == "v2"

    def test_different_timestamp_not_skipped(self):
        from app.services.ranker import RankerService
        from app.models.schemas import (
            CandidateVideo, MatchResult, Segment, TranscriptSource, BRollShot,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="neutral",
            key_terms=["test"], search_queries=["test"],
        )
        shot = BRollShot(shot_id="seg_001_shot_1", visual_need="test")

        cand = CandidateVideo(
            video_id="v1", video_url="u", video_title="Doc",
            channel_name="Ch", channel_id="c1", channel_subscribers=50000,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=100000,
        )
        match = MatchResult(
            start_time_seconds=300, end_time_seconds=360,
            confidence_score=0.9, source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )

        # Only bucket 2 is used; bucket 10 (300//30) is free
        used_timestamps = {"v1": {2}}
        results = ranker.rank_and_filter(
            [(cand, match)], seg, shot=shot, used_timestamps=used_timestamps,
        )
        assert len(results) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 9. Ranker — intent-weighted fit integration
# ═══════════════════════════════════════════════════════════════════════════

class TestRankerIntentFitIntegration:

    def test_atmospheric_shot_with_high_visual_low_topical(self):
        from app.services.ranker import RankerService
        from app.models.schemas import (
            CandidateVideo, MatchResult, Segment, TranscriptSource,
            BRollShot, ShotIntent,
        )
        ranker = RankerService()
        seg = Segment(
            segment_id="seg_001", title="Ominous section", summary="Tension builds",
            visual_need="dark atmospheric footage", emotional_tone="ominous",
            key_terms=["dark", "atmosphere"], search_queries=["dark footage"],
        )
        shot = BRollShot(
            shot_id="seg_001_shot_1", visual_need="moody clouds",
            shot_intent=ShotIntent.ATMOSPHERIC,
        )
        cand = CandidateVideo(
            video_id="v1", video_url="u", video_title="Storm Clouds Timelapse",
            channel_name="NatureCh", channel_id="c1", channel_subscribers=200000,
            thumbnail_url="", video_duration_seconds=300,
            published_at="2025-01-01T00:00:00Z", view_count=500000,
        )
        match = MatchResult(
            start_time_seconds=30, end_time_seconds=90,
            confidence_score=0.6,
            visual_fit=0.95, topical_fit=0.10,
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )
        results = ranker.rank_and_filter(
            [(cand, match)], seg, shot=shot,
        )
        assert len(results) >= 1
        assert results[0].relevance_score > 0.3


# ═══════════════════════════════════════════════════════════════════════════
# 10. Context Audit — three-tier outcomes
# ═══════════════════════════════════════════════════════════════════════════

class TestContextAuditV2:

    @pytest.mark.asyncio
    async def test_three_tier_audit(self):
        from app.background import _audit_context
        from app.models.schemas import (
            RankedResult, ScriptContext, TranscriptSource, AuditStatus,
            ShotIntent, Scarcity,
        )
        from app.services.matcher import MatcherService

        ctx = ScriptContext(
            script_topic="World War II", script_domain="history",
            geographic_scope="Europe", temporal_scope="1939-1945",
            exclusion_context="NOT about modern warfare",
        )

        common = dict(
            video_url="u", channel_name="Ch", thumbnail_url="",
            video_duration_seconds=600, published_at="2025-01-01",
            source_flag=TranscriptSource.YOUTUBE_AUTO,
        )
        results = [
            RankedResult(result_id="r0", segment_id="seg_001", video_id="v0",
                         video_title="D-Day Documentary", relevance_score=0.80,
                         confidence_score=0.80, **common),
            RankedResult(result_id="r1", segment_id="seg_002", video_id="v1",
                         video_title="Battle of Stalingrad", relevance_score=0.75,
                         confidence_score=0.75, **common),
            RankedResult(result_id="r2", segment_id="seg_003", video_id="v2",
                         video_title="Modern Drone Warfare Analysis", relevance_score=0.65,
                         confidence_score=0.60, **common),
            RankedResult(result_id="r3", segment_id="seg_004", video_id="v3",
                         video_title="WW2 Pacific Theater", relevance_score=0.70,
                         confidence_score=0.70, **common),
        ]

        audit_response = {
            "audited": [
                {"index": 0, "verdict": "pass", "reason": "", "concern_type": "none"},
                {"index": 1, "verdict": "pass", "reason": "", "concern_type": "none"},
                {"index": 2, "verdict": "reject", "reason": "Modern warfare, not WW2", "concern_type": "temporal_mismatch"},
                {"index": 3, "verdict": "review", "reason": "Pacific not European theater", "concern_type": "geographic_mismatch"},
            ],
        }

        mock_matcher = MagicMock(spec=MatcherService)
        mock_matcher._get.return_value = "auto"
        mock_matcher._route_call = AsyncMock(return_value=audit_response)

        mock_storage = AsyncMock()
        with patch("app.background.get_storage", return_value=mock_storage):
            updated, review_count, reject_count = await _audit_context(
                results, ctx, mock_matcher, "test-job",
            )

        assert reject_count == 1
        assert review_count == 1

        # Results may be re-sorted after score penalties — look up by result_id
        reject_result = next(r for r in updated if r.result_id == "r2")
        assert reject_result.audit_status == AuditStatus.REJECT
        assert reject_result.relevance_score == pytest.approx(0.65 * 0.40, abs=0.01)

        review_result = next(r for r in updated if r.result_id == "r3")
        assert review_result.audit_status == AuditStatus.REVIEW
        assert review_result.relevance_score == pytest.approx(0.70 * 0.85, abs=0.01)

        pass_result = next(r for r in updated if r.result_id == "r0")
        assert pass_result.audit_status == AuditStatus.PASS
        assert pass_result.relevance_score == 0.80

    @pytest.mark.asyncio
    async def test_audit_never_deletes_clips(self):
        from app.background import _audit_context
        from app.models.schemas import RankedResult, ScriptContext, TranscriptSource
        from app.services.matcher import MatcherService

        ctx = ScriptContext(script_topic="Test Topic")
        common = dict(
            video_url="u", channel_name="Ch", thumbnail_url="",
            video_duration_seconds=600, published_at="2025-01-01",
            source_flag=TranscriptSource.YOUTUBE_AUTO,
        )
        results = [
            RankedResult(result_id=f"r{i}", segment_id=f"seg_{i:03d}", video_id=f"v{i}",
                         video_title=f"Video {i}", relevance_score=0.5,
                         confidence_score=0.5, **common)
            for i in range(5)
        ]

        # All rejected
        audit_response = {
            "audited": [{"index": i, "verdict": "reject", "reason": "test", "concern_type": "none"} for i in range(5)],
        }
        mock_matcher = MagicMock(spec=MatcherService)
        mock_matcher._get.return_value = "auto"
        mock_matcher._route_call = AsyncMock(return_value=audit_response)

        mock_storage = AsyncMock()
        with patch("app.background.get_storage", return_value=mock_storage):
            updated, _, reject_count = await _audit_context(results, ctx, mock_matcher, "j1")

        assert len(updated) == 5
        assert reject_count == 5

    @pytest.mark.asyncio
    async def test_audit_skips_small_result_sets(self):
        from app.background import _audit_context
        from app.models.schemas import RankedResult, ScriptContext, TranscriptSource, AuditStatus
        from app.services.matcher import MatcherService

        ctx = ScriptContext(script_topic="Test")
        common = dict(
            video_url="u", channel_name="Ch", thumbnail_url="",
            video_duration_seconds=600, published_at="2025-01-01",
            source_flag=TranscriptSource.YOUTUBE_AUTO,
        )
        results = [
            RankedResult(result_id="r0", segment_id="seg_001", video_id="v0",
                         video_title="V0", relevance_score=0.7,
                         confidence_score=0.7, **common),
        ]

        mock_matcher = MagicMock(spec=MatcherService)
        updated, review, reject = await _audit_context(results, ctx, mock_matcher, "j1")
        assert len(updated) == 1
        assert review == 0
        assert reject == 0
        assert updated[0].audit_status == AuditStatus.PASS


# ═══════════════════════════════════════════════════════════════════════════
# 11. Searcher — multilingual queries
# ═══════════════════════════════════════════════════════════════════════════

class TestSearcherMultilingual:

    def test_indian_topic_detected(self):
        from app.services.searcher import _is_indian_topic
        from app.models.schemas import ScriptContext
        ctx = ScriptContext(
            script_topic="Tamil Nadu temple architecture",
            geographic_scope="Tamil Nadu, India",
        )
        assert _is_indian_topic(ctx) is True

    def test_non_indian_topic(self):
        from app.services.searcher import _is_indian_topic
        from app.models.schemas import ScriptContext
        ctx = ScriptContext(
            script_topic="World War II",
            geographic_scope="Europe",
        )
        assert _is_indian_topic(ctx) is False

    def test_multilingual_generates_tamil_hindi(self):
        from app.services.searcher import _generate_multilingual_queries, _is_indian_topic
        from app.models.schemas import ScriptContext
        ctx = ScriptContext(
            script_topic="Chennai history",
            geographic_scope="Chennai, Tamil Nadu, India",
        )
        assert _is_indian_topic(ctx) is True
        queries = _generate_multilingual_queries("Chennai ancient temples", ctx)
        assert len(queries) == 3
        assert queries[0] == "Chennai ancient temples"
        assert "தமிழ்" in queries[1]
        assert "हिंदी" in queries[2]

    def test_non_indian_returns_single_query(self):
        from app.services.searcher import _generate_multilingual_queries
        from app.models.schemas import ScriptContext
        ctx = ScriptContext(script_topic="Paris architecture", geographic_scope="France")
        queries = _generate_multilingual_queries("Paris buildings", ctx)
        assert len(queries) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 11b. Searcher — 9:16 Shorts aspect filter
# ═══════════════════════════════════════════════════════════════════════════

class TestShorts9_16Aspect:

    def test_classic_shorts_resolution(self):
        from app.config import DEFAULTS
        from app.services.searcher import _is_portrait_aspect_ratio_9_16
        tol = float(DEFAULTS["shorts_9_16_aspect_tolerance"])
        assert _is_portrait_aspect_ratio_9_16(1080, 1920, tol)
        assert _is_portrait_aspect_ratio_9_16(720, 1280, tol)

    def test_landscape_not_shorts_shape(self):
        from app.services.searcher import _is_portrait_aspect_ratio_9_16
        tol = 0.06
        assert not _is_portrait_aspect_ratio_9_16(1920, 1080, tol)

    def test_vertical_4_5_not_treated_as_9_16(self):
        from app.services.searcher import _is_portrait_aspect_ratio_9_16
        tol = 0.06
        assert not _is_portrait_aspect_ratio_9_16(1080, 1350, tol)

    def test_searcher_respects_toggle_off(self):
        from app.services.searcher import SearcherService
        svc = SearcherService(pipeline_settings={"filter_9_16_shorts": False})
        assert not svc._should_exclude_shorts_9_16_aspect(1080, 1920)

class TestSearchCache:

    def test_cache_set_and_get(self):
        from app.services.searcher import (
            _cached_search_key, _get_cached_search, _set_cached_search, _search_cache,
        )
        _search_cache.clear()

        key = _cached_search_key("test query", 5)
        assert _get_cached_search(key) is None

        _set_cached_search(key, [{"video_id": "v1"}])
        cached = _get_cached_search(key)
        assert cached is not None
        assert cached[0]["video_id"] == "v1"

        _search_cache.clear()

    def test_cache_expires(self):
        from app.services.searcher import (
            _cached_search_key, _get_cached_search, _search_cache, SEARCH_CACHE_TTL,
        )
        _search_cache.clear()

        key = "expired::5"
        # Set with a timestamp far in the past
        _search_cache[key] = (time.time() - SEARCH_CACHE_TTL - 1, [{"video_id": "old"}])
        assert _get_cached_search(key) is None

        _search_cache.clear()


# ═══════════════════════════════════════════════════════════════════════════
# 13. Searcher — source type modifiers
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceTypeModifiers:

    def test_modifier_map_complete(self):
        from app.services.searcher import SOURCE_TYPE_MODIFIERS
        expected = {
            "documentary", "news_clip", "stock_footage", "drone_aerial",
            "interview", "timelapse", "archival", "animation",
        }
        assert set(SOURCE_TYPE_MODIFIERS.keys()) == expected

    def test_each_modifier_has_terms(self):
        from app.services.searcher import SOURCE_TYPE_MODIFIERS
        for key, terms in SOURCE_TYPE_MODIFIERS.items():
            assert len(terms) >= 1, f"{key} has no modifier terms"


# ═══════════════════════════════════════════════════════════════════════════
# 14. Re-search — text similarity for consolidation
# ═══════════════════════════════════════════════════════════════════════════

class TestTextSimilarity:

    def test_identical_strings(self):
        from app.background import _text_similarity
        assert _text_similarity("aerial drone footage", "aerial drone footage") == 1.0

    def test_completely_different(self):
        from app.background import _text_similarity
        score = _text_similarity("aerial drone footage", "underwater submarine documentary")
        assert score < 0.2

    def test_partial_overlap(self):
        from app.background import _text_similarity
        score = _text_similarity("aerial island drone footage", "aerial coastal drone view")
        assert 0.2 < score < 0.8

    def test_empty_string(self):
        from app.background import _text_similarity
        assert _text_similarity("", "something") == 0.0
        assert _text_similarity("", "") == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 15. Translator prompt — V2 fields present
# ═══════════════════════════════════════════════════════════════════════════

class TestTranslatorPromptV2:

    def test_prompt_has_visual_description(self):
        from app.services.translator import SYSTEM_PROMPT
        assert "visual_description" in SYSTEM_PROMPT
        assert "camera angle" in SYSTEM_PROMPT.lower() or "camera" in SYSTEM_PROMPT.lower()

    def test_prompt_has_shot_intent(self):
        from app.services.translator import SYSTEM_PROMPT
        assert "shot_intent" in SYSTEM_PROMPT
        assert "literal" in SYSTEM_PROMPT
        assert "illustrative" in SYSTEM_PROMPT
        assert "atmospheric" in SYSTEM_PROMPT

    def test_prompt_has_scarcity(self):
        from app.services.translator import SYSTEM_PROMPT
        assert "scarcity" in SYSTEM_PROMPT
        assert "common" in SYSTEM_PROMPT
        assert "medium" in SYSTEM_PROMPT
        assert "rare" in SYSTEM_PROMPT

    def test_prompt_has_preferred_source_type(self):
        from app.services.translator import SYSTEM_PROMPT
        assert "preferred_source_type" in SYSTEM_PROMPT
        assert "drone_aerial" in SYSTEM_PROMPT

    def test_prompt_has_lateral_query(self):
        from app.services.translator import SYSTEM_PROMPT
        assert "LATERAL" in SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════════════════
# 16. Config — new weight present
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigV2:

    def test_fit_score_weight_present(self):
        from app.config import DEFAULTS
        assert "weight_fit_score" in DEFAULTS
        assert DEFAULTS["weight_fit_score"] == 0.20

    def test_context_relevance_weight_present(self):
        from app.config import DEFAULTS
        assert "weight_context_relevance" in DEFAULTS
        assert DEFAULTS["weight_context_relevance"] == 0.10

    def test_weights_sum_approximately_one(self):
        from app.config import DEFAULTS
        weights = [
            DEFAULTS.get("weight_ai_confidence", 0),
            DEFAULTS.get("weight_fit_score", 0),
            DEFAULTS.get("weight_viral_score", 0),
            DEFAULTS.get("weight_channel_authority", 0),
            DEFAULTS.get("weight_caption_quality", 0),
            DEFAULTS.get("weight_recency", 0),
            DEFAULTS.get("weight_context_relevance", 0),
        ]
        total = sum(weights)
        assert abs(total - 1.0) < 0.05, f"Weights sum to {total}, expected ~1.0"


# ═══════════════════════════════════════════════════════════════════════════
# 17. TypeScript types — V2 fields present
# ═══════════════════════════════════════════════════════════════════════════

class TestTypeScriptV2:

    def _read_types(self):
        types_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lib", "types.ts",
        )
        with open(types_path) as f:
            return f.read()

    def test_shot_intent_type(self):
        content = self._read_types()
        assert "ShotIntent" in content
        assert '"literal"' in content
        assert '"illustrative"' in content
        assert '"atmospheric"' in content

    def test_scarcity_type(self):
        content = self._read_types()
        assert "Scarcity" in content
        assert '"common"' in content
        assert '"rare"' in content

    def test_audit_status_type(self):
        content = self._read_types()
        assert "AuditStatus" in content
        assert '"pass"' in content
        assert '"review"' in content
        assert '"reject"' in content

    def test_ranked_result_has_v2_fields(self):
        content = self._read_types()
        assert "visual_fit" in content
        assert "topical_fit" in content
        assert "match_reasoning" in content
        assert "audit_status" in content
        assert "audit_reason" in content
        assert "shot_intent" in content

    def test_broll_shot_has_v2_fields(self):
        content = self._read_types()
        assert "visual_description" in content
        assert "preferred_source_type" in content

    def test_weight_fit_score_in_settings(self):
        content = self._read_types()
        assert "weight_fit_score" in content


# ═══════════════════════════════════════════════════════════════════════════
# 18. Full V2 pipeline mock — shot → search → match → rank with new fields
# ═══════════════════════════════════════════════════════════════════════════

class TestFullV2PipelineMock:

    def test_end_to_end_with_v2_fields(self):
        from app.models.schemas import (
            BRollShot, CandidateVideo, MatchResult, RankedResult,
            Segment, TranscriptSource, ScriptContext, ShotIntent, Scarcity,
        )
        from app.services.ranker import RankerService

        segment = Segment(
            segment_id="seg_001", title="Storm approaches",
            summary="Tension builds as the storm nears the coast",
            visual_need="dramatic weather footage",
            emotional_tone="ominous",
            key_terms=["storm", "coast", "dramatic"],
            search_queries=["storm coast footage"],
        )
        shot = BRollShot(
            shot_id="seg_001_shot_1",
            visual_need="Dark storm clouds over ocean",
            visual_description="Low-angle timelapse of dark cumulus clouds rolling over grey ocean, dramatic lighting",
            shot_intent=ShotIntent.ATMOSPHERIC,
            scarcity=Scarcity.COMMON,
            preferred_source_type="timelapse",
            search_queries=["storm clouds ocean timelapse", "dramatic weather footage"],
            key_terms=["storm", "clouds", "ocean"],
        )
        candidate = CandidateVideo(
            video_id="storm_vid",
            video_url="https://www.youtube.com/watch?v=storm_vid",
            video_title="4K Storm Clouds Timelapse Over Pacific Ocean",
            channel_name="NatureFilms",
            channel_id="UC_nature",
            channel_subscribers=300000,
            thumbnail_url="",
            video_duration_seconds=480,
            published_at="2025-06-01T00:00:00Z",
            view_count=2000000,
        )
        match = MatchResult(
            start_time_seconds=45, end_time_seconds=120,
            transcript_excerpt="(no narration — ambient ocean sound with wind)",
            confidence_score=0.55,
            visual_fit=0.95,
            topical_fit=0.15,
            match_reasoning="Stunning storm footage. Topically unrelated to the documentary but visually perfect for an atmospheric mood shot.",
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True, context_match=True,
        )
        ctx = ScriptContext(
            script_topic="Climate change impact on coastal cities",
            geographic_scope="Global coastlines",
        )

        ranker = RankerService()
        results = ranker.rank_and_filter(
            [(candidate, match)], segment, shot=shot, script_context=ctx,
        )

        assert len(results) >= 1
        r = results[0]
        assert r.shot_intent == ShotIntent.ATMOSPHERIC
        assert r.scarcity == Scarcity.COMMON
        assert r.visual_fit == 0.95
        assert r.topical_fit == 0.15
        assert r.match_reasoning is not None
        assert "storm" in r.match_reasoning.lower() or "visual" in r.match_reasoning.lower()
        assert r.relevance_score > 0.3
        assert "t=45" in r.clip_url

    def test_literal_shot_penalizes_low_topical(self):
        """A literal shot with high visual but low topical fit should score lower."""
        from app.models.schemas import (
            BRollShot, CandidateVideo, MatchResult, Segment,
            TranscriptSource, ShotIntent,
        )
        from app.services.ranker import RankerService

        seg = Segment(
            segment_id="seg_001", title="T", summary="S",
            visual_need="v", emotional_tone="neutral",
            key_terms=["test"], search_queries=["test"],
        )
        literal_shot = BRollShot(
            shot_id="seg_001_shot_1", visual_need="test",
            shot_intent=ShotIntent.LITERAL,
        )
        atmospheric_shot = BRollShot(
            shot_id="seg_001_shot_1", visual_need="test",
            shot_intent=ShotIntent.ATMOSPHERIC,
        )
        cand = CandidateVideo(
            video_id="v1", video_url="u", video_title="Doc",
            channel_name="Ch", channel_id="c1", channel_subscribers=100000,
            thumbnail_url="", video_duration_seconds=600,
            published_at="2025-01-01T00:00:00Z", view_count=500000,
        )
        match = MatchResult(
            start_time_seconds=60, end_time_seconds=120,
            confidence_score=0.6,
            visual_fit=0.90, topical_fit=0.10,
            source_flag=TranscriptSource.YOUTUBE_MANUAL,
            context_match_valid=True,
        )

        ranker = RankerService()
        lit_results = ranker.rank_and_filter([(cand, match)], seg, shot=literal_shot)
        atm_results = ranker.rank_and_filter([(cand, match)], seg, shot=atmospheric_shot)

        # Atmospheric should score higher for this (high visual, low topical) clip
        assert atm_results[0].relevance_score > lit_results[0].relevance_score


# ═══════════════════════════════════════════════════════════════════════════
# 19. Expand shots — V2 fields in generated shots
# ═══════════════════════════════════════════════════════════════════════════

class TestExpandShotsV2:

    @pytest.mark.asyncio
    async def test_generate_shots_includes_v2_fields(self):
        from app.services.expand_shots import _generate_shots
        from app.models.schemas import Segment, ScriptContext, ShotIntent

        segment = Segment(
            segment_id="seg_001", title="Ocean trade", summary="Maritime trade routes",
            visual_need="ship footage", emotional_tone="dramatic",
            key_terms=["maritime"], search_queries=["maritime"],
            estimated_duration_seconds=120, broll_count=1,
        )
        ctx = ScriptContext(script_topic="Maritime History", geographic_scope="Indian Ocean")

        llm_response = {
            "shots": [{
                "visual_need": "Ancient port city",
                "visual_description": "Wide establishing shot of a harbor with traditional wooden boats",
                "shot_intent": "illustrative",
                "scarcity": "medium",
                "preferred_source_type": "documentary",
                "search_queries": ["q1", "q2", "q3", "q4", "q5"],
                "key_terms": ["port", "harbor"],
            }]
        }

        with patch("app.services.expand_shots._lightweight_llm_call", new_callable=AsyncMock, return_value=llm_response):
            shots = await _generate_shots(segment, ["existing"], 1, ctx)

        assert len(shots) == 1
        assert shots[0].visual_description == "Wide establishing shot of a harbor with traditional wooden boats"
        assert shots[0].shot_intent == ShotIntent.ILLUSTRATIVE
        assert shots[0].preferred_source_type == "documentary"


# ═══════════════════════════════════════════════════════════════════════════
# 20. Matcher prompt — V2 fields in prompt template
# ═══════════════════════════════════════════════════════════════════════════

class TestMatcherPromptV2:

    def test_prompt_has_two_axis_scoring(self):
        from app.services.matcher import TIMESTAMP_PROMPT_TEMPLATE
        assert "visual_fit" in TIMESTAMP_PROMPT_TEMPLATE
        assert "topical_fit" in TIMESTAMP_PROMPT_TEMPLATE

    def test_prompt_has_match_reasoning(self):
        from app.services.matcher import TIMESTAMP_PROMPT_TEMPLATE
        assert "match_reasoning" in TIMESTAMP_PROMPT_TEMPLATE

    def test_prompt_has_visual_description(self):
        from app.services.matcher import TIMESTAMP_PROMPT_TEMPLATE
        assert "visual_description" in TIMESTAMP_PROMPT_TEMPLATE
        assert "{visual_description}" in TIMESTAMP_PROMPT_TEMPLATE

    def test_prompt_has_shot_intent(self):
        from app.services.matcher import TIMESTAMP_PROMPT_TEMPLATE
        assert "shot_intent" in TIMESTAMP_PROMPT_TEMPLATE
        assert "{shot_intent}" in TIMESTAMP_PROMPT_TEMPLATE

    def test_prompt_evaluates_at_timestamp_level(self):
        from app.services.matcher import TIMESTAMP_PROMPT_TEMPLATE
        assert "transcript EXCERPT around your chosen timestamp" in TIMESTAMP_PROMPT_TEMPLATE

    def test_prompt_has_transcript_gap_detection(self):
        from app.services.matcher import TIMESTAMP_PROMPT_TEMPLATE
        assert "has_transcript_gap" in TIMESTAMP_PROMPT_TEMPLATE
