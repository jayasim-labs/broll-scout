import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PRICING = {
    "gpt4o_input_per_1k": 0.005,
    "gpt4o_output_per_1k": 0.015,
    "gpt4o_mini_input_per_1k": 0.00015,
    "gpt4o_mini_output_per_1k": 0.0006,
    "whisper_per_minute": 0.006,
    "youtube_search_units": 100,
    "youtube_details_per_id": 1,
    "google_cse_per_call": 0.005,
    "gemini_flash_per_call": 0.0001,
    # AWS infrastructure (monthly)
    "ec2_t3_small_monthly": 16.56,     # t3.small on-demand us-east-1
    "dynamodb_monthly_estimate": 1.00,  # ~1GB storage, minimal read/write
    "route53_monthly": 0.50,            # hosted zone
    "aws_monthly_total": 18.06,         # sum of above
}


@dataclass
class JobCosts:
    """Tracks API usage for a single job."""
    openai_gpt4o_calls: int = 0
    openai_gpt4o_mini_calls: int = 0
    openai_gpt4o_input_tokens: int = 0
    openai_gpt4o_output_tokens: int = 0
    openai_gpt4o_mini_input_tokens: int = 0
    openai_gpt4o_mini_output_tokens: int = 0
    whisper_minutes: float = 0.0
    whisper_calls: int = 0
    youtube_api_units: int = 0
    google_cse_calls: int = 0
    gemini_calls: int = 0
    local_matcher_calls: int = 0
    local_matcher_total_latency_ms: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_gpt4o(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.openai_gpt4o_calls += 1
            self.openai_gpt4o_input_tokens += input_tokens
            self.openai_gpt4o_output_tokens += output_tokens

    def add_gpt4o_mini(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.openai_gpt4o_mini_calls += 1
            self.openai_gpt4o_mini_input_tokens += input_tokens
            self.openai_gpt4o_mini_output_tokens += output_tokens

    def add_whisper(self, minutes: float) -> None:
        with self._lock:
            self.whisper_calls += 1
            self.whisper_minutes += minutes

    def add_youtube_search(self) -> None:
        with self._lock:
            self.youtube_api_units += PRICING["youtube_search_units"]

    def add_youtube_details(self, count: int = 1) -> None:
        with self._lock:
            self.youtube_api_units += count * PRICING["youtube_details_per_id"]

    def add_google_cse(self) -> None:
        with self._lock:
            self.google_cse_calls += 1

    def add_gemini(self) -> None:
        with self._lock:
            self.gemini_calls += 1

    def add_local_match(self, latency_ms: int = 0) -> None:
        with self._lock:
            self.local_matcher_calls += 1
            self.local_matcher_total_latency_ms += latency_ms

    def calculate_cost(self) -> float:
        cost = 0.0
        cost += (self.openai_gpt4o_input_tokens / 1000) * PRICING["gpt4o_input_per_1k"]
        cost += (self.openai_gpt4o_output_tokens / 1000) * PRICING["gpt4o_output_per_1k"]
        cost += (self.openai_gpt4o_mini_input_tokens / 1000) * PRICING["gpt4o_mini_input_per_1k"]
        cost += (self.openai_gpt4o_mini_output_tokens / 1000) * PRICING["gpt4o_mini_output_per_1k"]
        cost += self.whisper_minutes * PRICING["whisper_per_minute"]
        cost += self.google_cse_calls * PRICING["google_cse_per_call"]
        cost += self.gemini_calls * PRICING["gemini_flash_per_call"]
        return round(cost, 4)

    def to_dict(self) -> Dict:
        return {
            "openai_calls": self.openai_gpt4o_calls,
            "openai_mini_calls": self.openai_gpt4o_mini_calls,
            "openai_input_tokens": self.openai_gpt4o_input_tokens + self.openai_gpt4o_mini_input_tokens,
            "openai_output_tokens": self.openai_gpt4o_output_tokens + self.openai_gpt4o_mini_output_tokens,
            "gpt4o_input_tokens": self.openai_gpt4o_input_tokens,
            "gpt4o_output_tokens": self.openai_gpt4o_output_tokens,
            "gpt4o_mini_input_tokens": self.openai_gpt4o_mini_input_tokens,
            "gpt4o_mini_output_tokens": self.openai_gpt4o_mini_output_tokens,
            "whisper_minutes": self.whisper_minutes,
            "whisper_calls": self.whisper_calls,
            "youtube_api_units": self.youtube_api_units,
            "google_cse_calls": self.google_cse_calls,
            "gemini_calls": self.gemini_calls,
            "local_matcher_calls": self.local_matcher_calls,
            "local_matcher_avg_latency_ms": (
                round(self.local_matcher_total_latency_ms / self.local_matcher_calls)
                if self.local_matcher_calls > 0 else 0
            ),
            "estimated_cost_usd": self.calculate_cost(),
        }


class CostTracker:
    """Global cost tracker with per-job tracking."""

    def __init__(self):
        self._jobs: Dict[str, JobCosts] = {}
        self._lock = threading.Lock()

    def start_job(self, job_id: str) -> JobCosts:
        with self._lock:
            costs = JobCosts()
            self._jobs[job_id] = costs
            return costs

    def get_job_costs(self, job_id: str) -> Optional[JobCosts]:
        with self._lock:
            return self._jobs.get(job_id)

    def end_job(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            costs = self._jobs.pop(job_id, None)
            return costs.to_dict() if costs else None

    def track_youtube_search(self, job_id: str) -> None:
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_youtube_search()

    def track_youtube_details(self, job_id: str, count: int = 1) -> None:
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_youtube_details(count)

    def track_google_cse(self, job_id: str) -> None:
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_google_cse()

    def track_gemini(self, job_id: str) -> None:
        costs = self.get_job_costs(job_id)
        if costs:
            costs.add_gemini()


_tracker: Optional[CostTracker] = None


def get_cost_tracker() -> CostTracker:
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker
