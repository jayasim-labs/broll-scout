"""
Ranker Service - Ranks and selects optimal clips for each cue
Applies user preferences, diversity scoring, and final selection
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

from app.models.schemas import (
    StockClip, VisualCue, MatchResult, RankedSelection,
    SelectionPreferences
)


@dataclass
class RankingWeights:
    """Weights for different ranking factors."""
    relevance: float = 0.4
    quality: float = 0.2
    duration: float = 0.15
    diversity: float = 0.15
    recency: float = 0.1


class RankerService:
    """Ranks clips and makes final selections based on multiple criteria."""
    
    def __init__(self):
        self.default_weights = RankingWeights()
        self.quality_scores = {
            "4K": 1.0,
            "1080p": 0.9,
            "720p": 0.7,
            "480p": 0.5,
            "SD": 0.3
        }
    
    def rank_matches(
        self,
        match_results: Dict[str, MatchResult],
        cues: List[VisualCue],
        preferences: Optional[SelectionPreferences] = None,
        weights: Optional[RankingWeights] = None
    ) -> Dict[str, RankedSelection]:
        """
        Rank matches for all cues and create final selections.
        
        Args:
            match_results: Dictionary of cue_id -> MatchResult
            cues: List of visual cues
            preferences: User preferences for selection
            weights: Custom ranking weights
            
        Returns:
            Dictionary of cue_id -> RankedSelection
        """
        weights = weights or self.default_weights
        preferences = preferences or SelectionPreferences()
        
        # Create cue lookup
        cue_map = {cue.id: cue for cue in cues}
        
        # Track used clips for diversity
        used_clips = set()
        selections = {}
        
        # Process cues in priority order
        sorted_cues = sorted(
            cues,
            key=lambda c: self._priority_value(c.priority),
            reverse=True
        )
        
        for cue in sorted_cues:
            match_result = match_results.get(cue.id)
            if not match_result or not match_result.matches:
                selections[cue.id] = RankedSelection(
                    cue_id=cue.id,
                    selected_clip=None,
                    alternatives=[],
                    ranking_scores={},
                    selection_reason="No matching clips found"
                )
                continue
            
            # Score all clips for this cue
            scored_clips = []
            for clip in match_result.matches:
                score, breakdown = self._calculate_final_score(
                    clip=clip,
                    cue=cue,
                    used_clips=used_clips,
                    preferences=preferences,
                    weights=weights
                )
                scored_clips.append((clip, score, breakdown))
            
            # Sort by score
            scored_clips.sort(key=lambda x: x[1], reverse=True)
            
            if scored_clips:
                best_clip, best_score, score_breakdown = scored_clips[0]
                
                # Mark as used for diversity
                used_clips.add(best_clip.id)
                
                # Get alternatives
                alternatives = [clip for clip, _, _ in scored_clips[1:preferences.max_alternatives]]
                
                selections[cue.id] = RankedSelection(
                    cue_id=cue.id,
                    selected_clip=best_clip,
                    alternatives=alternatives,
                    ranking_scores=score_breakdown,
                    final_score=best_score,
                    selection_reason=self._generate_selection_reason(score_breakdown)
                )
            else:
                selections[cue.id] = RankedSelection(
                    cue_id=cue.id,
                    selected_clip=None,
                    alternatives=[],
                    ranking_scores={},
                    selection_reason="No clips met minimum criteria"
                )
        
        return selections
    
    def _calculate_final_score(
        self,
        clip: StockClip,
        cue: VisualCue,
        used_clips: set,
        preferences: SelectionPreferences,
        weights: RankingWeights
    ) -> Tuple[float, Dict[str, float]]:
        """
        Calculate final ranking score for a clip.
        
        Returns:
            Tuple of (final_score, score_breakdown)
        """
        breakdown = {}
        
        # Relevance score (from matcher)
        relevance_score = clip.relevance_score or 0.5
        breakdown["relevance"] = relevance_score
        
        # Quality score
        quality_score = self.quality_scores.get(clip.quality, 0.5)
        if preferences.min_quality:
            min_quality_score = self.quality_scores.get(preferences.min_quality, 0)
            if quality_score < min_quality_score:
                quality_score *= 0.5  # Penalty for not meeting minimum
        breakdown["quality"] = quality_score
        
        # Duration score
        needed_duration = cue.timestamp_end - cue.timestamp_start
        duration_score = self._calculate_duration_score(clip.duration, needed_duration)
        breakdown["duration"] = duration_score
        
        # Diversity score (prefer clips not used elsewhere)
        diversity_score = 0.0 if clip.id in used_clips else 1.0
        breakdown["diversity"] = diversity_score
        
        # Recency score (placeholder - could use upload date if available)
        recency_score = 0.7  # Default neutral score
        breakdown["recency"] = recency_score
        
        # Calculate weighted final score
        final_score = (
            weights.relevance * relevance_score +
            weights.quality * quality_score +
            weights.duration * duration_score +
            weights.diversity * diversity_score +
            weights.recency * recency_score
        )
        
        # Apply preference boosts/penalties
        if preferences.preferred_sources:
            if clip.source in preferences.preferred_sources:
                final_score *= 1.1
            else:
                final_score *= 0.9
        
        if preferences.preferred_moods and clip.mood_match:
            if any(mood in clip.mood_match for mood in preferences.preferred_moods):
                final_score *= 1.05
        
        breakdown["final"] = final_score
        return final_score, breakdown
    
    def _calculate_duration_score(self, clip_duration: float, needed_duration: float) -> float:
        """
        Score based on how well clip duration matches need.
        
        Ideal: clip is 1.5x - 3x needed duration (allows for trimming)
        Too short: heavy penalty
        Too long: slight penalty (more trimming work)
        """
        if needed_duration <= 0:
            return 0.5
        
        ratio = clip_duration / needed_duration
        
        if ratio < 0.5:
            return 0.1  # Way too short
        elif ratio < 1.0:
            return 0.3 + (ratio - 0.5) * 0.8  # Linear increase from 0.3 to 0.7
        elif ratio < 1.5:
            return 0.7 + (ratio - 1.0) * 0.6  # 0.7 to 1.0 (ideal zone)
        elif ratio < 3.0:
            return 1.0  # Ideal range
        elif ratio < 5.0:
            return 1.0 - (ratio - 3.0) * 0.1  # Slight penalty for very long
        else:
            return 0.8  # Long but usable
    
    def _priority_value(self, priority: str) -> int:
        """Convert priority string to numeric value."""
        priorities = {"high": 3, "medium": 2, "low": 1}
        return priorities.get(priority, 2)
    
    def _generate_selection_reason(self, breakdown: Dict[str, float]) -> str:
        """Generate human-readable selection reason."""
        reasons = []
        
        if breakdown.get("relevance", 0) > 0.8:
            reasons.append("highly relevant content")
        elif breakdown.get("relevance", 0) > 0.6:
            reasons.append("good content match")
        
        if breakdown.get("quality", 0) > 0.8:
            reasons.append("high quality")
        
        if breakdown.get("duration", 0) > 0.8:
            reasons.append("ideal duration")
        
        if breakdown.get("diversity", 0) > 0.5:
            reasons.append("unique selection")
        
        if not reasons:
            reasons.append("best available match")
        
        return f"Selected for: {', '.join(reasons)}"
    
    def get_selection_summary(
        self,
        selections: Dict[str, RankedSelection],
        cues: List[VisualCue]
    ) -> Dict:
        """
        Generate a summary of the selection results.
        
        Returns:
            Summary statistics and quality metrics
        """
        total_cues = len(cues)
        filled_cues = sum(1 for s in selections.values() if s.selected_clip)
        
        avg_score = 0.0
        quality_distribution = defaultdict(int)
        source_distribution = defaultdict(int)
        
        for selection in selections.values():
            if selection.selected_clip:
                avg_score += selection.final_score or 0
                quality_distribution[selection.selected_clip.quality] += 1
                source_distribution[selection.selected_clip.source] += 1
        
        if filled_cues > 0:
            avg_score /= filled_cues
        
        return {
            "total_cues": total_cues,
            "filled_cues": filled_cues,
            "fill_rate": filled_cues / total_cues if total_cues > 0 else 0,
            "average_score": avg_score,
            "quality_distribution": dict(quality_distribution),
            "source_distribution": dict(source_distribution),
            "unfilled_cues": [
                cue.id for cue in cues
                if cue.id in selections and not selections[cue.id].selected_clip
            ]
        }


# Singleton instance
ranker_service = RankerService()
