import { NextRequest, NextResponse } from 'next/server'
import { jobs } from '../../route'

// GET /api/v1/jobs/[id]/status - Get job status
export async function GET(
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
  
  return NextResponse.json({
    id: job.id,
    status: job.status,
    stage: job.progress.stage,
    progress: job.progress,
    error: job.error
  })
}
