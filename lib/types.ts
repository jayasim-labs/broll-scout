export type JobStatus = "pending" | "processing" | "complete" | "partial" | "failed"

export type TranscriptSource =
  | "cached_transcript"
  | "youtube_captions"
  | "youtube_auto_captions"
  | "whisper_transcription"
  | "no_transcript"

export interface ActivityEntry {
  time: string
  icon: "brain" | "search" | "globe" | "sparkles" | "filter" | "check" | "alert" | "zap" | "eye" | "mic" | "clock" | "shield"
  text: string
  depth?: number
  group?: string
}

export interface JobProgress {
  stage: "queued" | "translating" | "searching" | "matching" | "ranking" | "completed" | "failed"
  percent_complete: number
  message: string
  activity_log: ActivityEntry[]
}

export interface APICosts {
  openai_calls: number
  openai_mini_calls: number
  openai_input_tokens: number
  openai_output_tokens: number
  whisper_minutes: number
  whisper_calls: number
  youtube_api_units: number
  google_cse_calls: number
  gemini_calls: number
  ytdlp_searches: number
  ytdlp_detail_lookups: number
  local_matcher_calls: number
  local_matcher_avg_latency_ms: number
  estimated_cost_usd: number
  search_mode?: string
  quota_exhausted?: boolean
}

export interface BRollShot {
  shot_id: string
  visual_need: string
  search_queries: string[]
  key_terms: string[]
}

export interface RankedResult {
  result_id: string
  segment_id: string
  shot_id: string | null
  shot_visual_need: string | null
  video_id: string
  video_url: string
  video_title: string
  channel_name: string
  channel_subscribers: number
  thumbnail_url: string
  video_duration_seconds: number
  published_at: string
  view_count: number
  start_time_seconds: number | null
  end_time_seconds: number | null
  clip_url: string | null
  transcript_excerpt: string | null
  the_hook: string | null
  relevance_note: string | null
  relevance_score: number
  confidence_score: number
  source_flag: TranscriptSource
  context_match: boolean
  context_mismatch_reason: string | null
  editor_rating: number | null
  clip_used: boolean
  editor_notes: string | null
}

export interface ScriptContext {
  script_topic: string
  script_domain: string
  geographic_scope: string
  temporal_scope: string
  exclusion_context: string
}

export interface Segment {
  segment_id: string
  title: string
  summary: string
  visual_need: string
  emotional_tone: string
  key_terms: string[]
  search_queries: string[]
  estimated_duration_seconds: number
  context_anchor?: string
  negative_keywords?: string[]
  broll_count: number
  broll_shots: BRollShot[]
  broll_note?: string | null
  results: RankedResult[]
}

export interface JobResponse {
  job_id: string
  status: JobStatus
  created_at: string
  completed_at: string | null
  processing_time_seconds: number | null
  script_duration_minutes: number
  total_segments: number
  total_shots: number
  total_results: number
  segments_with_no_broll: number
  minimum_results_met: boolean
  api_costs: APICosts
  segments: Segment[]
  english_translation: string | null
  project_id?: string | null
  title?: string | null
  category?: string | null
  script_context?: ScriptContext | null
  activity_log?: ActivityEntry[]
}

export interface JobSummary {
  job_id: string
  status: JobStatus
  created_at: string
  segment_count: number
  result_count: number
  project_id?: string | null
  title?: string | null
  category?: string | null
}

export type VideoCategory =
  | "history"
  | "mystery"
  | "current_affairs"
  | "science"
  | "finance"
  | "ai_tech"
  | "geo_politics"
  | "societal_issues"
  | "sports"

export const CATEGORY_OPTIONS: { value: VideoCategory; label: string }[] = [
  { value: "history", label: "History" },
  { value: "mystery", label: "Mystery" },
  { value: "current_affairs", label: "Current Affairs" },
  { value: "science", label: "Science" },
  { value: "finance", label: "Finance" },
  { value: "ai_tech", label: "AI & Tech" },
  { value: "geo_politics", label: "Geo Politics" },
  { value: "societal_issues", label: "Societal Issues" },
  { value: "sports", label: "Sports" },
]

export function categoryLabel(value: string | null | undefined): string {
  if (!value) return ""
  const found = CATEGORY_OPTIONS.find(c => c.value === value)
  return found?.label ?? value
}

export interface ProjectSummary {
  project_id: string
  title: string
  created_at: string
  updated_at: string
  job_count: number
  total_clips: number
  category?: string | null
}

export interface ProjectWithJobs extends ProjectSummary {
  jobs: JobSummary[]
}

export interface ChannelResolution {
  channel_id: string
  channel_name: string
  subscribers: number
  thumbnail_url: string
}

export interface UsagePeriod {
  openai_calls: number
  openai_mini_calls: number
  openai_input_tokens: number
  openai_output_tokens: number
  gpt4o_input_tokens: number
  gpt4o_output_tokens: number
  gpt4o_mini_input_tokens: number
  gpt4o_mini_output_tokens: number
  whisper_minutes: number
  whisper_calls: number
  youtube_api_units: number
  google_cse_calls: number
  gemini_calls: number
  ytdlp_searches: number
  ytdlp_detail_lookups: number
  estimated_cost_usd: number
  job_count: number
  last_calculated?: string
}

export interface UsageData {
  all_time: UsagePeriod
  current_month: UsagePeriod
  today: UsagePeriod
  pricing: Record<string, number>
}

// Library types

export interface LibraryClip {
  result_id: string
  segment_id: string
  shot_id: string | null
  shot_visual_need: string | null
  video_id: string
  video_url: string
  video_title: string
  channel_name: string
  channel_subscribers: number
  thumbnail_url: string
  video_duration_seconds: number
  published_at: string
  view_count: number
  start_time_seconds: number | null
  end_time_seconds: number | null
  clip_url: string | null
  transcript_excerpt: string | null
  the_hook: string | null
  relevance_note: string | null
  relevance_score: number
  confidence_score: number
  source_flag: TranscriptSource
  context_match: boolean
  editor_rating: number | null
  clip_used: boolean
  editor_notes: string | null
  categories: string[]
  job_id: string | null
  job_title: string | null
}

export interface LibraryCategoryCount {
  name: string
  count: number
}

export interface LibraryStats {
  videos_indexed: number
  clips_found: number
  transcripts_cached: number
  editor_rated: number
  usage_rate: number
  top_channels: Array<{ name: string; count: number }>
  top_categories: LibraryCategoryCount[]
}

export interface LibrarySearchResponse {
  total: number
  page: number
  results: LibraryClip[]
  stats: LibraryStats
  categories: LibraryCategoryCount[]
}

export interface PipelineSettings {
  search_queries_per_segment: number
  youtube_results_per_query: number
  max_candidates_per_segment: number
  top_results_per_segment: number
  total_results_target: number
  gemini_expanded_queries: number
  timestamp_model: string
  translation_model: string
  matcher_backend: string
  matcher_model: string
  confidence_threshold: number
  whisper_max_video_duration_min: number
  whisper_audio_trim_min: number
  min_video_duration_sec: number
  max_video_duration_sec: number
  prefer_min_subscribers: number
  recency_full_score_years: number
  weight_ai_confidence: number
  weight_keyword_density: number
  weight_viral_score: number
  weight_channel_authority: number
  weight_caption_quality: number
  weight_recency: number
  max_concurrent_segments: number
  segment_timeout_sec: number
  low_result_threshold: number
  search_backend: string
  preferred_channels_tier1: string[]
  preferred_channels_tier2: string[]
  blocked_networks: string[]
  blocked_studios: string[]
  blocked_sports: string[]
  custom_block_rules: string
  special_instructions: string
  enable_context_matching: boolean
  discard_clips_shorter_than_10s: boolean
  verify_timestamp_not_end_screen: boolean
  cap_end_timestamp: boolean
  public_domain_archives: Array<{ name: string; url: string }>
  stock_platforms: Record<string, boolean>
  [key: string]: unknown
}
