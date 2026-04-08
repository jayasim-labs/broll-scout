"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import { ResultsDisplay } from "@/components/results-display"
import { ProgressTracker } from "@/components/progress-tracker"
import { JobHistory } from "@/components/job-history"
import { AgentOnboardingBanner, useAgentLoop, abortCompanionAgentTasks } from "@/components/agent-status"
import { Loader2 } from "lucide-react"
import type { JobResponse, JobProgress, JobSummary, ProjectSummary } from "@/lib/types"

const API_BASE = "/api/v1"

export default function JobPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const jobId = params.id

  const [job, setJob] = useState<JobResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isProcessing, setIsProcessing] = useState(false)
  const [progress, setProgress] = useState<JobProgress>({
    stage: "queued",
    percent_complete: 0,
    message: "Loading...",
    activity_log: [],
  })
  const [jobHistory, setJobHistory] = useState<JobSummary[]>([])
  const [projects, setProjects] = useState<ProjectSummary[]>([])

  useAgentLoop(isProcessing, jobId)

  const loadJobHistory = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/jobs`)
      if (resp.ok) {
        const data = await resp.json()
        setJobHistory(data.jobs || [])
      }
    } catch { /* silent */ }
  }, [])

  const loadProjects = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/projects`)
      if (resp.ok) {
        const data = await resp.json()
        setProjects(data.projects || [])
      }
    } catch { /* silent */ }
  }, [])

  const loadJob = useCallback(async () => {
    try {
      const statusResp = await fetch(`${API_BASE}/jobs/${jobId}/status`)
      if (statusResp.ok) {
        const statusData = await statusResp.json()
        if (statusData.status === "processing" || statusData.status === "pending") {
          setIsProcessing(true)
          setLoading(false)
          if (statusData.progress) setProgress(statusData.progress)
          return
        }
      }

      const resp = await fetch(`${API_BASE}/jobs/${jobId}`)
      if (resp.ok) {
        const data = await resp.json()
        setJob(data)
        setIsProcessing(false)
      } else if (resp.status === 404) {
        setError("Job not found")
      } else {
        setError("Failed to load job")
      }
    } catch {
      setError("Failed to connect to backend")
    } finally {
      setLoading(false)
    }
  }, [jobId])

  useEffect(() => {
    loadJob()
    loadJobHistory()
    loadProjects()
  }, [loadJob, loadJobHistory, loadProjects])

  const pollJobStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/jobs/${jobId}/status`)
      if (!response.ok) return false
      const status = await response.json()

      if (status.progress) {
        setProgress(status.progress)
        setJobHistory(prev =>
          prev.map(j =>
            j.job_id === jobId
              ? { ...j, status: status.status, segment_count: status.segment_count ?? j.segment_count, result_count: status.result_count ?? j.result_count }
              : j
          )
        )
      }

      if (status.status === "complete") {
        const jobResponse = await fetch(`${API_BASE}/jobs/${jobId}`)
        if (jobResponse.ok) {
          const jobData = await jobResponse.json()
          setJob(jobData)
          setIsProcessing(false)
          toast.success("Scouting complete!")
          loadJobHistory()
          loadProjects()
        }
        return true
      } else if (status.status === "failed") {
        toast.error("Job failed")
        setIsProcessing(false)
        setError("Job failed")
        loadJobHistory()
        return true
      } else if (status.status === "cancelled") {
        toast.info("Job was cancelled")
        setIsProcessing(false)
        setError("Job was cancelled")
        loadJobHistory()
        return true
      }
      return false
    } catch {
      return false
    }
  }, [jobId, loadJobHistory, loadProjects])

  useEffect(() => {
    if (!isProcessing) return
    const interval = setInterval(async () => {
      const isDone = await pollJobStatus()
      if (isDone) clearInterval(interval)
    }, 2000)
    return () => clearInterval(interval)
  }, [isProcessing, pollJobStatus])

  const refreshJob = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/jobs/${jobId}`)
      if (resp.ok) {
        const data = await resp.json()
        setJob(data)
      }
    } catch { /* silent */ }
  }, [jobId])

  const handleCancel = async () => {
    try {
      const resp = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, { method: "POST" })
      const data = await resp.json()
      if (Array.isArray(data.cancelled_agent_task_ids) && data.cancelled_agent_task_ids.length) {
        await abortCompanionAgentTasks(data.cancelled_agent_task_ids)
      }
      if (data.cancelled) {
        toast.info("Job cancelled")
        setIsProcessing(false)
        router.push("/")
      } else {
        toast.info(data.message || "Job already finished")
      }
    } catch {
      toast.warning("Could not reach server")
    }
  }

  const handleSelectJob = (selectedJobId: string) => {
    if (selectedJobId === jobId) return
    router.push(`/jobs/${selectedJobId}`, { scroll: false })
  }

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <div className="flex max-w-[1600px] mx-auto">
        <aside className="hidden lg:block w-72 border-r border-border p-4 min-h-[calc(100vh-64px)] sticky top-16">
          <JobHistory
            jobs={jobHistory}
            projects={projects}
            activeJobId={jobId}
            activeProjectId={job?.project_id || null}
            onSelectJob={handleSelectJob}
            onSelectProject={() => {}}
            onRenameProject={async () => {}}
            onDeleteProject={async () => {}}
            onNewProject={() => router.push("/")}
          />
        </aside>

        <main className="flex-1 px-4 sm:px-6 lg:px-8 py-8 max-w-5xl">
          <AgentOnboardingBanner />

          {loading && (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="w-8 h-8 animate-spin text-primary" />
              <span className="ml-3 text-muted-foreground">Loading job...</span>
            </div>
          )}

          {error && !loading && (
            <div className="text-center py-20">
              <p className="text-muted-foreground text-lg">{error}</p>
              <button
                onClick={() => router.push("/")}
                className="mt-4 text-primary hover:underline"
              >
                Back to Scout
              </button>
            </div>
          )}

          {isProcessing && !loading && (
            <ProgressTracker progress={progress} onCancel={handleCancel} />
          )}

          {!loading && !error && !isProcessing && job && (
            <ResultsDisplay
              job={job}
              onNewSearch={() => router.push("/")}
              onRefreshJob={refreshJob}
            />
          )}
        </main>
      </div>
    </div>
  )
}
