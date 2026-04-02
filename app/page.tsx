"use client"

import { useState, useEffect, useCallback } from "react"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import { ScriptInput } from "@/components/script-input"
import { ProgressTracker } from "@/components/progress-tracker"
import { ResultsDisplay } from "@/components/results-display"
import { JobHistory } from "@/components/job-history"
import { AgentOnboardingBanner, useAgentLoop } from "@/components/agent-status"
import type { JobResponse, JobProgress, JobSummary } from "@/lib/types"

const API_BASE = '/api/v1'

type ViewState = 'input' | 'processing' | 'results'

export default function HomePage() {
  const [viewState, setViewState] = useState<ViewState>('input')
  const [isLoading, setIsLoading] = useState(false)
  const [currentJobId, setCurrentJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<JobProgress>({
    stage: 'queued',
    percent_complete: 0,
    message: 'Initializing...',
    activity_log: [],
  })
  const [job, setJob] = useState<JobResponse | null>(null)
  const [jobHistory, setJobHistory] = useState<JobSummary[]>([])
  useAgentLoop(viewState === 'processing')

  useEffect(() => {
    loadJobHistory()
  }, [])

  const loadJobHistory = async () => {
    try {
      const resp = await fetch(`${API_BASE}/jobs`)
      if (resp.ok) {
        const data = await resp.json()
        setJobHistory(data.jobs || [])
      }
    } catch {
      // Backend may not be running
    }
  }

  const pollJobStatus = useCallback(async (jobId: string) => {
    try {
      const response = await fetch(`${API_BASE}/jobs/${jobId}/status`)
      if (!response.ok) throw new Error('Failed to get job status')
      const status = await response.json()

      if (status.progress) {
        setProgress(status.progress)
      }

      if (status.status === 'complete') {
        const jobResponse = await fetch(`${API_BASE}/jobs/${jobId}`)
        if (jobResponse.ok) {
          const jobData = await jobResponse.json()
          setJob(jobData)
          setViewState('results')
          toast.success('Scouting complete!')
          loadJobHistory()
        }
        return true
      } else if (status.status === 'failed') {
        toast.error('Job failed')
        resetToInput()
        return true
      } else if (status.status === 'cancelled') {
        toast.info('Job was cancelled')
        resetToInput()
        return true
      }

      return false
    } catch {
      return false
    }
  }, [])

  useEffect(() => {
    if (viewState !== 'processing' || !currentJobId) return
    const interval = setInterval(async () => {
      const isDone = await pollJobStatus(currentJobId)
      if (isDone) clearInterval(interval)
    }, 2000)
    return () => clearInterval(interval)
  }, [viewState, currentJobId, pollJobStatus])

  const handleSubmit = async (script: string) => {
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script, editor_id: 'default_editor' })
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to create job')
      }

      const data = await response.json()
      setCurrentJobId(data.job_id)
      setViewState('processing')
      setProgress({
        stage: 'queued',
        percent_complete: 0,
        message: 'Starting pipeline...',
        activity_log: [{ time: new Date().toLocaleTimeString('en-GB'), icon: 'zap', text: 'Submitting your script to the pipeline...' }],
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to start scouting')
    } finally {
      setIsLoading(false)
    }
  }

  const handleLoadJob = async (jobId: string) => {
    try {
      const resp = await fetch(`${API_BASE}/jobs/${jobId}`)
      if (resp.ok) {
        const data = await resp.json()
        setJob(data)
        setCurrentJobId(jobId)
        setViewState('results')
      }
    } catch {
      toast.error('Failed to load job')
    }
  }

  const handleCancel = async () => {
    if (!currentJobId) {
      resetToInput()
      return
    }
    try {
      const resp = await fetch(`/api/v1/jobs/${currentJobId}/cancel`, { method: 'POST' })
      const data = await resp.json()
      if (data.cancelled) {
        toast.info('Job cancelled — pipeline stopped')
      } else {
        toast.info(data.message || 'Job already finished')
      }
    } catch {
      toast.warning('Could not reach server, resetting locally')
    }
    resetToInput()
  }

  const handleExport = () => {
    if (!job) return
    const blob = new Blob([JSON.stringify(job, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `broll-scout-${job.job_id}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Results exported')
  }

  const resetToInput = () => {
    setViewState('input')
    setCurrentJobId(null)
    setJob(null)
    setProgress({ stage: 'queued', percent_complete: 0, message: 'Initializing...', activity_log: [] })
  }

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <div className="flex max-w-[1600px] mx-auto">
        <aside className="hidden lg:block w-72 border-r border-border p-4 min-h-[calc(100vh-64px)] sticky top-16">
          <JobHistory
            jobs={jobHistory}
            activeJobId={currentJobId}
            onSelect={handleLoadJob}
          />
        </aside>

        <main className="flex-1 px-4 sm:px-6 lg:px-8 py-8 max-w-5xl">
          <AgentOnboardingBanner />
          {viewState === 'input' && (
            <ScriptInput onSubmit={handleSubmit} isLoading={isLoading} />
          )}
          {viewState === 'processing' && (
            <ProgressTracker progress={progress} onCancel={handleCancel} />
          )}
          {viewState === 'results' && job && (
            <ResultsDisplay
              job={job}
              onExport={handleExport}
              onNewSearch={resetToInput}
            />
          )}
        </main>
      </div>
    </div>
  )
}
