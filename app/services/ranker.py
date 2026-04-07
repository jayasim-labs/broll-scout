import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import DEFAULTS
from app.models.schemas import (
    AuditStatus, BRollShot, Scarcity, ScriptContext, Segment, ShotIntent,
    CandidateVideo, MatchResult, RankedResult, TranscriptSource,
)

logger = logging.getLogger(__name__)

SCARCITY_THRESHOLDS = {
    Scarcity.COMMON: 0.60,
    Scarcity.MEDIUM: 0.45,
    Scarcity.RARE: 0.30,
}

INTENT_WEIGHTS = {
    ShotIntent.LITERAL: (0.3, 0.7),
    ShotIntent.ILLUSTRATIVE: (0.7, 0.3),
    ShotIntent.ATMOSPHERIC: (0.9, 0.1),
}


class RankerService:
    """Computes relevance scores, filters, and returns top ranked results.

    V2 changes:
    - Uses visual_fit + topical_fit from matcher with intent-weighted combination
    - Dynamic thresholds based on shot scarcity
    - Source diversity bonus/penalty per channel
    - Per-shot independent ranking
    - Early stopping for common shots with 3+ above 0.75
    """

    def rank_and_filter(
        self,
        candidates: list[tuple[CandidateVideo, MatchResult]],
        segment: Segment,
        settings: dict | None = None,
        script_context: ScriptContext | None = None,
        shot: BRollShot | None = None,
        used_timestamps: dict[str, set[int]] | None = None,
    ) -> list[RankedResult]:
        cfg = settings or DEFAULTS

        w_ai = float(cfg.get("weight_ai_confidence", 0.35))
        w_fit = float(cfg.get("weight_fit_score", 0.20))
        w_vs = float(cfg.get("weight_viral_score", 0.10))
        w_ca = float(cfg.get("weight_channel_authority", 0.10))
        w_cq = float(cfg.get("weight_caption_quality", 0.05))
        w_re = float(cfg.get("weight_recency", 0.10))
        w_cx = float(cfg.get("weight_context_relevance", 0.10))
        total_w = w_ai + w_fit + w_vs + w_ca + w_cq + w_re + w_cx
        if total_w > 0 and abs(total_w - 1.0) > 0.01:
            factor = 1.0 / total_w
            w_ai *= factor
            w_fit *= factor
            w_vs *= factor
            w_ca *= factor
            w_cq *= factor
            w_re *= factor
            w_cx *= factor

        scarcity = (shot.scarcity if shot and hasattr(shot, 'scarcity') else Scarcity.COMMON)
        try:
            scarcity = Scarcity(scarcity)
        except ValueError:
            scarcity = Scarcity.COMMON
        threshold = SCARCITY_THRESHOLDS.get(scarcity, 0.45)

        shot_intent = (shot.shot_intent if shot and hasattr(shot, 'shot_intent') else ShotIntent.LITERAL)
        try:
            shot_intent = ShotIntent(shot_intent)
        except ValueError:
            shot_intent = ShotIntent.LITERAL

        top_n = int(cfg.get("top_results_per_shot", 2)) if shot else int(cfg.get("top_results_per_segment", 2))
        min_subs = int(cfg.get("prefer_min_subscribers", 0))
        negative_kw = segment.negative_keywords or []
        ranking_key_terms = (shot.key_terms if shot and shot.key_terms else segment.key_terms)

        channel_counts: dict[str, int] = {}
        scored: list[tuple[CandidateVideo, MatchResult, float]] = []
        best_fallback: tuple[CandidateVideo, MatchResult, float] | None = None

        for cand, match in candidates:
            if cand.is_blocked:
                continue
            if not match.context_match_valid:
                continue
            if match.confidence_score <= 0 and match.start_time_seconds is None:
                continue

            if negative_kw and self._has_negative_keyword(cand, match, negative_kw):
                logger.info("Rejected %s: negative keyword hit", cand.video_id)
                continue

            # Session-level timestamp dedup
            if used_timestamps and match.start_time_seconds is not None:
                bucket = match.start_time_seconds // 30
                if bucket in used_timestamps.get(cand.video_id, set()):
                    logger.info("Skipped %s@%ds: timestamp already used", cand.video_id, match.start_time_seconds)
                    continue

            if not match.context_match:
                fallback_score = match.confidence_score * 0.5
                if best_fallback is None or fallback_score > best_fallback[2]:
                    best_fallback = (cand, match, round(min(1.0, max(0.0, fallback_score)), 4))
                continue

            if match.source_flag == TranscriptSource.NONE:
                relevance = 0.3
            else:
                ai_score = match.confidence_score
                fit_score = self._intent_weighted_fit(match, shot_intent)
                vs_score = self._viral_score(cand.view_count)
                ca_score = self._channel_authority(cand, min_subs)
                cq_score = self._caption_quality(match.source_flag)
                re_score = self._recency_score(cand.published_at, cfg)
                cx_score = self._context_relevance(cand, script_context)
                relevance = (
                    w_ai * ai_score
                    + w_fit * fit_score
                    + w_vs * vs_score
                    + w_ca * ca_score
                    + w_cq * cq_score
                    + w_re * re_score
                    + w_cx * cx_score
                )

            # Source diversity: +0.05 first from channel, neutral second, -0.03 per extra
            ch_id = cand.channel_id
            ch_count = channel_counts.get(ch_id, 0)
            if ch_count == 0:
                relevance += 0.05
            elif ch_count >= 2:
                relevance -= 0.03 * (ch_count - 1)
            channel_counts[ch_id] = ch_count + 1

            scored.append((cand, match, round(min(1.0, max(0.0, relevance)), 4)))

        scored.sort(key=lambda x: x[2], reverse=True)

        # Early stopping: for common shots, if 3+ candidates above 0.75, take top_n immediately
        if scarcity == Scarcity.COMMON:
            above_75 = [s for s in scored if s[2] >= 0.75]
            if len(above_75) >= 3:
                scored = above_75

        if threshold > 0:
            above = [s for s in scored if s[2] >= threshold]
            if above:
                scored = above

        if not scored:
            if best_fallback is not None:
                logger.info("Using context-mismatch fallback for %s: %s (%.0f%%)",
                            segment.segment_id, best_fallback[0].video_id, best_fallback[2] * 100)
                scored = [best_fallback]
            elif candidates:
                for cand, match in candidates:
                    if match.start_time_seconds is not None and match.confidence_score > 0:
                        scored = [(cand, match, 0.1)]
                        break

        results: list[RankedResult] = []
        for idx, (cand, match, rel_score) in enumerate(scored[:top_n]):
            clip_url = cand.video_url
            if match.start_time_seconds is not None:
                clip_url = f"{cand.video_url}&t={match.start_time_seconds}"

            result_id_suffix = f"{shot.shot_id}_{idx + 1:03d}" if shot else f"{segment.segment_id}_{idx + 1:03d}"
            results.append(RankedResult(
                result_id=f"res_{result_id_suffix}",
                segment_id=segment.segment_id,
                shot_id=shot.shot_id if shot else None,
                shot_visual_need=shot.visual_need if shot else None,
                shot_intent=shot.shot_intent if shot else ShotIntent.LITERAL,
                scarcity=shot.scarcity if shot else Scarcity.COMMON,
                video_id=cand.video_id,
                video_url=cand.video_url,
                video_title=cand.video_title,
                channel_name=cand.channel_name,
                channel_subscribers=cand.channel_subscribers,
                thumbnail_url=cand.thumbnail_url,
                video_duration_seconds=cand.video_duration_seconds,
                published_at=cand.published_at,
                view_count=cand.view_count,
                start_time_seconds=match.start_time_seconds,
                end_time_seconds=match.end_time_seconds,
                clip_url=clip_url,
                transcript_excerpt=match.transcript_excerpt,
                the_hook=match.the_hook,
                relevance_note=match.relevance_note,
                match_reasoning=match.match_reasoning,
                relevance_score=rel_score,
                confidence_score=match.confidence_score,
                visual_fit=match.visual_fit,
                topical_fit=match.topical_fit,
                source_flag=match.source_flag,
                context_match=match.context_match,
                context_mismatch_reason=match.context_mismatch_reason,
            ))

        return results

    def deduplicate_across_segments(
        self, all_results: dict[str, list[RankedResult]]
    ) -> dict[str, list[RankedResult]]:
        """Light dedup: allow same video in different scenes if timestamps differ by 30s+."""
        seen: set[tuple[str, int]] = set()
        deduped: dict[str, list[RankedResult]] = {}

        for seg_id, results in all_results.items():
            filtered = []
            for r in results:
                key = (r.video_id, (r.start_time_seconds or 0) // 30)
                if key not in seen:
                    seen.add(key)
                    filtered.append(r)
            deduped[seg_id] = filtered if filtered else results[:1]

        return deduped

    @staticmethod
    def _intent_weighted_fit(match: MatchResult, shot_intent: ShotIntent) -> float:
        """Use visual_fit + topical_fit with intent-appropriate weights, fall back to confidence."""
        if match.visual_fit > 0 or match.topical_fit > 0:
            w_vis, w_top = INTENT_WEIGHTS.get(shot_intent, (0.5, 0.5))
            return w_vis * match.visual_fit + w_top * match.topical_fit
        return match.confidence_score

    @staticmethod
    def _viral_score(view_count: int) -> float:
        if view_count >= 1_000_000:
            return 1.0
        if view_count >= 100_000:
            return 0.8
        if view_count >= 10_000:
            return 0.5
        return 0.2

    @staticmethod
    def _channel_authority(cand: CandidateVideo, min_subs: int = 10000) -> float:
        if cand.is_preferred_tier1:
            return 1.0
        if cand.is_preferred_tier2:
            return 0.9
        subs = cand.channel_subscribers or 0
        if subs > 100_000:
            return 0.7
        if subs >= min_subs:
            return 0.5
        return 0.3

    @staticmethod
    def _caption_quality(source: TranscriptSource) -> float:
        if source in (TranscriptSource.YOUTUBE_MANUAL, TranscriptSource.CACHED):
            return 1.0
        if source == TranscriptSource.YOUTUBE_AUTO:
            return 0.8
        if source == TranscriptSource.WHISPER:
            return 0.6
        return 0.3

    @staticmethod
    def _has_negative_keyword(
        cand: CandidateVideo, match: MatchResult, negative_kw: list[str],
    ) -> bool:
        excerpt = (match.transcript_excerpt or "").lower()
        title = cand.video_title.lower()
        for kw in negative_kw:
            kw_l = kw.lower()
            if kw_l in title or kw_l in excerpt:
                return True
        return False

    @staticmethod
    def _context_relevance(
        cand: CandidateVideo, script_context: ScriptContext | None,
    ) -> float:
        if not script_context or not script_context.script_topic:
            return 0.5
        topic_words = set(script_context.script_topic.lower().split())
        title_words = set(cand.video_title.lower().split())
        if topic_words.intersection(title_words):
            return 1.0
        geo_words = set(script_context.geographic_scope.lower().replace(",", " ").split()) if script_context.geographic_scope else set()
        if geo_words.intersection(title_words):
            return 0.9
        return 0.4

    @staticmethod
    def _recency_score(published_at: str, cfg: dict) -> float:
        if not published_at:
            return 0.4
        try:
            pub_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            years = (now - pub_date).days / 365.25
            full_years = float(cfg.get("recency_full_score_years", 2))
            mid_years = float(cfg.get("recency_mid_score_years", 4))
            if years <= full_years:
                return 1.0
            if years <= mid_years:
                return 0.7
            return 0.4
        except Exception:
            return 0.4
