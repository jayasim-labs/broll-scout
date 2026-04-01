// B-Roll Scout Types

export interface VisualCue {
  id: string
  timestamp_start: number
  timestamp_end: number
  script_excerpt: string
  visual_description: string
  search_queries: string[]
  priority: 'high' | 'medium' | 'low'
  mood?: string
  scene_type?: string
}

export interface StockClip {
  id: string
  source: string
  title: string
  description: string
  duration: number
  quality: string
  preview_url: string
  download_url: string
  thumbnail_url: string
  tags: string[]
  license: string
}

export interface ClipSelection {
  cue_id: string
  selected_clip: StockClip | null
  alternatives: StockClip[]
  match_score: number
  selection_reason: string
}

export interface JobProgress {
  stage: 'queued' | 'translating' | 'searching' | 'matching' | 'ranking' | 'completed' | 'failed'
  percent_complete: number
  message: string
  current_cue?: number
  total_cues?: number
}

export interface JobSummary {
  total_cues: number
  filled_cues: number
  fill_rate: number
  total_clips_found: number
  average_match_score: number
}

export interface Job {
  id: string
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled'
  script_text: string
  video_style: string
  target_audience: string
  preferences: {
    min_quality: string
    results_per_cue?: number
  }
  visual_cues: VisualCue[]
  selections: Record<string, ClipSelection>
  progress: JobProgress
  summary?: JobSummary
  total_cost: number
  created_at: string
  completed_at?: string
  error?: string
}

export interface Settings {
  openaiKey: string
  pexelsKey: string
  defaultStyle: string
  defaultAudience: string
  defaultQuality: string
  resultsPerCue: number
  monthlyBudget: number
  budgetAlerts: boolean
  alertThreshold: number
}

export interface CostData {
  total_cost: number
  breakdown: {
    openai: number
    pexels: number
  }
  jobs_count: number
}
