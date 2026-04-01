"use client"

import { useState, useEffect, useCallback } from "react"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import { Save, RotateCcw, Plus, X, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import type { PipelineSettings } from "@/lib/types"

const API = "/api/v1"

const TABS = [
  { id: "sources", label: "Source Management" },
  { id: "blocked", label: "Blocked Sources" },
  { id: "pipeline", label: "Pipeline Parameters" },
  { id: "instructions", label: "Special Instructions" },
] as const

type TabId = (typeof TABS)[number]["id"]

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>("sources")
  const [settings, setSettings] = useState<PipelineSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    setLoading(true)
    try {
      const resp = await fetch(`${API}/settings`)
      if (resp.ok) {
        const data = await resp.json()
        setSettings(data.settings as PipelineSettings)
      }
    } catch {
      toast.error("Failed to load settings")
    } finally {
      setLoading(false)
    }
  }

  const updateLocal = useCallback((key: string, value: unknown) => {
    setSettings((prev) => prev ? { ...prev, [key]: value } as PipelineSettings : prev)
    setDirty(true)
  }, [])

  const saveAll = async () => {
    if (!settings || !dirty) return
    setSaving(true)
    try {
      const resp = await fetch(`${API}/settings/bulk`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings }),
      })
      if (resp.ok) {
        toast.success("Settings saved")
        setDirty(false)
      } else {
        toast.error("Failed to save settings")
      }
    } catch {
      toast.error("Failed to save settings")
    } finally {
      setSaving(false)
    }
  }

  const resetDefaults = async () => {
    try {
      const resp = await fetch(`${API}/settings/reset`, { method: "POST" })
      if (resp.ok) {
        toast.success("Settings reset to defaults")
        await loadSettings()
        setDirty(false)
      }
    } catch {
      toast.error("Failed to reset settings")
    }
  }

  if (loading || !settings) {
    return (
      <div className="min-h-screen bg-background">
        <Navbar />
        <div className="flex items-center justify-center h-[60vh]">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <Navbar />
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold">Settings</h1>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={resetDefaults} className="gap-1">
              <RotateCcw className="w-4 h-4" /> Reset
            </Button>
            <Button size="sm" onClick={saveAll} disabled={!dirty || saving} className="gap-1">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save
            </Button>
          </div>
        </div>

        <div className="flex border-b border-border mb-6 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                activeTab === tab.id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "sources" && <SourcesTab settings={settings} onChange={updateLocal} />}
        {activeTab === "blocked" && <BlockedTab settings={settings} onChange={updateLocal} />}
        {activeTab === "pipeline" && <PipelineTab settings={settings} onChange={updateLocal} />}
        {activeTab === "instructions" && <InstructionsTab settings={settings} onChange={updateLocal} />}
      </main>
    </div>
  )
}

function TagList({
  items, onAdd, onRemove, placeholder = "Add item...",
}: {
  items: string[]
  onAdd: (item: string) => void
  onRemove: (index: number) => void
  placeholder?: string
}) {
  const [input, setInput] = useState("")

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {items.map((item, i) => (
          <Badge key={i} variant="secondary" className="gap-1 text-xs">
            {item}
            <button onClick={() => onRemove(i)} className="hover:text-destructive">
              <X className="w-3 h-3" />
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={placeholder}
          className="flex-1"
          onKeyDown={(e) => {
            if (e.key === "Enter" && input.trim()) {
              onAdd(input.trim())
              setInput("")
            }
          }}
        />
        <Button
          variant="outline" size="sm"
          onClick={() => { if (input.trim()) { onAdd(input.trim()); setInput("") } }}
        >
          <Plus className="w-4 h-4" />
        </Button>
      </div>
    </div>
  )
}

function SourcesTab({ settings, onChange }: { settings: PipelineSettings; onChange: (k: string, v: unknown) => void }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle className="text-base">Preferred Channels (Tier 1 — Archives & History)</CardTitle></CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            Tier 1 channels are searched first and receive the highest ranking boost. Enter YouTube channel IDs (UC...).
          </p>
          <TagList
            items={settings.preferred_channels_tier1 || []}
            onAdd={(id) => onChange("preferred_channels_tier1", [...(settings.preferred_channels_tier1 || []), id])}
            onRemove={(i) => onChange("preferred_channels_tier1", (settings.preferred_channels_tier1 || []).filter((_, j) => j !== i))}
            placeholder="UC... channel ID"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Preferred Channels (Tier 2 — Documentary & Explainer)</CardTitle></CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            Tier 2 channels get a ranking boost but are not searched exclusively. Enter channel names.
          </p>
          <TagList
            items={settings.preferred_channels_tier2 || []}
            onAdd={(name) => onChange("preferred_channels_tier2", [...(settings.preferred_channels_tier2 || []), name])}
            onRemove={(i) => onChange("preferred_channels_tier2", (settings.preferred_channels_tier2 || []).filter((_, j) => j !== i))}
            placeholder="Channel name"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Public Domain Archives</CardTitle></CardHeader>
        <CardContent>
          <div className="space-y-2">
            {(settings.public_domain_archives || []).map((a, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <span className="font-medium">{a.name}</span>
                <span className="text-muted-foreground text-xs">{a.url}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Stock Footage Platforms</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(settings.stock_platforms || {}).map(([key, enabled]) => (
              <div key={key} className="flex items-center justify-between">
                <Label className="text-sm capitalize">{key.replace(/_/g, " ")}</Label>
                <Switch
                  checked={enabled as boolean}
                  onCheckedChange={(v) => onChange("stock_platforms", { ...settings.stock_platforms, [key]: v })}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function BlockedTab({ settings, onChange }: { settings: PipelineSettings; onChange: (k: string, v: unknown) => void }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle className="text-base">Blocked News Networks</CardTitle></CardHeader>
        <CardContent>
          <TagList
            items={settings.blocked_networks || []}
            onAdd={(n) => onChange("blocked_networks", [...(settings.blocked_networks || []), n])}
            onRemove={(i) => onChange("blocked_networks", (settings.blocked_networks || []).filter((_, j) => j !== i))}
            placeholder="Network name"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Blocked Studios & Entertainment</CardTitle></CardHeader>
        <CardContent>
          <TagList
            items={settings.blocked_studios || []}
            onAdd={(s) => onChange("blocked_studios", [...(settings.blocked_studios || []), s])}
            onRemove={(i) => onChange("blocked_studios", (settings.blocked_studios || []).filter((_, j) => j !== i))}
            placeholder="Studio name"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Blocked Sports Leagues</CardTitle></CardHeader>
        <CardContent>
          <TagList
            items={settings.blocked_sports || []}
            onAdd={(s) => onChange("blocked_sports", [...(settings.blocked_sports || []), s])}
            onRemove={(i) => onChange("blocked_sports", (settings.blocked_sports || []).filter((_, j) => j !== i))}
            placeholder="League name"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Custom Block Rules</CardTitle></CardHeader>
        <CardContent>
          <Textarea
            rows={4}
            value={settings.custom_block_rules || ""}
            onChange={(e) => onChange("custom_block_rules", e.target.value)}
            placeholder='Block any channel with "reaction" or "compilation" in the title'
            className="text-sm"
          />
          <p className="text-xs text-muted-foreground mt-1">
            These rules are passed to GPT-4o-mini during the ranking step as additional filtering instructions.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

function SliderSetting({
  label, value, min, max, step = 1, onChange, unit = "",
}: {
  label: string; value: number; min: number; max: number; step?: number;
  onChange: (v: number) => void; unit?: string
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-sm">
        <Label>{label}</Label>
        <span className="font-mono text-muted-foreground tabular-nums">
          {Number.isInteger(step) ? value : value.toFixed(2)}{unit}
        </span>
      </div>
      <Slider
        value={[value]}
        min={min} max={max} step={step}
        onValueChange={(v) => onChange(v[0])}
      />
    </div>
  )
}

function PipelineTab({ settings, onChange }: { settings: PipelineSettings; onChange: (k: string, v: unknown) => void }) {
  const normalizeWeights = (key: string, newVal: number) => {
    const keys = [
      "weight_keyword_density", "weight_viral_score",
      "weight_channel_authority", "weight_caption_quality", "weight_recency",
    ]
    const current: Record<string, number> = {}
    keys.forEach((k) => (current[k] = (settings as Record<string, unknown>)[k] as number || 0))
    current[key] = newVal
    const total = Object.values(current).reduce((a, b) => a + b, 0)
    if (total > 0) {
      keys.forEach((k) => onChange(k, Math.round((current[k] / total) * 100) / 100))
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle className="text-base">Search Settings</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <SliderSetting label="Search queries per segment" value={settings.search_queries_per_segment} min={1} max={5} onChange={(v) => onChange("search_queries_per_segment", v)} />
          <SliderSetting label="YouTube results per query" value={settings.youtube_results_per_query} min={3} max={10} onChange={(v) => onChange("youtube_results_per_query", v)} />
          <SliderSetting label="Max candidates per segment" value={settings.max_candidates_per_segment} min={5} max={20} onChange={(v) => onChange("max_candidates_per_segment", v)} />
          <SliderSetting label="Final results per segment" value={settings.top_results_per_segment} min={1} max={5} onChange={(v) => onChange("top_results_per_segment", v)} />
          <SliderSetting label="Target total results" value={settings.total_results_target} min={15} max={60} onChange={(v) => onChange("total_results_target", v)} />
          <SliderSetting label="Gemini expanded queries" value={settings.gemini_expanded_queries} min={0} max={10} onChange={(v) => onChange("gemini_expanded_queries", v)} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Timestamp Detection</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-sm">Timestamp model</Label>
              <Select value={settings.timestamp_model} onValueChange={(v) => onChange("timestamp_model", v)}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="gpt-4o-mini">gpt-4o-mini</SelectItem>
                  <SelectItem value="gpt-4o">gpt-4o</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-sm">Translation model</Label>
              <Select value={settings.translation_model} onValueChange={(v) => onChange("translation_model", v)}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="gpt-4o">gpt-4o</SelectItem>
                  <SelectItem value="gpt-4o-mini">gpt-4o-mini</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <SliderSetting label="Confidence threshold" value={settings.confidence_threshold} min={0.1} max={0.9} step={0.1} onChange={(v) => onChange("confidence_threshold", v)} />
          <SliderSetting label="Max video length for Whisper" value={settings.whisper_max_video_duration_min} min={10} max={120} onChange={(v) => onChange("whisper_max_video_duration_min", v)} unit=" min" />
          <SliderSetting label="Audio trim length for Whisper" value={settings.whisper_audio_trim_min} min={5} max={30} onChange={(v) => onChange("whisper_audio_trim_min", v)} unit=" min" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Video Filtering</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <SliderSetting label="Min video duration" value={settings.min_video_duration_sec} min={30} max={600} onChange={(v) => onChange("min_video_duration_sec", v)} unit="s" />
          <SliderSetting label="Max video duration" value={settings.max_video_duration_sec} min={600} max={10800} step={300} onChange={(v) => onChange("max_video_duration_sec", v)} unit="s" />
          <SliderSetting label="Prefer channels with min subscribers" value={settings.prefer_min_subscribers} min={0} max={100000} step={1000} onChange={(v) => onChange("prefer_min_subscribers", v)} />
          <SliderSetting label="Full recency score within years" value={settings.recency_full_score_years} min={1} max={10} onChange={(v) => onChange("recency_full_score_years", v)} unit=" yr" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Ranking Weights</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">Weights auto-normalize to sum to 1.0 when adjusted.</p>
          <SliderSetting label="Keyword density" value={settings.weight_keyword_density} min={0} max={1} step={0.05} onChange={(v) => normalizeWeights("weight_keyword_density", v)} />
          <SliderSetting label="View count (viral)" value={settings.weight_viral_score} min={0} max={1} step={0.05} onChange={(v) => normalizeWeights("weight_viral_score", v)} />
          <SliderSetting label="Channel authority" value={settings.weight_channel_authority} min={0} max={1} step={0.05} onChange={(v) => normalizeWeights("weight_channel_authority", v)} />
          <SliderSetting label="Caption quality" value={settings.weight_caption_quality} min={0} max={1} step={0.05} onChange={(v) => normalizeWeights("weight_caption_quality", v)} />
          <SliderSetting label="Recency" value={settings.weight_recency} min={0} max={1} step={0.05} onChange={(v) => normalizeWeights("weight_recency", v)} />

          <div className="flex gap-1 h-6 mt-2">
            <div className="bg-blue-500 rounded-l" style={{ width: `${settings.weight_keyword_density * 100}%` }} title="Keyword" />
            <div className="bg-green-500" style={{ width: `${settings.weight_viral_score * 100}%` }} title="Viral" />
            <div className="bg-yellow-500" style={{ width: `${settings.weight_channel_authority * 100}%` }} title="Authority" />
            <div className="bg-purple-500" style={{ width: `${settings.weight_caption_quality * 100}%` }} title="Caption" />
            <div className="bg-red-500 rounded-r" style={{ width: `${settings.weight_recency * 100}%` }} title="Recency" />
          </div>
          <div className="flex text-xs text-muted-foreground gap-3">
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-blue-500 rounded-full" />Keyword</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-green-500 rounded-full" />Viral</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-yellow-500 rounded-full" />Authority</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-purple-500 rounded-full" />Caption</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500 rounded-full" />Recency</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Performance</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <SliderSetting label="Max concurrent segments" value={settings.max_concurrent_segments} min={1} max={10} onChange={(v) => onChange("max_concurrent_segments", v)} />
          <SliderSetting label="Segment timeout" value={settings.segment_timeout_sec} min={30} max={180} onChange={(v) => onChange("segment_timeout_sec", v)} unit="s" />
          <SliderSetting label="Low result threshold" value={settings.low_result_threshold} min={10} max={30} onChange={(v) => onChange("low_result_threshold", v)} />
        </CardContent>
      </Card>
    </div>
  )
}

function InstructionsTab({ settings, onChange }: { settings: PipelineSettings; onChange: (k: string, v: unknown) => void }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle className="text-base">Custom Instructions</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            rows={10}
            value={settings.special_instructions || ""}
            onChange={(e) => onChange("special_instructions", e.target.value)}
            className="text-sm font-mono"
          />
          <p className="text-xs text-muted-foreground">
            These instructions are sent to the AI during translation and ranking. Be specific about what you want prioritized or avoided.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Context-Matching Rules</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <Label className="text-sm">Enable context-matching synthesis</Label>
            <Switch
              checked={settings.enable_context_matching}
              onCheckedChange={(v) => onChange("enable_context_matching", v)}
            />
          </div>
          <div className="flex items-center justify-between">
            <Label className="text-sm">Discard clips shorter than 10 seconds</Label>
            <Switch
              checked={settings.discard_clips_shorter_than_10s}
              onCheckedChange={(v) => onChange("discard_clips_shorter_than_10s", v)}
            />
          </div>
          <div className="flex items-center justify-between">
            <Label className="text-sm">Verify timestamp doesn&apos;t land on end screen</Label>
            <Switch
              checked={settings.verify_timestamp_not_end_screen}
              onCheckedChange={(v) => onChange("verify_timestamp_not_end_screen", v)}
            />
          </div>
          <div className="flex items-center justify-between">
            <Label className="text-sm">Cap end timestamp at video duration - 5s</Label>
            <Switch
              checked={settings.cap_end_timestamp}
              onCheckedChange={(v) => onChange("cap_end_timestamp", v)}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
