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
  { key: 'translating', label: 'Translate', icon: FileText },
  { key: 'searching', label: 'Search', icon: Search },
  { key: 'matching', label: 'Match', icon: ClipboardList },
  { key: 'ranking', label: 'Rank', icon: Sparkles },
] as const

export function ProgressTracker({ progress, onCancel }: ProgressTrackerProps) {
  const currentStageIndex = stages.findIndex(s => s.key === progress.stage)
  
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Processing</CardTitle>
        <Button variant="ghost" size="sm" onClick={onCancel} className="text-destructive hover:text-destructive">
          Cancel
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Progress value={progress.percent_complete} className="h-2" />
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{progress.message}</span>
            <span className="font-medium">{progress.percent_complete}%</span>
          </div>
        </div>

        <div className="flex items-center justify-between pt-4">
          {stages.map((stage, index) => {
            const Icon = stage.icon
            const isCompleted = index < currentStageIndex
            const isCurrent = index === currentStageIndex
            
            return (
              <div key={stage.key} className="flex flex-col items-center">
                <div
                  className={cn(
                    "w-10 h-10 rounded-full flex items-center justify-center border-2 transition-colors",
                    isCompleted && "bg-accent border-accent",
                    isCurrent && "bg-primary border-primary",
                    !isCompleted && !isCurrent && "bg-secondary border-border"
                  )}
                >
                  {isCompleted ? (
                    <Check className="w-5 h-5 text-accent-foreground" />
                  ) : (
                    <Icon className={cn(
                      "w-5 h-5",
                      isCurrent ? "text-primary-foreground" : "text-muted-foreground"
                    )} />
                  )}
                </div>
                <span className="text-xs text-muted-foreground mt-2">{stage.label}</span>
                {index < stages.length - 1 && (
                  <div className="hidden" /> 
                )}
              </div>
            )
          })}
        </div>

        <div className="flex items-center justify-between pt-4">
          <div className="flex-1 h-0.5 bg-secondary" />
        </div>
      </CardContent>
    </Card>
  )
}
