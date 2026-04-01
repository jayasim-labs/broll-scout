import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import DEFAULTS
from app.models.schemas import (
    Segment, CandidateVideo, MatchResult, RankedResult, TranscriptSource,
)

logger = logging.getLogger(__name__)


class RankerService:
    """Computes relevance scores, filters, and returns top ranked results."""

    def rank_and_filter(
        self,
        candidates: list[tuple[CandidateVideo, MatchResult]],
        segment: Segment,
        settings: dict | None = None,
    ) -> list[RankedResult]:
        cfg = settings or DEFAULTS

        w_kw = float(cfg.get("weight_keyword_density", 0.30))
        w_vs = float(cfg.get("weight_viral_score", 0.20))
        w_ca = float(cfg.get("weight_channel_authority", 0.20))
        w_cq = float(cfg.get("weight_caption_quality", 0.10))
        w_re = float(cfg.get("weight_recency", 0.20))
        total_w = w_kw + w_vs + w_ca + w_cq + w_re
        if total_w > 0 and abs(total_w - 1.0) > 0.01:
            w_kw /= total_w
            w_vs /= total_w
            w_ca /= total_w
            w_cq /= total_w
            w_re /= total_w

        threshold = float(cfg.get("confidence_threshold", 0.4))
        top_n = int(cfg.get("top_results_per_segment", 1))

        scored: list[tuple[CandidateVideo, MatchResult, float]] = []
        for cand, match in candidates:
            if cand.is_blocked:
                continue
            if match.start_time_seconds == 0 and match.transcript_excerpt:
                excerpt_lower = (match.transcript_excerpt[:200] or "").lower()
                if not any(t.lower() in excerpt_lower for t in segment.key_terms):
                    continue
            if not match.context_match_valid:
                continue
            if match.confidence_score < threshold:
                has_others = any(
                    m.confidence_score >= threshold and m.start_time_seconds is not None
                    for _, m in candidates
                )
                if has_others:
                    continue

            if match.source_flag == TranscriptSource.NONE:
                relevance = 0.3
            else:
                kw_score = self._keyword_density(segment.key_terms, match.transcript_excerpt)
                vs_score = self._viral_score(cand.view_count)
                ca_score = self._channel_authority(cand)
                cq_score = self._caption_quality(match.source_flag)
                re_score = self._recency_score(cand.published_at, cfg)
                relevance = (
                    w_kw * kw_score
                    + w_vs * vs_score
                    + w_ca * ca_score
                    + w_cq * cq_score
                    + w_re * re_score
                )

            scored.append((cand, match, round(min(1.0, max(0.0, relevance)), 4)))

        scored.sort(key=lambda x: x[2], reverse=True)

        if not scored and candidates:
            best_cand, best_match = candidates[0]
            scored = [(best_cand, best_match, 0.1)]

        results: list[RankedResult] = []
        for idx, (cand, match, rel_score) in enumerate(scored[:top_n]):
            clip_url = cand.video_url
            if match.start_time_seconds is not None:
                clip_url = f"{cand.video_url}&t={match.start_time_seconds}"

            results.append(RankedResult(
                result_id=f"res_{segment.segment_id}_{idx + 1:03d}",
                segment_id=segment.segment_id,
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
                relevance_score=rel_score,
                confidence_score=match.confidence_score,
                source_flag=match.source_flag,
            ))

        return results

    def deduplicate_across_segments(
        self, all_results: dict[str, list[RankedResult]]
    ) -> dict[str, list[RankedResult]]:
        video_best: dict[str, tuple[str, float]] = {}

        for seg_id, results in all_results.items():
            for r in results:
                existing = video_best.get(r.video_id)
                if existing is None or r.relevance_score > existing[1]:
                    video_best[r.video_id] = (seg_id, r.relevance_score)

        deduped: dict[str, list[RankedResult]] = {}
        for seg_id, results in all_results.items():
            filtered = []
            for r in results:
                best_seg, _ = video_best.get(r.video_id, (seg_id, 0))
                if best_seg == seg_id:
                    filtered.append(r)
            deduped[seg_id] = filtered if filtered else results[:1]

        return deduped

    @staticmethod
    def _keyword_density(key_terms: list[str], excerpt: str | None) -> float:
        if not excerpt or not key_terms:
            return 0.0
        excerpt_lower = excerpt.lower()
        matches = sum(1 for t in key_terms if t.lower() in excerpt_lower)
        return min(1.0, matches / len(key_terms))

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
    def _channel_authority(cand: CandidateVideo) -> float:
        if cand.is_preferred_tier1:
            return 1.0
        if cand.is_preferred_tier2:
            return 0.9
        if cand.channel_subscribers > 100_000:
            return 0.7
        return 0.4

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
