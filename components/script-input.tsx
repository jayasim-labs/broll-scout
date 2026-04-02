"use client"

import { useState, useMemo } from "react"
import { Search, Loader2, Upload, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"

interface ScriptInputProps {
  onSubmit: (script: string, options?: { enableGeminiExpansion: boolean }) => void
  isLoading: boolean
}

export function ScriptInput({ onSubmit, isLoading }: ScriptInputProps) {
  const [script, setScript] = useState("")
  const [enableGeminiExpansion, setEnableGeminiExpansion] = useState(false)

  const charCount = script.length
  const wordCount = useMemo(() => {
    const text = script.trim()
    return text ? text.split(/\s+/).length : 0
  }, [script])

  const estimatedMinutes = useMemo(() => Math.ceil(wordCount / 150), [wordCount])

  const handleSubmit = () => {
    if (charCount < 100) return
    onSubmit(script, { enableGeminiExpansion })
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

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h1 className="text-3xl font-bold">B-Roll Scout</h1>
        <p className="text-muted-foreground text-lg">
          Paste your Tamil script below. The AI will translate, segment, and find
          timestamped B-roll clips from YouTube.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Tamil Script</CardTitle>
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
          <Textarea
            rows={12}
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder="உங்கள் தமிழ் ஸ்கிரிப்டை இங்கே பேஸ்ட் செய்யுங்கள்... (Paste your Tamil script here...)"
            className="resize-y bg-secondary font-mono text-sm"
          />

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
            <div className="flex items-center gap-3">
              <Switch
                id="gemini-toggle"
                checked={enableGeminiExpansion}
                onCheckedChange={setEnableGeminiExpansion}
              />
              <Label htmlFor="gemini-toggle" className="flex items-center gap-1.5 text-sm text-muted-foreground cursor-pointer">
                <Sparkles className="w-3.5 h-3.5" />
                Gemini AI Expansion
              </Label>
              {enableGeminiExpansion && (
                <span className="text-[10px] text-amber-500 bg-amber-500/10 px-1.5 py-0.5 rounded">
                  Slower but finds more creative B-roll
                </span>
              )}
            </div>
            <Button
              onClick={handleSubmit}
              disabled={isLoading || charCount < 100}
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
        </CardContent>
      </Card>
    </div>
  )
}
