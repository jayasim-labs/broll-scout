import { NextRequest, NextResponse } from 'next/server'
import { jobs } from '../../route'

// Simulated processing stages
const stages = [
  { name: 'translating', message: 'Analyzing script and extracting visual cues...', duration: 2000 },
  { name: 'searching', message: 'Searching for stock footage...', duration: 3000 },
  { name: 'matching', message: 'Matching clips to visual cues...', duration: 2000 },
  { name: 'ranking', message: 'Ranking and selecting best matches...', duration: 1500 },
]

// Generate mock visual cues from script
function generateVisualCues(scriptText: string): VisualCue[] {
  const sentences = scriptText.split(/[.!?]+/).filter(s => s.trim().length > 10)
  const cues: VisualCue[] = []
  
  const visualDescriptions = [
    'Wide establishing shot of urban cityscape at golden hour',
    'Close-up of hands typing on keyboard with soft lighting',
    'Aerial drone shot of natural landscape',
    'Medium shot of diverse group collaborating in modern office',
    'Slow motion footage of water droplets or nature elements',
    'Time-lapse of busy street scene or workflow',
    'Abstract technology visualization or data flow',
    'Portrait shot with shallow depth of field',
  ]
  
  const searchQueries = [
    ['cityscape', 'urban', 'golden hour'],
    ['typing', 'keyboard', 'technology'],
    ['aerial', 'nature', 'landscape'],
    ['teamwork', 'collaboration', 'office'],
    ['slow motion', 'nature', 'abstract'],
    ['timelapse', 'city', 'busy'],
    ['technology', 'data', 'digital'],
    ['portrait', 'professional', 'business'],
  ]
  
  let currentTime = 0
  
  for (let i = 0; i < Math.min(sentences.length, 6); i++) {
    const duration = 3 + Math.random() * 4
    const descIndex = i % visualDescriptions.length
    
    cues.push({
      id: `cue_${i + 1}`,
      timestamp_start: currentTime,
      timestamp_end: currentTime + duration,
      script_excerpt: sentences[i].trim().substring(0, 100),
      visual_description: visualDescriptions[descIndex],
      search_queries: searchQueries[descIndex],
      priority: i < 2 ? 'high' : i < 4 ? 'medium' : 'low',
    })
    
    currentTime += duration + 0.5
  }
  
  return cues
}

// Generate mock stock clips
function generateMockClips(cue: VisualCue): { selected: StockClip | null; alternatives: StockClip[] } {
  const shouldMatch = Math.random() > 0.15 // 85% match rate
  
  if (!shouldMatch) {
    return { selected: null, alternatives: [] }
  }
  
  const clipCount = 1 + Math.floor(Math.random() * 3)
  const clips: StockClip[] = []
  
  for (let i = 0; i < clipCount; i++) {
    clips.push({
      id: `clip_${cue.id}_${i + 1}`,
      source: 'Pexels',
      title: `Stock footage matching "${cue.search_queries[0]}"`,
      description: `High quality ${cue.visual_description.toLowerCase()}`,
      duration: 8 + Math.floor(Math.random() * 20),
      quality: ['720p', '1080p', '4K'][Math.floor(Math.random() * 3)],
      preview_url: `https://images.pexels.com/videos/${1000000 + Math.floor(Math.random() * 9000000)}/free-video.jpg?auto=compress&w=320`,
      download_url: `https://www.pexels.com/video/${1000000 + Math.floor(Math.random() * 9000000)}/`,
      thumbnail_url: `https://images.pexels.com/videos/${1000000 + Math.floor(Math.random() * 9000000)}/free-video.jpg?auto=compress&w=160`,
      tags: cue.search_queries,
      license: 'Pexels License (Free)',
    })
  }
  
  return {
    selected: clips[0],
    alternatives: clips.slice(1),
  }
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

// POST /api/v1/jobs/[id]/start - Start processing a job
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const job = jobs.get(id)
  
  if (!job) {
    return NextResponse.json(
      { detail: 'Job not found' },
      { status: 404 }
    )
  }
  
  if (job.status !== 'queued') {
    return NextResponse.json(
      { detail: 'Job has already been started' },
      { status: 400 }
    )
  }
  
  // Start processing in background
  job.status = 'processing'
  jobs.set(id, job)
  
  // Simulate async processing
  processJob(id)
  
  return NextResponse.json({ message: 'Job started', job_id: id })
}

async function processJob(jobId: string) {
  const job = jobs.get(jobId)
  if (!job) return
  
  try {
    let progress = 0
    
    for (let i = 0; i < stages.length; i++) {
      const stage = stages[i]
      
      // Check if job was cancelled
      const currentJob = jobs.get(jobId)
      if (!currentJob || currentJob.status === 'cancelled') return
      
      // Update progress
      currentJob.progress = {
        stage: stage.name,
        percent_complete: Math.round(progress + (100 / stages.length) * 0.5),
        message: stage.message,
      }
      jobs.set(jobId, currentJob)
      
      // Simulate processing time
      await new Promise(resolve => setTimeout(resolve, stage.duration))
      
      progress += 100 / stages.length
      
      // Generate data at appropriate stages
      if (stage.name === 'translating') {
        currentJob.visual_cues = generateVisualCues(currentJob.script_text)
        currentJob.progress.total_cues = currentJob.visual_cues.length
      }
      
      if (stage.name === 'matching') {
        let filledCues = 0
        for (const cue of currentJob.visual_cues) {
          const { selected, alternatives } = generateMockClips(cue)
          currentJob.selections[cue.id] = {
            cue_id: cue.id,
            selected_clip: selected,
            alternatives,
            match_score: selected ? 0.7 + Math.random() * 0.25 : 0,
            selection_reason: selected 
              ? `Best match for "${cue.visual_description}" with ${Math.round((0.7 + Math.random() * 0.25) * 100)}% relevance score`
              : 'No suitable clips found for this visual cue',
          }
          if (selected) filledCues++
        }
        
        currentJob.summary = {
          total_cues: currentJob.visual_cues.length,
          filled_cues: filledCues,
          fill_rate: filledCues / currentJob.visual_cues.length,
          total_clips_found: Object.values(currentJob.selections).reduce(
            (sum, s) => sum + (s.selected_clip ? 1 : 0) + s.alternatives.length, 0
          ),
          average_match_score: filledCues > 0 
            ? Object.values(currentJob.selections).reduce((sum, s) => sum + s.match_score, 0) / filledCues
            : 0,
        }
      }
      
      jobs.set(jobId, currentJob)
    }
    
    // Mark as completed
    const finalJob = jobs.get(jobId)
    if (finalJob && finalJob.status !== 'cancelled') {
      finalJob.status = 'completed'
      finalJob.completed_at = new Date().toISOString()
      finalJob.total_cost = 0.015 + Math.random() * 0.01 // Mock cost
      finalJob.progress = {
        stage: 'completed',
        percent_complete: 100,
        message: 'Processing complete!',
      }
      jobs.set(jobId, finalJob)
    }
  } catch (error) {
    const failedJob = jobs.get(jobId)
    if (failedJob) {
      failedJob.status = 'failed'
      failedJob.error = error instanceof Error ? error.message : 'Unknown error'
      jobs.set(jobId, failedJob)
    }
  }
}
