"use client"

import { useState, useMemo, useEffect } from "react"
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
import type { JobSummary, ProjectSummary } from "@/lib/types"

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
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")

  const { projectJobMap, orphanJobs } = useMemo(() => {
    const map: Record<string, JobSummary[]> = {}
    const orphans: JobSummary[] = []
    for (const j of jobs) {
      if (j.project_id) {
        if (!map[j.project_id]) map[j.project_id] = []
        map[j.project_id].push(j)
      } else {
        orphans.push(j)
      }
    }
    return { projectJobMap: map, orphanJobs: orphans }
  }, [jobs])

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
    }
  }, [activeJobId, activeProjectId, jobs])

  const toggleExpand = (projectId: string) => {
    setExpandedProjects(prev => {
      const next = new Set(prev)
      if (next.has(projectId)) next.delete(projectId)
      else next.add(projectId)
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
        {projects.map((project) => {
          const isExpanded = expandedProjects.has(project.project_id)
          const isActive = activeProjectId === project.project_id
          const projectJobs = projectJobMap[project.project_id] || []
          const hasProcessing = projectJobs.some(j => j.status === "processing")
          const liveClipCount = projectJobs.reduce((sum, j) => sum + (j.result_count || 0), 0)

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
                  {isExpanded ? (
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
                      {projectJobs.length} job{projectJobs.length !== 1 ? "s" : ""}
                      {" · "}
                      {liveClipCount} clip{liveClipCount !== 1 ? "s" : ""}
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
                      onClick={() => onDeleteProject(project.project_id)}
                    >
                      <Trash2 className="w-3.5 h-3.5 mr-2" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              {isExpanded && projectJobs.length > 0 && (
                <div className="ml-5 pl-3 border-l border-border/50 space-y-0.5 mt-0.5">
                  {projectJobs.map((job) => (
                    <JobItem
                      key={job.job_id}
                      job={job}
                      isActive={activeJobId === job.job_id}
                      onSelect={onSelectJob}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
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
