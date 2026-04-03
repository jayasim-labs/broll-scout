"use client"

import { useState, useRef, useCallback, useMemo } from "react"
import {
  Download, ChevronDown, ChevronUp, ExternalLink, RefreshCw,
  ThumbsUp, ThumbsDown, Clock, Eye, Play, Scissors,
  Check, Pause, SkipBack, SkipForward, X,
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
import type { JobResponse, Segment, RankedResult, ActivityEntry } from "@/lib/types"
import { categoryLabel } from "@/lib/types"

interface ResultsDisplayProps {
  job: JobResponse
  onExport: () => void
  onNewSearch: () => void
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

const SEGMENTS_PER_PAGE = 15

export function ResultsDisplay({ job, onExport, onNewSearch }: ResultsDisplayProps) {
  const activityLog = job.activity_log || []
  const segmentsWithClips = useMemo(
    () => job.segments.filter(s => s.results.length > 0),
    [job.segments],
  )
  const totalPages = Math.max(1, Math.ceil(segmentsWithClips.length / SEGMENTS_PER_PAGE))
  const [currentPage, setCurrentPage] = useState(1)
  const pageSegments = useMemo(
    () => segmentsWithClips.slice((currentPage - 1) * SEGMENTS_PER_PAGE, currentPage * SEGMENTS_PER_PAGE),
    [segmentsWithClips, currentPage],
  )
  const emptySegmentCount = job.segments.length - segmentsWithClips.length

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

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="Segments" value={job.total_segments} />
        <StatCard label="Clips Found" value={job.total_results} />
        <StatCard
          label="Min Met"
          value={job.minimum_results_met ? "Yes" : "No"}
          accent={job.minimum_results_met}
        />
        <StatCard label="Duration" value={`${job.script_duration_minutes} min`} />
        <StatCard label="API Cost" value={`$${job.api_costs.estimated_cost_usd.toFixed(2)}`} />
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
            <Button variant="outline" size="sm" onClick={onExport} className="gap-2">
              <Download className="w-4 h-4" />
              Export JSON
            </Button>
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
              />
            ))}
          </div>

          {emptySegmentCount > 0 && (
            <p className="text-xs text-muted-foreground mt-3">
              {emptySegmentCount} segment{emptySegmentCount !== 1 ? "s" : ""} with no clips hidden
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

function SegmentCard({ segment, index, jobId }: { segment: Segment; index: number; jobId: string }) {
  const [isOpen, setIsOpen] = useState(index < 3)

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <Card>
        <CollapsibleTrigger asChild>
          <CardHeader className="cursor-pointer hover:bg-secondary/30 transition-colors py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono text-muted-foreground w-12">
                  {segment.segment_id.replace("seg_", "#")}
                </span>
                <div>
                  <h3 className="font-medium text-sm">{segment.title}</h3>
                  <p className="text-xs text-muted-foreground">{segment.emotional_tone}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  {segment.results.length} clip{segment.results.length !== 1 ? 's' : ''}
                </Badge>
                {isOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </div>
            </div>
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="pt-0 space-y-4">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{segment.summary}</p>
              <p className="text-sm"><span className="font-medium">Visual need:</span> {segment.visual_need}</p>
            </div>
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
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
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
      } else {
        setDlState("error")
        setDlInfo(data.message || "Download failed")
      }
    } catch {
      setDlState("error")
      const ytdlpCmd = `yt-dlp "${result.video_url}" --download-sections "*${formatTime(clipStart)}-${formatTime(clipEnd)}" --force-keyframes-at-cuts -o "clip_${result.video_id}_${clipStart}-${clipEnd}.mp4"`
      try { await navigator.clipboard.writeText(ytdlpCmd) } catch {}
      setDlInfo("Companion not running. yt-dlp command copied to clipboard.")
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
          </div>

          {result.the_hook && (
            <p className="text-xs text-primary italic line-clamp-1">&quot;{result.the_hook}&quot;</p>
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
              className="h-7 text-xs gap-1.5 px-2.5"
              onClick={handleClipDownload}
            >
              <Scissors className="w-3 h-3" />
              Clip & Download
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
