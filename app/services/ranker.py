import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import DEFAULTS
from app.models.schemas import (
    ScriptContext, Segment, CandidateVideo, MatchResult, RankedResult, TranscriptSource,
)

logger = logging.getLogger(__name__)


class RankerService:
    """Computes relevance scores, filters, and returns top ranked results."""

    def rank_and_filter(
        self,
        candidates: list[tuple[CandidateVideo, MatchResult]],
        segment: Segment,
        settings: dict | None = None,
        script_context: ScriptContext | None = None,
    ) -> list[RankedResult]:
        cfg = settings or DEFAULTS

        w_ai = float(cfg.get("weight_ai_confidence", 0.35))
        w_kw = float(cfg.get("weight_keyword_density", 0.10))
        w_vs = float(cfg.get("weight_viral_score", 0.15))
        w_ca = float(cfg.get("weight_channel_authority", 0.10))
        w_cq = float(cfg.get("weight_caption_quality", 0.05))
        w_re = float(cfg.get("weight_recency", 0.10))
        w_cx = float(cfg.get("weight_context_relevance", 0.15))
        total_w = w_ai + w_kw + w_vs + w_ca + w_cq + w_re + w_cx
        if total_w > 0 and abs(total_w - 1.0) > 0.01:
            w_ai /= total_w
            w_kw /= total_w
            w_vs /= total_w
            w_ca /= total_w
            w_cq /= total_w
            w_re /= total_w
            w_cx /= total_w

        threshold = float(cfg.get("confidence_threshold", 0.4))
        top_n = int(cfg.get("top_results_per_segment", 1))

        min_subs = int(cfg.get("prefer_min_subscribers", 0))
        negative_kw = segment.negative_keywords or []

        scored: list[tuple[CandidateVideo, MatchResult, float]] = []
        for cand, match in candidates:
            if cand.is_blocked:
                continue
            if not match.context_match_valid:
                continue
            if not match.context_match:
                logger.info("Rejected %s: context mismatch — %s", cand.video_id, match.context_mismatch_reason)
                continue
            if match.confidence_score <= 0 and match.start_time_seconds is None:
                continue

            if negative_kw and self._has_negative_keyword(cand, match, negative_kw):
                logger.info("Rejected %s: negative keyword hit", cand.video_id)
                continue

            if match.source_flag == TranscriptSource.NONE:
                relevance = 0.3
            else:
                ai_score = match.confidence_score
                kw_score = self._keyword_density(segment.key_terms, match.transcript_excerpt)
                vs_score = self._viral_score(cand.view_count)
                ca_score = self._channel_authority(cand, min_subs)
                cq_score = self._caption_quality(match.source_flag)
                re_score = self._recency_score(cand.published_at, cfg)
                cx_score = self._context_relevance(cand, script_context)
                relevance = (
                    w_ai * ai_score
                    + w_kw * kw_score
                    + w_vs * vs_score
                    + w_ca * ca_score
                    + w_cq * cq_score
                    + w_re * re_score
                    + w_cx * cx_score
                )

            scored.append((cand, match, round(min(1.0, max(0.0, relevance)), 4)))

        scored.sort(key=lambda x: x[2], reverse=True)

        if not scored and candidates:
            for cand, match in candidates:
                if match.start_time_seconds is not None and match.confidence_score > 0:
                    scored = [(cand, match, 0.1)]
                    break

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
                relevance_note=match.relevance_note,
                relevance_score=rel_score,
                confidence_score=match.confidence_score,
                source_flag=match.source_flag,
                context_match=match.context_match,
                context_mismatch_reason=match.context_mismatch_reason,
            ))

        return results

    def deduplicate_across_segments(
        self, all_results: dict[str, list[RankedResult]]
    ) -> dict[str, list[RankedResult]]:
        """Light dedup: allow same video in different scenes if timestamps differ.
        Only remove exact duplicates (same video + overlapping timestamp)."""
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
