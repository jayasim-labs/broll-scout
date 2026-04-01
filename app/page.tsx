"use client"

import { useState, useEffect, useCallback } from "react"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import { ScriptInput } from "@/components/script-input"
import { ProgressTracker } from "@/components/progress-tracker"
import { ResultsDisplay } from "@/components/results-display"
import type { Job, JobProgress } from "@/lib/types"

const API_BASE = '/api/v1'

type ViewState = 'input' | 'processing' | 'results'

export default function HomePage() {
  const [viewState, setViewState] = useState<ViewState>('input')
  const [isLoading, setIsLoading] = useState(false)
  const [currentJobId, setCurrentJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<JobProgress>({
    stage: 'queued',
    percent_complete: 0,
    message: 'Initializing...'
  })
  const [job, setJob] = useState<Job | null>(null)

  const pollJobStatus = useCallback(async (jobId: string) => {
    try {
      const response = await fetch(`${API_BASE}/jobs/${jobId}/status`)
      if (!response.ok) throw new Error('Failed to get job status')
      
      const status = await response.json()
      
      if (status.progress) {
        setProgress(status.progress)
      }
      
      if (status.status === 'completed') {
        const jobResponse = await fetch(`${API_BASE}/jobs/${jobId}`)
        if (jobResponse.ok) {
          const jobData = await jobResponse.json()
          setJob(jobData)
          setViewState('results')
          toast.success('Scouting complete!')
        }
        return true
      } else if (status.status === 'failed') {
        toast.error(status.error || 'Job failed')
        resetToInput()
        return true
      }
      
      return false
    } catch (error) {
      console.error('Polling error:', error)
      return false
    }
  }, [])

  useEffect(() => {
    if (viewState !== 'processing' || !currentJobId) return

    const interval = setInterval(async () => {
      const isDone = await pollJobStatus(currentJobId)
      if (isDone) {
        clearInterval(interval)
      }
    }, 1000)

    return () => clearInterval(interval)
  }, [viewState, currentJobId, pollJobStatus])

  const handleSubmit = async (data: {
    script: string
    videoStyle: string
    targetAudience: string
    minQuality: string
  }) => {
    setIsLoading(true)
    
    try {
      // Create job
      const response = await fetch(`${API_BASE}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          script_text: data.script,
          video_style: data.videoStyle,
          target_audience: data.targetAudience,
          preferences: {
            min_quality: data.minQuality
          }
        })
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to create job')
      }

      const jobData = await response.json()
      setCurrentJobId(jobData.id)

      // Start the job
      await fetch(`${API_BASE}/jobs/${jobData.id}/start`, { method: 'POST' })

      setViewState('processing')
      setProgress({
        stage: 'queued',
        percent_complete: 0,
        message: 'Starting...'
      })
    } catch (error) {
      console.error('Submit error:', error)
      toast.error(error instanceof Error ? error.message : 'Failed to start scouting')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCancel = async () => {
    if (!currentJobId) return

    try {
      await fetch(`${API_BASE}/jobs/${currentJobId}/cancel`, { method: 'POST' })
      toast.info('Job cancelled')
    } catch (error) {
      console.error('Cancel error:', error)
    }

    resetToInput()
  }

  const handleExport = () => {
    if (!job) return

    const blob = new Blob([JSON.stringify(job, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `broll-scout-${job.id}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Results exported')
  }

  const resetToInput = () => {
    setViewState('input')
    setCurrentJobId(null)
    setJob(null)
    setProgress({
      stage: 'queued',
      percent_complete: 0,
      message: 'Initializing...'
    })
  }

  return (
    <div className="min-h-screen bg-background">
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
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
  )
}
