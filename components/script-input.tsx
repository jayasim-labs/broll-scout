"use client"

import { useState, useMemo, useEffect, useCallback } from "react"
import { Search, Loader2, Upload, Sparkles, ClipboardCheck, Layers, Film, Clock, DollarSign, Cpu, ChevronDown, ChevronUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import type { ProjectSummary, VideoCategory, PipelineEstimate } from "@/lib/types"
import { CATEGORY_OPTIONS } from "@/lib/types"

interface ScriptInputProps {
  onSubmit: (script: string, options: {
    enableGeminiExpansion: boolean
    title: string
    projectId?: string
    category?: string
  }) => void
  isLoading: boolean
  projects?: ProjectSummary[]
  preselectedProjectId?: string | null
}

export function ScriptInput({ onSubmit, isLoading, projects = [], preselectedProjectId }: ScriptInputProps) {
  const [script, setScript] = useState("")
  const [title, setTitle] = useState("")
  const [selectedProjectId, setSelectedProjectId] = useState<string | undefined>(
    preselectedProjectId ?? undefined
  )
  const [category, setCategory] = useState<VideoCategory | "">("")
  const [enableGeminiExpansion, setEnableGeminiExpansion] = useState(false)
  const [estimate, setEstimate] = useState<PipelineEstimate | null>(null)
  const [isEstimating, setIsEstimating] = useState(false)
  const [showConfig, setShowConfig] = useState(false)
  const [lastEstimatedScript, setLastEstimatedScript] = useState("")

  useEffect(() => {
    fetch("/api/v1/settings")
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.settings?.enable_gemini_expansion) {
          setEnableGeminiExpansion(true)
        }
      })
      .catch(() => {})
  }, [])

  const charCount = script.length
  const wordCount = useMemo(() => {
    const text = script.trim()
    return text ? text.split(/\s+/).length : 0
  }, [script])

  const estimatedMinutes = useMemo(() => Math.ceil(wordCount / 100), [wordCount])

  // Clear estimate when script changes significantly
  useEffect(() => {
    if (estimate && script !== lastEstimatedScript) {
      const currentWords = script.trim().split(/\s+/).length
      const prevWords = lastEstimatedScript.trim().split(/\s+/).length
      if (Math.abs(currentWords - prevWords) > 20) {
        setEstimate(null)
      }
    }
  }, [script, estimate, lastEstimatedScript])

  const handleCheckScript = useCallback(async () => {
    if (charCount < 100) return
    setIsEstimating(true)
    try {
      const resp = await fetch("/api/v1/jobs/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script,
          title: title.trim() || "Untitled",
          editor_id: "default_editor",
          enable_gemini_expansion: enableGeminiExpansion,
        }),
      })
      if (resp.ok) {
        const data: PipelineEstimate = await resp.json()
        setEstimate(data)
        setLastEstimatedScript(script)
      }
    } catch { /* backend unavailable */ }
    finally { setIsEstimating(false) }
  }, [charCount, script, title, enableGeminiExpansion])

  const handleSubmit = () => {
    if (charCount < 100) return
    if (!title.trim() && !selectedProjectId) return
    if (!category) return
    onSubmit(script, {
      enableGeminiExpansion,
      title: title.trim(),
      projectId: selectedProjectId,
      category: category || undefined,
    })
  }

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === "string") {
        setScript(reader.result)
      }
    }
    reader.readAsText(file)
  }

  const isValid = charCount >= 100 && (title.trim() || selectedProjectId) && !!category

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h1 className="text-3xl font-bold">B-Roll Scout</h1>
        <p className="text-muted-foreground text-lg">
          Paste your detailed script below. The AI will translate, segment, and find
          timestamped B-roll clips from YouTube.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>New Script</CardTitle>
            <label className="cursor-pointer">
              <input
                type="file"
                accept=".txt,.doc,.docx"
                className="hidden"
                onChange={handleFileUpload}
              />
              <span className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground border border-border rounded-md hover:bg-secondary transition-colors">
                <Upload className="w-4 h-4" />
                Upload File
              </span>
            </label>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="script-title" className="text-sm font-medium">
              Project Title <span className="text-destructive">*</span>
            </Label>
            {projects.length > 0 ? (
              <div className="space-y-2">
                <select
                  value={selectedProjectId || "__new__"}
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === "__new__") {
                      setSelectedProjectId(undefined)
                    } else {
                      setSelectedProjectId(v)
                      const proj = projects.find(p => p.project_id === v)
                      if (proj) {
                        setTitle(proj.title)
                        if (proj.category) setCategory(proj.category as VideoCategory)
                      }
                    }
                  }}
                  className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="__new__">+ Create New Project</option>
                  {projects.map(p => (
                    <option key={p.project_id} value={p.project_id}>
                      {p.title} ({p.job_count} jobs)
                    </option>
                  ))}
                </select>
                {!selectedProjectId && (
                  <Input
                    id="script-title"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder='e.g., "India Space Program" or "Tamil Cinema History"'
                    className="border border-input bg-background"
                  />
                )}
              </div>
            ) : (
              <Input
                id="script-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder='e.g., "India Space Program" or "Tamil Cinema History"'
                className="border border-input bg-background"
              />
            )}
            <p className="text-[11px] text-muted-foreground">
              Each script becomes a job inside a project folder. You can add multiple scripts to the same project.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="category-select" className="text-sm font-medium">
              Category <span className="text-destructive">*</span>
            </Label>
            <select
              id="category-select"
              value={category}
              onChange={(e) => setCategory(e.target.value as VideoCategory | "")}
              className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">Select a category...</option>
              {CATEGORY_OPTIONS.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground">
              Tag this project for indexing — helps organize and search across your B-roll library.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="script-textarea" className="text-sm font-medium">
              Detailed Script <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id="script-textarea"
              rows={12}
              value={script}
              onChange={(e) => setScript(e.target.value)}
              placeholder="Paste your detailed script here..."
              className="resize-y border border-input bg-background font-mono text-sm"
            />
          </div>

          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <div className="flex gap-4">
              <span>{charCount.toLocaleString()} characters</span>
              <span>{wordCount.toLocaleString()} words</span>
              {estimatedMinutes > 0 && (
                <span>~{estimatedMinutes} min script</span>
              )}
            </div>
            {charCount > 0 && charCount < 100 && (
              <span className="text-destructive">Minimum 100 characters required</span>
            )}
          </div>

          <div className="flex items-center justify-between pt-2">
            <div className="space-y-1.5">
              <div className="flex items-center gap-3">
                <Switch
                  id="gemini-toggle"
                  checked={enableGeminiExpansion}
                  onCheckedChange={(v) => { setEnableGeminiExpansion(v); setEstimate(null) }}
                />
                <Label htmlFor="gemini-toggle" className="flex items-center gap-1.5 text-sm cursor-pointer">
                  <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                  Gemini AI Expansion
                </Label>
                {enableGeminiExpansion && (
                  <span className="text-[10px] text-amber-500 bg-amber-500/10 px-1.5 py-0.5 rounded font-medium">
                    ON
                  </span>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground max-w-lg leading-relaxed pl-10">
                {enableGeminiExpansion
                  ? "After GPT-4o generates search queries, Gemini 1.5 Flash will analyze initial YouTube results and suggest 5 additional creative queries per segment — finds more diverse B-roll but takes longer."
                  : "Default: GPT-4o translates your script, segments it, and generates 3 targeted YouTube search queries per segment. Fast and cost-effective for most scripts."}
              </p>
            </div>
            <div className="flex flex-col gap-2 self-start">
              <Button
                onClick={handleCheckScript}
                disabled={isEstimating || charCount < 100}
                variant="outline"
                size="lg"
                className="gap-2"
              >
                {isEstimating ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <ClipboardCheck className="w-4 h-4" />
                    Check Script
                  </>
                )}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {estimate && (
        <Card className="border-blue-500/30 bg-blue-500/[0.03]">
          <CardContent className="pt-6 pb-5">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center">
                    <Layers className="w-4 h-4 text-blue-500" />
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold">Pipeline Plan</h3>
                    <p className="text-[11px] text-muted-foreground">
                      {estimate.script_type} &middot; ~{estimate.est_duration_minutes} min &middot; {estimate.word_count.toLocaleString()} words
                    </p>
                  </div>
                </div>
                <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-500 uppercase tracking-wider">
                  Preview
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                <PlanStat
                  icon={<Layers className="w-3.5 h-3.5" />}
                  label="Segments"
                  value={`~${estimate.est_segments}`}
                  sub={`${estimate.est_no_broll_segments} host-on-camera`}
                />
                <PlanStat
                  icon={<Film className="w-3.5 h-3.5" />}
                  label="B-Roll Shots"
                  value={`~${estimate.est_shots}`}
                  sub={`${estimate.est_queries} search queries`}
                />
                <PlanStat
                  icon={<Search className="w-3.5 h-3.5" />}
                  label="YouTube Searches"
                  value={`~${estimate.est_youtube_searches}`}
                  sub={`~${estimate.est_videos_to_match} videos to match`}
                />
                <PlanStat
                  icon={<Clock className="w-3.5 h-3.5" />}
                  label="Est. Pipeline Time"
                  value={`~${estimate.est_pipeline_time_min}–${estimate.est_pipeline_time_max} min`}
                />
                <PlanStat
                  icon={<DollarSign className="w-3.5 h-3.5" />}
                  label="Est. API Cost"
                  value={`~$${estimate.est_cost_usd.toFixed(2)}`}
                  sub="GPT-4o translation + matching"
                />
                <PlanStat
                  icon={<Cpu className="w-3.5 h-3.5" />}
                  label="Translation Model"
                  value={estimate.config.translation_model}
                  sub={`Matcher: ${estimate.config.matcher_backend}`}
                />
              </div>

              <div className="pt-1">
                <button
                  onClick={() => setShowConfig(!showConfig)}
                  className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showConfig ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  {showConfig ? "Hide" : "Show"} pipeline config
                </button>
                {showConfig && (
                  <div className="mt-2 rounded-md bg-muted/50 p-3 font-mono text-[11px] text-muted-foreground space-y-0.5">
                    <div>yt_results_per_query: {estimate.config.youtube_results_per_query}</div>
                    <div>max_candidates_per_shot: {estimate.config.max_candidates_per_shot}</div>
                    <div>matcher_model: {estimate.config.matcher_model}</div>
                    <div>whisper_model: {estimate.config.whisper_model}</div>
                    <div>gemini_expansion: {estimate.config.enable_gemini_expansion ? "enabled" : "disabled"}</div>
                  </div>
                )}
              </div>

              <div className="flex justify-end pt-1">
                <Button
                  onClick={handleSubmit}
                  disabled={isLoading || !isValid}
                  size="lg"
                  className="gap-2"
                >
                  {isLoading ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" />
                      Processing...
                    </>
                  ) : (
                    <>
                      <Search className="w-5 h-5" />
                      Scout B-Roll
                    </>
                  )}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function PlanStat({ icon, label, value, sub }: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
}) {
  return (
    <div className="rounded-lg border border-border/50 bg-background/50 p-3 space-y-1">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-[10px] font-medium uppercase tracking-wider">{label}</span>
      </div>
      <p className="text-sm font-semibold">{value}</p>
      {sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}
    </div>
  )
}
