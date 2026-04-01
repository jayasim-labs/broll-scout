import { NextRequest, NextResponse } from 'next/server'

// In-memory storage for demo (in production, use a database)
const jobs = new Map<string, Job>()

interface Job {
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

interface VisualCue {
  id: string
  timestamp_start: number
  timestamp_end: number
  script_excerpt: string
  visual_description: string
  search_queries: string[]
  priority: 'high' | 'medium' | 'low'
}

interface ClipSelection {
  cue_id: string
  selected_clip: StockClip | null
  alternatives: StockClip[]
  match_score: number
  selection_reason: string
}

interface StockClip {
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

interface JobProgress {
  stage: string
  percent_complete: number
  message: string
  current_cue?: number
  total_cues?: number
}

interface JobSummary {
  total_cues: number
  filled_cues: number
  fill_rate: number
  total_clips_found: number
  average_match_score: number
}

// Generate a unique ID
function generateId(): string {
  return `job_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
}

// POST /api/v1/jobs - Create a new job
export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    
    const job: Job = {
      id: generateId(),
      status: 'queued',
      script_text: body.script_text,
      video_style: body.video_style || 'documentary',
      target_audience: body.target_audience || 'general',
      preferences: body.preferences || { min_quality: '1080p' },
      visual_cues: [],
      selections: {},
      progress: {
        stage: 'queued',
        percent_complete: 0,
        message: 'Job created, waiting to start...'
      },
      total_cost: 0,
      created_at: new Date().toISOString()
    }
    
    jobs.set(job.id, job)
    
    return NextResponse.json(job)
  } catch (error) {
    console.error('Error creating job:', error)
    return NextResponse.json(
      { detail: 'Failed to create job' },
      { status: 500 }
    )
  }
}

// GET /api/v1/jobs - List all jobs
export async function GET() {
  const allJobs = Array.from(jobs.values())
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  
  return NextResponse.json(allJobs)
}

// DELETE /api/v1/jobs - Clear all jobs
export async function DELETE() {
  jobs.clear()
  return NextResponse.json({ message: 'All jobs cleared' })
}

// Export jobs map for use in other routes
export { jobs }
