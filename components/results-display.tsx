"use client"

import { useState } from "react"
import Image from "next/image"
import { Download, ChevronDown, ChevronUp, ExternalLink, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"
import type { Job, VisualCue, ClipSelection } from "@/lib/types"

interface ResultsDisplayProps {
  job: Job
  onExport: () => void
  onNewSearch: () => void
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

export function ResultsDisplay({ job, onExport, onNewSearch }: ResultsDisplayProps) {
  const { visual_cues, selections, summary } = job

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4">
            <p className="text-muted-foreground text-sm">Visual Cues</p>
            <p className="text-2xl font-bold">{summary?.total_cues || visual_cues.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-muted-foreground text-sm">Clips Found</p>
            <p className="text-2xl font-bold">{summary?.filled_cues || 0}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-muted-foreground text-sm">Fill Rate</p>
            <p className="text-2xl font-bold text-accent">
              {Math.round((summary?.fill_rate || 0) * 100)}%
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-muted-foreground text-sm">Total Cost</p>
            <p className="text-2xl font-bold">${(job.total_cost || 0).toFixed(3)}</p>
          </CardContent>
        </Card>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Visual Cues & Matches</h2>
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

      {/* Cue Cards */}
      <div className="space-y-4">
        {visual_cues.map((cue) => (
          <CueCard
            key={cue.id}
            cue={cue}
            selection={selections[cue.id]}
          />
        ))}
      </div>
    </div>
  )
}

interface CueCardProps {
  cue: VisualCue
  selection?: ClipSelection
}

function CueCard({ cue, selection }: CueCardProps) {
  const [showAlternatives, setShowAlternatives] = useState(false)
  const hasClip = selection?.selected_clip
  
  const priorityColors = {
    high: "bg-red-500",
    medium: "bg-yellow-500",
    low: "bg-green-500",
  }

  return (
    <Card>
      <CardContent className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 pb-4 border-b border-border">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-medium text-muted-foreground">
                {formatTime(cue.timestamp_start)} - {formatTime(cue.timestamp_end)}
              </span>
              <span className={cn("w-2 h-2 rounded-full", priorityColors[cue.priority])} />
              <span className="text-xs text-muted-foreground capitalize">{cue.priority} priority</span>
            </div>
            <p className="text-sm text-muted-foreground italic mb-2">
              &ldquo;{cue.script_excerpt}&rdquo;
            </p>
            <p className="font-medium">{cue.visual_description}</p>
          </div>
          <Badge variant={hasClip ? "default" : "destructive"}>
            {hasClip ? "Matched" : "No match"}
          </Badge>
        </div>

        {/* Search Queries */}
        <div className="flex flex-wrap gap-2 py-3">
          {cue.search_queries.map((query, idx) => (
            <Badge key={idx} variant="secondary" className="text-xs">
              {query}
            </Badge>
          ))}
        </div>

        {/* Selected Clip */}
        {hasClip && selection && (
          <div className="pt-3 border-t border-border">
            <div className="flex gap-4">
              {selection.selected_clip?.preview_url && (
                <div className="w-40 h-24 rounded-lg overflow-hidden bg-secondary flex-shrink-0 relative">
                  <Image
                    src={selection.selected_clip.preview_url}
                    alt="Preview"
                    fill
                    className="object-cover"
                  />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <Badge variant="outline" className="text-xs">
                    {selection.selected_clip?.source}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {selection.selected_clip?.quality} &bull; {selection.selected_clip?.duration}s
                  </span>
                </div>
                <p className="text-sm text-muted-foreground mb-2">
                  {selection.selection_reason}
                </p>
                <div className="flex items-center gap-3">
                  <a
                    href={selection.selected_clip?.download_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-primary hover:underline font-medium flex items-center gap-1"
                  >
                    <Download className="w-4 h-4" />
                    Download
                  </a>
                  {selection.alternatives && selection.alternatives.length > 0 && (
                    <Collapsible open={showAlternatives} onOpenChange={setShowAlternatives}>
                      <CollapsibleTrigger asChild>
                        <Button variant="ghost" size="sm" className="text-muted-foreground">
                          {showAlternatives ? (
                            <ChevronUp className="w-4 h-4 mr-1" />
                          ) : (
                            <ChevronDown className="w-4 h-4 mr-1" />
                          )}
                          {selection.alternatives.length} alternatives
                        </Button>
                      </CollapsibleTrigger>
                      <CollapsibleContent className="mt-4">
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                          {selection.alternatives.map((alt) => (
                            <a
                              key={alt.id}
                              href={alt.download_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="block p-2 bg-secondary rounded-lg hover:bg-border transition-colors"
                            >
                              {alt.preview_url ? (
                                <div className="relative w-full h-20 rounded mb-2">
                                  <Image
                                    src={alt.preview_url}
                                    alt="Alternative"
                                    fill
                                    className="object-cover rounded"
                                  />
                                </div>
                              ) : (
                                <div className="w-full h-20 bg-card rounded mb-2 flex items-center justify-center">
                                  <ExternalLink className="w-8 h-8 text-muted-foreground" />
                                </div>
                              )}
                              <p className="text-xs text-muted-foreground">
                                {alt.quality} &bull; {alt.duration}s
                              </p>
                            </a>
                          ))}
                        </div>
                      </CollapsibleContent>
                    </Collapsible>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
