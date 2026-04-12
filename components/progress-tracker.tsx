"use client"

import { useEffect, useRef, useState, useMemo } from "react"
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
  ChevronDown,
  ChevronRight,
  Languages,
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

function formatActivityTime(raw: string): string {
  try {
    const d = new Date(raw)
    if (isNaN(d.getTime())) return raw
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true })
  } catch {
    return raw
  }
}

const stages = [
  { key: "translating", label: "Translate", icon: FileText, desc: "Tamil → English + Scene Segments" },
  { key: "searching", label: "Search", icon: Search, desc: "YouTube + yt-dlp Video Discovery" },
  { key: "matching", label: "Match", icon: ClipboardList, desc: "AI Timestamp Detection" },
  { key: "ranking", label: "Rank", icon: Sparkles, desc: "Score, Filter & Deduplicate" },
] as const

const GROUP_LABELS: Record<string, { label: string; icon: LucideIcon; color: string }> = {
  translate: { label: "Translation & Segmentation", icon: Languages, color: "text-violet-400" },
  search: { label: "Video Discovery", icon: Search, color: "text-blue-400" },
  transcript: { label: "Transcript & Whisper", icon: Mic, color: "text-teal-400" },
  match: { label: "Timestamp Analysis", icon: Eye, color: "text-pink-400" },
  rank: { label: "Ranking & Deduplication", icon: Filter, color: "text-orange-400" },
  done: { label: "Complete", icon: Check, color: "text-emerald-400" },
}

interface ActivityGroup {
  key: string
  label: string
  icon: LucideIcon
  color: string
  entries: ActivityEntry[]
}

function groupActivityLog(log: ActivityEntry[]): ActivityGroup[] {
  const groups: ActivityGroup[] = []
  let currentKey = ""

  for (const entry of log) {
    const gk = entry.group || "general"
    const base = gk.startsWith("match-") ? "match" : gk

    if (base !== currentKey) {
      currentKey = base
      const meta = GROUP_LABELS[base] || { label: base, icon: Zap, color: "text-muted-foreground" }
      groups.push({ key: `${base}-${groups.length}`, label: meta.label, icon: meta.icon, color: meta.color, entries: [] })
    }
    groups[groups.length - 1].entries.push(entry)
  }
  return groups
}

function PipelineFlowDiagram({ currentStage }: { currentStage: string }) {
  const steps = [
    {
      id: "translate",
      stage: "translating",
      icon: Languages,
      title: "Translate & Anchor",
      detail: "GPT-4o translates your script, extracts the documentary's topic/geography/domain, and breaks it into scenes — each with context anchors and negative keywords to prevent wrong matches.",
    },
    {
      id: "search",
      stage: "searching",
      icon: Search,
      title: "Context-Aware Search",
      detail: "For each scene, searches preferred channels then yt-dlp. Generic queries are auto-anchored to the script's topic so 'tropical forest' becomes 'Sentinel Island tropical forest'.",
    },
    {
      id: "match",
      stage: "matching",
      icon: Eye,
      title: "Match & Reject",
      detail: "Fetches transcripts (cache → YouTube captions → Whisper), then your local Qwen3 model (via Ollama) checks geographic/subject context FIRST — rejects mismatches before pinpointing the exact visual timestamp. Falls back to GPT-4o-mini if the local model is unavailable.",
    },
    {
      id: "rank",
      stage: "ranking",
      icon: Sparkles,
      title: "Rank & Audit",
      detail: "Scores each clip on AI confidence, keyword density, context relevance, views, channel authority, caption quality, and recency. Hard-rejects negative keyword hits and context mismatches. Each shot gets exactly one best clip.",
    },
  ]

  const currentIdx = steps.findIndex(s => s.stage === currentStage)

  return (
    <div className="grid grid-cols-4 gap-1.5">
      {steps.map((step, idx) => {
        const Icon = step.icon
        const isCompleted = currentIdx > idx
        const isCurrent = currentIdx === idx
        const isPending = currentIdx < idx

        return (
          <div key={step.id} className="relative">
            <div className={cn(
              "rounded-lg border p-2.5 transition-all h-full",
              isCompleted && "bg-emerald-500/10 border-emerald-500/30",
              isCurrent && "bg-primary/5 border-primary/30 ring-1 ring-primary/20",
              isPending && "bg-secondary/50 border-border/50 opacity-50",
            )}>
              <div className="flex items-center gap-1.5 mb-1">
                {isCompleted ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : isCurrent ? (
                  <Icon className="w-3.5 h-3.5 text-primary animate-pulse" />
                ) : (
                  <Icon className="w-3.5 h-3.5 text-muted-foreground" />
                )}
                <span className={cn(
                  "text-[11px] font-semibold",
                  isCompleted && "text-emerald-400",
                  isCurrent && "text-primary",
                  isPending && "text-muted-foreground",
                )}>
                  {step.title}
                </span>
              </div>
              <p className={cn(
                "text-[10px] leading-relaxed",
                isPending ? "text-muted-foreground/50" : "text-muted-foreground",
              )}>
                {step.detail}
              </p>
            </div>
            {idx < steps.length - 1 && (
              <div className={cn(
                "absolute top-1/2 -right-1.5 w-2.5 h-px z-10",
                isCompleted ? "bg-emerald-500/50" : "bg-border/50",
              )} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function ActivityGroupSection({ group, isLast }: { group: ActivityGroup; isLast: boolean }) {
  const [manualToggle, setManualToggle] = useState<boolean | null>(null)
  const expanded = manualToggle !== null ? manualToggle : isLast
  const Icon = group.icon

  const depth0 = group.entries.filter(e => !e.depth || e.depth === 0)
  const childEntries = group.entries.filter(e => (e.depth || 0) >= 1)

  return (
    <div className="relative">
      <button
        className="flex items-center gap-2 w-full text-left py-1.5 px-1 rounded hover:bg-secondary/50 transition-colors"
        onClick={() => setManualToggle(prev => prev !== null ? !prev : !isLast ? true : false)}
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-muted-foreground flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 text-muted-foreground flex-shrink-0" />
        )}
        <Icon className={cn("w-3.5 h-3.5 flex-shrink-0", group.color)} />
        <span className={cn("text-xs font-semibold", group.color)}>{group.label}</span>
        {!expanded && (
          <span className="text-[10px] text-muted-foreground/50 ml-1">
            — {group.entries.length} event{group.entries.length !== 1 ? "s" : ""}
          </span>
        )}
        {isLast && (
          <span className="ml-auto">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
          </span>
        )}
      </button>

      {expanded && (
        <div className="ml-3 pl-3 border-l border-border/40 space-y-0">
          {depth0.map((entry, i) => (
            <ActivityLine key={`d0-${i}`} entry={entry} isLatest={isLast && i === depth0.length - 1 && childEntries.length === 0} depth={0} />
          ))}
          {childEntries.length > 0 && (
            <ChildEntries entries={childEntries} isLastGroup={isLast} />
          )}
        </div>
      )}
    </div>
  )
}

function ChildEntries({ entries, isLastGroup }: { entries: ActivityEntry[]; isLastGroup: boolean }) {
  const segmentGroups = useMemo(() => {
    const groups: { label: string; items: ActivityEntry[] }[] = []
    let currentLabel = ""

    for (const e of entries) {
      const d = e.depth || 1
      if (d === 1) {
        groups.push({ label: e.text, items: [e] })
        currentLabel = e.text
      } else {
        if (groups.length === 0) {
          groups.push({ label: "", items: [] })
        }
        groups[groups.length - 1].items.push(e)
      }
    }
    return groups
  }, [entries])

  return (
    <div className="space-y-0">
      {segmentGroups.map((sg, gi) => (
        <SegmentGroup
          key={gi}
          group={sg}
          isLast={isLastGroup && gi === segmentGroups.length - 1}
        />
      ))}
    </div>
  )
}

function SegmentGroup({ group, isLast }: { group: { label: string; items: ActivityEntry[] }; isLast: boolean }) {
  const [expanded, setExpanded] = useState(true)
  const header = group.items[0]
  const children = group.items.slice(1)

  if (group.items.length === 1) {
    return <ActivityLine entry={header} isLatest={isLast} depth={1} />
  }

  return (
    <div>
      <button
        className="flex items-center gap-1.5 w-full text-left py-0.5 pl-1 rounded hover:bg-secondary/30 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="w-2.5 h-2.5 text-muted-foreground/50 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-2.5 h-2.5 text-muted-foreground/50 flex-shrink-0" />
        )}
        <ActivityLineInner entry={header} isLatest={false} />
        {!expanded && children.length > 0 && (
          <span className="text-[9px] text-muted-foreground/40 ml-auto flex-shrink-0">
            +{children.length}
          </span>
        )}
      </button>
      {expanded && children.length > 0 && (
        <div className="ml-4 pl-2.5 border-l border-border/30 space-y-0">
          {children.map((e, i) => (
            <ActivityLine key={i} entry={e} isLatest={isLast && i === children.length - 1} depth={(e.depth || 2)} />
          ))}
        </div>
      )}
    </div>
  )
}

function ActivityLine({ entry, isLatest, depth }: { entry: ActivityEntry; isLatest: boolean; depth: number }) {
  return (
    <div className={cn(
      "flex items-start gap-2 py-1 transition-opacity duration-300",
      isLatest ? "opacity-100" : "opacity-70",
      depth >= 2 && "ml-1",
      depth >= 3 && "ml-2",
    )}>
      <ActivityLineInner entry={entry} isLatest={isLatest} />
    </div>
  )
}

function renderTextWithLinks(text: string) {
  const parts = text.split(/(https?:\/\/[^\s)]+)/)
  if (parts.length === 1) return text
  return parts.map((part, i) => {
    const isUrl = i % 2 === 1
    if (isUrl) {
      const label = part.startsWith("https://youtu.be/")
        ? `🔗 ${part.replace("https://youtu.be/", "")}`
        : part
      return (
        <a
          key={i}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-dotted underline-offset-2 hover:text-blue-400 transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          {label}
        </a>
      )
    }
    return part ? <span key={i}>{part}</span> : null
  })
}

function ActivityLineInner({ entry, isLatest }: { entry: ActivityEntry; isLatest: boolean }) {
  const IconComp = iconMap[entry.icon] || Zap
  const color = iconColor[entry.icon] || "text-muted-foreground"

  return (
    <>
      <span className="text-[10px] text-muted-foreground/50 tabular-nums pt-0.5 w-[70px] shrink-0" title={entry.time}>
        {formatActivityTime(entry.time)}
      </span>
      <IconComp className={cn("w-3 h-3 shrink-0 mt-0.5", color)} />
      <span
        className={cn(
          "text-[11px] leading-relaxed",
          isLatest ? "text-foreground" : "text-muted-foreground",
          entry.icon === "terminal" && "font-mono text-[10px] text-lime-400/80",
        )}
      >
        {renderTextWithLinks(entry.text)}
      </span>
    </>
  )
}

export function ProgressTracker({ progress, onCancel }: ProgressTrackerProps) {
  const currentStageIndex = stages.findIndex((s) => s.key === progress.stage)
  const logEndRef = useRef<HTMLDivElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)
  const [showHowItWorks, setShowHowItWorks] = useState(true)

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" })
    }
  }, [progress.activity_log?.length])

  const activityLog = progress.activity_log || []
  const groups = useMemo(() => groupActivityLog(activityLog), [activityLog])

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
          <div className="space-y-1.5">
            <Progress value={progress.percent_complete} className="h-2" />
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{progress.message}</span>
              <span className="font-medium tabular-nums text-muted-foreground">
                {progress.percent_complete}%
              </span>
            </div>
          </div>

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
                    !isCompleted && !isCurrent && "bg-secondary text-muted-foreground"
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

      {/* Pipeline Flow Diagram */}
      <Card>
        <CardHeader className="pb-2">
          <button
            className="flex items-center gap-2 w-full text-left"
            onClick={() => setShowHowItWorks(!showHowItWorks)}
          >
            {showHowItWorks ? (
              <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
            )}
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Brain className="w-3.5 h-3.5" />
              How It Works
            </CardTitle>
          </button>
        </CardHeader>
        {showHowItWorks && (
          <CardContent className="pt-0 pb-3">
            <PipelineFlowDiagram currentStage={progress.stage} />
            <div className="mt-3 px-2 py-2 bg-secondary/30 rounded-md">
              <p className="text-[10px] text-muted-foreground leading-relaxed">
                <strong className="text-foreground/80">Example:</strong>{" "}
                Your script is about North Sentinel Island →{" "}
                <span className="text-violet-400">GPT-4o</span> translates, extracts context (&quot;Andaman Islands, NOT mainland forests&quot;), creates scene with 3 shots: &quot;aerial island view&quot;, &quot;dense canopy&quot;, &quot;coral reef&quot; — each with its own search queries &amp; negative keywords [&quot;Telangana&quot;, &quot;safari&quot;] →{" "}
                <span className="text-blue-400">yt-dlp</span> searches &quot;Sentinel Island tropical forest&quot; per shot (not generic &quot;tropical forest&quot;) →{" "}
                <span className="text-teal-400">Whisper</span> transcribes audio →{" "}
                <span className="text-orange-400">Qwen3 (local Ollama)</span> reads each transcript, checks geographic context first — rejects a Telangana forest video (wrong geography), pinpoints Andaman drone footage at 2:34. If the local model is offline, <span className="text-pink-400">GPT-4o-mini</span> takes over →{" "}
                <span className="text-amber-400">Ranker</span> scores confidence + keyword density + context + views + recency, hard-rejects negative keyword hits →{" "}
                Editor gets one best clip per shot, contextually accurate.
              </p>
            </div>
          </CardContent>
        )}
      </Card>

      {/* Hierarchical Activity Feed */}
      {activityLog.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Zap className="w-3.5 h-3.5" />
              Live Activity
              <span className="ml-auto text-[10px] font-normal text-muted-foreground/50">
                {Intl.DateTimeFormat().resolvedOptions().timeZone}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div
              ref={logContainerRef}
              className="max-h-[500px] overflow-y-auto px-4 pb-4"
            >
              <div className="space-y-1">
                {groups.map((group, gi) => (
                  <ActivityGroupSection
                    key={group.key}
                    group={group}
                    isLast={gi === groups.length - 1}
                  />
                ))}
              </div>
              <div ref={logEndRef} />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
