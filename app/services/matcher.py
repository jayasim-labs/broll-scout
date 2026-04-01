"""
Matcher Service - Analyzes stock clips and matches them to visual cues
Uses AI vision to analyze clip content and semantic matching
"""

import json
from typing import List, Optional, Dict, Any
import httpx

from app.config import get_settings
from app.models.schemas import (
    StockClip, VisualCue, MatchResult, ClipAnalysis,
    SearchResult
)
from app.utils.cost_tracker import get_cost_tracker


class MatcherService:
    """Matches stock clips to visual cues using AI analysis."""
    
    def __init__(self):
        cfg = get_settings()
        self.api_key = cfg.openai_api_key
        self.model = "gpt-4o-mini"
        self.vision_model = "gpt-4o-mini"
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.cost_tracker = get_cost_tracker()
    
    async def analyze_clip(
        self,
        clip: StockClip,
        cue: VisualCue,
        job_id: Optional[str] = None
    ) -> ClipAnalysis:
        """
        Analyze a clip's relevance to a visual cue.
        
        Args:
            clip: The stock clip to analyze
            cue: The visual cue to match against
            job_id: Optional job ID for cost tracking
            
        Returns:
            ClipAnalysis with relevance scores and metadata
        """
        # For MVP, use text-based analysis
        # Future: Use vision API to analyze preview frames
        
        analysis = await self._text_based_analysis(clip, cue, job_id)
        return analysis
    
    async def match_clips_to_cue(
        self,
        clips: List[StockClip],
        cue: VisualCue,
        top_k: int = 5,
        job_id: Optional[str] = None
    ) -> MatchResult:
        """
        Match and rank clips for a visual cue.
        
        Args:
            clips: List of candidate clips
            cue: The visual cue to match
            top_k: Number of top matches to return
            job_id: Optional job ID for tracking
            
        Returns:
            MatchResult with ranked clips
        """
        if not clips:
            return MatchResult(
                cue_id=cue.id,
                matches=[],
                total_analyzed=0
            )
        
        # Batch analyze clips
        analyses = []
        for clip in clips:
            try:
                analysis = await self.analyze_clip(clip, cue, job_id)
                analyses.append((clip, analysis))
            except Exception as e:
                print(f"Failed to analyze clip {clip.id}: {str(e)}")
                continue
        
        # Sort by relevance score
        analyses.sort(key=lambda x: x[1].relevance_score, reverse=True)
        
        # Get top matches
        top_matches = []
        for clip, analysis in analyses[:top_k]:
            clip.relevance_score = analysis.relevance_score
            clip.match_reasons = analysis.match_reasons
            clip.analysis = analysis
            top_matches.append(clip)
        
        return MatchResult(
            cue_id=cue.id,
            matches=top_matches,
            total_analyzed=len(analyses)
        )
    
    async def match_search_results(
        self,
        search_results: List[SearchResult],
        cues: List[VisualCue],
        top_k_per_cue: int = 3,
        job_id: Optional[str] = None
    ) -> Dict[str, MatchResult]:
        """
        Match search results to their corresponding cues.
        
        Args:
            search_results: List of search results
            cues: List of visual cues
            top_k_per_cue: Top matches per cue
            job_id: Optional job ID
            
        Returns:
            Dictionary mapping cue_id to MatchResult
        """
        # Create cue lookup
        cue_map = {cue.id: cue for cue in cues}
        
        results = {}
        for search_result in search_results:
            cue = cue_map.get(search_result.cue_id)
            if not cue:
                continue
            
            match_result = await self.match_clips_to_cue(
                clips=search_result.clips,
                cue=cue,
                top_k=top_k_per_cue,
                job_id=job_id
            )
            results[cue.id] = match_result
        
        return results
    
    async def _text_based_analysis(
        self,
        clip: StockClip,
        cue: VisualCue,
        job_id: Optional[str] = None
    ) -> ClipAnalysis:
        """
        Perform text-based relevance analysis using LLM.
        """
        if not self.api_key:
            # Fallback to simple keyword matching
            return self._simple_keyword_match(clip, cue)
        
        prompt = self._build_analysis_prompt(clip, cue)
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self._get_system_prompt()},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3,
                        "response_format": {"type": "json_object"}
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                usage = result.get("usage", {})
                if job_id:
                    job_costs = self.cost_tracker.get_job_costs(job_id)
                    if job_costs:
                        job_costs.add_gpt4o_mini(
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0)
                        )
                
                # Parse response
                content = result["choices"][0]["message"]["content"]
                return self._parse_analysis(content, clip.id, cue.id)
                
        except Exception as e:
            print(f"AI analysis failed, using keyword match: {str(e)}")
            return self._simple_keyword_match(clip, cue)
    
    def _get_system_prompt(self) -> str:
        """System prompt for clip analysis."""
        return """You are a video editing assistant that evaluates how well stock footage clips match visual requirements.

Analyze the provided clip metadata against the visual cue requirements and score the match.

Respond with JSON in this format:
{
    "relevance_score": 0.0-1.0,
    "match_reasons": ["reason 1", "reason 2"],
    "mood_match": true/false,
    "duration_suitable": true/false,
    "quality_assessment": "excellent/good/fair/poor",
    "concerns": ["concern 1"],
    "recommendation": "strong_match/good_match/weak_match/no_match"
}"""

    def _build_analysis_prompt(self, clip: StockClip, cue: VisualCue) -> str:
        """Build analysis prompt for a clip-cue pair."""
        return f"""Evaluate this stock footage clip against the visual requirement:

**Visual Requirement (Cue):**
- Description: {cue.visual_description}
- Script Context: {cue.script_excerpt}
- Desired Mood: {cue.mood}
- Duration Needed: {cue.timestamp_end - cue.timestamp_start:.1f} seconds
- Search Queries Used: {', '.join(cue.search_queries)}

**Stock Clip Metadata:**
- Source: {clip.source}
- Duration: {clip.duration} seconds
- Quality: {clip.quality}
- Resolution: {clip.width}x{clip.height}
- Found via query: {clip.source_query or 'N/A'}

Analyze how well this clip matches the requirement."""

    def _parse_analysis(self, content: str, clip_id: str, cue_id: str) -> ClipAnalysis:
        """Parse LLM analysis response."""
        try:
            data = json.loads(content)
            return ClipAnalysis(
                clip_id=clip_id,
                cue_id=cue_id,
                relevance_score=float(data.get("relevance_score", 0.5)),
                match_reasons=data.get("match_reasons", []),
                mood_match=data.get("mood_match", False),
                duration_suitable=data.get("duration_suitable", True),
                quality_assessment=data.get("quality_assessment", "fair"),
                concerns=data.get("concerns", []),
                recommendation=data.get("recommendation", "weak_match")
            )
        except Exception:
            return ClipAnalysis(
                clip_id=clip_id,
                cue_id=cue_id,
                relevance_score=0.5,
                match_reasons=["Analysis parsing failed"],
                mood_match=False,
                duration_suitable=True,
                quality_assessment="unknown",
                concerns=["Could not fully analyze"],
                recommendation="weak_match"
            )
    
    def _simple_keyword_match(self, clip: StockClip, cue: VisualCue) -> ClipAnalysis:
        """Simple keyword-based matching as fallback."""
        # Extract keywords from cue
        cue_keywords = set()
        for query in cue.search_queries:
            cue_keywords.update(query.lower().split())
        cue_keywords.update(cue.visual_description.lower().split())
        
        # Check clip metadata
        clip_text = f"{clip.title} {clip.description} {' '.join(clip.tags)}".lower()
        
        # Count matches
        matches = sum(1 for kw in cue_keywords if kw in clip_text)
        score = min(matches / max(len(cue_keywords), 1), 1.0)
        
        # Duration check
        needed_duration = cue.timestamp_end - cue.timestamp_start
        duration_ok = clip.duration >= needed_duration * 0.5
        
        return ClipAnalysis(
            clip_id=clip.id,
            cue_id=cue.id,
            relevance_score=score,
            match_reasons=[f"Keyword overlap: {matches} terms"],
            mood_match=False,
            duration_suitable=duration_ok,
            quality_assessment="unknown",
            concerns=[] if duration_ok else ["Clip may be too short"],
            recommendation="good_match" if score > 0.6 else "weak_match"
        )


# Singleton instance
matcher_service = MatcherService()
