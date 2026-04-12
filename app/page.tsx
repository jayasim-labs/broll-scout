"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import { ScriptInput } from "@/components/script-input"
import { ProgressTracker } from "@/components/progress-tracker"
import { JobHistory } from "@/components/job-history"
import { AgentOnboardingBanner, useAgentLoop, abortCompanionAgentTasks } from "@/components/agent-status"
import type { JobProgress, JobSummary, ProjectSummary } from "@/lib/types"

const API_BASE = '/api/v1'

type ViewState = 'input' | 'processing'

export default function HomePage() {
  const router = useRouter()
  const [viewState, setViewState] = useState<ViewState>('input')
  const [isLoading, setIsLoading] = useState(false)
  const [currentJobId, setCurrentJobId] = useState<string | null>(null)
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  const [progress, setProgress] = useState<JobProgress>({
    stage: 'queued',
    percent_complete: 0,
    message: 'Initializing...',
    activity_log: [],
  })
  const [jobHistory, setJobHistory] = useState<JobSummary[]>([])
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  useAgentLoop(viewState === 'processing', currentJobId)

  useEffect(() => {
    loadJobHistory()
    loadProjects()
  }, [])

  const loadJobHistory = async () => {
    try {
      const resp = await fetch(`${API_BASE}/jobs?limit=100`)
      if (resp.ok) {
        const data = await resp.json()
        setJobHistory(data.jobs || [])
      }
    } catch { /* Backend may not be running */ }
  }

  const loadProjects = async () => {
    try {
      const resp = await fetch(`${API_BASE}/projects`)
      if (resp.ok) {
        const data = await resp.json()
        setProjects(data.projects || [])
      }
    } catch { /* Backend may not be running */ }
  }

  const pollJobStatus = useCallback(async (jobId: string) => {
    try {
      const response = await fetch(`${API_BASE}/jobs/${jobId}/status`)
      if (!response.ok) throw new Error('Failed to get job status')
      const status = await response.json()

      if (status.progress) {
        setProgress(status.progress)
        setJobHistory(prev => prev.map(j =>
          j.job_id === jobId
            ? { ...j, status: status.status, segment_count: status.segment_count ?? j.segment_count, result_count: status.result_count ?? j.result_count }
            : j
        ))
      }

      if (status.status === 'complete') {
        toast.success('Scouting complete!')
        loadJobHistory()
        loadProjects()
        router.push(`/jobs/${jobId}`)
        return true
      } else if (status.status === 'failed') {
        toast.error('Job failed')
        loadJobHistory()
        loadProjects()
        resetToInput()
        return true
      } else if (status.status === 'cancelled') {
        toast.info('Job was cancelled')
        loadJobHistory()
        loadProjects()
        resetToInput()
        return true
      }

      return false
    } catch {
      return false
    }
  }, [router])

  useEffect(() => {
    if (viewState !== 'processing' || !currentJobId) return
    const interval = setInterval(async () => {
      const isDone = await pollJobStatus(currentJobId)
      if (isDone) clearInterval(interval)
    }, 2000)
    return () => clearInterval(interval)
  }, [viewState, currentJobId, pollJobStatus])

  const handleSubmit = async (script: string, options: {
    enableGeminiExpansion: boolean
    title: string
    projectId?: string
    category?: string
  }) => {
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          script,
          title: options.title,
          project_id: options.projectId || null,
          editor_id: 'default_editor',
          enable_gemini_expansion: options.enableGeminiExpansion,
          category: options.category || null,
        })
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to create job')
      }

      const data = await response.json()
      setCurrentJobId(data.job_id)
      if (data.project_id) setActiveProjectId(data.project_id)
      setViewState('processing')
      setProgress({
        stage: 'queued',
        percent_complete: 0,
        message: 'Starting pipeline...',
        activity_log: [{ time: new Date().toLocaleTimeString('en-GB'), icon: 'zap', text: 'Submitting your script to the pipeline...' }],
      })
      setJobHistory(prev => {
        if (prev.some(j => j.job_id === data.job_id)) return prev
        return [{
          job_id: data.job_id,
          status: 'processing' as const,
          created_at: new Date().toISOString(),
          segment_count: 0,
          result_count: 0,
          project_id: data.project_id || null,
          title: data.title || null,
        }, ...prev]
      })
      loadProjects()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to start scouting')
    } finally {
      setIsLoading(false)
    }
  }

  const handleSelectJob = (jobId: string) => {
    router.push(`/jobs/${jobId}`, { scroll: false })
  }

  const handleSelectProject = (projectId: string) => {
    setActiveProjectId(projectId)
  }

  const handleRenameProject = async (projectId: string, newTitle: string) => {
    try {
      const resp = await fetch(`${API_BASE}/projects/${projectId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      })
      if (resp.ok) {
        setProjects(prev =>
          prev.map(p => p.project_id === projectId ? { ...p, title: newTitle } : p)
        )
        toast.success('Project renamed')
      }
    } catch {
      toast.error('Failed to rename project')
    }
  }

  const handleDeleteProject = async (projectId: string) => {
    try {
      const resp = await fetch(`${API_BASE}/projects/${projectId}`, { method: 'DELETE' })
      if (resp.ok) {
        setProjects(prev => prev.filter(p => p.project_id !== projectId))
        if (activeProjectId === projectId) setActiveProjectId(null)
        toast.success('Project deleted')
      }
    } catch {
      toast.error('Failed to delete project')
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
      if (Array.isArray(data.cancelled_agent_task_ids) && data.cancelled_agent_task_ids.length) {
        await abortCompanionAgentTasks(data.cancelled_agent_task_ids)
      }
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

  const resetToInput = () => {
    setViewState('input')
    setCurrentJobId(null)
    setProgress({ stage: 'queued', percent_complete: 0, message: 'Initializing...', activity_log: [] })
  }

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <div className="flex max-w-[1600px] mx-auto">
        <aside className="hidden lg:block w-72 border-r border-border p-4 max-h-[calc(100vh-64px)] overflow-y-auto sticky top-16">
          <JobHistory
            jobs={jobHistory}
            projects={projects}
            activeJobId={currentJobId}
            activeProjectId={activeProjectId}
            onSelectJob={handleSelectJob}
            onSelectProject={handleSelectProject}
            onRenameProject={handleRenameProject}
            onDeleteProject={handleDeleteProject}
            onNewProject={resetToInput}
          />
        </aside>

        <main className="flex-1 px-4 sm:px-6 lg:px-8 py-8 max-w-5xl">
          <AgentOnboardingBanner />
          {viewState === 'input' && (
            <ScriptInput
              onSubmit={handleSubmit}
              isLoading={isLoading}
              projects={projects}
              preselectedProjectId={activeProjectId}
            />
          )}
          {viewState === 'processing' && (
            <ProgressTracker progress={progress} onCancel={handleCancel} />
          )}
        </main>
      </div>
    </div>
  )
}
