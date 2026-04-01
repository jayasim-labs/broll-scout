import { NextResponse } from 'next/server'
import { jobs } from '../../jobs/route'

// GET /api/v1/costs/monthly - Get monthly cost data
export async function GET() {
  // Calculate costs from completed jobs this month
  const now = new Date()
  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1)
  
  let totalCost = 0
  let openaiCost = 0
  let pexelsCost = 0
  let jobsCount = 0
  
  for (const job of jobs.values()) {
    if (job.status === 'completed' && job.created_at) {
      const jobDate = new Date(job.created_at)
      if (jobDate >= startOfMonth) {
        totalCost += job.total_cost || 0
        // Assume 80% OpenAI, 20% Pexels (Pexels is free but track for reference)
        openaiCost += (job.total_cost || 0) * 0.8
        pexelsCost += (job.total_cost || 0) * 0.2
        jobsCount++
      }
    }
  }
  
  return NextResponse.json({
    total_cost: totalCost,
    breakdown: {
      openai: openaiCost,
      pexels: pexelsCost,
    },
    jobs_count: jobsCount,
    period: {
      start: startOfMonth.toISOString(),
      end: now.toISOString(),
    }
  })
}
