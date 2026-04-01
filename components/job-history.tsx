"use client"

import { Clock, CheckCircle, XCircle, Loader2, FileText } from "lucide-react"
import { cn } from "@/lib/utils"
import type { JobSummary } from "@/lib/types"

interface JobHistoryProps {
  jobs: JobSummary[]
  activeJobId: string | null
  onSelect: (jobId: string) => void
}

export function JobHistory({ jobs, activeJobId, onSelect }: JobHistoryProps) {
  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">
        Job History
      </h3>
      {jobs.length === 0 ? (
        <p className="text-xs text-muted-foreground">No jobs yet</p>
      ) : (
        <div className="space-y-1">
          {jobs.map((job) => (
            <button
              key={job.job_id}
              onClick={() => onSelect(job.job_id)}
              className={cn(
                "w-full text-left px-3 py-2 rounded-md text-sm transition-colors",
                activeJobId === job.job_id
                  ? "bg-primary/10 text-primary"
                  : "hover:bg-secondary text-foreground"
              )}
            >
              <div className="flex items-center gap-2">
                <StatusIcon status={job.status} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">
                    {job.job_id.slice(0, 8)}...
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {job.segment_count} seg &middot; {job.result_count} clips
                  </p>
                </div>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {formatDate(job.created_at)}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "complete":
      return <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
    case "failed":
      return <XCircle className="w-4 h-4 text-red-500 flex-shrink-0" />
    case "processing":
      return <Loader2 className="w-4 h-4 text-yellow-500 animate-spin flex-shrink-0" />
    default:
      return <FileText className="w-4 h-4 text-muted-foreground flex-shrink-0" />
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}
