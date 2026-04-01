import { NextRequest, NextResponse } from 'next/server'
import { jobs } from '../../route'

// POST /api/v1/jobs/[id]/cancel - Cancel a job
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
  
  if (job.status === 'completed' || job.status === 'failed') {
    return NextResponse.json(
      { detail: 'Cannot cancel a completed or failed job' },
      { status: 400 }
    )
  }
  
  job.status = 'cancelled'
  job.progress = {
    ...job.progress,
    message: 'Job cancelled by user',
  }
  jobs.set(id, job)
  
  return NextResponse.json({ message: 'Job cancelled', job_id: id })
}
