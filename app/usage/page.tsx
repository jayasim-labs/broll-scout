"use client"

import { useEffect, useState, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  DollarSign, RefreshCw, Brain, Mic, Search, Sparkles, Globe,
  ArrowUpRight, Clock, Zap, TrendingUp, Loader2,
} from "lucide-react"
import type { UsageData, UsagePeriod } from "@/lib/types"

function emptyPeriod(): UsagePeriod {
  return {
    openai_calls: 0,
    openai_mini_calls: 0,
    openai_input_tokens: 0,
    openai_output_tokens: 0,
    gpt4o_input_tokens: 0,
    gpt4o_output_tokens: 0,
    gpt4o_mini_input_tokens: 0,
    gpt4o_mini_output_tokens: 0,
    whisper_minutes: 0,
    whisper_calls: 0,
    youtube_api_units: 0,
    google_cse_calls: 0,
    gemini_calls: 0,
    ytdlp_searches: 0,
    ytdlp_detail_lookups: 0,
    estimated_cost_usd: 0,
    job_count: 0,
  }
}

function fmt(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M"
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K"
  return n.toFixed(0)
}

function CostCard({
  title,
  icon: Icon,
  period,
  highlight,
}: {
  title: string
  icon: React.ElementType
  period: UsagePeriod
  highlight?: boolean
}) {
  return (
    <Card className={highlight ? "border-primary/40 bg-primary/5" : ""}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
          <Icon className="w-4 h-4 text-muted-foreground" />
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">
          ${period.estimated_cost_usd.toFixed(4)}
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          {period.job_count} job{period.job_count !== 1 ? "s" : ""} processed
        </p>
      </CardContent>
    </Card>
  )
}

function ServiceBreakdown({ period, pricing }: { period: UsagePeriod; pricing: Record<string, number> }) {
  const gpt4oIn = period.gpt4o_input_tokens || 0
  const gpt4oOut = period.gpt4o_output_tokens || 0
  const miniIn = period.gpt4o_mini_input_tokens || 0
  const miniOut = period.gpt4o_mini_output_tokens || 0

  const gpt4oCost =
    (gpt4oIn / 1000) * (pricing.gpt4o_input_per_1k || 0.005) +
    (gpt4oOut / 1000) * (pricing.gpt4o_output_per_1k || 0.015)
  const miniCost =
    (miniIn / 1000) * (pricing.gpt4o_mini_input_per_1k || 0.00015) +
    (miniOut / 1000) * (pricing.gpt4o_mini_output_per_1k || 0.0006)
  const whisperCost = period.whisper_minutes * (pricing.whisper_per_minute || 0.006)
  const geminiCost = period.gemini_calls * (pricing.gemini_flash_per_call || 0.0001)

  const services = [
    {
      name: "GPT-4o (Translation)",
      icon: Brain,
      calls: period.openai_calls || 0,
      tokens: `${fmt(gpt4oIn)} in / ${fmt(gpt4oOut)} out`,
      cost: gpt4oCost,
      color: "text-violet-500",
      bg: "bg-violet-500/10",
    },
    {
      name: "GPT-4o-mini (Timestamps)",
      icon: Zap,
      calls: period.openai_mini_calls || 0,
      tokens: `${fmt(miniIn)} in / ${fmt(miniOut)} out`,
      cost: miniCost,
      color: "text-blue-500",
      bg: "bg-blue-500/10",
    },
    {
      name: "Whisper (Transcription)",
      icon: Mic,
      calls: period.whisper_calls,
      tokens: `${period.whisper_minutes.toFixed(1)} minutes transcribed`,
      cost: whisperCost,
      color: "text-green-500",
      bg: "bg-green-500/10",
    },
    {
      name: "Gemini Flash (Expansion)",
      icon: Sparkles,
      calls: period.gemini_calls,
      tokens: "Query expansion calls",
      cost: geminiCost,
      color: "text-amber-500",
      bg: "bg-amber-500/10",
    },
    {
      name: "YouTube Data API",
      icon: Search,
      calls: Math.round(period.youtube_api_units / 100),
      tokens: `${fmt(period.youtube_api_units)} quota units used`,
      cost: 0,
      color: "text-red-500",
      bg: "bg-red-500/10",
      note: "Free tier (10K units/day)",
    },
    {
      name: "yt-dlp (Local Search)",
      icon: Globe,
      calls: period.ytdlp_searches + period.ytdlp_detail_lookups,
      tokens: `${period.ytdlp_searches} searches, ${period.ytdlp_detail_lookups} lookups`,
      cost: 0,
      color: "text-emerald-500",
      bg: "bg-emerald-500/10",
      note: "Free (runs locally)",
    },
  ]

  return (
    <div className="space-y-3">
      {services.map((svc) => (
        <div
          key={svc.name}
          className="flex items-center justify-between p-3 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${svc.bg}`}>
              <svc.icon className={`w-4 h-4 ${svc.color}`} />
            </div>
            <div>
              <p className="font-medium text-sm">{svc.name}</p>
              <p className="text-xs text-muted-foreground">{svc.tokens}</p>
            </div>
          </div>
          <div className="text-right">
            <p className="font-mono text-sm font-medium">
              {svc.cost > 0 ? `$${svc.cost.toFixed(4)}` : "—"}
            </p>
            <p className="text-xs text-muted-foreground">
              {svc.calls} call{svc.calls !== 1 ? "s" : ""}
              {svc.note && (
                <Badge variant="outline" className="ml-2 text-[10px] py-0 px-1">
                  {svc.note}
                </Badge>
              )}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

function AWSCostEstimate() {
  const monthlyCosts = [
    { name: "EC2 (t3.small)", cost: 15.18, note: "24/7 on-demand" },
    { name: "DynamoDB", cost: 2.50, note: "On-demand, ~8 tables" },
    { name: "Secrets Manager", cost: 0.40, note: "Per secret/month" },
    { name: "Route 53", cost: 0.50, note: "Hosted zone" },
    { name: "Data Transfer", cost: 1.00, note: "Estimate" },
  ]
  const total = monthlyCosts.reduce((s, c) => s + c.cost, 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ArrowUpRight className="w-5 h-5 text-orange-500" />
          AWS Infrastructure (Monthly Estimate)
        </CardTitle>
        <CardDescription>
          Fixed infrastructure costs — not per-job
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {monthlyCosts.map((item) => (
            <div key={item.name} className="flex items-center justify-between py-1.5">
              <div>
                <p className="text-sm font-medium">{item.name}</p>
                <p className="text-xs text-muted-foreground">{item.note}</p>
              </div>
              <p className="font-mono text-sm">${item.cost.toFixed(2)}/mo</p>
            </div>
          ))}
          <div className="border-t pt-2 mt-2 flex items-center justify-between">
            <p className="font-semibold text-sm">Total AWS</p>
            <p className="font-mono font-bold text-base">${total.toFixed(2)}/mo</p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function UsagePage() {
  const [data, setData] = useState<UsageData | null>(null)
  const [loading, setLoading] = useState(true)
  const [recalculating, setRecalculating] = useState(false)

  const fetchUsage = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/usage", { cache: "no-store" })
      if (resp.ok) {
        setData(await resp.json())
      }
    } catch {
      /* silent */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchUsage()
    const iv = setInterval(fetchUsage, 60_000)
    return () => clearInterval(iv)
  }, [fetchUsage])

  const handleRecalculate = async () => {
    setRecalculating(true)
    try {
      const resp = await fetch("/api/v1/usage", { method: "POST" })
      if (resp.ok) {
        await fetchUsage()
      }
    } catch {
      /* silent */
    } finally {
      setRecalculating(false)
    }
  }

  const allTime = data?.all_time ?? emptyPeriod()
  const month = data?.current_month ?? emptyPeriod()
  const today = data?.today ?? emptyPeriod()
  const pricing = data?.pricing ?? {}

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="max-w-[1200px] mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">API Usage & Costs</h1>
          <p className="text-muted-foreground mt-1">
            Track spending across OpenAI, Whisper, Gemini, YouTube, and AWS
          </p>
          {allTime.last_calculated && (
            <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
              <Clock className="w-3 h-3" />
              Last calculated: {new Date(allTime.last_calculated).toLocaleString()}
              &nbsp;· Auto-refreshes every hour
            </p>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRecalculate}
          disabled={recalculating}
        >
          {recalculating ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4 mr-2" />
          )}
          Recalculate Now
        </Button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <CostCard title="All Time" icon={TrendingUp} period={allTime} highlight />
        <CostCard title="This Month" icon={DollarSign} period={month} />
        <CostCard title="Today" icon={Clock} period={today} />
      </div>

      {/* Service Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <DollarSign className="w-5 h-5 text-primary" />
              API Cost Breakdown (All Time)
            </CardTitle>
            <CardDescription>
              Per-service cost breakdown based on actual API token usage
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ServiceBreakdown period={allTime} pricing={pricing} />
          </CardContent>
        </Card>

        <div className="space-y-6">
          <AWSCostEstimate />

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Zap className="w-5 h-5 text-amber-500" />
                Pricing Reference
              </CardTitle>
              <CardDescription>Current API rates used for cost calculation</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">GPT-4o input</span>
                  <span className="font-mono">${pricing.gpt4o_input_per_1k || 0.005}/1K tokens</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">GPT-4o output</span>
                  <span className="font-mono">${pricing.gpt4o_output_per_1k || 0.015}/1K tokens</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">GPT-4o-mini input</span>
                  <span className="font-mono">${pricing.gpt4o_mini_input_per_1k || 0.00015}/1K tokens</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">GPT-4o-mini output</span>
                  <span className="font-mono">${pricing.gpt4o_mini_output_per_1k || 0.0006}/1K tokens</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Whisper</span>
                  <span className="font-mono">${pricing.whisper_per_minute || 0.006}/minute</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Gemini 1.5 Flash</span>
                  <span className="font-mono">${pricing.gemini_flash_per_call || 0.0001}/call</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">YouTube Data API</span>
                  <span className="font-mono">Free (10K units/day)</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">yt-dlp</span>
                  <span className="font-mono">Free (local)</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
