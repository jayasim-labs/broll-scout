"""
B-Roll Scout - API Cost Tracker
Tracks all external API calls and estimates costs per job.
"""

import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import threading
import logging

logger = logging.getLogger(__name__)


# Pricing as of 2024 (approximate)
PRICING = {
    # OpenAI GPT-4o pricing per 1M tokens
    "gpt4o_input": 5.00,      # $5 per 1M input tokens
    "gpt4o_output": 15.00,    # $15 per 1M output tokens
    
    # OpenAI GPT-4o-mini pricing per 1M tokens
    "gpt4o_mini_input": 0.15,  # $0.15 per 1M input tokens
    "gpt4o_mini_output": 0.60, # $0.60 per 1M output tokens
    
    # Whisper pricing per minute
    "whisper_per_minute": 0.006,  # $0.006 per minute
    
    # YouTube API - free within quota (10,000 units/day)
    # But we track units for quota management
    "youtube_search_unit": 100,      # 100 units per search
    "youtube_videos_unit": 1,        # 1 unit per video in list
    "youtube_captions_unit": 50,     # 50 units per caption download
    
    # Google Custom Search
    "google_cse_per_query": 0.005,   # ~$5 per 1000 queries
    
    # Gemini - free tier, but track for potential future billing
    "gemini_per_call": 0.00,
}


@dataclass
class JobCosts:
    """Tracks costs for a single job."""
    
    # OpenAI GPT-4o
    openai_gpt4o_calls: int = 0
    openai_gpt4o_input_tokens: int = 0
    openai_gpt4o_output_tokens: int = 0
    
    # OpenAI GPT-4o-mini
    openai_gpt4o_mini_calls: int = 0
    openai_gpt4o_mini_input_tokens: int = 0
    openai_gpt4o_mini_output_tokens: int = 0
    
    # Whisper
    whisper_calls: int = 0
    whisper_minutes: float = 0.0
    
    # YouTube API
    youtube_search_calls: int = 0
    youtube_video_detail_calls: int = 0
    youtube_caption_calls: int = 0
    youtube_api_units: int = 0
    
    # Google CSE
    google_cse_calls: int = 0
    
    # Gemini
    gemini_calls: int = 0
    
    # Lock for thread-safe updates
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def add_gpt4o(self, input_tokens: int, output_tokens: int) -> None:
        """Record a GPT-4o call."""
        with self._lock:
            self.openai_gpt4o_calls += 1
            self.openai_gpt4o_input_tokens += input_tokens
            self.openai_gpt4o_output_tokens += output_tokens
    
    def add_gpt4o_mini(self, input_tokens: int, output_tokens: int) -> None:
        """Record a GPT-4o-mini call."""
        with self._lock:
            self.openai_gpt4o_mini_calls += 1
            self.openai_gpt4o_mini_input_tokens += input_tokens
            self.openai_gpt4o_mini_output_tokens += output_tokens
    
    def add_whisper(self, minutes: float) -> None:
        """Record a Whisper call."""
        with self._lock:
            self.whisper_calls += 1
            self.whisper_minutes += minutes
    
    def add_youtube_search(self) -> None:
        """Record a YouTube search call."""
        with self._lock:
            self.youtube_search_calls += 1
            self.youtube_api_units += PRICING["youtube_search_unit"]
    
    def add_youtube_video_details(self, count: int = 1) -> None:
        """Record YouTube video detail lookups."""
        with self._lock:
            self.youtube_video_detail_calls += 1
            self.youtube_api_units += PRICING["youtube_videos_unit"] * count
    
    def add_youtube_captions(self) -> None:
        """Record a YouTube caption download."""
        with self._lock:
            self.youtube_caption_calls += 1
            self.youtube_api_units += PRICING["youtube_captions_unit"]
    
    def add_google_cse(self) -> None:
        """Record a Google CSE call."""
        with self._lock:
            self.google_cse_calls += 1
    
    def add_gemini(self) -> None:
        """Record a Gemini call."""
        with self._lock:
            self.gemini_calls += 1
    
    def calculate_cost(self) -> float:
        """Calculate total estimated cost in USD."""
        cost = 0.0
        
        # GPT-4o costs
        cost += (self.openai_gpt4o_input_tokens / 1_000_000) * PRICING["gpt4o_input"]
        cost += (self.openai_gpt4o_output_tokens / 1_000_000) * PRICING["gpt4o_output"]
        
        # GPT-4o-mini costs
        cost += (self.openai_gpt4o_mini_input_tokens / 1_000_000) * PRICING["gpt4o_mini_input"]
        cost += (self.openai_gpt4o_mini_output_tokens / 1_000_000) * PRICING["gpt4o_mini_output"]
        
        # Whisper costs
        cost += self.whisper_minutes * PRICING["whisper_per_minute"]
        
        # Google CSE costs
        cost += self.google_cse_calls * PRICING["google_cse_per_query"]
        
        return round(cost, 4)
    
    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary for storage."""
        return {
            "openai_calls": self.openai_gpt4o_calls,
            "openai_mini_calls": self.openai_gpt4o_mini_calls,
            "openai_gpt4o_input_tokens": self.openai_gpt4o_input_tokens,
            "openai_gpt4o_output_tokens": self.openai_gpt4o_output_tokens,
            "openai_gpt4o_mini_input_tokens": self.openai_gpt4o_mini_input_tokens,
            "openai_gpt4o_mini_output_tokens": self.openai_gpt4o_mini_output_tokens,
            "whisper_minutes": self.whisper_minutes,
            "youtube_api_units": self.youtube_api_units,
            "google_cse_calls": self.google_cse_calls,
            "gemini_calls": self.gemini_calls,
            "estimated_cost_usd": self.calculate_cost()
        }


class CostTracker:
    """
    Global cost tracker that manages per-job cost tracking.
    Thread-safe and async-safe for concurrent segment processing.
    """
    
    def __init__(self):
        self._jobs: Dict[str, JobCosts] = {}
        self._lock = threading.Lock()
    
    def start_job(self, job_id: str) -> JobCosts:
        """Start tracking costs for a new job."""
        with self._lock:
            costs = JobCosts()
            self._jobs[job_id] = costs
            return costs
    
    def get_job_costs(self, job_id: str) -> Optional[JobCosts]:
        """Get the cost tracker for a job."""
        with self._lock:
            return self._jobs.get(job_id)
    
    def end_job(self, job_id: str) -> Dict[str, Any]:
        """End tracking and return final costs."""
        with self._lock:
            costs = self._jobs.pop(job_id, None)
            if costs:
                return costs.to_dict()
            return {}
    
    @asynccontextmanager
    async def track_gpt4o(self, job_id: str):
        """
        Context manager for tracking GPT-4o calls.
        Yields a callback to record token usage.
        """
        costs = self.get_job_costs(job_id)
        
        class TokenRecorder:
            def __init__(self, costs: JobCosts):
                self._costs = costs
                
            def record(self, input_tokens: int, output_tokens: int):
                if self._costs:
                    self._costs.add_gpt4o(input_tokens, output_tokens)
        
        yield TokenRecorder(costs)
    
    @asynccontextmanager
    async def track_gpt4o_mini(self, job_id: str):
        """Context manager for tracking GPT-4o-mini calls."""
        costs = self.get_job_costs(job_id)
        
        class TokenRecorder:
            def __init__(self, costs: JobCosts):
                self._costs = costs
                
            def record(self, input_tokens: int, output_tokens: int):
                if self._costs:
                    self._costs.add_gpt4o_mini(input_tokens, output_tokens)
        
        yield TokenRecorder(costs)
    
    def track_whisper(self, job_id: str, minutes: float) -> None:
        """Track a Whisper API call."""
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_whisper(minutes)
    
    def track_youtube_search(self, job_id: str) -> None:
        """Track a YouTube search API call."""
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_youtube_search()
    
    def track_youtube_details(self, job_id: str, count: int = 1) -> None:
        """Track YouTube video detail lookups."""
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_youtube_video_details(count)
    
    def track_youtube_captions(self, job_id: str) -> None:
        """Track a YouTube caption download."""
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_youtube_captions()
    
    def track_google_cse(self, job_id: str) -> None:
        """Track a Google CSE call."""
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_google_cse()
    
    def track_gemini(self, job_id: str) -> None:
        """Track a Gemini call."""
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_gemini()


# Global singleton
_tracker: Optional[CostTracker] = None


def get_cost_tracker() -> CostTracker:
    """Get the global cost tracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker
