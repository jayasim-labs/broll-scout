import logging
from functools import lru_cache

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    openai_api_key: str = ""
    gemini_api_key: str = ""
    youtube_api_key: str = ""
    google_search_api_key: str = ""
    google_search_cx: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    dynamodb_table_prefix: str = "broll_"
    api_key: str = ""
    debug: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


DEFAULTS = {
    "search_queries_per_segment": 3,
    "youtube_results_per_query": 5,
    "max_candidates_per_segment": 12,
    "top_results_per_segment": 3,
    "total_results_target": 30,
    "gemini_expanded_queries": 5,

    "timestamp_model": "gpt-4o-mini",
    "translation_model": "gpt-4o",
    "confidence_threshold": 0.15,
    "whisper_max_video_duration_min": 60,
    "whisper_audio_trim_min": 20,
    "transcript_excerpt_max_words": 200,

    "min_video_duration_sec": 120,
    "max_video_duration_sec": 5400,
    "prefer_min_subscribers": 10000,
    "recency_full_score_years": 2,
    "recency_mid_score_years": 4,

    "weight_ai_confidence": 0.40,
    "weight_keyword_density": 0.15,
    "weight_viral_score": 0.15,
    "weight_channel_authority": 0.10,
    "weight_caption_quality": 0.05,
    "weight_recency": 0.15,

    "max_concurrent_segments": 5,
    "max_concurrent_candidates": 3,
    "segment_timeout_sec": 300,
    "retry_attempts": 3,
    "retry_backoff_base_sec": 1,
    "youtube_api_batch_size": 50,
    "low_result_threshold": 20,
    "search_backend": "ytdlp_only",
    "enable_gemini_expansion": False,
    "min_total_results_for_long_scripts": 30,

    "preferred_channels_tier1": [
        "UC_5jTJ1XNWcq9FOWX6Q7hCg",
        "UC_7Lda9tyy13VZcR2LD2WtA",
        "UCvdAeRBfFBiw2JhQFEYv2zw",
        "UC79E_7rLuWtFFZvJOo-8KuA",
        "UCvZDzlgIh1inM_wohqhsviQ",
        "UCPtnYLYSize_pzKA8YE8wZw",
        "UCWxqSvqiH8PGpSNPSVtFwGw",
        "UCzmI2CEnbKq0yswTpqdQfyg",
        "UCzUBKNqxSedgYiTOMCP4eQA",
        "UC7o-UFkoAPCoKxpKOfrs4zQ",
        "UCUYK0BJZF3yNb2fw1EdAXUQ",
    ],

    "preferred_channels_tier2": [
        "Kurzgesagt",
        "Real Engineering",
        "Wendover Productions",
        "PolyMatter",
        "Johnny Harris",
        "Visual Politik",
        "CNA Insider",
        "The Economist",
        "Half as Interesting",
        "Mustard",
        "TED",
        "TED-Ed",
    ],

    "blocked_networks": [
        "CNN", "BBC News", "Fox News", "Al Jazeera", "NBC", "ABC", "Sky News",
        "FirstPost", "ANI", "AP", "NDTV", "WION", "Reuters",
    ],
    "blocked_studios": [
        "Disney", "Warner Bros", "Universal", "Sony", "Paramount", "Netflix",
        "Amazon Studios", "HBO", "Lionsgate",
    ],
    "blocked_sports": [
        "FIFA", "NFL", "NBA", "IPL", "Formula 1", "UEFA", "ICC", "WWE",
    ],
    "custom_block_rules": "",

    "special_instructions": (
        "- Prioritize archival footage and historical documentaries over news clips\n"
        "- Prefer long-form documentary content over short news segments\n"
        "- For geopolitics topics, look for neutral analysis channels rather than partisan news\n"
        "- For science/technology topics, prefer channels with scientific visualizations and animations\n"
        "- Avoid footage that appears to be ripped from movies, TV shows, or copyrighted broadcasts\n"
        "- When multiple clips are similar, prefer the one with higher production quality"
    ),

    "enable_context_matching": True,
    "discard_clips_shorter_than_10s": True,
    "verify_timestamp_not_end_screen": True,
    "cap_end_timestamp": True,

    "public_domain_archives": [
        {"name": "Prelinger Archives", "url": "https://archive.org/details/prelinger"},
        {"name": "Public Domain Review", "url": "https://publicdomainreview.org/collections/film/"},
    ],

    "stock_platforms": {
        "pexels": True,
        "pixabay": True,
        "wikimedia_commons": True,
        "nasa": True,
        "esa": True,
        "national_archives": True,
        "library_of_congress": True,
    },
}
