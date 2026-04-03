"use client"

import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { Navbar } from "@/components/navbar"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import {
  Search, ExternalLink, Copy, Eye, Star, Clock,
  Database, FileText, Loader2, Sparkles, Library as LibraryIcon,
  Film, Filter, X, ArrowUpDown, ChevronDown, ChevronUp,
} from "lucide-react"
import type {
  LibraryClip, LibraryStats, LibraryCategoryCount, LibrarySearchResponse,
} from "@/lib/types"

const API_BASE = "/api/v1"

const SORT_OPTIONS = [
  { value: "relevance", label: "Relevance" },
  { value: "rating", label: "Highest Rated" },
  { value: "views", label: "Most Views" },
  { value: "recent", label: "Most Recent" },
  { value: "added", label: "Recently Added" },
] as const

const RATING_OPTIONS = [
  { value: "", label: "Any" },
  { value: "3", label: "3+" },
  { value: "4", label: "4+" },
  { value: "5", label: "5 only" },
] as const

const USED_OPTIONS = [
  { value: "", label: "All" },
  { value: "used", label: "Used before" },
  { value: "unused", label: "Not used yet" },
] as const

const FIXED_CATEGORIES = [
  { value: "history", label: "History" },
  { value: "mystery", label: "Mystery" },
  { value: "current_affairs", label: "Current Affairs" },
  { value: "science", label: "Science" },
  { value: "finance", label: "Finance" },
  { value: "ai_tech", label: "AI & Tech" },
  { value: "geo_politics", label: "Geo Politics" },
  { value: "societal_issues", label: "Societal Issues" },
  { value: "sports", label: "Sports" },
] as const

function formatViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return n.toString()
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

function scoreColor(score: number): string {
  if (score >= 0.7) return "bg-green-500/20 text-green-400 border-green-500/30"
  if (score >= 0.4) return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
  return "bg-red-500/20 text-red-400 border-red-500/30"
}

function ratingStars(rating: number): string {
  return "★".repeat(rating) + "☆".repeat(5 - rating)
}

export default function LibraryPage() {
  const [query, setQuery] = useState("")
  const [mode, setMode] = useState<"metadata" | "deep">("metadata")
  const [sort, setSort] = useState("relevance")
  const [minRating, setMinRating] = useState("")
  const [used, setUsed] = useState("")
  const [activeCategories, setActiveCategories] = useState<Set<string>>(new Set())
  const [showFilters, setShowFilters] = useState(false)

  const [results, setResults] = useState<LibraryClip[]>([])
  const [stats, setStats] = useState<LibraryStats | null>(null)
  const [categories, setCategories] = useState<LibraryCategoryCount[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null)

  // Autocomplete
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [allTitles, setAllTitles] = useState<string[]>([])
  const searchRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  const fetchStats = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/library/stats`)
      if (resp.ok) {
        const data = await resp.json()
        setStats(data)
      }
    } catch { /* silent */ }
  }, [])

  // Actual search function that takes explicit params to avoid stale closures
  const executeSearch = useCallback(async (params: {
    q?: string
    searchMode?: "metadata" | "deep"
    sortBy?: string
    rating?: string
    usedFilter?: string
    cats?: Set<string>
    pageNum?: number
  }) => {
    const q = (params.q ?? query).trim()
    const searchMode = params.searchMode ?? mode
    const sortBy = params.sortBy ?? sort
    const rating = params.rating ?? minRating
    const usedFilter = params.usedFilter ?? used
    const cats = params.cats ?? activeCategories
    const pageNum = params.pageNum ?? page

    setLoading(true)
    try {
      if (searchMode === "deep" && q) {
        const resp = await fetch(`${API_BASE}/library/deep-search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q, max_results: 20 }),
        })
        if (resp.ok) {
          const data: LibrarySearchResponse = await resp.json()
          setResults(data.results)
          setTotal(data.total)
          if (data.stats) setStats(data.stats)
          setCategories(data.categories || [])
        }
      } else {
        const urlParams = new URLSearchParams()
        if (q) urlParams.set("q", q)
        if (cats.size > 0) urlParams.set("categories", Array.from(cats).join(","))
        if (rating) urlParams.set("min_rating", rating)
        if (usedFilter) urlParams.set("used", usedFilter)
        urlParams.set("sort", sortBy)
        urlParams.set("page", String(pageNum))
        urlParams.set("per_page", "50")

        const resp = await fetch(`${API_BASE}/library/search?${urlParams.toString()}`)
        if (resp.ok) {
          const data: LibrarySearchResponse = await resp.json()
          setResults(data.results)
          setTotal(data.total)
          if (data.stats) setStats(data.stats)
          setCategories(data.categories || [])
        }
      }
    } catch { /* silent */ }
    setLoading(false)
    setSearched(true)
  }, [query, mode, sort, minRating, used, activeCategories, page])

  // Initial load
  useEffect(() => {
    fetchStats()
    executeSearch({ pageNum: 1 })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-search when filters/sort/page change (but NOT on query change — that requires Enter/click)
  useEffect(() => {
    if (!searched) return
    executeSearch({ pageNum: page })
  }, [sort, minRating, used, activeCategories, page]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = () => {
    setPage(1)
    executeSearch({ pageNum: 1 })
    setShowSuggestions(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch()
    } else if (e.key === "Escape") {
      setShowSuggestions(false)
    }
  }

  const toggleCategory = (cat: string) => {
    setActiveCategories(prev => {
      const next = new Set(prev)
      if (next.has(cat)) next.delete(cat)
      else next.add(cat)
      return next
    })
    setPage(1)
  }

  const clearAllFilters = () => {
    setActiveCategories(new Set())
    setMinRating("")
    setUsed("")
    setSort("relevance")
    setPage(1)
  }

  const copyUrl = async (clip: LibraryClip) => {
    const url = clip.clip_url || clip.video_url
    await navigator.clipboard.writeText(url)
    setCopyFeedback(clip.result_id)
    setTimeout(() => setCopyFeedback(null), 1500)
  }

  // Build autocomplete index from results
  useEffect(() => {
    if (results.length === 0) return
    const titles = new Set<string>()
    for (const clip of results) {
      if (clip.video_title) titles.add(clip.video_title)
      if (clip.channel_name) titles.add(clip.channel_name)
      const cats = clip.categories || []
      for (const c of cats) {
        const label = FIXED_CATEGORIES.find(fc => fc.value === c)?.label ?? c
        titles.add(label)
      }
    }
    setAllTitles(Array.from(titles))
  }, [results])

  // Autocomplete filtering
  const handleQueryChange = (value: string) => {
    setQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      if (value.trim().length >= 2 && allTitles.length > 0) {
        const lower = value.trim().toLowerCase()
        const matches = allTitles
          .filter(t => t.toLowerCase().includes(lower))
          .slice(0, 8)
        setSuggestions(matches)
        setShowSuggestions(matches.length > 0)
      } else {
        setShowSuggestions(false)
      }
    }, 150)
  }

  const selectSuggestion = (s: string) => {
    setQuery(s)
    setShowSuggestions(false)
    setPage(1)
    executeSearch({ q: s, pageNum: 1 })
  }

  // Close suggestions on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const groupedResults = useMemo(() => {
    const groups = new Map<string, LibraryClip[]>()
    for (const clip of results) {
      const cats = clip.categories && clip.categories.length > 0
        ? clip.categories
        : ["uncategorized"]
      for (const cat of cats) {
        if (!groups.has(cat)) groups.set(cat, [])
        groups.get(cat)!.push(clip)
      }
    }
    return groups
  }, [results])

  const hasActiveFilters = activeCategories.size > 0 || minRating || used

  const isEmpty = stats && stats.clips_found === 0

  const totalPages = Math.ceil(total / 50)

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Navbar />
      <main className="max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Page header */}
        <div className="flex items-center gap-3 mb-6">
          <Film className="w-7 h-7 text-primary" />
          <div>
            <h1 className="text-2xl font-bold">B-Roll Library</h1>
            <p className="text-sm text-muted-foreground">
              Search across all previously discovered clips, transcripts, and matches
            </p>
          </div>
        </div>

        {/* Stats row */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <StatCard icon={Eye} label="Videos Indexed" value={stats.videos_indexed} color="text-blue-400" />
            <StatCard icon={Film} label="Clips Found" value={stats.clips_found} color="text-purple-400" />
            <StatCard icon={FileText} label="Transcripts Cached" value={stats.transcripts_cached} color="text-emerald-400" />
            <StatCard icon={Star} label="Editor Rated" value={stats.editor_rated} color="text-amber-400" />
          </div>
        )}

        {isEmpty ? (
          <EmptyLibrary />
        ) : (
          <>
            {/* Search bar with autocomplete */}
            <div className="flex flex-col gap-3 mb-4">
              <div className="flex gap-2">
                <div className="relative flex-1" ref={searchRef}>
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    value={query}
                    onChange={e => handleQueryChange(e.target.value)}
                    onKeyDown={handleKeyDown}
                    onFocus={() => {
                      if (suggestions.length > 0 && query.trim().length >= 2) setShowSuggestions(true)
                    }}
                    placeholder="Search transcripts, topics, channels..."
                    className="pl-10"
                  />
                  {query && (
                    <button
                      onClick={() => { setQuery(""); setShowSuggestions(false) }}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {/* Autocomplete dropdown */}
                  {showSuggestions && suggestions.length > 0 && (
                    <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-popover border border-border rounded-lg shadow-lg overflow-hidden">
                      {suggestions.map((s, i) => (
                        <button
                          key={i}
                          onClick={() => selectSuggestion(s)}
                          className="w-full text-left px-4 py-2 text-sm hover:bg-accent transition-colors truncate"
                        >
                          <Search className="w-3 h-3 inline mr-2 text-muted-foreground" />
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <Button onClick={handleSearch} disabled={loading}>
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  <span className="ml-2 hidden sm:inline">Search</span>
                </Button>
              </div>

              {/* Mode toggle + filter toggle */}
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-1 p-1 bg-muted rounded-lg">
                  <button
                    onClick={() => { setMode("metadata"); if (searched) { setPage(1); executeSearch({ searchMode: "metadata", pageNum: 1 }) } }}
                    className={cn(
                      "px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                      mode === "metadata" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    Metadata
                  </button>
                  <button
                    onClick={() => { setMode("deep"); if (searched && query.trim()) { setPage(1); executeSearch({ searchMode: "deep", pageNum: 1 }) } }}
                    className={cn(
                      "px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                      mode === "deep" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <Sparkles className="w-3 h-3 inline mr-1" />
                    Deep Search
                  </button>
                </div>
                {mode === "deep" && (
                  <span className="text-xs text-muted-foreground">
                    Searches cached transcripts via local LLM — no API calls, 10-30s
                  </span>
                )}
                <div className="flex items-center gap-2 ml-auto">
                  {hasActiveFilters && (
                    <button
                      onClick={clearAllFilters}
                      className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300"
                    >
                      <X className="w-3 h-3" />
                      Clear filters
                    </button>
                  )}
                  <button
                    onClick={() => setShowFilters(!showFilters)}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  >
                    <Filter className="w-3.5 h-3.5" />
                    Filters
                    {showFilters ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  </button>
                </div>
              </div>
            </div>

            {/* Category pills */}
            <div className="flex flex-wrap gap-2 mb-4">
              <button
                onClick={() => { setActiveCategories(new Set()); setPage(1) }}
                className={cn(
                  "px-3 py-1 text-xs font-medium rounded-full border transition-colors",
                  activeCategories.size === 0
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-muted text-muted-foreground border-border hover:text-foreground"
                )}
              >
                All clips
              </button>
              {FIXED_CATEGORIES.map(cat => {
                const apiCat = categories.find(c => c.name === cat.value)
                const count = apiCat?.count ?? 0
                return (
                  <button
                    key={cat.value}
                    onClick={() => toggleCategory(cat.value)}
                    className={cn(
                      "px-3 py-1 text-xs font-medium rounded-full border transition-colors",
                      activeCategories.has(cat.value)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-muted text-muted-foreground border-border hover:text-foreground"
                    )}
                  >
                    {cat.label}{count > 0 ? ` (${count})` : ""}
                  </button>
                )
              })}
            </div>

            {/* Advanced filters */}
            {showFilters && (
              <div className="flex flex-wrap gap-4 mb-4 p-4 rounded-lg border border-border bg-card">
                <FilterSelect
                  label="Min rating"
                  value={minRating}
                  onChange={v => { setMinRating(v); setPage(1) }}
                  options={RATING_OPTIONS}
                />
                <FilterSelect
                  label="Used in video"
                  value={used}
                  onChange={v => { setUsed(v); setPage(1) }}
                  options={USED_OPTIONS}
                />
                <FilterSelect
                  label="Sort by"
                  value={sort}
                  onChange={v => { setSort(v); setPage(1) }}
                  options={SORT_OPTIONS}
                />
              </div>
            )}

            {/* Results header */}
            <div className="flex items-center justify-between mb-4">
              <span className="text-sm text-muted-foreground">
                {total > 0 ? (
                  <>
                    <span className="font-medium text-foreground">{total}</span> clips
                    {query.trim() && <> matching &ldquo;{query.trim()}&rdquo;</>}
                    {activeCategories.size > 0 && (
                      <> in {Array.from(activeCategories).map(c => FIXED_CATEGORIES.find(fc => fc.value === c)?.label ?? c).join(", ")}</>
                    )}
                  </>
                ) : searched ? (
                  "No clips found"
                ) : (
                  "Showing all library clips"
                )}
              </span>
              <div className="flex items-center gap-2">
                <ArrowUpDown className="w-3.5 h-3.5 text-muted-foreground" />
                <select
                  value={sort}
                  onChange={e => { setSort(e.target.value); setPage(1) }}
                  className="text-xs bg-muted border border-border rounded px-2 py-1"
                >
                  {SORT_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Results */}
            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
                <span className="text-sm text-muted-foreground">
                  {mode === "deep" ? "Searching cached transcripts via local LLM..." : "Searching library..."}
                </span>
              </div>
            ) : results.length === 0 && searched ? (
              <NoResults query={query} hasFilters={hasActiveFilters} onClear={clearAllFilters} />
            ) : (
              <div className="space-y-8">
                {Array.from(groupedResults.entries()).map(([category, clips]) => (
                  <div key={category}>
                    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-2">
                      <Database className="w-3.5 h-3.5" />
                      {FIXED_CATEGORIES.find(c => c.value === category)?.label ?? category} — {clips.length} clip{clips.length !== 1 ? "s" : ""}
                    </h3>
                    <div className="space-y-3">
                      {clips.map((clip, idx) => (
                        <ClipCard
                          key={`${clip.result_id}_${clip.job_id ?? idx}`}
                          clip={clip}
                          onCopy={() => copyUrl(clip)}
                          copied={copyFeedback === clip.result_id}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex justify-center items-center gap-2 mt-8">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage(p => p - 1)}
                >
                  Previous
                </Button>
                <div className="flex items-center gap-1">
                  {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                    let pageNum: number
                    if (totalPages <= 7) {
                      pageNum = i + 1
                    } else if (page <= 4) {
                      pageNum = i + 1
                    } else if (page >= totalPages - 3) {
                      pageNum = totalPages - 6 + i
                    } else {
                      pageNum = page - 3 + i
                    }
                    return (
                      <button
                        key={pageNum}
                        onClick={() => setPage(pageNum)}
                        disabled={loading}
                        className={cn(
                          "w-8 h-8 text-xs rounded-md transition-colors",
                          pageNum === page
                            ? "bg-primary text-primary-foreground"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground"
                        )}
                      >
                        {pageNum}
                      </button>
                    )
                  })}
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage(p => p + 1)}
                >
                  Next
                </Button>
                <span className="text-xs text-muted-foreground ml-2">
                  {total} clips
                </span>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}


function StatCard({ icon: Icon, label, value, color }: {
  icon: React.ElementType
  label: string
  value: number
  color: string
}) {
  return (
    <Card className="bg-card border-border">
      <CardContent className="p-4 flex items-center gap-3">
        <Icon className={cn("w-5 h-5", color)} />
        <div>
          <p className="text-2xl font-bold">{value.toLocaleString()}</p>
          <p className="text-xs text-muted-foreground">{label}</p>
        </div>
      </CardContent>
    </Card>
  )
}


function ClipCard({ clip, onCopy, copied }: {
  clip: LibraryClip
  onCopy: () => void
  copied: boolean
}) {
  const clipUrl = clip.clip_url || clip.video_url
  const start = clip.start_time_seconds ?? 0
  const end = clip.end_time_seconds
  const timeRange = end ? `${formatTime(start)}-${formatTime(end)}` : formatTime(start)

  return (
    <Card className="bg-card border-border hover:border-primary/30 transition-colors">
      <CardContent className="p-4">
        <div className="flex gap-4">
          {/* Thumbnail */}
          <a
            href={clipUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-shrink-0 relative w-40 h-[90px] rounded-lg overflow-hidden bg-muted group"
          >
            <img
              src={clip.thumbnail_url || `https://img.youtube.com/vi/${clip.video_id}/mqdefault.jpg`}
              alt=""
              className="w-full h-full object-cover group-hover:scale-105 transition-transform"
            />
            <span className="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
              {timeRange}
            </span>
          </a>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <a
              href={clipUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-sm hover:text-primary transition-colors line-clamp-1"
            >
              {clip.video_title}
            </a>
            <p className="text-xs text-muted-foreground mt-0.5">
              {clip.channel_name}
              {clip.view_count > 0 && <> · {formatViews(clip.view_count)} views</>}
              {clip.published_at && <> · {clip.published_at.slice(0, 10)}</>}
            </p>

            {/* Badges */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {clip.relevance_score > 0 && (
                <Badge variant="outline" className={cn("text-[10px]", scoreColor(clip.relevance_score))}>
                  {clip.relevance_score.toFixed(2)} match
                </Badge>
              )}
              {clip.view_count > 0 && (
                <Badge variant="outline" className="text-[10px] bg-blue-500/10 text-blue-400 border-blue-500/20">
                  {formatViews(clip.view_count)} views
                </Badge>
              )}
              {clip.editor_rating != null && (
                <Badge variant="outline" className="text-[10px] bg-amber-500/10 text-amber-400 border-amber-500/20">
                  {ratingStars(clip.editor_rating)}
                </Badge>
              )}
              {clip.clip_used && (
                <Badge variant="outline" className="text-[10px] bg-green-500/10 text-green-400 border-green-500/20">
                  Used
                </Badge>
              )}
              {clip.categories?.map(cat => (
                <Badge key={cat} variant="outline" className="text-[10px] bg-muted text-muted-foreground border-border">
                  {FIXED_CATEGORIES.find(c => c.value === cat)?.label ?? cat}
                </Badge>
              ))}
            </div>

            {/* Hook */}
            {clip.the_hook && (
              <p className="text-xs italic text-muted-foreground mt-2 line-clamp-2">
                &ldquo;{clip.the_hook}&rdquo;
              </p>
            )}

            {/* Actions */}
            <div className="flex items-center gap-2 mt-2">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <a
                      href={clipUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Open clip at {formatTime(start)}
                    </a>
                  </TooltipTrigger>
                  <TooltipContent>Opens YouTube at the matched timestamp</TooltipContent>
                </Tooltip>
              </TooltipProvider>

              <button
                onClick={onCopy}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <Copy className="w-3 h-3" />
                {copied ? "Copied!" : "Copy URL"}
              </button>
            </div>

            {/* Job context */}
            {clip.job_title && (
              <p className="text-[10px] text-muted-foreground mt-1.5">
                From: {clip.job_title}
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}


function FilterSelect({ label, value, onChange, options }: {
  label: string
  value: string
  onChange: (v: string) => void
  options: ReadonlyArray<{ value: string; label: string }>
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-muted-foreground">{label}</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="text-xs bg-muted border border-border rounded px-2 py-1.5 min-w-[120px]"
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  )
}


function EmptyLibrary() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <LibraryIcon className="w-16 h-16 text-muted-foreground/40 mb-4" />
      <h2 className="text-xl font-semibold mb-2">Your B-Roll library is empty</h2>
      <p className="text-sm text-muted-foreground max-w-md">
        Run your first Scout job to start building your library.
        Every video the system finds gets indexed here for instant reuse.
      </p>
    </div>
  )
}


function NoResults({ query, hasFilters, onClear }: { query: string; hasFilters: boolean; onClear: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Search className="w-12 h-12 text-muted-foreground/40 mb-4" />
      <h3 className="text-lg font-semibold mb-1">No clips found</h3>
      <p className="text-sm text-muted-foreground max-w-md">
        {query.trim()
          ? <>No clips found for &ldquo;{query.trim()}&rdquo;. Try broader search terms, or run a Scout job to find new clips.</>
          : "Try different filters or run more Scout jobs to grow your library."
        }
      </p>
      {hasFilters && (
        <Button variant="outline" size="sm" onClick={onClear} className="mt-4">
          <X className="w-3.5 h-3.5 mr-1" />
          Clear all filters
        </Button>
      )}
    </div>
  )
}
