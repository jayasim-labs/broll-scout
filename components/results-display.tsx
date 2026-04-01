"use client"

import { useState } from "react"
import {
  Download, ChevronDown, ChevronUp, ExternalLink, RefreshCw,
  ThumbsUp, ThumbsDown, Clock, Eye, Star,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"
import type { JobResponse, Segment, RankedResult } from "@/lib/types"

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

export function ResultsDisplay({ job, onExport, onNewSearch }: ResultsDisplayProps) {
  return (
    <div className="space-y-6">
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

      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">
          Segments & B-Roll Clips
        </h2>
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

      <div className="space-y-3">
        {job.segments.map((segment, idx) => (
          <SegmentCard key={segment.segment_id} segment={segment} index={idx} jobId={job.job_id} />
        ))}
      </div>

      <Card>
        <CardContent className="pt-4">
          <p className="text-sm text-muted-foreground mb-2">API Usage</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            <span>GPT-4o: {job.api_costs.openai_calls} calls</span>
            <span>GPT-4o-mini: {job.api_costs.openai_mini_calls} calls</span>
            <span>YouTube: {job.api_costs.youtube_api_units} units</span>
            <span>Gemini: {job.api_costs.gemini_calls} calls</span>
          </div>
          {job.processing_time_seconds && (
            <p className="text-xs text-muted-foreground mt-2">
              Processed in {job.processing_time_seconds.toFixed(1)}s
            </p>
          )}
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

function ResultCard({ result, jobId }: { result: RankedResult; jobId: string }) {
  const [feedbackSent, setFeedbackSent] = useState(false)

  const sendFeedback = async (rating: number, clipUsed: boolean) => {
    try {
      await fetch(`/api/v1/feedback/${result.result_id}?job_id=${jobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, clip_used: clipUsed }),
      })
      setFeedbackSent(true)
    } catch {}
  }

  return (
    <div className="flex gap-3 p-3 bg-secondary/30 rounded-lg border border-border">
      {result.thumbnail_url && (
        <a
          href={result.clip_url || result.video_url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex-shrink-0"
        >
          <img
            src={result.thumbnail_url}
            alt={result.video_title}
            className="w-40 h-24 rounded object-cover hover:opacity-80 transition-opacity"
          />
        </a>
      )}
      <div className="flex-1 min-w-0 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div>
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
          <Badge className={cn("text-xs border", scoreColor(result.relevance_score))}>
            {(result.relevance_score * 100).toFixed(0)}%
          </Badge>
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
          <p className="text-xs text-primary italic">&quot;{result.the_hook}&quot;</p>
        )}

        {result.transcript_excerpt && (
          <p className="text-xs text-muted-foreground line-clamp-2">
            {result.transcript_excerpt}
          </p>
        )}

        <div className="flex items-center gap-2 pt-1">
          <a
            href={result.clip_url || result.video_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            <ExternalLink className="w-3 h-3" />
            Open clip
          </a>
          {!feedbackSent ? (
            <div className="flex items-center gap-1 ml-auto">
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => sendFeedback(5, true)}>
                <ThumbsUp className="w-3 h-3" />
              </Button>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => sendFeedback(1, false)}>
                <ThumbsDown className="w-3 h-3" />
              </Button>
              <Button variant="ghost" size="sm" className="h-6 text-xs px-2" onClick={() => sendFeedback(5, true)}>
                <Star className="w-3 h-3 mr-1" />
                Used
              </Button>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground ml-auto">Feedback sent</span>
          )}
        </div>
      </div>
    </div>
  )
}
