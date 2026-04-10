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

export type ShotIntent = "literal" | "illustrative" | "atmospheric"
export type Scarcity = "common" | "medium" | "rare"
export type AuditStatus = "pass" | "review" | "reject" | "unaudited"

export interface BRollShot {
  shot_id: string
  visual_need: string
  visual_description?: string
  search_queries: string[]
  key_terms: string[]
  shot_intent?: ShotIntent
  scarcity?: Scarcity
  preferred_source_type?: string
}

export interface RankedResult {
  result_id: string
  segment_id: string
  shot_id: string | null
  shot_visual_need: string | null
  shot_intent?: ShotIntent
  scarcity?: Scarcity
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
  match_reasoning: string | null
  relevance_score: number
  confidence_score: number
  visual_fit: number
  topical_fit: number
  source_flag: TranscriptSource
  context_match: boolean
  context_mismatch_reason: string | null
  audit_status?: AuditStatus
  audit_reason?: string | null
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

export interface ShotWarning {
  segment_id: string
  message: string
  severity: "info" | "warning"
}

export interface CoverageAssessment {
  shots_per_minute: number
  clips_found: number
  total_shots: number
  longest_no_broll_gap_seconds: number
  longest_no_broll_gap_segments: string[]
  note: string
  warnings_count: number
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
  coverage_assessment?: CoverageAssessment | null
  warnings?: ShotWarning[]
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

export interface ChannelEntry {
  channel_id: string
  channel_name: string
  channel_url: string
  channel_handle: string
  thumbnail_url: string
  subscriber_count: number
  subscriber_display: string
  video_count: number | null
  description: string
  category: string
  tier: string
  added_at: string
  added_by: string
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
  shot_intent?: ShotIntent
  scarcity?: Scarcity
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
  match_reasoning: string | null
  relevance_score: number
  confidence_score: number
  visual_fit: number
  topical_fit: number
  source_flag: TranscriptSource
  context_match: boolean
  audit_status?: AuditStatus
  audit_reason?: string | null
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
  // Search
  youtube_results_per_query: number
  max_candidates_per_segment: number

  max_candidates_per_shot: number
  top_results_per_shot: number

  // Models & matching
  timestamp_model: string
  translation_model: string
  matcher_backend: string
  matcher_model: string
  api_fallback_enabled: boolean
  confident_fallback_enabled: boolean
  lightweight_model: string
  confidence_threshold: number
  whisper_max_video_duration_min: number

  // Video filtering & ranking
  min_video_duration_sec: number
  max_video_duration_sec: number
  /** Exclude ~9:16 portrait (YouTube Shorts shape); not all vertical video */
  filter_9_16_shorts: boolean
  /** Allowed deviation of width/height from 9/16 (e.g. 0.06) */
  shorts_9_16_aspect_tolerance: number
  prefer_min_subscribers: number
  recency_full_score_years: number
  weight_ai_confidence: number
  weight_fit_score: number
  weight_viral_score: number
  weight_channel_authority: number
  weight_caption_quality: number
  weight_recency: number
  weight_context_relevance: number

  // Performance
  segment_timeout_sec: number

  // Channel management
  preferred_channels_tier1: string[]
  preferred_channels_tier2: string[]
  blocked_networks: string[]
  blocked_studios: string[]
  blocked_sports: string[]
  custom_block_rules: string
  channel_sources: ChannelEntry[]

  // Instructions & validation
  special_instructions: string
  enable_context_matching: boolean
  discard_clips_shorter_than_10s: boolean
  verify_timestamp_not_end_screen: boolean
  cap_end_timestamp: boolean

  [key: string]: unknown
}
