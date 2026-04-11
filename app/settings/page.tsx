"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { toast } from "sonner"
import { Navbar } from "@/components/navbar"
import {
  Save, RotateCcw, Plus, X, Loader2, Users, Eye, Shield, Tv,
  Film, Zap, Globe, Ban, BookOpen, Settings2, MessageSquare,
  Search, ExternalLink, Sparkles,
} from "lucide-react"
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
import { cn } from "@/lib/utils"
import type { PipelineSettings, ChannelEntry } from "@/lib/types"
import { DEFAULT_MIN_VIDEO_DURATION_SEC } from "@/lib/pipeline-defaults"

const API = "/api/v1"

interface ResolvedChannel {
  channel_id: string
  channel_name: string
  channel_url: string
  channel_handle: string
  thumbnail_url: string
  subscriber_count: number
  subscriber_display: string
  video_count: number | null
  description: string
}

const TABS = [
  { id: "sources", label: "Preferred Channels", icon: Tv },
  { id: "blocked", label: "Blocked Sources", icon: Shield },
  { id: "pipeline", label: "Pipeline Parameters", icon: Settings2 },
  { id: "instructions", label: "Special Instructions", icon: MessageSquare },
] as const

type TabId = (typeof TABS)[number]["id"]

const SECTION_CONFIG = {
  "blocked-news": { label: "Blocked News Channels", desc: "Videos from these specific YouTube channels will be excluded from results.", icon: Ban, color: "red", tier: "blocked", category: "news" },
  "blocked-studio": { label: "Blocked Studio Channels", desc: "Copyright-protected content from these studio channels is excluded.", icon: Film, color: "red", tier: "blocked", category: "studio" },
  "blocked-sports": { label: "Blocked Sports Channels", desc: "Sports league content is excluded to avoid copyright issues.", icon: Shield, color: "red", tier: "blocked", category: "sports" },
  "preferred-tier1": { label: "Priority Channels — Tier 1 (Archives & History)", desc: "Searched first with highest ranking boost. Archive, history, and documentary channels.", icon: Zap, color: "amber", tier: "tier1", category: "archive" },
  "preferred-tier2": { label: "Documentary & Explainer — Tier 2", desc: "Ranking boost applied when these channels appear in results.", icon: Film, color: "blue", tier: "tier2", category: "documentary" },
} as const

type SectionKey = keyof typeof SECTION_CONFIG

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>("sources")
  const [settings, setSettings] = useState<PipelineSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [channelSources, setChannelSources] = useState<ChannelEntry[]>([])

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    setLoading(true)
    try {
      const [settingsResp, channelsResp] = await Promise.all([
        fetch(`${API}/settings`),
        fetch(`${API}/settings/channels`),
      ])
      if (settingsResp.ok) {
        const data = await settingsResp.json()
        setSettings(data.settings as PipelineSettings)
      }
      if (channelsResp.ok) {
        const data = await channelsResp.json()
        const groups = data.groups || {}
        const all: ChannelEntry[] = []
        for (const entries of Object.values(groups)) {
          if (Array.isArray(entries)) all.push(...(entries as ChannelEntry[]))
        }
        setChannelSources(all)
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
      const { channel_sources: _, ...settingsWithoutChannels } = settings as PipelineSettings & { channel_sources?: unknown }
      const resp = await fetch(`${API}/settings/bulk`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: settingsWithoutChannels }),
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

  const addChannel = async (section: SectionKey, channel: ResolvedChannel) => {
    const cfg = SECTION_CONFIG[section]
    const entry: ChannelEntry = {
      ...channel,
      category: cfg.category,
      tier: cfg.tier,
      added_at: new Date().toISOString(),
      added_by: "editor",
    }
    try {
      const resp = await fetch(`${API}/settings/channels/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry),
      })
      if (resp.ok) {
        setChannelSources((prev) => [...prev, entry])
        toast.success(`Added ${channel.channel_name}`)
      } else if (resp.status === 409) {
        toast.error("Channel already exists in this section")
      }
    } catch {
      toast.error("Failed to add channel")
    }
  }

  const removeChannel = async (channelId: string) => {
    try {
      const resp = await fetch(`${API}/settings/channels/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_id: channelId }),
      })
      if (resp.ok) {
        setChannelSources((prev) => prev.filter((c) => c.channel_id !== channelId))
        toast.success("Channel removed")
      }
    } catch {
      toast.error("Failed to remove channel")
    }
  }

  const getChannelsForSection = (section: SectionKey): ChannelEntry[] => {
    const cfg = SECTION_CONFIG[section]
    return channelSources.filter((c) => c.tier === cfg.tier && c.category === cfg.category)
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
          {TABS.map((tab) => {
            const Icon = tab.icon
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "flex items-center gap-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors",
                  activeTab === tab.id
                    ? "border-primary text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            )
          })}
        </div>

        {activeTab === "sources" && (
          <ChannelSectionsTab
            sections={["preferred-tier1", "preferred-tier2"]}
            getChannels={getChannelsForSection}
            onAdd={addChannel}
            onRemove={removeChannel}
            settings={settings}
          />
        )}
        {activeTab === "blocked" && (
          <BlockedTab
            settings={settings}
            onChange={updateLocal}
            getChannels={getChannelsForSection}
            onAddChannel={addChannel}
            onRemoveChannel={removeChannel}
          />
        )}
        {activeTab === "pipeline" && <PipelineTab settings={settings} onChange={updateLocal} />}
        {activeTab === "instructions" && <InstructionsTab settings={settings} onChange={updateLocal} />}
      </main>
    </div>
  )
}

/* ─── Channel Card ──────────────────────────────────────────────────── */

function ChannelCard({ channel, onRemove }: { channel: ChannelEntry; onRemove: () => void }) {
  const channelUrl = channel.channel_url || `https://www.youtube.com/channel/${channel.channel_id}`
  const [imgError, setImgError] = useState(false)
  return (
    <div className="flex items-center gap-3 p-2.5 bg-secondary/40 rounded-lg border border-border group hover:border-primary/30 transition-colors">
      <a href={channelUrl} target="_blank" rel="noopener noreferrer" className="shrink-0" title="Open channel on YouTube">
        {channel.thumbnail_url && !imgError ? (
          <img
            src={channel.thumbnail_url}
            alt={channel.channel_name}
            referrerPolicy="no-referrer"
            onError={() => setImgError(true)}
            className="w-10 h-10 rounded-full object-cover ring-2 ring-border hover:ring-primary/50 transition-all"
          />
        ) : (
          <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center ring-2 ring-border hover:ring-primary/50 transition-all">
            <Tv className="w-4 h-4 text-muted-foreground" />
          </div>
        )}
      </a>
      <a href={channelUrl} target="_blank" rel="noopener noreferrer" className="flex-1 min-w-0 hover:opacity-80 transition-opacity">
        <p className="text-sm font-medium truncate">
          {channel.channel_name || channel.channel_id}
        </p>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {channel.subscriber_count > 0 && (
            <span className="flex items-center gap-0.5">
              <Users className="w-3 h-3" />
              {channel.subscriber_display || formatSubs(channel.subscriber_count)} subscribers
            </span>
          )}
          {channel.channel_handle && (
            <span className="opacity-60">{channel.channel_handle}</span>
          )}
        </div>
        <span className="text-[10px] text-muted-foreground/50 truncate block">
          {channelUrl}
        </span>
      </a>
      <button
        onClick={(e) => { e.stopPropagation(); onRemove() }}
        className="opacity-0 group-hover:opacity-100 p-1 hover:bg-destructive/10 rounded transition-opacity"
        title="Remove channel"
      >
        <X className="w-3.5 h-3.5 text-destructive" />
      </button>
    </div>
  )
}

/* ─── Channel Search Input ──────────────────────────────────────────── */

function ChannelSearchInput({
  section,
  onAdd,
}: {
  section: SectionKey
  onAdd: (section: SectionKey, channel: ResolvedChannel) => void
}) {
  const [query, setQuery] = useState("")
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<ResolvedChannel[]>([])
  const [showResults, setShowResults] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowResults(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  const doSearch = async () => {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    setResults([])
    setShowResults(true)
    try {
      const resp = await fetch(`${API}/settings/resolve-channel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: q }),
      })
      if (resp.ok) {
        const data = await resp.json()
        const channels = data.channels || []
        if (channels.length === 1 && isDirectInput(q)) {
          onAdd(section, channels[0])
          setQuery("")
          setShowResults(false)
          setResults([])
          return
        }
        setResults(channels)
      }
    } catch {
      toast.error("Search failed — check companion app")
    } finally {
      setSearching(false)
    }
  }

  const handleSelect = (ch: ResolvedChannel) => {
    onAdd(section, ch)
    setQuery("")
    setShowResults(false)
    setResults([])
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Paste YouTube channel URL, handle (@CNN), or search by name"
          className="flex-1"
          onKeyDown={(e) => {
            if (e.key === "Enter") doSearch()
          }}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={doSearch}
          disabled={searching || !query.trim()}
          className="gap-1 shrink-0"
        >
          {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Search
        </Button>
      </div>

      {showResults && (
        <div className="absolute z-50 mt-1 w-full bg-popover border border-border rounded-lg shadow-lg max-h-80 overflow-y-auto">
          {searching && (
            <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" /> Searching YouTube...
            </div>
          )}
          {!searching && results.length === 0 && (
            <div className="p-3 text-sm text-muted-foreground">
              No channels found. Try a different search term.
            </div>
          )}
          {results.map((ch) => (
            <button
              key={ch.channel_id}
              onClick={() => handleSelect(ch)}
              className="w-full flex items-center gap-3 p-2.5 hover:bg-accent/50 transition-colors text-left border-b border-border/50 last:border-0"
            >
              {ch.thumbnail_url ? (
                <img src={ch.thumbnail_url} alt={ch.channel_name} referrerPolicy="no-referrer" className="w-9 h-9 rounded-full object-cover shrink-0" />
              ) : (
                <div className="w-9 h-9 rounded-full bg-muted flex items-center justify-center shrink-0">
                  <Tv className="w-4 h-4 text-muted-foreground" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{ch.channel_name}</p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{ch.subscriber_display || "N/A"} subscribers</span>
                  {ch.channel_handle && <span>{ch.channel_handle}</span>}
                </div>
                {ch.description && (
                  <p className="text-[10px] text-muted-foreground/70 truncate mt-0.5">{ch.description}</p>
                )}
              </div>
              <Badge variant="secondary" className="text-[10px] shrink-0 gap-1">
                <Plus className="w-3 h-3" /> Add
              </Badge>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function isDirectInput(query: string): boolean {
  return query.startsWith("http") || query.startsWith("@") || /^UC[\w-]{22}$/.test(query)
}

function formatSubs(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return n.toString()
}

/* ─── Channel Section (reusable for any blocked/preferred group) ──── */

function ChannelSection({
  section,
  channels,
  onAdd,
  onRemove,
}: {
  section: SectionKey
  channels: ChannelEntry[]
  onAdd: (section: SectionKey, channel: ResolvedChannel) => void
  onRemove: (channelId: string) => void
}) {
  const cfg = SECTION_CONFIG[section]
  const Icon = cfg.icon
  const colorMap: Record<string, string> = {
    red: "bg-red-500/10 text-red-400",
    amber: "bg-amber-500/10 text-amber-400",
    blue: "bg-blue-500/10 text-blue-400",
  }
  const iconColor = colorMap[cfg.color] || "bg-muted text-muted-foreground"

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <div className={cn("p-1.5 rounded-md", iconColor.split(" ")[0])}>
            <Icon className={cn("w-4 h-4", iconColor.split(" ")[1])} />
          </div>
          <div>
            <CardTitle className="text-base">{cfg.label}</CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">{cfg.desc}</p>
          </div>
          {channels.length > 0 && (
            <Badge variant="secondary" className="ml-auto text-xs">{channels.length}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {channels.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {channels.map((ch) => (
              <ChannelCard
                key={ch.channel_id}
                channel={ch}
                onRemove={() => onRemove(ch.channel_id)}
              />
            ))}
          </div>
        )}
        <ChannelSearchInput section={section} onAdd={onAdd} />
      </CardContent>
    </Card>
  )
}

/* ─── Preferred Channels Tab (Sources tab) ──────────────────────────── */

function ChannelSectionsTab({
  sections,
  getChannels,
  onAdd,
  onRemove,
  settings,
}: {
  sections: SectionKey[]
  getChannels: (section: SectionKey) => ChannelEntry[]
  onAdd: (section: SectionKey, channel: ResolvedChannel) => void
  onRemove: (channelId: string) => void
  settings: PipelineSettings
}) {
  return (
    <div className="space-y-6">
      {sections.map((section) => (
        <ChannelSection
          key={section}
          section={section}
          channels={getChannels(section)}
          onAdd={onAdd}
          onRemove={onRemove}
        />
      ))}

      {/* Public Domain Archives */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-md bg-emerald-500/10">
              <BookOpen className="w-4 h-4 text-emerald-400" />
            </div>
            <div>
              <CardTitle className="text-base">Public Domain Archives</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                Free-to-use archival footage sources.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {(settings.public_domain_archives || []).map((a, i) => (
              <a
                key={i}
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 p-2.5 bg-secondary/40 rounded-lg border border-border hover:border-emerald-500/30 transition-colors group"
              >
                <div className="w-10 h-10 rounded-lg bg-emerald-500/10 flex items-center justify-center shrink-0">
                  <Globe className="w-5 h-5 text-emerald-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{a.name}</p>
                  <p className="text-xs text-muted-foreground truncate group-hover:text-emerald-400 transition-colors">{a.url}</p>
                </div>
                <ExternalLink className="w-4 h-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
              </a>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Stock Footage Platforms */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-md bg-purple-500/10">
              <Eye className="w-4 h-4 text-purple-400" />
            </div>
            <div>
              <CardTitle className="text-base">Stock Footage Platforms</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                Enable or disable stock footage platforms for search.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(settings.stock_platforms || {}).map(([key, enabled]) => (
              <div key={key} className="flex items-center justify-between p-2.5 bg-secondary/30 rounded-lg border border-border">
                <Label className="text-sm capitalize font-medium">{key.replace(/_/g, " ")}</Label>
                <Switch
                  checked={enabled as boolean}
                  onCheckedChange={() => {}}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

/* ─── Blocked Sources Tab ───────────────────────────────────────────── */

function BlockedTab({
  settings,
  onChange,
  getChannels,
  onAddChannel,
  onRemoveChannel,
}: {
  settings: PipelineSettings
  onChange: (k: string, v: unknown) => void
  getChannels: (section: SectionKey) => ChannelEntry[]
  onAddChannel: (section: SectionKey, channel: ResolvedChannel) => void
  onRemoveChannel: (channelId: string) => void
}) {
  return (
    <div className="space-y-6">
      <ChannelSection
        section="blocked-news"
        channels={getChannels("blocked-news")}
        onAdd={onAddChannel}
        onRemove={onRemoveChannel}
      />
      <ChannelSection
        section="blocked-studio"
        channels={getChannels("blocked-studio")}
        onAdd={onAddChannel}
        onRemove={onRemoveChannel}
      />
      <ChannelSection
        section="blocked-sports"
        channels={getChannels("blocked-sports")}
        onAdd={onAddChannel}
        onRemove={onRemoveChannel}
      />

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-md bg-orange-500/10">
              <MessageSquare className="w-4 h-4 text-orange-400" />
            </div>
            <div>
              <CardTitle className="text-base">Custom Block Rules</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                One keyword per line. Channels whose name contains any keyword will be blocked.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <Textarea
            rows={4}
            value={settings.custom_block_rules || ""}
            onChange={(e) => onChange("custom_block_rules", e.target.value)}
            placeholder={'e.g., reaction\ncompilation\nfan edit'}
            className="text-sm"
          />
        </CardContent>
      </Card>
    </div>
  )
}

/* ─── Helpers ───────────────────────────────────────────────────────── */

function SliderSetting({
  label, value, min, max, step = 1, onChange, unit = "", help,
}: {
  label: string; value: number | undefined; min: number; max: number; step?: number;
  onChange: (v: number) => void; unit?: string; help?: string
}) {
  const safe = typeof value === "number" && !Number.isNaN(value) ? value : 0
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-sm">
        <Label>{label}</Label>
        <span className="font-mono text-muted-foreground tabular-nums">
          {Number.isInteger(step) ? safe : safe.toFixed(2)}{unit}
        </span>
      </div>
      <Slider
        value={[safe]}
        min={min} max={max} step={step}
        onValueChange={(v) => onChange(v[0])}
      />
      {help && <p className="text-[11px] text-muted-foreground/70 leading-tight">{help}</p>}
    </div>
  )
}

/* ─── Model Selector (Timestamp Matching Engine) ───────────────────── */

interface ModelStatus {
  installed: boolean
  ready: boolean
  display_name: string
  min_vram_gb: number
  pull_size_gb: number
  ollama_outdated?: boolean
  min_ollama_version?: string | null
}

interface ModelsStatusResponse {
  ollama_version: string
  ollama_running: boolean
  active_model: string
  models: Record<string, ModelStatus>
}

const MODEL_META: Record<string, { subtitle: string; badge?: string }> = {
  "qwen3:8b": { subtitle: "~5GB VRAM. Faster inference, good Tamil context understanding.", badge: "Default" },
  "gemma4:26b": { subtitle: "~18GB VRAM. MoE — activates 4B params/token. Better reasoning, 256K context.", badge: "Quality" },
  "gemma4:e4b": { subtitle: "~10GB VRAM. Gemma 4 effective 4B variant. 128K context, good balance of speed and quality." },
  "qwen3:4b": { subtitle: "~3GB VRAM. Faster but less accurate. For low-VRAM machines." },
  "llama3.3:8b": { subtitle: "~5GB VRAM. Meta's Llama 3.3. Good general-purpose model." },
}

const COMPANION_URL = "http://127.0.0.1:9876"

function ModelSelector({ currentModel, onSelect }: { currentModel: string; onSelect: (m: string) => void }) {
  const [statuses, setStatuses] = useState<ModelsStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [pulling, setPulling] = useState<string | null>(null)
  const [switching, setSwitching] = useState(false)
  const [companionDown, setCompanionDown] = useState(false)

  const fetchStatuses = useCallback(async () => {
    try {
      const resp = await fetch(`${COMPANION_URL}/models/status`, { signal: AbortSignal.timeout(3000) })
      if (resp.ok) {
        setStatuses(await resp.json())
        setCompanionDown(false)
      } else {
        setCompanionDown(true)
      }
    } catch {
      setCompanionDown(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchStatuses() }, [fetchStatuses])

  const handlePull = async (modelKey: string) => {
    setPulling(modelKey)
    try {
      const resp = await fetch(`${COMPANION_URL}/models/pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelKey }),
        signal: AbortSignal.timeout(1800000),
      })
      if (resp.ok) {
        toast.success(`${MODEL_META[modelKey]?.subtitle?.split(".")[0] || modelKey} ready!`)
        await fetchStatuses()
      } else {
        const err = await resp.json().catch(() => ({}))
        toast.error(err.error || "Pull failed")
      }
    } catch {
      toast.error("Pull failed — check companion app")
    } finally {
      setPulling(null)
    }
  }

  const handleSelect = async (modelKey: string) => {
    onSelect(modelKey)
    const ms = statuses?.models[modelKey]
    if (!ms?.ready) return

    setSwitching(true)
    try {
      await fetch(`${COMPANION_URL}/models/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelKey }),
        signal: AbortSignal.timeout(60000),
      })
      toast.success(`Switched to ${statuses?.models[modelKey]?.display_name || modelKey}. Next job will use this model.`)
      await fetchStatuses()
    } catch {
      toast.error("Switch failed — model will be loaded on next job")
    } finally {
      setSwitching(false)
    }
  }

  if (companionDown) {
    return (
      <div className="p-3 rounded-md border border-yellow-500/30 bg-yellow-500/5 text-sm text-muted-foreground">
        Companion app not reachable at {COMPANION_URL}. Start the companion to manage models.
        <br />
        <span className="text-xs">Current setting: <code className="text-[10px]">{currentModel}</code></span>
      </div>
    )
  }

  if (loading) {
    return <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Checking companion models...</div>
  }

  const models = statuses?.models || {}
  const modelKeys = Object.keys(models).length > 0 ? Object.keys(models) : Object.keys(MODEL_META)

  return (
    <div className="space-y-2">
      {modelKeys.map((key) => {
        const ms = models[key]
        const meta = MODEL_META[key] || { subtitle: "" }
        const isSelected = currentModel === key
        const isReady = ms?.ready ?? false
        const isInstalled = ms?.installed ?? false
        const isPulling = pulling === key
        const isOllamaOutdated = ms?.ollama_outdated ?? false

        return (
          <label
            key={key}
            className={cn(
              "flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors",
              isSelected ? "border-primary bg-primary/5" : "border-border hover:border-primary/30",
              !isReady && !isPulling && "opacity-70",
            )}
            onClick={(e) => {
              e.preventDefault()
              if (isPulling || switching) return
              handleSelect(key)
            }}
          >
            <div className={cn(
              "mt-0.5 w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0",
              isSelected ? "border-primary" : "border-muted-foreground/40",
            )}>
              {isSelected && <div className="w-2 h-2 rounded-full bg-primary" />}
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{ms?.display_name || key}</span>
                {meta.badge && (
                  <span className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded font-medium",
                    meta.badge === "Default" ? "bg-blue-500/10 text-blue-500" : "bg-purple-500/10 text-purple-500",
                  )}>{meta.badge}</span>
                )}
                {isOllamaOutdated && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded font-medium bg-red-500/10 text-red-500">
                    Ollama outdated
                  </span>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground/70 mt-0.5">{meta.subtitle}</p>
              {isOllamaOutdated && !isInstalled && (
                <p className="text-[11px] text-red-400 mt-1">
                  Requires Ollama {ms?.min_ollama_version}+. Current: {statuses?.ollama_version}.{" "}
                  <a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer" className="underline hover:text-red-300">
                    Update Ollama
                  </a>
                </p>
              )}
            </div>

            <div className="flex items-center gap-2 shrink-0 mt-0.5">
              {isPulling ? (
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Downloading ~{ms?.pull_size_gb || "?"}GB...
                </span>
              ) : isReady ? (
                <span className="flex items-center gap-1 text-xs text-green-500">
                  <span className="w-2 h-2 rounded-full bg-green-500" /> Ready
                </span>
              ) : isInstalled ? (
                <span className="flex items-center gap-1 text-xs text-yellow-500">
                  <span className="w-2 h-2 rounded-full bg-yellow-500" /> Installed (Ollama down)
                </span>
              ) : isOllamaOutdated ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs gap-1 border-red-500/30 text-red-400"
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); window.open("https://ollama.com/download", "_blank") }}
                >
                  Update Ollama
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs gap-1"
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); handlePull(key) }}
                  disabled={!!pulling}
                >
                  <Plus className="w-3 h-3" /> Pull (~{ms?.pull_size_gb || "?"}GB)
                </Button>
              )}
            </div>
          </label>
        )
      })}
      {switching && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground px-1">
          <Loader2 className="w-3 h-3 animate-spin" /> Switching model...
        </div>
      )}
    </div>
  )
}

/* ─── Pipeline Tab ──────────────────────────────────────────────────── */

function PipelineTab({ settings, onChange }: { settings: PipelineSettings; onChange: (k: string, v: unknown) => void }) {
  const normalizeWeights = (key: string, newVal: number) => {
    const keys = [
      "weight_ai_confidence", "weight_fit_score", "weight_viral_score",
      "weight_channel_authority", "weight_caption_quality", "weight_recency",
      "weight_context_relevance",
    ] as const
    const current: Record<string, number> = {}
    keys.forEach((k) => {
      const raw = (settings as Record<string, unknown>)[k]
      current[k] = typeof raw === "number" && !Number.isNaN(raw) ? raw : 0
    })
    current[key] = newVal
    const total = Object.values(current).reduce((a, b) => a + b, 0)
    if (total > 0) {
      keys.forEach((k) => onChange(k, Math.round((current[k] / total) * 100) / 100))
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Search Settings</CardTitle>
          <p className="text-xs text-muted-foreground">Controls how the pipeline searches YouTube for candidate videos. GPT-4o generates search queries from your script, then these settings determine how many queries to run and how many results to collect.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label className="text-sm">Search backend</Label>
            <div className="mt-1 flex items-center gap-2 px-3 py-2 rounded-md border bg-muted/30">
              <span className="text-sm font-medium">yt-dlp via companion app</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-600 font-medium">No quota limits</span>
            </div>
            <p className="text-[11px] text-muted-foreground/70 mt-1">
              All YouTube searches run through yt-dlp on your local companion app. No YouTube API quota is consumed — you can run unlimited jobs per day.
            </p>
          </div>
          <SliderSetting
            label="YouTube results per query" value={settings.youtube_results_per_query} min={3} max={15}
            onChange={(v) => onChange("youtube_results_per_query", v)}
            help="How many YouTube results to fetch per search query. GPT-4o generates ~5 search queries per shot, so 5 queries × 12 results = 60 raw candidates per shot before filtering."
          />
          <SliderSetting
            label="Max candidates per segment" value={settings.max_candidates_per_segment} min={5} max={30}
            onChange={(v) => onChange("max_candidates_per_segment", v)}
            help="After deduplication and duration filtering, keep at most this many videos per scene for transcript analysis. Higher = more thorough but slower (each candidate needs a transcript fetch + AI match call)."
          />
          <SliderSetting
            label="Clips per shot" value={settings.top_results_per_shot ?? 5} min={1} max={10}
            onChange={(v) => onChange("top_results_per_shot", v)}
            help="How many final clips to keep per shot for editors to choose from. Higher = more choices but more matching time. Set to 5 to give editors a good selection."
          />
          <div className="pt-2 border-t">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-amber-500" />
                <Label className="text-sm">Gemini AI Expansion (default)</Label>
              </div>
              <Switch
                checked={!!(settings as Record<string, unknown>).enable_gemini_expansion}
                onCheckedChange={(v) => onChange("enable_gemini_expansion", v)}
              />
            </div>
            <p className="text-[11px] text-muted-foreground/70 mt-1.5 leading-relaxed">
              After GPT-4o generates search queries, Gemini 1.5 Flash analyzes initial YouTube results and suggests 5 additional creative queries per segment — finds more diverse B-roll but takes longer. This sets the default; editors can still override per-job on the Scout page.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Timestamp Detection</CardTitle>
          <p className="text-xs text-muted-foreground">Controls how the AI reads video transcripts and pinpoints the exact start/end seconds of the best visual moment. The timestamp model analyzes each transcript, and the translation model handles script segmentation.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-sm">Matcher backend</Label>
              <Select value={settings.matcher_backend || "auto"} onValueChange={(v) => onChange("matcher_backend", v)}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto — local Ollama first</SelectItem>
                  <SelectItem value="local">Local only — Ollama via companion ($0 cost)</SelectItem>
                  <SelectItem value="api">API only — OpenAI GPT-4o-mini</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground/70 mt-1">
                Auto uses the local Qwen3 model via Ollama (free). If the local model fails, the clip is skipped unless API fallback is enabled below.
              </p>
              <div className="flex items-center justify-between mt-3 p-2.5 rounded-md border border-border bg-secondary/30">
                <div>
                  <Label className="text-sm">API fallback (GPT-4o-mini)</Label>
                  <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                    When enabled, uses GPT-4o-mini as fallback if local Ollama fails or is unavailable. Costs ~$0.001/video. When disabled, clips are skipped if the local model can&apos;t match — zero API cost for matching.
                  </p>
                </div>
                <Switch
                  checked={!!settings.api_fallback_enabled}
                  onCheckedChange={(v) => onChange("api_fallback_enabled", v)}
                />
              </div>
              <div className="flex items-center justify-between mt-3 p-2.5 rounded-md border border-amber-500/20 bg-amber-500/5">
                <div>
                  <Label className="text-sm">Zero-confidence rescue (GPT-4o-mini)</Label>
                  <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                    When the local model returns confidence 0 (couldn&apos;t find a match), retry with GPT-4o-mini which may find valid timestamps the local model missed. Unlike full API fallback, this only fires on zero-confidence — context mismatches from Ollama are still trusted. Costs ~$0.001 per rescue attempt.
                  </p>
                </div>
                <Switch
                  checked={!!settings.confident_fallback_enabled}
                  onCheckedChange={(v) => onChange("confident_fallback_enabled", v)}
                />
              </div>
            </div>
            <div>
              <Label className="text-sm">Timestamp Matching Engine</Label>
              <p className="text-[11px] text-muted-foreground/70 mt-0.5 mb-2">
                Used when matcher backend is &quot;auto&quot; or &quot;local&quot;. Requires Ollama installed and the model pulled on the companion machine.
              </p>
              <ModelSelector
                currentModel={settings.matcher_model || "qwen3:8b"}
                onSelect={(v) => onChange("matcher_model", v)}
              />
              <div className="flex items-center justify-between mt-3 p-2.5 rounded-md border border-purple-500/20 bg-purple-500/5">
                <div>
                  <Label className="text-sm">A/B Test Mode</Label>
                  <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                    Both the primary model above and a second model will process each candidate. Results show side-by-side comparison so editors can compare quality. This doubles processing time per job.
                  </p>
                </div>
                <Switch
                  checked={!!(settings as Record<string, unknown>).ab_test_mode}
                  onCheckedChange={(v) => onChange("ab_test_mode", v)}
                />
              </div>
              {!!(settings as Record<string, unknown>).ab_test_mode && (
                <div className="mt-2">
                  <Label className="text-[11px] text-muted-foreground">A/B comparison model</Label>
                  <Select value={((settings as Record<string, unknown>).ab_test_model_b as string) || "gemma4:26b"} onValueChange={(v) => onChange("ab_test_model_b", v)}>
                    <SelectTrigger className="mt-1 h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="gemma4:26b">Gemma 4 26B MoE</SelectItem>
                      <SelectItem value="gemma4:e4b">Gemma 4 E4B</SelectItem>
                      <SelectItem value="qwen3:8b">Qwen3 8B</SelectItem>
                      <SelectItem value="qwen3:4b">Qwen3 4B</SelectItem>
                      <SelectItem value="llama3.3:8b">Llama 3.3 8B</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-sm">API timestamp model (fallback)</Label>
              <Select value={settings.timestamp_model} onValueChange={(v) => onChange("timestamp_model", v)}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="gpt-4o-mini">gpt-4o-mini (~$0.001/video)</SelectItem>
                  <SelectItem value="gpt-4o">gpt-4o (~$0.01/video, higher quality)</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground/70 mt-1">
                Used when backend is &quot;api&quot; or as fallback in &quot;auto&quot; mode (only if API fallback is enabled). Called once per candidate video.
              </p>
            </div>
            <div>
              <Label className="text-sm">Translation model</Label>
              <Select value={settings.translation_model} onValueChange={(v) => onChange("translation_model", v)}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="gpt-4o">gpt-4o (best multilingual quality)</SelectItem>
                  <SelectItem value="gpt-4o-mini">gpt-4o-mini (cheaper, slightly lower quality)</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground/70 mt-1">
                Called once per job to translate your script and break it into visual scenes with search queries.
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-sm">Lightweight model (query generation & re-search)</Label>
              <Select value={settings.lightweight_model || "gpt-4o-mini"} onValueChange={(v) => onChange("lightweight_model", v)}>
                <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="gpt-4o-mini">GPT-4o-mini (~$0.0001/call, fast)</SelectItem>
                  <SelectItem value="ollama">Ollama / Local LLM ($0, uses companion)</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground/70 mt-1">
                Used for generating alternative search queries (re-search pass for low-confidence shots) and &quot;Add another shot&quot; ideation. GPT-4o-mini is faster and more creative; Ollama is free but shares GPU with matching.
              </p>
            </div>
          </div>
          <SliderSetting
            label="Confidence threshold" value={settings.confidence_threshold} min={0.1} max={0.9} step={0.1}
            onChange={(v) => onChange("confidence_threshold", v)}
            help="Minimum confidence score (0.0–1.0) from the AI to include a clip. Lower = more results but some may be less relevant. Higher = stricter, fewer but more accurate clips. Clips below threshold are still kept if no better alternatives exist for a scene."
          />
          <div>
            <Label className="text-sm">Whisper transcription model</Label>
            <Select value={settings.whisper_model || "large-v3-turbo"} onValueChange={(v) => onChange("whisper_model", v)}>
              <SelectTrigger className="mt-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="large-v3-turbo">large-v3-turbo (recommended — fast + accurate)</SelectItem>
                <SelectItem value="large-v3">large-v3 (highest accuracy, 4× slower)</SelectItem>
                <SelectItem value="small">small (lightweight, lower accuracy)</SelectItem>
                <SelectItem value="base">base (minimal, CPU-friendly)</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground/70 mt-1">
              Used when no YouTube captions exist. Runs locally via the companion app. large-v3-turbo gives near-identical accuracy to large-v3 at 8× the speed. Requires GPU (MPS on Mac, CUDA on Windows) for large models — falls back to CPU &quot;base&quot; if unavailable.
            </p>
          </div>
          <SliderSetting
            label="Max video length for Whisper" value={settings.whisper_max_video_duration_min} min={10} max={120}
            onChange={(v) => onChange("whisper_max_video_duration_min", v)} unit=" min"
            help="Videos longer than this are skipped for Whisper transcription (too slow/large to download audio locally). Only applies when no YouTube captions exist."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Video Filtering</CardTitle>
          <p className="text-xs text-muted-foreground">Hard filters applied before ranking. Videos outside these ranges are excluded entirely — they never reach the transcript or timestamp stage.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <SliderSetting
            label="Min video duration" value={settings.min_video_duration_sec} min={30} max={600}
            onChange={(v) => onChange("min_video_duration_sec", v)} unit="s"
            help={
              `Videos shorter than this are excluded (trailers, shorts, etc.). ` +
              `Backend default is ${DEFAULT_MIN_VIDEO_DURATION_SEC}s (2 min); DynamoDB entries still at 30s are auto-upgraded on API restart. ` +
              `Run python scripts/sync_min_video_duration_dynamo.py if you need to force-sync.`
            }
          />
          <SliderSetting
            label="Max video duration" value={settings.max_video_duration_sec} min={600} max={10800} step={300}
            onChange={(v) => onChange("max_video_duration_sec", v)} unit="s"
            help="Videos longer than this are excluded. Filters out extremely long livestreams and multi-hour content. Default 5400s (90 min)."
          />
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Exclude ~9:16 Shorts-shaped video</Label>
              <Switch
                checked={settings.filter_9_16_shorts ?? true}
                onCheckedChange={(v) => onChange("filter_9_16_shorts", v)}
              />
            </div>
            <p className="text-[11px] text-muted-foreground/70 leading-tight">
              Uses yt-dlp width/height: drops portrait clips whose aspect is close to 9:16 (typical YouTube Shorts). Other tall formats (e.g. 4:5) are kept, so this targets Shorts more than a generic “vertical” rule. Turn off if you need Shorts as B-roll.
            </p>
          </div>
          <SliderSetting
            label="9:16 detection tolerance" value={settings.shorts_9_16_aspect_tolerance ?? 0.06} min={0.02} max={0.12} step={0.01}
            onChange={(v) => onChange("shorts_9_16_aspect_tolerance", v)}
            help="How strictly width÷height must match 9÷16. Lower = only near-perfect Shorts; higher = slightly wider/narrower portrait still excluded."
          />
          <SliderSetting
            label="Prefer channels with min subscribers" value={settings.prefer_min_subscribers} min={0} max={100000} step={1000}
            onChange={(v) => onChange("prefer_min_subscribers", v)}
            help="Channels below this subscriber count receive a lower authority score (0.3 instead of 0.5) in ranking. They're not excluded — just ranked lower. Set to 0 to treat all channels equally."
          />
          <SliderSetting
            label="Full recency score within years" value={settings.recency_full_score_years} min={1} max={10}
            onChange={(v) => onChange("recency_full_score_years", v)} unit=" yr"
            help="Videos published within this many years get full recency score (1.0). Videos 2–4 years old get 0.7, older get 0.4. Increase if you work with historical/archival content."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ranking Weights</CardTitle>
          <p className="text-xs text-muted-foreground">Each clip is scored across seven dimensions. Adjust weights, then save — values auto-normalize to sum to 1.0.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <SliderSetting
            label="AI confidence (matcher)" value={settings.weight_ai_confidence} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_ai_confidence", v)}
            help="Confidence from the timestamp matcher (transcript fit). Usually the strongest signal for clip quality."
          />
          <SliderSetting
            label="Visual / topical fit" value={settings.weight_fit_score} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_fit_score", v)}
            help="Intent-weighted visual_fit and topical_fit from matching — how well the clip matches the shot visually and by topic."
          />
          <SliderSetting
            label="View count (viral)" value={settings.weight_viral_score} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_viral_score", v)}
            help="Tiers: &gt;1M views = 1.0, &gt;100K = 0.8, &gt;10K = 0.5, below = 0.2."
          />
          <SliderSetting
            label="Channel authority" value={settings.weight_channel_authority} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_channel_authority", v)}
            help="Tier 1 preferred = 1.0, Tier 2 = 0.9, subscriber tiers below."
          />
          <SliderSetting
            label="Caption quality" value={settings.weight_caption_quality} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_caption_quality", v)}
            help="Manual captions = 1.0, auto = 0.8, Whisper = 0.6, none = 0.3."
          />
          <SliderSetting
            label="Recency" value={settings.weight_recency} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_recency", v)}
            help="Based on publish date vs &apos;Full recency score&apos; setting above."
          />
          <SliderSetting
            label="Script context relevance" value={settings.weight_context_relevance} min={0} max={1} step={0.05}
            onChange={(v) => normalizeWeights("weight_context_relevance", v)}
            help="How well the video title aligns with script topic / geography (light keyword overlap signal)."
          />

          <div className="flex gap-px h-6 mt-2 rounded overflow-hidden">
            <div className="bg-cyan-500" style={{ width: `${(settings.weight_ai_confidence ?? 0) * 100}%` }} title="AI confidence" />
            <div className="bg-blue-500" style={{ width: `${(settings.weight_fit_score ?? 0) * 100}%` }} title="Fit score" />
            <div className="bg-green-500" style={{ width: `${(settings.weight_viral_score ?? 0) * 100}%` }} title="Viral" />
            <div className="bg-yellow-500" style={{ width: `${(settings.weight_channel_authority ?? 0) * 100}%` }} title="Authority" />
            <div className="bg-purple-500" style={{ width: `${(settings.weight_caption_quality ?? 0) * 100}%` }} title="Caption" />
            <div className="bg-red-500" style={{ width: `${(settings.weight_recency ?? 0) * 100}%` }} title="Recency" />
            <div className="bg-orange-500" style={{ width: `${(settings.weight_context_relevance ?? 0) * 100}%` }} title="Context" />
          </div>
          <div className="flex flex-wrap text-xs text-muted-foreground gap-3">
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-cyan-500 rounded-full" />AI confidence</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-blue-500 rounded-full" />Fit</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-green-500 rounded-full" />Viral</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-yellow-500 rounded-full" />Authority</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-purple-500 rounded-full" />Caption</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500 rounded-full" />Recency</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 bg-orange-500 rounded-full" />Context</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Performance</CardTitle>
          <p className="text-xs text-muted-foreground">Controls parallelism, timeouts, and recovery behavior. Tune these if jobs are too slow or timing out.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <SliderSetting
            label="Segment timeout" value={settings.segment_timeout_sec} min={60} max={600}
            onChange={(v) => onChange("segment_timeout_sec", v)} unit="s"
            help="Max time allowed for recovery search per scene. If a scene takes longer (e.g., slow Whisper transcription), it's skipped and the pipeline moves on."
          />
        </CardContent>
      </Card>
    </div>
  )
}

/* ─── Instructions Tab ──────────────────────────────────────────────── */

function InstructionsTab({ settings, onChange }: { settings: PipelineSettings; onChange: (k: string, v: unknown) => void }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Custom Instructions</CardTitle>
          <p className="text-xs text-muted-foreground">These instructions guide two stages of the pipeline: (1) Search query generation — influences what kind of videos the system looks for, and (2) Timestamp matching — influences which moments are selected from each video. Changes apply to all new jobs. Running jobs are not affected.</p>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            rows={10}
            value={settings.special_instructions || ""}
            onChange={(e) => onChange("special_instructions", e.target.value)}
            className="text-sm font-mono"
          />
          <p className="text-[11px] text-muted-foreground/70">
            Write in plain English. Each line is a separate instruction. Be specific — e.g., &quot;Prefer aerial/drone shots over talking heads&quot; or &quot;Avoid footage with visible watermarks or logos&quot;.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Context-Matching Rules</CardTitle>
          <p className="text-xs text-muted-foreground">Quality checks applied after the AI finds a timestamp. These catch bad clips before they reach your results — invalid timestamps, end screens, and unusable short clips.</p>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Enable context-matching validation</Label>
              <Switch
                checked={settings.enable_context_matching}
                onCheckedChange={(v) => onChange("enable_context_matching", v)}
              />
            </div>
            <p className="text-[11px] text-muted-foreground/70 leading-tight">Master switch for all validation rules below. When off, every AI-returned timestamp is accepted as-is without checking if it falls past the video end, lands on an end screen, or is too short. Keep this on unless you want raw unfiltered results.</p>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Discard clips shorter than 10 seconds</Label>
              <Switch
                checked={settings.discard_clips_shorter_than_10s}
                onCheckedChange={(v) => onChange("discard_clips_shorter_than_10s", v)}
              />
            </div>
            <p className="text-[11px] text-muted-foreground/70 leading-tight">Filters out clips where the AI-detected start and end timestamps are less than 10 seconds apart. These are usually too short for usable B-roll. Turn off if you need very short insert shots.</p>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Verify timestamp doesn&apos;t land on end screen</Label>
              <Switch
                checked={settings.verify_timestamp_not_end_screen}
                onCheckedChange={(v) => onChange("verify_timestamp_not_end_screen", v)}
              />
            </div>
            <p className="text-[11px] text-muted-foreground/70 leading-tight">If the AI picks a start timestamp in the last 30 seconds of a video, it&apos;s likely an end screen (&quot;subscribe&quot; cards, credits, etc.). This applies a -0.3 confidence penalty, pushing those clips down in ranking.</p>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Cap end timestamp at video duration - 5s</Label>
              <Switch
                checked={settings.cap_end_timestamp}
                onCheckedChange={(v) => onChange("cap_end_timestamp", v)}
              />
            </div>
            <p className="text-[11px] text-muted-foreground/70 leading-tight">If the AI returns an end timestamp past the actual video length (e.g., hallucinated timestamps), this clamps it to 5 seconds before the video ends. Prevents download errors and ensures the clip is within valid range.</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
