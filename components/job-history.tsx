"use client"

import { useState, useMemo, useEffect, useCallback } from "react"
import {
  Clock, CheckCircle, XCircle, Loader2, FileText, Ban,
  FolderOpen, Folder, ChevronRight, ChevronDown, Film,
  Plus, Pencil, Trash2, MoreHorizontal,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { JobSummary, ProjectSummary } from "@/lib/types"

const API = "/api/v1"

interface JobHistoryProps {
  jobs: JobSummary[]
  projects: ProjectSummary[]
  activeJobId: string | null
  activeProjectId: string | null
  onSelectJob: (jobId: string) => void
  onSelectProject: (projectId: string) => void
  onRenameProject: (projectId: string, newTitle: string) => void
  onDeleteProject: (projectId: string) => void
  onNewProject: () => void
}

export function JobHistory({
  jobs,
  projects,
  activeJobId,
  activeProjectId,
  onSelectJob,
  onSelectProject,
  onRenameProject,
  onDeleteProject,
  onNewProject,
}: JobHistoryProps) {
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(new Set())
  const [projectJobs, setProjectJobs] = useState<Record<string, JobSummary[]>>({})
  const [loadingProjects, setLoadingProjects] = useState<Set<string>>(new Set())
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [visibleCount, setVisibleCount] = useState(20)
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  // Orphan jobs (no project) — these still come from the global jobs prop
  const orphanJobs = useMemo(
    () => jobs.filter(j => !j.project_id),
    [jobs],
  )

  // Merge any in-flight processing jobs from the global jobs prop into projectJobs
  // so newly submitted jobs appear immediately without waiting for a refetch
  const getProjectJobs = useCallback((projectId: string): JobSummary[] => {
    const fetched = projectJobs[projectId] || []
    const fetchedIds = new Set(fetched.map(j => j.job_id))
    const liveExtras = jobs.filter(
      j => j.project_id === projectId && !fetchedIds.has(j.job_id)
    )
    if (liveExtras.length === 0) return fetched
    return [...liveExtras, ...fetched]
  }, [projectJobs, jobs])

  const fetchProjectJobs = useCallback(async (projectId: string) => {
    setLoadingProjects(prev => new Set(prev).add(projectId))
    try {
      const resp = await fetch(`${API}/projects/${projectId}/jobs`)
      if (resp.ok) {
        const data = await resp.json()
        setProjectJobs(prev => ({ ...prev, [projectId]: data.jobs || [] }))
      }
    } catch { /* silent */ }
    setLoadingProjects(prev => {
      const next = new Set(prev)
      next.delete(projectId)
      return next
    })
  }, [])

  // Auto-expand the project that contains the active job or active project
  useEffect(() => {
    const idsToExpand: string[] = []
    if (activeProjectId) idsToExpand.push(activeProjectId)
    if (activeJobId) {
      const ownerProject = jobs.find(j => j.job_id === activeJobId)?.project_id
      if (ownerProject) idsToExpand.push(ownerProject)
    }
    if (idsToExpand.length > 0) {
      setExpandedProjects(prev => {
        const next = new Set(prev)
        let changed = false
        for (const id of idsToExpand) {
          if (!next.has(id)) { next.add(id); changed = true }
        }
        return changed ? next : prev
      })
      for (const id of idsToExpand) {
        if (!projectJobs[id]) fetchProjectJobs(id)
      }
    }
  }, [activeJobId, activeProjectId, jobs, fetchProjectJobs, projectJobs])

  const toggleExpand = (projectId: string) => {
    setExpandedProjects(prev => {
      const next = new Set(prev)
      if (next.has(projectId)) {
        next.delete(projectId)
      } else {
        next.add(projectId)
        if (!projectJobs[projectId]) fetchProjectJobs(projectId)
      }
      return next
    })
  }

  const startRename = (projectId: string, currentTitle: string) => {
    setRenamingId(projectId)
    setRenameValue(currentTitle)
  }

  const submitRename = (projectId: string) => {
    if (renameValue.trim()) {
      onRenameProject(projectId, renameValue.trim())
    }
    setRenamingId(null)
    setRenameValue("")
  }

  const confirmHardDelete = async () => {
    if (!deleteTarget) return
    setIsDeleting(true)
    await onDeleteProject(deleteTarget.id)
    setIsDeleting(false)
    setDeleteTarget(null)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">
          Projects
        </h3>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={onNewProject}
          title="New script"
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>

      {projects.length === 0 && orphanJobs.length === 0 && (
        <p className="text-xs text-muted-foreground">No projects yet. Submit a script to create one.</p>
      )}

      <div className="space-y-0.5">
        {projects.slice(0, visibleCount).map((project) => {
          const isExpanded = expandedProjects.has(project.project_id)
          const isActive = activeProjectId === project.project_id
          const isLoading = loadingProjects.has(project.project_id)
          const pJobs = getProjectJobs(project.project_id)
          const hasProcessing = pJobs.some(j => j.status === "processing")

          // Use project summary counts (always accurate from API)
          // but show live count if we have fetched jobs and they differ
          const displayJobCount = pJobs.length > 0
            ? Math.max(pJobs.length, project.job_count)
            : project.job_count
          const displayClipCount = pJobs.length > 0
            ? pJobs.reduce((sum, j) => sum + (j.result_count || 0), 0)
            : project.total_clips

          return (
            <div key={project.project_id}>
              <div
                className={cn(
                  "group flex items-center gap-1.5 px-2 py-1.5 rounded-md text-sm transition-colors cursor-pointer",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "hover:bg-secondary text-foreground"
                )}
              >
                <button
                  className="flex-shrink-0 p-0.5 rounded hover:bg-secondary/80"
                  onClick={() => toggleExpand(project.project_id)}
                >
                  {isLoading ? (
                    <Loader2 className="w-3.5 h-3.5 text-muted-foreground animate-spin" />
                  ) : isExpanded ? (
                    <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  )}
                </button>

                {isExpanded ? (
                  <FolderOpen className="w-4 h-4 text-amber-500 flex-shrink-0" />
                ) : (
                  <Folder className="w-4 h-4 text-amber-500/70 flex-shrink-0" />
                )}

                {renamingId === project.project_id ? (
                  <Input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => submitRename(project.project_id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") submitRename(project.project_id)
                      if (e.key === "Escape") { setRenamingId(null); setRenameValue("") }
                    }}
                    className="h-5 text-xs px-1 py-0 flex-1"
                    autoFocus
                  />
                ) : (
                  <button
                    className="flex-1 text-left min-w-0"
                    onClick={() => {
                      onSelectProject(project.project_id)
                      if (!isExpanded) toggleExpand(project.project_id)
                    }}
                  >
                    <p className="text-xs font-medium truncate">{project.title}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {displayJobCount} job{displayJobCount !== 1 ? "s" : ""}
                      {" · "}
                      {displayClipCount} clip{displayClipCount !== 1 ? "s" : ""}
                      {hasProcessing && (
                        <Loader2 className="inline w-3 h-3 ml-1 animate-spin text-yellow-500" />
                      )}
                    </p>
                  </button>
                )}

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-secondary/80 flex-shrink-0 transition-opacity">
                      <MoreHorizontal className="w-3.5 h-3.5 text-muted-foreground" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-36">
                    <DropdownMenuItem onClick={() => startRename(project.project_id, project.title)}>
                      <Pencil className="w-3.5 h-3.5 mr-2" />
                      Rename
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onClick={() => setDeleteTarget({ id: project.project_id, title: project.title })}
                    >
                      <Trash2 className="w-3.5 h-3.5 mr-2" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              {isExpanded && (
                <div className="ml-5 pl-3 border-l border-border/50 space-y-0.5 mt-0.5">
                  {isLoading && pJobs.length === 0 ? (
                    <div className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      Loading jobs...
                    </div>
                  ) : pJobs.length > 0 ? (
                    pJobs.map((job) => (
                      <JobItem
                        key={job.job_id}
                        job={job}
                        isActive={activeJobId === job.job_id}
                        onSelect={onSelectJob}
                      />
                    ))
                  ) : (
                    <p className="px-2 py-2 text-[10px] text-muted-foreground">No jobs yet</p>
                  )}
                </div>
              )}
            </div>
          )
        })}

        {projects.length > visibleCount && (
          <Button
            variant="ghost"
            size="sm"
            className="w-full text-xs text-muted-foreground h-7"
            onClick={() => setVisibleCount(prev => prev + 20)}
          >
            Show {Math.min(20, projects.length - visibleCount)} more project{projects.length - visibleCount > 1 ? "s" : ""}...
          </Button>
        )}
      </div>

      {orphanJobs.length > 0 && (
        <div className="space-y-1 pt-2 border-t border-border/50">
          <h4 className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider px-2">
            Unlinked Jobs
          </h4>
          <div className="space-y-0.5">
            {orphanJobs.map((job) => (
              <JobItem
                key={job.job_id}
                job={job}
                isActive={activeJobId === job.job_id}
                onSelect={onSelectJob}
              />
            ))}
          </div>
        </div>
      )}

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{deleteTarget?.title}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete the project, all its jobs, segments,
              results, and audit logs from the database. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmHardDelete}
              disabled={isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? (
                <><Loader2 className="w-3.5 h-3.5 mr-2 animate-spin" /> Deleting...</>
              ) : (
                "Delete Everything"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function JobItem({
  job,
  isActive,
  onSelect,
}: {
  job: JobSummary
  isActive: boolean
  onSelect: (jobId: string) => void
}) {
  return (
    <button
      onClick={() => onSelect(job.job_id)}
      className={cn(
        "w-full text-left px-2 py-1.5 rounded-md text-sm transition-colors flex items-center gap-2",
        isActive
          ? "bg-primary/10 text-primary"
          : "hover:bg-secondary text-foreground"
      )}
    >
      <StatusIcon status={job.status} />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium truncate">
          {job.title || job.job_id.slice(0, 8) + "..."}
        </p>
        <p className="text-[10px] text-muted-foreground">
          {job.segment_count} seg · {job.result_count} clips
          <span className="ml-1">{formatDate(job.created_at)}</span>
        </p>
        <p className="text-[9px] text-muted-foreground/50 font-mono truncate">
          {job.job_id.slice(0, 8)}
        </p>
      </div>
    </button>
  )
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "complete":
      return <CheckCircle className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
    case "failed":
      return <XCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />
    case "cancelled":
      return <Ban className="w-3.5 h-3.5 text-orange-500 flex-shrink-0" />
    case "processing":
      return <Loader2 className="w-3.5 h-3.5 text-yellow-500 animate-spin flex-shrink-0" />
    default:
      return <Film className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  } catch {
    return iso
  }
}
