import { NextRequest, NextResponse } from 'next/server'
import { jobs } from '../route'

// GET /api/v1/jobs/[id] - Get job details
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
  
  return NextResponse.json(job)
}

// DELETE /api/v1/jobs/[id] - Delete a job
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  
  if (!jobs.has(id)) {
    return NextResponse.json(
      { detail: 'Job not found' },
      { status: 404 }
    )
  }
  
  jobs.delete(id)
  
  return NextResponse.json({ message: 'Job deleted' })
}
