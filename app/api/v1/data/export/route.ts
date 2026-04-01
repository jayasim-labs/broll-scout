import { NextResponse } from 'next/server'
import { jobs } from '../../jobs/route'
import { settings } from '../../settings/route'

// GET /api/v1/data/export - Export all data
export async function GET() {
  const allJobs = Array.from(jobs.values())
  
  const exportData = {
    exported_at: new Date().toISOString(),
    settings: {
      ...settings,
      // Mask API keys
      openaiKey: settings.openaiKey ? '****' + settings.openaiKey.slice(-4) : '',
      pexelsKey: settings.pexelsKey ? '****' + settings.pexelsKey.slice(-4) : '',
    },
    jobs: allJobs,
    summary: {
      total_jobs: allJobs.length,
      completed_jobs: allJobs.filter(j => j.status === 'completed').length,
      total_cost: allJobs.reduce((sum, j) => sum + (j.total_cost || 0), 0),
    }
  }
  
  return NextResponse.json(exportData)
}
