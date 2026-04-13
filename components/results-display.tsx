"use client"

import { useState, useRef, useCallback, useMemo, useEffect } from "react"
import {
  Download, ChevronDown, ChevronUp, ExternalLink, RefreshCw,
  ThumbsUp, ThumbsDown, Clock, Eye, Play, Scissors,
  Check, Pause, SkipBack, SkipForward, X, Plus, Loader2,
  Brain, Search, Globe, Sparkles, Filter, AlertTriangle, Zap,
  Mic, Shield, Terminal, ChevronRight, Languages,
  type LucideIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { JobResponse, Segment, RankedResult, ActivityEntry, ShotIntent, Scarcity, AuditStatus } from "@/lib/types"
import { categoryLabel } from "@/lib/types"

interface ResultsDisplayProps {
  job: JobResponse
  onExport?: () => void
  onNewSearch: () => void
  onRefreshJob?: () => void
  onResume?: (fromStage: "transcripts" | "matching") => void
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return n.toString()
}

function scoreColor(score: number): string {
  if (score >= 0.7) return "bg-green-500/20 text-green-400 border-green-500/30"
  if (score >= 0.4) return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
  return "bg-red-500/20 text-red-400 border-red-500/30"
}

const INTENT_LABELS: Record<string, { label: string; color: string }> = {
  literal: { label: "Literal", color: "bg-blue-500/15 text-blue-400 border-blue-500/30" },
  illustrative: { label: "Illustrative", color: "bg-purple-500/15 text-purple-400 border-purple-500/30" },
  atmospheric: { label: "Atmospheric", color: "bg-teal-500/15 text-teal-400 border-teal-500/30" },
}

const SCARCITY_LABELS: Record<string, { label: string; color: string }> = {
  common: { label: "Common", color: "bg-green-500/15 text-green-400 border-green-500/30" },
  medium: { label: "Medium", color: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30" },
  rare: { label: "Rare", color: "bg-red-500/15 text-red-400 border-red-500/30" },
}

const AUDIT_LABELS: Record<string, { label: string; color: string; icon: typeof Check }> = {
  pass: { label: "Passed", color: "bg-green-500/15 text-green-400 border-green-500/30", icon: Check },
  review: { label: "Review", color: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30", icon: AlertTriangle },
  reject: { label: "Flagged", color: "bg-red-500/15 text-red-400 border-red-500/30", icon: X },
  unaudited: { label: "", color: "", icon: Check },
}

const SEGMENTS_PER_PAGE = 15

export function ResultsDisplay({ job, onExport, onNewSearch, onRefreshJob, onResume }: ResultsDisplayProps) {
  const activityLog = job.activity_log || []
  const displaySegments = useMemo(
    () => job.segments.filter(s => s.results.length > 0 || s.broll_count === 0),
    [job.segments],
  )
  const hiddenCount = job.segments.length - displaySegments.length
  const totalPages = Math.max(1, Math.ceil(displaySegments.length / SEGMENTS_PER_PAGE))
  const [currentPage, setCurrentPage] = useState(1)
  const pageSegments = useMemo(
    () => displaySegments.slice((currentPage - 1) * SEGMENTS_PER_PAGE, currentPage * SEGMENTS_PER_PAGE),
    [displaySegments, currentPage],
  )

  return (
    <div className="space-y-6">
      {(job.title || job.category) && (
        <div className="flex items-center gap-3">
          {job.title && <h2 className="text-lg font-semibold">{job.title}</h2>}
          {job.category && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
              {categoryLabel(job.category)}
            </span>
          )}
        </div>
      )}

      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {job.script_duration_minutes}-minute script &middot;{" "}
          {job.total_segments} segments &middot;{" "}
          {job.total_shots || 0} B-roll shots &middot;{" "}
          {job.total_results} clips found &middot;{" "}
          {job.segments_with_no_broll} host-on-camera segments
        </p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Duration" value={`${job.script_duration_minutes} min`} />
          <StatCard label="Segments" value={job.total_segments} />
          <StatCard label="B-Roll Shots" value={job.total_shots || 0} />
          <StatCard label="Clips Found" value={job.total_results} />
        </div>
        {job.coverage_assessment?.note && (
          <p className="text-xs text-muted-foreground italic">
            {job.coverage_assessment.note}
          </p>
        )}
        {job.warnings && job.warnings.length > 0 && (
          <div className="space-y-1">
            {job.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>{w.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <Tabs defaultValue="clips" className="w-full">
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="clips" className="gap-1.5">
              <Scissors className="w-3.5 h-3.5" />
              Segments & Clips
            </TabsTrigger>
            <TabsTrigger value="activity" className="gap-1.5">
              <Zap className="w-3.5 h-3.5" />
              Activity Log
              {activityLog.length > 0 && (
                <Badge variant="secondary" className="ml-1 text-[10px] px-1.5 py-0">
                  {activityLog.length}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>
          <div className="flex items-center gap-2">
            {onResume && job.pipeline_checkpoint && job.pipeline_checkpoint !== "completed" && (job.status === "failed" || job.status === "partial") && (
              <>
                {(job.pipeline_checkpoint === "searched" || job.pipeline_checkpoint === "matched") && (
                  <Button variant="default" size="sm" onClick={() => onResume("transcripts")} className="gap-2">
                    <RefreshCw className="w-4 h-4" />
                    Resume from Transcripts
                  </Button>
                )}
                {job.pipeline_checkpoint === "matched" && (
                  <Button variant="secondary" size="sm" onClick={() => onResume("matching")} className="gap-2">
                    <RefreshCw className="w-4 h-4" />
                    Resume from Matching
                  </Button>
                )}
              </>
            )}
            <Button variant="secondary" size="sm" onClick={onNewSearch} className="gap-2">
              <RefreshCw className="w-4 h-4" />
              New Search
            </Button>
          </div>
        </div>

        <TabsContent value="clips">
          <div className="space-y-3">
            {pageSegments.map((segment, idx) => (
              <SegmentCard
                key={segment.segment_id}
                segment={segment}
                index={(currentPage - 1) * SEGMENTS_PER_PAGE + idx}
                jobId={job.job_id}
                onRefreshJob={onRefreshJob}
              />
            ))}
          </div>

          {hiddenCount > 0 && (
            <p className="text-xs text-muted-foreground mt-3">
              {hiddenCount} segment{hiddenCount !== 1 ? "s" : ""} with no clips hidden
            </p>
          )}

          {totalPages > 1 && (
            <Pagination
              currentPage={currentPage}
              totalPages={totalPages}
              onPageChange={setCurrentPage}
            />
          )}
        </TabsContent>

        <TabsContent value="activity">
          <ActivityLogPanel entries={activityLog} />
        </TabsContent>
      </Tabs>

      <Card>
        <CardContent className="pt-4">
          <p className="text-sm text-muted-foreground mb-2">API & Resource Usage</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-1.5 text-xs">
            <span className="text-muted-foreground">GPT-4o <span className="text-foreground/70">(translation)</span>: <span className="text-foreground">{job.api_costs.openai_calls}</span> calls</span>
            {(job.api_costs.openai_mini_calls || 0) > 0 && (
              <span className="text-muted-foreground">GPT-4o-mini <span className="text-foreground/70">(timestamps)</span>: <span className="text-foreground">{job.api_costs.openai_mini_calls}</span> calls</span>
            )}
            {(job.api_costs.local_matcher_calls || 0) > 0 && (
              <span className="text-muted-foreground">Local LLM <span className="text-foreground/70">(timestamps, $0)</span>: <span className="text-foreground">{job.api_costs.local_matcher_calls}</span> calls{job.api_costs.local_matcher_avg_latency_ms > 0 ? ` (~${(job.api_costs.local_matcher_avg_latency_ms / 1000).toFixed(1)}s avg)` : ''}</span>
            )}
            {(job.api_costs.openai_mini_calls || 0) === 0 && (job.api_costs.local_matcher_calls || 0) === 0 && (
              <span className="text-muted-foreground">Timestamps: <span className="text-foreground">0</span> calls</span>
            )}
            <span className="text-muted-foreground">Whisper base <span className="text-foreground/70">(local)</span>: <span className="text-foreground">{job.api_costs.whisper_calls || 0}</span> videos{job.api_costs.whisper_minutes > 0 ? ` (${job.api_costs.whisper_minutes.toFixed(1)} min audio)` : ''}</span>
            <span className="text-muted-foreground">yt-dlp searches <span className="text-foreground/70">(local)</span>: <span className="text-foreground">{job.api_costs.ytdlp_searches || 0}</span></span>
            <span className="text-muted-foreground">YouTube API: <span className="text-foreground">{job.api_costs.youtube_api_units}</span> units</span>
            {job.api_costs.gemini_calls > 0 && (
              <span className="text-muted-foreground">Gemini <span className="text-foreground/70">(expansion)</span>: <span className="text-foreground">{job.api_costs.gemini_calls}</span> calls</span>
            )}
          </div>
          <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
            {job.processing_time_seconds && (
              <span>Processed in {job.processing_time_seconds.toFixed(1)}s</span>
            )}
            {job.api_costs.estimated_cost_usd > 0 && (
              <span>Est. cost: ${job.api_costs.estimated_cost_usd.toFixed(4)}</span>
            )}
            {job.api_costs.search_mode && (
              <span className="capitalize">Search: {job.api_costs.search_mode}</span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function StatCard({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="pt-3 pb-3">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className={cn("text-xl font-bold", accent === true && "text-green-400", accent === false && "text-red-400")}>
          {value}
        </p>
      </CardContent>
    </Card>
  )
}

function Pagination({ currentPage, totalPages, onPageChange }: {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
}) {
  const pages = useMemo(() => {
    const items: (number | "...")[] = []
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) items.push(i)
    } else {
      items.push(1)
      if (currentPage > 3) items.push("...")
      for (let i = Math.max(2, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
        items.push(i)
      }
      if (currentPage < totalPages - 2) items.push("...")
      items.push(totalPages)
    }
    return items
  }, [currentPage, totalPages])

  return (
    <div className="flex items-center justify-center gap-1.5 pt-4">
      <Button
        variant="outline"
        size="sm"
        className="h-8 w-8 p-0"
        disabled={currentPage === 1}
        onClick={() => onPageChange(currentPage - 1)}
      >
        <ChevronUp className="w-4 h-4 -rotate-90" />
      </Button>
      {pages.map((p, i) =>
        p === "..." ? (
          <span key={`ellipsis-${i}`} className="px-1 text-xs text-muted-foreground">...</span>
        ) : (
          <Button
            key={p}
            variant={p === currentPage ? "default" : "outline"}
            size="sm"
            className="h-8 w-8 p-0 text-xs"
            onClick={() => { onPageChange(p); window.scrollTo({ top: 0, behavior: "smooth" }) }}
          >
            {p}
          </Button>
        )
      )}
      <Button
        variant="outline"
        size="sm"
        className="h-8 w-8 p-0"
        disabled={currentPage === totalPages}
        onClick={() => onPageChange(currentPage + 1)}
      >
        <ChevronDown className="w-4 h-4 -rotate-90" />
      </Button>
      <span className="text-xs text-muted-foreground ml-2">
        Page {currentPage} of {totalPages}
      </span>
    </div>
  )
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  if (m === 0) return `${s}s`
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}

function SegmentCard({ segment, index, jobId, onRefreshJob }: { segment: Segment; index: number; jobId: string; onRefreshJob?: () => void }) {
  const [isOpen, setIsOpen] = useState(true)
  const isNoBroll = segment.broll_count === 0
  const hasShots = segment.broll_shots && segment.broll_shots.length > 0
  const resultsByShot = useMemo(() => {
    if (!hasShots) return null
    const knownShotIds = new Set(segment.broll_shots.map(s => s.shot_id))
    const map: Record<string, typeof segment.results> = {}
    for (const shot of segment.broll_shots) {
      map[shot.shot_id] = segment.results.filter(r => r.shot_id === shot.shot_id)
    }
    const extra = segment.results.filter(r => r.shot_id && !knownShotIds.has(r.shot_id))
    const unassigned = segment.results.filter(r => !r.shot_id)
    if (extra.length > 0 || unassigned.length > 0) {
      map["_extra"] = [...extra, ...unassigned]
    }
    return map
  }, [segment, hasShots])

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <Card className={cn(isNoBroll && "opacity-60")}>
        <CollapsibleTrigger asChild>
          <CardHeader className="cursor-pointer hover:bg-secondary/30 transition-colors py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-primary/10 text-primary font-bold text-sm shrink-0">
                  {index + 1}
                </div>
                <div>
                  <h3 className="font-medium text-sm">{segment.title}</h3>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span>{segment.emotional_tone}</span>
                    <span>·</span>
                    <span>{formatDuration(segment.estimated_duration_seconds)}</span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {isNoBroll ? (
                  <Badge variant="secondary" className="text-xs text-muted-foreground">
                    No B-roll
                  </Badge>
                ) : (
                  <>
                    <Badge variant="outline" className="text-xs">
                      {segment.results.length}/{Math.max(segment.broll_count, segment.results.length)} shot{Math.max(segment.broll_count, segment.results.length) !== 1 ? 's' : ''}
                    </Badge>
                  </>
                )}
                {isOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </div>
            </div>
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="pt-0 space-y-4">
            {isNoBroll ? (
              <p className="text-sm text-muted-foreground italic">
                {segment.broll_note || "Host on camera — no B-roll needed for this segment."}
              </p>
            ) : (
              <>
                <div className="space-y-1">
                  <p className="text-sm text-muted-foreground">{segment.summary}</p>
                </div>
                {hasShots && resultsByShot ? (
                  <div className="space-y-4">
                    {segment.broll_shots.map((shot, shotIdx) => {
                      const shotResults = resultsByShot[shot.shot_id] || []
                      const intentMeta = INTENT_LABELS[shot.shot_intent || "literal"]
                      const scarcityMeta = SCARCITY_LABELS[shot.scarcity || "common"]
                      return (
                        <div key={shot.shot_id} className="space-y-2">
                          <div className="flex items-start gap-2">
                            <span className="text-xs font-mono text-muted-foreground/70 pt-0.5 shrink-0">
                              Shot {shotIdx + 1}
                            </span>
                            <div className="min-w-0">
                              <p className="text-sm font-medium">{shot.visual_need}</p>
                              <div className="flex items-center gap-1.5 mt-0.5">
                                {intentMeta && (
                                  <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border", intentMeta.color)}>
                                    {intentMeta.label}
                                  </Badge>
                                )}
                                {scarcityMeta && shot.scarcity && shot.scarcity !== "common" && (
                                  <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border", scarcityMeta.color)}>
                                    {scarcityMeta.label} footage
                                  </Badge>
                                )}
                              </div>
                            </div>
                          </div>
                          {shotResults.length > 0 ? (
                            <div className="space-y-2 ml-12">
                              {shotResults.map((result) => (
                                <ResultCard key={result.result_id} result={result} jobId={jobId} />
                              ))}
                            </div>
                          ) : (
                            <p className="text-xs text-muted-foreground italic ml-12">No clip found for this shot</p>
                          )}
                        </div>
                      )
                    })}
                    {resultsByShot["_extra"] && resultsByShot["_extra"].length > 0 && (
                      <div className="space-y-2 pt-2 border-t border-border/40">
                        {resultsByShot["_extra"].map((result, i) => (
                          <div key={result.result_id} className="space-y-2">
                            <div className="flex items-start gap-2">
                              <span className="text-xs font-mono text-muted-foreground/70 pt-0.5 shrink-0">
                                Shot {segment.broll_shots.length + i + 1}
                              </span>
                              <span className="text-sm font-medium flex items-center">
                                {result.shot_visual_need || "Editor-added shot"}
                                <Badge variant="secondary" className="ml-2 text-[9px] px-1.5 py-0">expanded</Badge>
                              </span>
                            </div>
                            <div className="space-y-2 ml-12">
                              <ResultCard result={result} jobId={jobId} />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-1">
                      {segment.key_terms.map((term, i) => (
                        <Badge key={i} variant="secondary" className="text-xs">{term}</Badge>
                      ))}
                    </div>
                    {segment.results.length > 0 ? (
                      <div className="space-y-3">
                        {segment.results.map((result) => (
                          <ResultCard key={result.result_id} result={result} jobId={jobId} />
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground italic">No clips found for this segment</p>
                    )}
                  </>
                )}
                <LibrarySuggestions segment={segment} />
                <ExpandShotButton jobId={jobId} segment={segment} onRefreshJob={onRefreshJob} />
              </>
            )}
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}

type ExpandPhase = "idle" | "requesting" | "generating" | "searching" | "transcripts" | "matching" | "ranking" | "done" | "error" | "unknown"

interface ExpandLogEntry {
  time: string
  phase: string
  message: string
  detail?: string
}

const PHASE_ORDER: ExpandPhase[] = ["generating", "searching", "transcripts", "matching", "ranking"]

const PHASE_LABELS: Record<string, string> = {
  generating: "Ideate",
  searching: "Search",
  transcripts: "Transcripts",
  matching: "Match",
  ranking: "Rank",
}

const PHASE_ICONS: Record<string, LucideIcon> = {
  generating: Brain,
  searching: Search,
  transcripts: Globe,
  matching: Zap,
  ranking: Filter,
  done: Check,
  error: X,
}

function ExpandShotButton({ jobId, segment, onRefreshJob }: { jobId: string; segment: Segment; onRefreshJob?: () => void }) {
  const [phase, setPhase] = useState<ExpandPhase>("idle")
  const [logEntries, setLogEntries] = useState<ExpandLogEntry[]>([])
  const [newResults, setNewResults] = useState<RankedResult[]>([])
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [showLog, setShowLog] = useState(true)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const initialResultCount = useRef(segment.results.length)
  const logEndRef = useRef<HTMLDivElement>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  useEffect(() => () => stopPolling(), [stopPolling])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [logEntries.length])

  const startPolling = useCallback(() => {
    let elapsed = 0
    const POLL_INTERVAL = 3000
    const MAX_POLL = 180000

    pollRef.current = setInterval(async () => {
      elapsed += POLL_INTERVAL

      try {
        const progressResp = await fetch(
          `/api/v1/jobs/${jobId}/segments/${segment.segment_id}/expand-progress`
        )
        if (progressResp.ok) {
          const data = await progressResp.json()
          if (data.phase && data.phase !== "unknown") {
            setPhase(data.phase as ExpandPhase)
          }
          if (data.log && data.log.length > 0) {
            setLogEntries(data.log)
          }

          if (data.phase === "done" || data.phase === "error") {
            stopPolling()
            if (data.phase === "error") {
              setErrorMsg(data.log?.slice(-1)[0]?.message || "Pipeline error")
            }
            const lastMsg = (data.log?.slice(-1)[0]?.message || "") as string
            const didAddClip = lastMsg.toLowerCase().includes("added")
            if (didAddClip) {
              const jobResp = await fetch(`/api/v1/jobs/${jobId}`)
              if (jobResp.ok) {
                const jobData = await jobResp.json()
                const seg = jobData.segments?.find((s: Segment) => s.segment_id === segment.segment_id)
                if (seg) {
                  const knownIds = new Set(segment.results.map(r => r.result_id))
                  const added = seg.results.filter((r: RankedResult) => !knownIds.has(r.result_id))
                  if (added.length > 0) {
                    setNewResults(added)
                  } else {
                    setNewResults(seg.results.slice(-1))
                  }
                }
              }
            }
            onRefreshJob?.()
            return
          }
        }
      } catch { /* keep polling */ }

      if (elapsed >= MAX_POLL) {
        stopPolling()
        setPhase("done")
        setErrorMsg("Timed out — the expansion may still be processing. Try refreshing the page.")
        onRefreshJob?.()
      }
    }, POLL_INTERVAL)
  }, [jobId, segment.segment_id, stopPolling, onRefreshJob])

  // On mount: check if there's an in-flight or completed expansion from a previous visit
  useEffect(() => {
    let cancelled = false
    async function checkExistingProgress() {
      try {
        const resp = await fetch(
          `/api/v1/jobs/${jobId}/segments/${segment.segment_id}/expand-progress`
        )
        if (!resp.ok || cancelled) return
        const data = await resp.json()
        if (!data.phase || data.phase === "unknown" || !data.log?.length) return

        setPhase(data.phase as ExpandPhase)
        setLogEntries(data.log || [])
        setShowLog(true)

        if (data.phase === "done") {
          const lastMsg = (data.log?.slice(-1)[0]?.message || "") as string
          if (lastMsg.toLowerCase().includes("added")) {
            const jobResp = await fetch(`/api/v1/jobs/${jobId}`)
            if (jobResp.ok && !cancelled) {
              const jobData = await jobResp.json()
              const seg = jobData.segments?.find((s: Segment) => s.segment_id === segment.segment_id)
              if (seg) {
                const knownIds = new Set(segment.results.map(r => r.result_id))
                const added = seg.results.filter((r: RankedResult) => !knownIds.has(r.result_id))
                if (added.length > 0) setNewResults(added)
              }
            }
            onRefreshJob?.()
          }
        } else if (data.phase === "error") {
          setErrorMsg(data.log?.slice(-1)[0]?.message || "Pipeline error")
        } else {
          startPolling()
        }
      } catch { /* silent */ }
    }
    checkExistingProgress()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, segment.segment_id])

  const handleExpand = async () => {
    setPhase("requesting")
    setLogEntries([])
    setNewResults([])
    setErrorMsg(null)
    setShowLog(true)
    initialResultCount.current = segment.results.length
    try {
      const res = await fetch(`/api/v1/jobs/${jobId}/segments/${segment.segment_id}/expand-shots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, segment_id: segment.segment_id, count: 1 }),
      })
      if (res.ok) {
        setPhase("generating")
        startPolling()
      } else {
        const err = await res.json().catch(() => null)
        setPhase("error")
        setErrorMsg(err?.detail || `Backend returned ${res.status}`)
      }
    } catch {
      setPhase("error")
      setErrorMsg("Failed to connect to backend")
    }
  }

  const handleReset = () => {
    stopPolling()
    setPhase("idle")
    setLogEntries([])
    setNewResults([])
    setErrorMsg(null)
  }

  if (phase === "idle") {
    return (
      <div className="mt-2">
        <Button
          variant="ghost"
          size="sm"
          className="text-xs text-muted-foreground gap-1.5 hover:text-primary"
          onClick={handleExpand}
        >
          <Plus className="w-3.5 h-3.5" />
          Add another shot for this segment
        </Button>
      </div>
    )
  }

  const isInProgress = !["idle", "done", "error"].includes(phase)
  const currentPhaseIdx = PHASE_ORDER.indexOf(phase as typeof PHASE_ORDER[number])

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-secondary/30 p-3 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isInProgress && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
          {phase === "done" && !errorMsg && newResults.length > 0 && <Check className="w-4 h-4 text-green-500" />}
          {phase === "done" && !errorMsg && newResults.length === 0 && <AlertTriangle className="w-4 h-4 text-yellow-500" />}
          {phase === "error" && <X className="w-4 h-4 text-red-500" />}
          <span className="text-xs font-medium">
            {errorMsg
              ? errorMsg
              : phase === "done" && newResults.length > 0
                ? `New clip added for "${segment.title}"`
                : phase === "done"
                  ? "No matching clip found"
                  : `Expanding "${segment.title}"...`}
          </span>
        </div>
        {logEntries.length > 0 && (
          <button
            onClick={() => setShowLog(!showLog)}
            className="text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            <Terminal className="w-3 h-3" />
            {showLog ? "Hide" : "Show"} log ({logEntries.length})
          </button>
        )}
      </div>

      {/* Progress steps */}
      {isInProgress && (
        <div className="flex gap-0.5">
          {PHASE_ORDER.map((step, stepIdx) => {
            const isDone = stepIdx < currentPhaseIdx
            const isCurrent = stepIdx === currentPhaseIdx
            const StepIcon = PHASE_ICONS[step] || Zap
            return (
              <div key={step} className="flex-1 flex flex-col items-center gap-1">
                <div className={cn(
                  "h-1.5 w-full rounded-full transition-all duration-500",
                  isDone ? "bg-green-500" : isCurrent ? "bg-primary animate-pulse" : "bg-muted"
                )} />
                <div className="flex items-center gap-0.5">
                  <StepIcon className={cn(
                    "w-2.5 h-2.5",
                    isDone ? "text-green-500" : isCurrent ? "text-primary" : "text-muted-foreground/40"
                  )} />
                  <span className={cn(
                    "text-[9px]",
                    isDone ? "text-green-500" : isCurrent ? "text-primary font-medium" : "text-muted-foreground/40"
                  )}>
                    {PHASE_LABELS[step] || step}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Inline activity log */}
      {showLog && logEntries.length > 0 && (
        <div className="rounded-md border border-border/40 bg-background/50 overflow-hidden">
          <div className="max-h-48 overflow-y-auto px-3 py-2 space-y-1 text-[11px] font-mono">
            {logEntries.map((entry, i) => {
              const PhaseIcon = PHASE_ICONS[entry.phase] || Zap
              const isLatest = i === logEntries.length - 1 && isInProgress
              return (
                <div key={i} className={cn(
                  "flex items-start gap-2 py-0.5",
                  isLatest ? "text-primary" : "text-muted-foreground"
                )}>
                  <span className="text-muted-foreground/50 flex-shrink-0 tabular-nums">{entry.time}</span>
                  <PhaseIcon className={cn("w-3 h-3 flex-shrink-0 mt-0.5",
                    entry.phase === "done" ? "text-green-500" :
                    entry.phase === "error" ? "text-red-500" :
                    isLatest ? "text-primary" : "text-muted-foreground/60"
                  )} />
                  <div className="min-w-0">
                    <span className={isLatest ? "font-medium" : ""}>{entry.message}</span>
                    {entry.detail && (
                      <>
                        {entry.detail.startsWith("http") ? (
                          <a
                            href={entry.detail}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ml-1 text-blue-400 hover:underline"
                          >
                            {entry.detail.includes("youtube.com") ? "[YouTube]" : "[link]"}
                          </a>
                        ) : (
                          <span className="ml-1 text-muted-foreground/50">{entry.detail}</span>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )
            })}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* New result card */}
      {newResults.length > 0 && (
        <div className="space-y-2 pt-1">
          <p className="text-[10px] text-green-500 uppercase tracking-wider font-semibold">New clip added</p>
          {newResults.map(r => (
            <div key={r.result_id} className="flex items-start gap-3 rounded-md border border-green-500/20 bg-green-500/5 p-2.5">
              <a
                href={r.clip_url || `https://youtube.com/watch?v=${r.video_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="w-28 h-16 rounded overflow-hidden flex-shrink-0 bg-muted relative group"
              >
                <img
                  src={r.thumbnail_url || `https://img.youtube.com/vi/${r.video_id}/mqdefault.jpg`}
                  alt=""
                  className="w-full h-full object-cover"
                />
                <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                  <Play className="w-5 h-5 text-white" />
                </div>
              </a>
              <div className="min-w-0 flex-1">
                <a
                  href={r.clip_url || `https://youtube.com/watch?v=${r.video_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs font-medium hover:text-primary line-clamp-1"
                >
                  {r.video_title}
                </a>
                <p className="text-[11px] text-muted-foreground">{r.channel_name}</p>
                {r.the_hook && (
                  <p className="text-[10px] text-primary/80 italic mt-0.5 line-clamp-2">&quot;{r.the_hook}&quot;</p>
                )}
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  {r.relevance_score > 0 && (
                    <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0", scoreColor(r.relevance_score))}>
                      {Math.round(r.relevance_score * 100)}% match
                    </Badge>
                  )}
                  {r.matcher_source && (
                    <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border",
                      r.matcher_source.includes("gemma") || r.matcher_source.includes("Gemma") ? "border-purple-500/40 text-purple-400" :
                      r.matcher_source.includes("gpt") || r.matcher_source.includes("GPT") ? "border-yellow-500/40 text-yellow-400" :
                      "border-green-500/40 text-green-400"
                    )}>
                      {r.matcher_source.includes("gemma") || r.matcher_source.includes("Gemma") ? "Gemma4" :
                       r.matcher_source.includes("gpt") || r.matcher_source.includes("GPT") ? "GPT-4o-mini" :
                       r.matcher_source.includes("Ollama") ? r.matcher_source.replace("Ollama/", "") :
                       r.matcher_source}
                    </Badge>
                  )}
                  {r.start_time_seconds != null && r.end_time_seconds != null && (
                    <span className="text-[10px] text-muted-foreground">
                      {formatTime(r.start_time_seconds)} – {formatTime(r.end_time_seconds)}
                    </span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {phase === "done" && !errorMsg && newResults.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No matching clip found for the new visual idea. You can try again for a different suggestion.
        </p>
      )}

      {(phase === "done" || phase === "error") && (
        <Button variant="ghost" size="sm" className="text-xs gap-1.5" onClick={handleReset}>
          <Plus className="w-3 h-3" />
          {newResults.length > 0 ? "Add yet another shot" : "Try again"}
        </Button>
      )}
    </div>
  )
}

function LibrarySuggestions({ segment }: { segment: Segment }) {
  const [suggestions, setSuggestions] = useState<any[]>([])
  const [expanded, setExpanded] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const loadSuggestions = useCallback(async () => {
    if (loaded) { setExpanded(!expanded); return }
    try {
      const params = new URLSearchParams({
        q: segment.key_terms.slice(0, 5).join(" "),
        per_page: "3",
      })
      const resp = await fetch(`/api/v1/library/search?${params.toString()}`)
      if (resp.ok) {
        const data = await resp.json()
        const existingIds = new Set(segment.results.map(r => r.video_id))
        const filtered = (data.results || []).filter(
          (c: any) => !existingIds.has(c.video_id)
        )
        setSuggestions(filtered.slice(0, 3))
      }
    } catch { /* silent */ }
    setLoaded(true)
    setExpanded(true)
  }, [segment, loaded, expanded])

  if (segment.broll_count === 0) return null

  return (
    <div className="mt-3 pt-3 border-t border-border/50">
      <button
        onClick={loadSuggestions}
        className="text-xs text-primary hover:underline flex items-center gap-1"
      >
        <Sparkles className="w-3 h-3" />
        {expanded ? "Hide library suggestions" : "Show clips from your library"}
        {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>
      {expanded && suggestions.length > 0 && (
        <div className="mt-2 space-y-2 pl-4 border-l-2 border-primary/20">
          {suggestions.map((clip: any) => (
            <div key={clip.result_id} className="flex items-start gap-2 text-xs">
              <a
                href={clip.clip_url || clip.video_url}
                target="_blank"
                rel="noopener noreferrer"
                className="w-16 h-9 rounded overflow-hidden flex-shrink-0 bg-muted"
              >
                <img
                  src={clip.thumbnail_url || `https://img.youtube.com/vi/${clip.video_id}/mqdefault.jpg`}
                  alt="" className="w-full h-full object-cover"
                />
              </a>
              <div className="min-w-0">
                <a
                  href={clip.clip_url || clip.video_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium hover:text-primary line-clamp-1"
                >
                  {clip.video_title}
                </a>
                <p className="text-muted-foreground">{clip.channel_name}</p>
              </div>
            </div>
          ))}
        </div>
      )}
      {expanded && loaded && suggestions.length === 0 && (
        <p className="text-xs text-muted-foreground italic mt-1">No additional clips in your library for this segment</p>
      )}
    </div>
  )
}

const COMPANION_URL = "http://localhost:9876"

type DownloadState = "idle" | "downloading" | "done" | "error"

function ResultCard({ result, jobId }: { result: RankedResult; jobId: string }) {
  const [showPreview, setShowPreview] = useState(false)
  const [markedUsed, setMarkedUsed] = useState(result.clip_used)
  const [feedbackSent, setFeedbackSent] = useState(false)
  const initStart = result.start_time_seconds ?? 0
  const initEnd = result.end_time_seconds ?? initStart + 45
  const [clipStart, setClipStart] = useState(Math.min(initStart, initEnd))
  const [clipEnd, setClipEnd] = useState(Math.max(initStart, initEnd) || initStart + 45)
  const [dlState, setDlState] = useState<DownloadState>("idle")
  const [dlInfo, setDlInfo] = useState<string>("")
  const playerRef = useRef<HTMLIFrameElement>(null)

  const sendFeedback = async (rating: number, clipUsed: boolean) => {
    try {
      await fetch(`/api/v1/results/${result.result_id}/feedback?job_id=${jobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, clip_used: clipUsed }),
      })
      if (clipUsed) setMarkedUsed(true)
      setFeedbackSent(true)
    } catch {}
  }

  const clipDuration = Math.max(0, clipEnd - clipStart)
  const embedUrl = `https://www.youtube.com/embed/${result.video_id}?start=${clipStart}&end=${clipEnd}&autoplay=1&rel=0&modestbranding=1`

  const handleClipDownload = useCallback(async () => {
    if (clipStart >= clipEnd) {
      setDlState("error")
      setDlInfo(`Invalid clip range: start (${formatTime(clipStart)}) must be before end (${formatTime(clipEnd)}). Adjust the timestamps above.`)
      return
    }
    setDlState("downloading")
    setDlInfo("Sending clip request to companion app...")

    try {
      const resp = await fetch(`${COMPANION_URL}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_type: "clip",
          payload: {
            video_id: result.video_id,
            start_seconds: clipStart,
            end_seconds: clipEnd,
          },
        }),
        mode: "cors",
      })
      const data = await resp.json()
      if (data.status === "ok") {
        setDlState("done")
        setDlInfo(`Saved to ${data.file_path} (${data.file_size_mb} MB)`)
        setTimeout(() => { setDlState("idle"); setDlInfo("") }, 5000)
      } else {
        setDlState("error")
        setDlInfo(data.message || "Download failed")
        setTimeout(() => { setDlState("idle"); setDlInfo("") }, 8000)
      }
    } catch {
      setDlState("error")
      const ytdlpCmd = `yt-dlp "${result.video_url}" --download-sections "*${formatTime(clipStart)}-${formatTime(clipEnd)}" --force-keyframes-at-cuts -o "clip_${result.video_id}_${clipStart}-${clipEnd}.mp4"`
      try { await navigator.clipboard.writeText(ytdlpCmd) } catch {}
      setDlInfo("Companion not running. yt-dlp command copied to clipboard.")
      setTimeout(() => { setDlState("idle"); setDlInfo("") }, 8000)
    }
  }, [result.video_id, result.video_url, clipStart, clipEnd])

  return (
    <div className="bg-secondary/30 rounded-lg border border-border overflow-hidden">
      {/* Main card row */}
      <div className="flex gap-3 p-3">
        {/* Thumbnail / Preview toggle */}
        <div className="flex-shrink-0 relative group">
          {result.thumbnail_url && (
            <button onClick={() => setShowPreview(!showPreview)} className="relative block">
              <img
                src={result.thumbnail_url}
                alt={result.video_title}
                className="w-44 h-[100px] rounded object-cover transition-opacity group-hover:opacity-80"
              />
              <div className="absolute inset-0 flex items-center justify-center bg-black/30 rounded opacity-0 group-hover:opacity-100 transition-opacity">
                {showPreview ? (
                  <X className="w-8 h-8 text-white" />
                ) : (
                  <Play className="w-8 h-8 text-white fill-white" />
                )}
              </div>
              {result.start_time_seconds != null && (
                <span className="absolute bottom-1 left-1 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded font-mono">
                  {formatTime(result.start_time_seconds)}
                </span>
              )}
            </button>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <a
                href={result.clip_url || result.video_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm font-medium hover:text-primary transition-colors line-clamp-1"
              >
                {result.video_title}
              </a>
              <p className="text-xs text-muted-foreground">{result.channel_name}</p>
            </div>
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge className={cn("text-xs border shrink-0 cursor-help", scoreColor(result.relevance_score))}>
                    {(result.relevance_score * 100).toFixed(0)}% match
                  </Badge>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-52 text-xs">
                  <p className="font-medium">Relevance Score</p>
                  <p className="text-muted-foreground mt-0.5">
                    How closely this clip matches your script segment, scored by AI analysis of the transcript.
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>

          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            {result.start_time_seconds != null && (
              <span className="inline-flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatTime(result.start_time_seconds)}
                {result.end_time_seconds != null && ` – ${formatTime(result.end_time_seconds)}`}
              </span>
            )}
            <span className="inline-flex items-center gap-1">
              <Eye className="w-3 h-3" />
              {formatViews(result.view_count)} views
            </span>
            <Badge variant="outline" className="text-xs">
              {result.source_flag.replace(/_/g, ' ')}
            </Badge>
            {result.matcher_source && (
              <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border",
                result.matcher_source.includes("Gemma") || result.matcher_source.includes("gemma") ? "border-purple-500/40 text-purple-400" :
                result.matcher_source.includes("gpt") || result.matcher_source.includes("GPT") ? "border-yellow-500/40 text-yellow-400" :
                "border-green-500/40 text-green-400"
              )}>
                {result.matcher_source.includes("gemma") || result.matcher_source.includes("Gemma") ? "Gemma4" :
                 result.matcher_source.includes("gpt") || result.matcher_source.includes("GPT") ? "GPT-4o-mini" :
                 result.matcher_source.includes("Ollama") ? result.matcher_source.replace("Ollama/", "") :
                 result.matcher_source}
              </Badge>
            )}
            {result.visual_fit > 0 && (
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border cursor-help", scoreColor(result.visual_fit))}>
                      VF {(result.visual_fit * 100).toFixed(0)}%
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs max-w-48">
                    <p className="font-medium">Visual Fit</p>
                    <p className="text-muted-foreground">How well the footage looks like what the shot needs</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            {result.topical_fit > 0 && (
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border cursor-help", scoreColor(result.topical_fit))}>
                      TF {(result.topical_fit * 100).toFixed(0)}%
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs max-w-48">
                    <p className="font-medium">Topical Fit</p>
                    <p className="text-muted-foreground">How well the content matches the documentary topic</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            {result.audit_status && result.audit_status !== "unaudited" && result.audit_status !== "pass" && (() => {
              const auditMeta = AUDIT_LABELS[result.audit_status]
              const AuditIcon = auditMeta?.icon || AlertTriangle
              return (
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge variant="outline" className={cn("text-[9px] px-1.5 py-0 border cursor-help gap-0.5", auditMeta?.color)}>
                        <AuditIcon className="w-2.5 h-2.5" />
                        {auditMeta?.label}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs max-w-64">
                      <p className="font-medium">Audit: {auditMeta?.label}</p>
                      {result.audit_reason && <p className="text-muted-foreground">{result.audit_reason}</p>}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )
            })()}
          </div>

          {result.match_reasoning && (
            <p className="text-xs text-muted-foreground/80 italic line-clamp-2">{result.match_reasoning}</p>
          )}

          {result.the_hook && !result.match_reasoning && (
            <p className="text-xs text-primary italic line-clamp-1">&quot;{result.the_hook}&quot;</p>
          )}

          {result.ab_matcher_source && result.ab_confidence_score != null && (
            <div className="mt-1 p-2 rounded border border-purple-500/20 bg-purple-500/5">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-medium text-purple-400">A/B comparison: {result.ab_matcher_source.replace("Ollama/", "")}</span>
                <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-purple-500/40 text-purple-400">
                  {Math.round(result.ab_confidence_score * 100)}% confidence
                </Badge>
              </div>
              {result.ab_start_time_seconds != null && result.ab_end_time_seconds != null && (
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Timestamps: {formatTime(result.ab_start_time_seconds)} – {formatTime(result.ab_end_time_seconds)}
                </p>
              )}
              {result.ab_match_reasoning && (
                <p className="text-[10px] text-muted-foreground/70 italic mt-0.5 line-clamp-2">{result.ab_match_reasoning}</p>
              )}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-1.5 pt-1 flex-wrap">
            <Button
              variant={showPreview ? "secondary" : "default"}
              size="sm"
              className="h-7 text-xs gap-1.5 px-2.5"
              onClick={() => setShowPreview(!showPreview)}
            >
              {showPreview ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
              {showPreview ? 'Close Preview' : 'Preview'}
            </Button>

            <Button
              variant="outline"
              size="sm"
              className={cn("h-7 text-xs gap-1.5 px-2.5", dlState === "done" && "border-green-500/40 text-green-400", dlState === "error" && "border-red-500/40 text-red-400")}
              onClick={handleClipDownload}
              disabled={dlState === "downloading"}
            >
              {dlState === "downloading" ? (
                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
              ) : dlState === "done" ? (
                <Check className="w-3 h-3" />
              ) : (
                <Scissors className="w-3 h-3" />
              )}
              {dlState === "downloading" ? "Downloading..." : dlState === "done" ? "Saved!" : dlState === "error" ? "Failed" : "Clip & Download"}
            </Button>

            <a
              href={result.clip_url || result.video_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button variant="ghost" size="sm" className="h-7 text-xs gap-1.5 px-2.5">
                <ExternalLink className="w-3 h-3" />
                YouTube
              </Button>
            </a>

            <div className="flex items-center gap-1 ml-auto">
              {!markedUsed ? (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={() => sendFeedback(5, false)}
                    title="Good clip"
                  >
                    <ThumbsUp className="w-3.5 h-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={() => sendFeedback(1, false)}
                    title="Bad clip"
                  >
                    <ThumbsDown className="w-3.5 h-3.5" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1.5 px-2.5 border-green-500/40 text-green-400 hover:bg-green-500/10"
                    onClick={() => sendFeedback(5, true)}
                  >
                    <Check className="w-3.5 h-3.5" />
                    Mark Used
                  </Button>
                </>
              ) : (
                <Badge className="bg-green-500/20 text-green-400 border-green-500/30 text-xs gap-1">
                  <Check className="w-3 h-3" />
                  Used in project
                </Badge>
              )}
            </div>
          </div>

          {!showPreview && dlInfo && (
            <p className={cn("text-xs mt-1", dlState === "done" ? "text-green-400" : dlState === "error" ? "text-red-400" : "text-muted-foreground")}>
              {dlInfo}
            </p>
          )}
        </div>
      </div>

      {/* Expandable preview + clip controls */}
      {showPreview && (
        <div className="border-t border-border bg-black/20 p-3 space-y-3">
          {/* Embedded YouTube player */}
          <div className="aspect-video w-full max-w-2xl mx-auto rounded-lg overflow-hidden bg-black">
            <iframe
              ref={playerRef}
              src={embedUrl}
              className="w-full h-full"
              allow="autoplay; encrypted-media"
              allowFullScreen
              title={`Preview: ${result.video_title}`}
            />
          </div>

          {/* Clip range controls */}
          <div className="max-w-2xl mx-auto space-y-2">
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground w-20">Clip range:</span>
              <div className="flex items-center gap-2 flex-1">
                <div className="flex items-center gap-1">
                  <label className="text-[10px] text-muted-foreground uppercase">Start</label>
                  <Input
                    type="number"
                    min={0}
                    max={result.video_duration_seconds}
                    value={clipStart}
                    onChange={(e) => setClipStart(Math.min(clipEnd - 1, Math.max(0, parseInt(e.target.value) || 0)))}
                    className="w-20 h-7 text-xs font-mono"
                  />
                  <span className="text-[10px] text-muted-foreground">{formatTime(clipStart)}</span>
                </div>
                <span className="text-muted-foreground">–</span>
                <div className="flex items-center gap-1">
                  <label className="text-[10px] text-muted-foreground uppercase">End</label>
                  <Input
                    type="number"
                    min={clipStart}
                    max={result.video_duration_seconds}
                    value={clipEnd}
                    onChange={(e) => setClipEnd(Math.max(clipStart, parseInt(e.target.value) || 0))}
                    className="w-20 h-7 text-xs font-mono"
                  />
                  <span className="text-[10px] text-muted-foreground">{formatTime(clipEnd)}</span>
                </div>
                <Badge variant="secondary" className="text-[10px] ml-1">
                  {clipDuration}s clip
                </Badge>
              </div>
            </div>

            {/* Quick adjust buttons */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-20">Quick adjust:</span>
              <div className="flex gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] px-2"
                  onClick={() => { setClipStart(Math.max(0, clipStart - 10)) }}
                >
                  <SkipBack className="w-3 h-3 mr-0.5" /> -10s
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] px-2"
                  onClick={() => { setClipStart(Math.min(clipEnd - 1, clipStart + 10)) }}
                >
                  +10s <SkipForward className="w-3 h-3 ml-0.5" />
                </Button>
                <span className="text-muted-foreground/40 mx-1">|</span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] px-2"
                  onClick={() => { setClipEnd(Math.max(clipStart, clipEnd - 10)) }}
                >
                  <SkipBack className="w-3 h-3 mr-0.5" /> -10s end
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] px-2"
                  onClick={() => { setClipEnd(clipEnd + 10) }}
                >
                  +10s end <SkipForward className="w-3 h-3 ml-0.5" />
                </Button>
                <span className="text-muted-foreground/40 mx-1">|</span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] px-2"
                  onClick={() => {
                    setClipStart(result.start_time_seconds ?? 0)
                    setClipEnd(result.end_time_seconds ?? (result.start_time_seconds ?? 0) + 45)
                  }}
                >
                  Reset
                </Button>
              </div>
            </div>

            {/* Download row */}
            <div className="space-y-2 pt-1 border-t border-border/50">
              <div className="flex items-center gap-2 flex-wrap">
                <Button
                  size="sm"
                  className="h-8 text-xs gap-2 bg-primary"
                  onClick={handleClipDownload}
                  disabled={dlState === "downloading"}
                >
                  {dlState === "downloading" ? (
                    <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  ) : (
                    <Download className="w-3.5 h-3.5" />
                  )}
                  {dlState === "downloading"
                    ? "Downloading..."
                    : `Clip & Download (${formatTime(clipStart)} – ${formatTime(clipEnd)})`}
                </Button>
                {!markedUsed && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 text-xs gap-2 border-green-500/40 text-green-400 hover:bg-green-500/10"
                    onClick={() => sendFeedback(5, true)}
                  >
                    <Check className="w-3.5 h-3.5" />
                    Mark as Used & Save to Library
                  </Button>
                )}
                {markedUsed && (
                  <Badge className="bg-green-500/20 text-green-400 border-green-500/30 text-xs gap-1 h-8 px-3">
                    <Check className="w-3.5 h-3.5" />
                    Saved to library
                  </Badge>
                )}
              </div>
              {dlInfo && (
                <p className={cn(
                  "text-xs px-2 py-1.5 rounded",
                  dlState === "done" && "bg-green-500/10 text-green-400",
                  dlState === "error" && "bg-amber-500/10 text-amber-400",
                  dlState === "downloading" && "bg-blue-500/10 text-blue-400",
                )}>
                  {dlState === "done" && <Check className="w-3 h-3 inline mr-1" />}
                  {dlInfo}
                </p>
              )}
            </div>
          </div>

          {/* Transcript excerpt for context */}
          {result.transcript_excerpt && (
            <div className="max-w-2xl mx-auto">
              <p className="text-[10px] text-muted-foreground uppercase mb-1">Transcript at this moment</p>
              <p className="text-xs text-muted-foreground bg-black/30 rounded p-2 font-mono leading-relaxed">
                {result.transcript_excerpt}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── Activity Log Panel (for completed jobs) ── */

const logIconMap: Record<string, LucideIcon> = {
  brain: Brain, search: Search, globe: Globe, sparkles: Sparkles,
  filter: Filter, check: Check, alert: AlertTriangle, zap: Zap,
  eye: Eye, mic: Mic, clock: Clock, shield: Shield, terminal: Terminal,
}

const logIconColor: Record<string, string> = {
  brain: "text-violet-400", search: "text-blue-400", globe: "text-cyan-400",
  sparkles: "text-amber-400", filter: "text-orange-400", check: "text-emerald-400",
  alert: "text-red-400", zap: "text-yellow-400", eye: "text-pink-400",
  mic: "text-teal-400", clock: "text-slate-400", shield: "text-indigo-400",
  terminal: "text-lime-400",
}

const LOG_GROUP_LABELS: Record<string, { label: string; icon: LucideIcon; color: string }> = {
  translate: { label: "Translation & Segmentation", icon: Languages, color: "text-violet-400" },
  search: { label: "Video Discovery", icon: Search, color: "text-blue-400" },
  transcript: { label: "Transcript & Whisper", icon: Mic, color: "text-teal-400" },
  match: { label: "Timestamp Analysis", icon: Eye, color: "text-pink-400" },
  rank: { label: "Ranking & Deduplication", icon: Filter, color: "text-orange-400" },
  done: { label: "Complete", icon: Check, color: "text-emerald-400" },
}

interface LogGroup {
  key: string
  label: string
  icon: LucideIcon
  color: string
  entries: ActivityEntry[]
}

function groupLogEntries(log: ActivityEntry[]): LogGroup[] {
  const groups: LogGroup[] = []
  let currentKey = ""
  for (const entry of log) {
    const gk = entry.group || "general"
    const base = gk.startsWith("match-") ? "match" : gk
    if (base !== currentKey) {
      currentKey = base
      const meta = LOG_GROUP_LABELS[base] || { label: base, icon: Zap, color: "text-muted-foreground" }
      groups.push({ key: `${base}-${groups.length}`, label: meta.label, icon: meta.icon, color: meta.color, entries: [] })
    }
    groups[groups.length - 1].entries.push(entry)
  }
  return groups
}

function formatLogTime(raw: string): string {
  try {
    const d = new Date(raw)
    if (isNaN(d.getTime())) return raw
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true })
  } catch {
    return raw
  }
}

function ActivityLogPanel({ entries }: { entries: ActivityEntry[] }) {
  const groups = useMemo(() => groupLogEntries(entries), [entries])

  if (entries.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Zap className="w-8 h-8 text-muted-foreground/30 mx-auto mb-3" />
          <p className="text-sm text-muted-foreground">
            No activity log available for this job.
          </p>
          <p className="text-xs text-muted-foreground/60 mt-1">
            Activity logs are stored for new jobs going forward.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="max-h-[600px] overflow-y-auto px-4 py-3">
          <div className="space-y-1">
            {groups.map((group) => (
              <LogGroupSection key={group.key} group={group} />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function LogGroupSection({ group }: { group: LogGroup }) {
  const [expanded, setExpanded] = useState(true)
  const Icon = group.icon
  const depth0 = group.entries.filter(e => !e.depth || e.depth === 0)
  const childEntries = group.entries.filter(e => (e.depth || 0) >= 1)

  return (
    <div className="relative">
      <button
        className="flex items-center gap-2 w-full text-left py-1.5 px-1 rounded hover:bg-secondary/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <ChevronDown className="w-3 h-3 text-muted-foreground flex-shrink-0" />
          : <ChevronRight className="w-3 h-3 text-muted-foreground flex-shrink-0" />}
        <Icon className={cn("w-3.5 h-3.5 flex-shrink-0", group.color)} />
        <span className={cn("text-xs font-semibold", group.color)}>{group.label}</span>
        <span className="text-[10px] text-muted-foreground/50 ml-auto">
          {group.entries.length} event{group.entries.length !== 1 ? "s" : ""}
        </span>
      </button>
      {expanded && (
        <div className="ml-3 pl-3 border-l border-border/40 space-y-0">
          {depth0.map((entry, i) => (
            <LogEntryLine key={`d0-${i}`} entry={entry} />
          ))}
          {childEntries.length > 0 && (
            <LogChildEntries entries={childEntries} />
          )}
        </div>
      )}
    </div>
  )
}

function LogChildEntries({ entries }: { entries: ActivityEntry[] }) {
  const segmentGroups = useMemo(() => {
    const groups: { label: string; items: ActivityEntry[] }[] = []
    for (const e of entries) {
      const d = e.depth || 1
      if (d === 1) {
        groups.push({ label: e.text, items: [e] })
      } else {
        if (groups.length === 0) groups.push({ label: "", items: [] })
        groups[groups.length - 1].items.push(e)
      }
    }
    return groups
  }, [entries])

  return (
    <div className="space-y-0">
      {segmentGroups.map((sg, gi) => (
        <LogSegmentGroup key={gi} group={sg} />
      ))}
    </div>
  )
}

function LogSegmentGroup({ group }: { group: { label: string; items: ActivityEntry[] } }) {
  const [expanded, setExpanded] = useState(false)
  const header = group.items[0]
  const children = group.items.slice(1)

  if (group.items.length === 1) {
    return <LogEntryLine entry={header} />
  }

  return (
    <div>
      <button
        className="flex items-center gap-1.5 w-full text-left py-0.5 pl-1 rounded hover:bg-secondary/30 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <ChevronDown className="w-2.5 h-2.5 text-muted-foreground/50 flex-shrink-0" />
          : <ChevronRight className="w-2.5 h-2.5 text-muted-foreground/50 flex-shrink-0" />}
        <LogEntryInner entry={header} />
        {!expanded && children.length > 0 && (
          <span className="text-[9px] text-muted-foreground/40 ml-auto flex-shrink-0">
            +{children.length}
          </span>
        )}
      </button>
      {expanded && children.length > 0 && (
        <div className="ml-4 pl-2.5 border-l border-border/30 space-y-0">
          {children.map((e, i) => (
            <LogEntryLine key={i} entry={e} />
          ))}
        </div>
      )}
    </div>
  )
}

function LogEntryLine({ entry }: { entry: ActivityEntry }) {
  return (
    <div className={cn(
      "flex items-start gap-2 py-1 opacity-80",
      (entry.depth || 0) >= 2 && "ml-1",
      (entry.depth || 0) >= 3 && "ml-2",
    )}>
      <LogEntryInner entry={entry} />
    </div>
  )
}

function LogEntryInner({ entry }: { entry: ActivityEntry }) {
  const IconComp = logIconMap[entry.icon] || Zap
  const color = logIconColor[entry.icon] || "text-muted-foreground"
  return (
    <>
      <span className="text-[10px] text-muted-foreground/50 tabular-nums pt-0.5 w-[70px] shrink-0" title={entry.time}>
        {formatLogTime(entry.time)}
      </span>
      <IconComp className={cn("w-3 h-3 shrink-0 mt-0.5", color)} />
      <span className={cn(
        "text-[11px] leading-relaxed text-muted-foreground",
        entry.icon === "terminal" && "font-mono text-[10px] text-lime-400/80",
      )}>
        {entry.text}
      </span>
    </>
  )
}
