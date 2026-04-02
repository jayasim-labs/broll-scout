"use client"

import { useEffect, useRef } from "react"
import {
  Brain,
  Search,
  Globe,
  Sparkles,
  Filter,
  Check,
  AlertTriangle,
  Zap,
  Eye,
  Mic,
  Clock,
  Shield,
  FileText,
  ClipboardList,
  Terminal,
  type LucideIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { cn } from "@/lib/utils"
import type { JobProgress, ActivityEntry } from "@/lib/types"

interface ProgressTrackerProps {
  progress: JobProgress
  onCancel: () => void
}

const iconMap: Record<string, LucideIcon> = {
  brain: Brain,
  search: Search,
  globe: Globe,
  sparkles: Sparkles,
  filter: Filter,
  check: Check,
  alert: AlertTriangle,
  zap: Zap,
  eye: Eye,
  mic: Mic,
  clock: Clock,
  shield: Shield,
  terminal: Terminal,
}

const iconColor: Record<string, string> = {
  brain: "text-violet-400",
  search: "text-blue-400",
  globe: "text-cyan-400",
  sparkles: "text-amber-400",
  filter: "text-orange-400",
  check: "text-emerald-400",
  alert: "text-red-400",
  zap: "text-yellow-400",
  eye: "text-pink-400",
  mic: "text-teal-400",
  clock: "text-slate-400",
  shield: "text-indigo-400",
  terminal: "text-lime-400",
}

const stages = [
  { key: "translating", label: "Translate", icon: FileText },
  { key: "searching", label: "Search", icon: Search },
  { key: "matching", label: "Match", icon: ClipboardList },
  { key: "ranking", label: "Rank", icon: Sparkles },
] as const

export function ProgressTracker({ progress, onCancel }: ProgressTrackerProps) {
  const currentStageIndex = stages.findIndex((s) => s.key === progress.stage)
  const logEndRef = useRef<HTMLDivElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" })
    }
  }, [progress.activity_log?.length])

  const activityLog = progress.activity_log || []

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="text-lg">Scouting B-Roll...</CardTitle>
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            className="text-destructive hover:text-destructive"
          >
            Cancel
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Progress bar */}
          <div className="space-y-1.5">
            <Progress value={progress.percent_complete} className="h-2" />
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{progress.message}</span>
              <span className="font-medium tabular-nums text-muted-foreground">
                {progress.percent_complete}%
              </span>
            </div>
          </div>

          {/* Stage pills */}
          <div className="flex gap-2">
            {stages.map((stage, index) => {
              const Icon = stage.icon
              const isCompleted = currentStageIndex > index
              const isCurrent = index === currentStageIndex
              return (
                <div
                  key={stage.key}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all",
                    isCompleted && "bg-emerald-500/20 text-emerald-400",
                    isCurrent && "bg-primary/20 text-primary animate-pulse",
                    !isCompleted &&
                      !isCurrent &&
                      "bg-secondary text-muted-foreground"
                  )}
                >
                  {isCompleted ? (
                    <Check className="w-3 h-3" />
                  ) : (
                    <Icon className="w-3 h-3" />
                  )}
                  {stage.label}
                </div>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {/* Activity feed */}
      {activityLog.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Zap className="w-3.5 h-3.5" />
              Live Activity
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div
              ref={logContainerRef}
              className="max-h-[400px] overflow-y-auto px-4 pb-4"
            >
              <div className="space-y-0.5">
                {activityLog.map((entry: ActivityEntry, i: number) => {
                  const IconComp = iconMap[entry.icon] || Zap
                  const color = iconColor[entry.icon] || "text-muted-foreground"
                  const isLatest = i === activityLog.length - 1
                  return (
                    <div
                      key={i}
                      className={cn(
                        "flex items-start gap-2.5 py-1.5 transition-opacity duration-300",
                        isLatest ? "opacity-100" : "opacity-70"
                      )}
                    >
                      <span className="text-[10px] text-muted-foreground/60 tabular-nums pt-0.5 w-12 shrink-0">
                        {entry.time}
                      </span>
                      <IconComp className={cn("w-3.5 h-3.5 shrink-0 mt-0.5", color)} />
                      <span
                        className={cn(
                          "text-xs leading-relaxed",
                          isLatest
                            ? "text-foreground"
                            : "text-muted-foreground",
                          entry.icon === "terminal" && "font-mono text-[11px] text-lime-400/80"
                        )}
                      >
                        {entry.text}
                      </span>
                    </div>
                  )
                })}
              </div>
              <div ref={logEndRef} />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
