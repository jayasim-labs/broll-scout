"""
Translator Service - Converts raw script into timestamped visual cues
Uses OpenAI GPT-4o-mini to analyze script and generate visual search queries
"""

import json
import re
from typing import List, Optional
from datetime import datetime
import httpx

from app.config import get_settings
from app.models.schemas import VisualCue, TranslationResult
from app.utils.cost_tracker import get_cost_tracker


class TranslatorService:
    """Translates raw video scripts into timestamped visual cues."""
    
    def __init__(self):
        cfg = get_settings()
        self.api_key = cfg.openai_api_key
        self.model = "gpt-4o-mini"
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.cost_tracker = get_cost_tracker()
    
    async def translate_script(
        self,
        script_text: str,
        video_style: str = "documentary",
        target_audience: str = "general",
        job_id: Optional[str] = None
    ) -> TranslationResult:
        """
        Translate a script into visual cues.
        
        Args:
            script_text: The raw script text to analyze
            video_style: Style of video (documentary, corporate, educational, etc.)
            target_audience: Target audience for the video
            job_id: Optional job ID for cost tracking
            
        Returns:
            TranslationResult with list of visual cues
        """
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")
        
        system_prompt = self._build_system_prompt(video_style, target_audience)
        user_prompt = self._build_user_prompt(script_text)
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.7,
                        "response_format": {"type": "json_object"}
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                usage = result.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                
                if job_id:
                    job_costs = self.cost_tracker.get_job_costs(job_id)
                    if job_costs:
                        job_costs.add_gpt4o_mini(input_tokens, output_tokens)
                
                cost = (input_tokens / 1_000_000) * 0.15 + (output_tokens / 1_000_000) * 0.60
                
                # Parse the response
                content = result["choices"][0]["message"]["content"]
                visual_cues = self._parse_response(content)
                
                return TranslationResult(
                    visual_cues=visual_cues,
                    total_cues=len(visual_cues),
                    estimated_duration=self._estimate_duration(visual_cues),
                    cost=cost,
                    model_used=self.model
                )
                
        except httpx.HTTPStatusError as e:
            raise Exception(f"OpenAI API error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            raise Exception(f"Translation failed: {str(e)}")
    
    def _build_system_prompt(self, video_style: str, target_audience: str) -> str:
        """Build the system prompt for the translator."""
        return f"""You are an expert video editor assistant that analyzes scripts and identifies visual opportunities for b-roll footage.

Your task is to analyze the provided script and generate a list of visual cues - specific moments where b-roll footage would enhance the video.

Video Style: {video_style}
Target Audience: {target_audience}

For each visual cue, provide:
1. timestamp_start: Estimated start time in seconds (based on ~150 words per minute speaking rate)
2. timestamp_end: Estimated end time in seconds
3. script_excerpt: The exact text from the script this cue relates to
4. visual_description: A clear description of what visual would work here
5. search_queries: 2-3 specific search queries for finding this footage on stock sites
6. mood: The emotional tone (cinematic, energetic, calm, professional, etc.)
7. priority: high, medium, or low based on visual impact
8. notes: Any additional notes for the editor

Respond with valid JSON in this format:
{{
    "visual_cues": [
        {{
            "timestamp_start": 0,
            "timestamp_end": 5,
            "script_excerpt": "exact text from script",
            "visual_description": "description of ideal visual",
            "search_queries": ["query 1", "query 2"],
            "mood": "cinematic",
            "priority": "high",
            "notes": "optional notes"
        }}
    ]
}}

Focus on:
- Key concepts that benefit from visual support
- Transitions between topics
- Emotional moments that need visual reinforcement
- Abstract concepts that need concrete visualization
- Data or statistics that could use visual representation"""

    def _build_user_prompt(self, script_text: str) -> str:
        """Build the user prompt with the script."""
        return f"""Analyze this script and generate visual cues for b-roll placement:

---
{script_text}
---

Generate comprehensive visual cues for this script. Identify at least one visual opportunity per major point or every 10-15 seconds of estimated runtime."""

    def _parse_response(self, content: str) -> List[VisualCue]:
        """Parse the API response into VisualCue objects."""
        try:
            data = json.loads(content)
            cues = []
            
            for idx, cue_data in enumerate(data.get("visual_cues", [])):
                cue = VisualCue(
                    id=f"cue_{idx + 1}",
                    timestamp_start=float(cue_data.get("timestamp_start", 0)),
                    timestamp_end=float(cue_data.get("timestamp_end", 0)),
                    script_excerpt=cue_data.get("script_excerpt", ""),
                    visual_description=cue_data.get("visual_description", ""),
                    search_queries=cue_data.get("search_queries", []),
                    mood=cue_data.get("mood", "neutral"),
                    priority=cue_data.get("priority", "medium"),
                    notes=cue_data.get("notes", ""),
                    status="pending"
                )
                cues.append(cue)
            
            return cues
            
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse translator response: {str(e)}")
    
    def _estimate_duration(self, cues: List[VisualCue]) -> float:
        """Estimate total video duration based on cues."""
        if not cues:
            return 0.0
        return max(cue.timestamp_end for cue in cues)


# Singleton instance
translator_service = TranslatorService()
