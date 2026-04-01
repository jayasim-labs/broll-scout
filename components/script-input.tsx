"use client"

import { useState, useMemo } from "react"
import { Search, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface ScriptInputProps {
  onSubmit: (data: {
    script: string
    videoStyle: string
    targetAudience: string
    minQuality: string
  }) => void
  isLoading: boolean
}

export function ScriptInput({ onSubmit, isLoading }: ScriptInputProps) {
  const [script, setScript] = useState("")
  const [videoStyle, setVideoStyle] = useState("documentary")
  const [targetAudience, setTargetAudience] = useState("general")
  const [minQuality, setMinQuality] = useState("1080p")

  const wordCount = useMemo(() => {
    const text = script.trim()
    return text ? text.split(/\s+/).length : 0
  }, [script])

  const estimatedCost = useMemo(() => {
    const estimatedCues = Math.ceil(wordCount / 50)
    const translationCost = (wordCount / 1000) * 0.015
    const matchingCost = estimatedCues * 0.005
    return (translationCost + matchingCost).toFixed(3)
  }, [wordCount])

  const handleSubmit = () => {
    if (!script.trim() || wordCount < 10) return
    onSubmit({ script, videoStyle, targetAudience, minQuality })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Paste Your Script</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <Label htmlFor="script-input" className="text-muted-foreground">
            Video Script or Transcript
          </Label>
          <Textarea
            id="script-input"
            rows={8}
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder="Paste your video script here. The AI will analyze it and find relevant b-roll footage for each section..."
            className="mt-2 resize-y bg-secondary"
          />
          <p className="text-xs text-muted-foreground mt-1">
            {wordCount} words
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <Label htmlFor="video-style" className="text-muted-foreground">
              Video Style
            </Label>
            <Select value={videoStyle} onValueChange={setVideoStyle}>
              <SelectTrigger id="video-style" className="mt-2 bg-secondary">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="documentary">Documentary</SelectItem>
                <SelectItem value="corporate">Corporate</SelectItem>
                <SelectItem value="educational">Educational</SelectItem>
                <SelectItem value="cinematic">Cinematic</SelectItem>
                <SelectItem value="social-media">Social Media</SelectItem>
                <SelectItem value="news">News/Journalism</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label htmlFor="target-audience" className="text-muted-foreground">
              Target Audience
            </Label>
            <Select value={targetAudience} onValueChange={setTargetAudience}>
              <SelectTrigger id="target-audience" className="mt-2 bg-secondary">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="general">General</SelectItem>
                <SelectItem value="professional">Professional/Business</SelectItem>
                <SelectItem value="young-adult">Young Adult (18-35)</SelectItem>
                <SelectItem value="tech-savvy">Tech Savvy</SelectItem>
                <SelectItem value="academic">Academic</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label htmlFor="min-quality" className="text-muted-foreground">
              Minimum Quality
            </Label>
            <Select value={minQuality} onValueChange={setMinQuality}>
              <SelectTrigger id="min-quality" className="mt-2 bg-secondary">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="720p">720p (HD)</SelectItem>
                <SelectItem value="1080p">1080p (Full HD)</SelectItem>
                <SelectItem value="4K">4K (Ultra HD)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex items-center justify-between pt-4">
          <p className="text-sm text-muted-foreground">
            Estimated cost: <span className="font-medium text-accent">${estimatedCost}</span>
          </p>
          <Button
            onClick={handleSubmit}
            disabled={isLoading || wordCount < 10}
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
  )
}
