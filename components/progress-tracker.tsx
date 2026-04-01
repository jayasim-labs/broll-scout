"use client"

import { FileText, Search, ClipboardList, Sparkles, Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"
import type { JobProgress } from "@/lib/types"

interface ProgressTrackerProps {
  progress: JobProgress
  onCancel: () => void
}

const stages = [
  { key: 'translating', label: 'Translating', icon: FileText, desc: 'GPT-4o segments' },
  { key: 'searching', label: 'Searching', icon: Search, desc: 'YouTube + Gemini' },
  { key: 'matching', label: 'Matching', icon: ClipboardList, desc: 'GPT-4o-mini timestamps' },
  { key: 'ranking', label: 'Ranking', icon: Sparkles, desc: 'Scoring & filtering' },
] as const

export function ProgressTracker({ progress, onCancel }: ProgressTrackerProps) {
  const currentStageIndex = stages.findIndex(s => s.key === progress.stage)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Scouting B-Roll...</CardTitle>
        <Button variant="ghost" size="sm" onClick={onCancel} className="text-destructive hover:text-destructive">
          Cancel
        </Button>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Progress value={progress.percent_complete} className="h-3" />
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{progress.message}</span>
            <span className="font-medium tabular-nums">{progress.percent_complete}%</span>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-4">
          {stages.map((stage, index) => {
            const Icon = stage.icon
            const isCompleted = currentStageIndex > index
            const isCurrent = index === currentStageIndex

            return (
              <div key={stage.key} className="flex flex-col items-center text-center gap-2">
                <div
                  className={cn(
                    "w-12 h-12 rounded-full flex items-center justify-center border-2 transition-all",
                    isCompleted && "bg-primary border-primary",
                    isCurrent && "bg-primary/20 border-primary animate-pulse",
                    !isCompleted && !isCurrent && "bg-secondary border-border"
                  )}
                >
                  {isCompleted ? (
                    <Check className="w-5 h-5 text-primary-foreground" />
                  ) : (
                    <Icon className={cn(
                      "w-5 h-5",
                      isCurrent ? "text-primary" : "text-muted-foreground"
                    )} />
                  )}
                </div>
                <div>
                  <p className={cn(
                    "text-sm font-medium",
                    (isCompleted || isCurrent) ? "text-foreground" : "text-muted-foreground"
                  )}>
                    {stage.label}
                  </p>
                  <p className="text-xs text-muted-foreground">{stage.desc}</p>
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
