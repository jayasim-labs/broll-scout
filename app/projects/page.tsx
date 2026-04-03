"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import {
  FolderOpen, Folder, Film, Clock, CheckCircle, XCircle,
  Loader2, Ban, ChevronDown, ChevronRight, Pencil, Trash2,
  MoreHorizontal, Search, Plus, FileText, ArrowRight,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import type { ProjectSummary, ProjectWithJobs, JobSummary } from "@/lib/types"
import { categoryLabel } from "@/lib/types"

const API = "/api/v1"

export default function ProjectsPage() {
  const router = useRouter()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [expandedProject, setExpandedProject] = useState<ProjectWithJobs | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [orphanJobs, setOrphanJobs] = useState<JobSummary[]>([])

  const loadProjects = useCallback(async () => {
    try {
      const [projRes, jobRes] = await Promise.all([
        fetch(`${API}/projects`),
        fetch(`${API}/jobs`),
      ])
      if (projRes.ok) {
        const data = await projRes.json()
        setProjects(data.projects || [])
      }
      if (jobRes.ok) {
        const data = await jobRes.json()
        const jobs: JobSummary[] = data.jobs || []
        setOrphanJobs(jobs.filter(j => !j.project_id))
      }
    } catch {
      // backend may not be running
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadProjects()
  }, [loadProjects])

  const toggleExpand = async (projectId: string) => {
    if (expandedId === projectId) {
      setExpandedId(null)
      setExpandedProject(null)
      return
    }
    setExpandedId(projectId)
    setLoadingDetail(true)
    try {
      const resp = await fetch(`${API}/projects/${projectId}`)
      if (resp.ok) {
        const data = await resp.json()
        setExpandedProject(data)
      }
    } catch {
      toast.error("Failed to load project details")
    } finally {
      setLoadingDetail(false)
    }
  }

  const handleRename = async (projectId: string) => {
    if (!renameValue.trim()) return
    try {
      const resp = await fetch(`${API}/projects/${projectId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: renameValue.trim() }),
      })
      if (resp.ok) {
        setProjects(prev =>
          prev.map(p => p.project_id === projectId ? { ...p, title: renameValue.trim() } : p)
        )
        toast.success("Project renamed")
      }
    } catch {
      toast.error("Failed to rename project")
    }
    setRenamingId(null)
    setRenameValue("")
  }

  const handleDelete = async (projectId: string, title: string) => {
    if (!confirm(`Delete project "${title}"? The jobs will remain but won't be linked to a project.`)) return
    try {
      const resp = await fetch(`${API}/projects/${projectId}`, { method: "DELETE" })
      if (resp.ok) {
        setProjects(prev => prev.filter(p => p.project_id !== projectId))
        if (expandedId === projectId) {
          setExpandedId(null)
          setExpandedProject(null)
        }
        toast.success("Project deleted")
      }
    } catch {
      toast.error("Failed to delete project")
    }
  }

  const filtered = searchQuery.trim()
    ? projects.filter(p =>
        p.title.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : projects

  const totalJobs = projects.reduce((s, p) => s + p.job_count, 0)
  const totalClips = projects.reduce((s, p) => s + p.total_clips, 0)

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">Projects</h1>
            <p className="text-sm text-muted-foreground">
              Every script you submit becomes a project. Each project contains one or more scouting jobs.
            </p>
          </div>
          <Button onClick={() => router.push("/")} className="gap-2 shrink-0">
            <Plus className="w-4 h-4" />
            New Script
          </Button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4">
          <Card>
            <CardContent className="pt-5 pb-4 flex items-center gap-3">
              <div className="p-2 rounded-lg bg-amber-500/10">
                <FolderOpen className="w-5 h-5 text-amber-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{projects.length}</p>
                <p className="text-xs text-muted-foreground">Projects</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-5 pb-4 flex items-center gap-3">
              <div className="p-2 rounded-lg bg-blue-500/10">
                <FileText className="w-5 h-5 text-blue-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{totalJobs}</p>
                <p className="text-xs text-muted-foreground">Total Jobs</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-5 pb-4 flex items-center gap-3">
              <div className="p-2 rounded-lg bg-emerald-500/10">
                <Film className="w-5 h-5 text-emerald-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{totalClips}</p>
                <p className="text-xs text-muted-foreground">Total Clips Found</p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search projects by title..."
            className="pl-9"
          />
        </div>

        {/* Project List */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : filtered.length === 0 && orphanJobs.length === 0 ? (
          <Card>
            <CardContent className="py-16 text-center">
              <FolderOpen className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
              <p className="text-muted-foreground">
                {searchQuery ? "No projects match your search." : "No projects yet. Submit a script to create your first project."}
              </p>
              {!searchQuery && (
                <Button variant="outline" className="mt-4 gap-2" onClick={() => router.push("/")}>
                  <Plus className="w-4 h-4" />
                  Create First Project
                </Button>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-2">
            {filtered.map((project) => (
              <ProjectCard
                key={project.project_id}
                project={project}
                isExpanded={expandedId === project.project_id}
                expandedDetail={expandedId === project.project_id ? expandedProject : null}
                loadingDetail={expandedId === project.project_id && loadingDetail}
                isRenaming={renamingId === project.project_id}
                renameValue={renameValue}
                onToggle={() => toggleExpand(project.project_id)}
                onStartRename={() => { setRenamingId(project.project_id); setRenameValue(project.title) }}
                onRenameChange={setRenameValue}
                onRenameSubmit={() => handleRename(project.project_id)}
                onRenameCancel={() => { setRenamingId(null); setRenameValue("") }}
                onDelete={() => handleDelete(project.project_id, project.title)}
                onViewJob={(jobId) => router.push(`/?job=${jobId}`)}
              />
            ))}

            {orphanJobs.length > 0 && (
              <div className="pt-4 border-t border-border">
                <h3 className="text-sm font-medium text-muted-foreground mb-2 flex items-center gap-2">
                  <FileText className="w-4 h-4" />
                  Jobs Without a Project ({orphanJobs.length})
                </h3>
                <div className="space-y-1">
                  {orphanJobs.map((job) => (
                    <JobRow key={job.job_id} job={job} onView={() => router.push(`/?job=${job.job_id}`)} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

function ProjectCard({
  project,
  isExpanded,
  expandedDetail,
  loadingDetail,
  isRenaming,
  renameValue,
  onToggle,
  onStartRename,
  onRenameChange,
  onRenameSubmit,
  onRenameCancel,
  onDelete,
  onViewJob,
}: {
  project: ProjectSummary
  isExpanded: boolean
  expandedDetail: ProjectWithJobs | null
  loadingDetail: boolean
  isRenaming: boolean
  renameValue: string
  onToggle: () => void
  onStartRename: () => void
  onRenameChange: (v: string) => void
  onRenameSubmit: () => void
  onRenameCancel: () => void
  onDelete: () => void
  onViewJob: (jobId: string) => void
}) {
  return (
    <Card className={cn(
      "transition-all",
      isExpanded && "ring-1 ring-primary/20",
    )}>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-3">
          <button onClick={onToggle} className="flex items-center gap-2 flex-1 text-left min-w-0">
            <div className="flex-shrink-0">
              {isExpanded ? (
                <ChevronDown className="w-4 h-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              )}
            </div>
            <div className="flex-shrink-0">
              {isExpanded ? (
                <FolderOpen className="w-5 h-5 text-amber-500" />
              ) : (
                <Folder className="w-5 h-5 text-amber-500/70" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              {isRenaming ? (
                <Input
                  value={renameValue}
                  onChange={(e) => onRenameChange(e.target.value)}
                  onBlur={onRenameSubmit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onRenameSubmit()
                    if (e.key === "Escape") onRenameCancel()
                  }}
                  className="h-7 text-sm"
                  autoFocus
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <div className="flex items-center gap-2 min-w-0">
                  <CardTitle className="text-base truncate">{project.title}</CardTitle>
                  {project.category && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary font-medium shrink-0">
                      {categoryLabel(project.category)}
                    </span>
                  )}
                </div>
              )}
            </div>
          </button>

          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="hidden sm:flex items-center gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <FileText className="w-3.5 h-3.5" />
                {project.job_count} job{project.job_count !== 1 ? "s" : ""}
              </span>
              <span className="flex items-center gap-1">
                <Film className="w-3.5 h-3.5" />
                {project.total_clips} clip{project.total_clips !== 1 ? "s" : ""}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="w-3.5 h-3.5" />
                {formatDate(project.created_at)}
              </span>
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-7 w-7">
                  <MoreHorizontal className="w-4 h-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-40">
                <DropdownMenuItem onClick={onStartRename}>
                  <Pencil className="w-3.5 h-3.5 mr-2" /> Rename
                </DropdownMenuItem>
                <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={onDelete}>
                  <Trash2 className="w-3.5 h-3.5 mr-2" /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* Mobile stats */}
        <div className="flex sm:hidden items-center gap-4 text-xs text-muted-foreground pl-11 pt-1">
          <span>{project.job_count} jobs</span>
          <span>{project.total_clips} clips</span>
          <span>{formatDate(project.created_at)}</span>
        </div>
      </CardHeader>

      {isExpanded && (
        <CardContent className="pt-0 pb-3">
          {loadingDetail ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : expandedDetail && expandedDetail.jobs.length > 0 ? (
            <div className="border-t border-border pt-3 space-y-1">
              <div className="grid grid-cols-[auto_1fr_80px_80px_80px_100px_32px] gap-2 px-2 pb-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                <span />
                <span>Title / Job ID</span>
                <span className="text-center">Status</span>
                <span className="text-center">Segments</span>
                <span className="text-center">Clips</span>
                <span className="text-center">Date</span>
                <span />
              </div>
              {expandedDetail.jobs.map((job) => (
                <JobRow key={job.job_id} job={job} onView={() => onViewJob(job.job_id)} />
              ))}
            </div>
          ) : (
            <div className="border-t border-border pt-3">
              <p className="text-sm text-muted-foreground text-center py-4">
                No jobs in this project yet.
              </p>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function JobRow({ job, onView }: { job: JobSummary; onView: () => void }) {
  return (
    <button
      onClick={onView}
      className="w-full grid grid-cols-[auto_1fr_80px_80px_80px_100px_32px] gap-2 items-center px-2 py-2 rounded-md text-sm hover:bg-secondary/60 transition-colors text-left"
    >
      <StatusIcon status={job.status} />
      <span className="truncate text-xs font-medium">
        {job.title || job.job_id.slice(0, 12) + "..."}
      </span>
      <span className="text-center">
        <StatusBadge status={job.status} />
      </span>
      <span className="text-xs text-center text-muted-foreground">
        {job.segment_count}
      </span>
      <span className="text-xs text-center text-muted-foreground">
        {job.result_count}
      </span>
      <span className="text-[10px] text-center text-muted-foreground">
        {formatDate(job.created_at)}
      </span>
      <ArrowRight className="w-3.5 h-3.5 text-muted-foreground/50" />
    </button>
  )
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "complete":
      return <CheckCircle className="w-4 h-4 text-emerald-500 flex-shrink-0" />
    case "failed":
      return <XCircle className="w-4 h-4 text-red-500 flex-shrink-0" />
    case "cancelled":
      return <Ban className="w-4 h-4 text-orange-500 flex-shrink-0" />
    case "processing":
      return <Loader2 className="w-4 h-4 text-yellow-500 animate-spin flex-shrink-0" />
    default:
      return <FileText className="w-4 h-4 text-muted-foreground flex-shrink-0" />
  }
}

function StatusBadge({ status }: { status: string }) {
  const base = "text-[10px] px-1.5 py-0.5 rounded-full font-medium"
  switch (status) {
    case "complete":
      return <span className={cn(base, "bg-emerald-500/15 text-emerald-400")}>Complete</span>
    case "failed":
      return <span className={cn(base, "bg-red-500/15 text-red-400")}>Failed</span>
    case "cancelled":
      return <span className={cn(base, "bg-orange-500/15 text-orange-400")}>Cancelled</span>
    case "processing":
      return <span className={cn(base, "bg-yellow-500/15 text-yellow-400")}>Running</span>
    default:
      return <span className={cn(base, "bg-secondary text-muted-foreground")}>{status}</span>
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
  } catch {
    return iso
  }
}
