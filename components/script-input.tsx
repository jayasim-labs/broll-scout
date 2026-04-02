"use client"

import { useState, useMemo } from "react"
import { Search, Loader2, Upload, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import type { ProjectSummary } from "@/lib/types"

interface ScriptInputProps {
  onSubmit: (script: string, options: {
    enableGeminiExpansion: boolean
    title: string
    projectId?: string
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
  const [enableGeminiExpansion, setEnableGeminiExpansion] = useState(false)

  const charCount = script.length
  const wordCount = useMemo(() => {
    const text = script.trim()
    return text ? text.split(/\s+/).length : 0
  }, [script])

  const estimatedMinutes = useMemo(() => Math.ceil(wordCount / 100), [wordCount])

  const handleSubmit = () => {
    if (charCount < 100) return
    if (!title.trim() && !selectedProjectId) return
    onSubmit(script, {
      enableGeminiExpansion,
      title: title.trim(),
      projectId: selectedProjectId,
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

  const isValid = charCount >= 100 && (title.trim() || selectedProjectId)

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
                      if (proj) setTitle(proj.title)
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
                  onCheckedChange={setEnableGeminiExpansion}
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
            <Button
              onClick={handleSubmit}
              disabled={isLoading || !isValid}
              size="lg"
              className="gap-2 self-start"
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
        </CardContent>
      </Card>
    </div>
  )
}
