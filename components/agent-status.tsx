"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Wifi, WifiOff, Download, X, ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

const COMPANION_URL = "http://localhost:9876"
const POLL_INTERVAL_ACTIVE = 500
const POLL_INTERVAL_IDLE = 10000
const HEALTH_CHECK_INTERVAL = 15000

type AgentState = "connected" | "disconnected" | "checking"

interface CompanionHealth {
  status: string
  ytdlp_version?: string
  ytdlp_ok?: boolean
  ffmpeg_ok?: boolean
  ffmpeg_version?: string
  whisper_ok?: boolean
  cookie_status?: string
  ollama_available?: boolean
  ollama_server?: string
  matcher_model?: string
  model_loaded?: boolean
}

export function AgentStatusBadge() {
  const [state, setState] = useState<AgentState>("checking")
  const [health, setHealth] = useState<CompanionHealth | null>(null)
  const [showPanel, setShowPanel] = useState(false)

  useEffect(() => {
    checkCompanion()
    const interval = setInterval(checkCompanion, HEALTH_CHECK_INTERVAL)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (!showPanel) return
    function handleClick(e: MouseEvent) {
      const target = e.target as HTMLElement
      if (!target.closest("[data-agent-panel]")) setShowPanel(false)
    }
    document.addEventListener("click", handleClick)
    return () => document.removeEventListener("click", handleClick)
  }, [showPanel])

  async function checkCompanion() {
    try {
      const resp = await fetch(`${COMPANION_URL}/health`, { mode: "cors" })
      const data: CompanionHealth = await resp.json()
      if (data.status === "ok" || data.ytdlp_ok) {
        setState("connected")
        // Back-compat: older companion doesn't send *_ok booleans
        if (data.ytdlp_version && data.ytdlp_ok === undefined) {
          data.ytdlp_ok = true
        }
        setHealth(data)
      } else {
        setState("disconnected")
        setHealth(null)
      }
    } catch {
      setState("disconnected")
      setHealth(null)
    }
  }

  if (state === "checking") return null

  const activeCount = health
    ? [health.ytdlp_ok, health.ffmpeg_ok, health.whisper_ok, health.model_loaded].filter(Boolean).length
    : 0

  return (
    <div className="relative" data-agent-panel>
      <button
        onClick={() => setShowPanel(!showPanel)}
        className={cn(
          "flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full transition-colors",
          state === "connected"
            ? "bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20"
            : "bg-muted text-muted-foreground hover:bg-muted/80"
        )}
      >
        {state === "connected" ? (
          <Wifi className="w-3 h-3" />
        ) : (
          <WifiOff className="w-3 h-3" />
        )}
        <span className="hidden sm:inline">
          {state === "connected"
            ? `Agent · ${activeCount}/4`
            : "No Agent"}
        </span>
        <ChevronDown className={cn("w-3 h-3 transition-transform", showPanel && "rotate-180")} />
      </button>

      {showPanel && (
        <div className="absolute right-0 top-full mt-2 w-72 bg-popover border border-border rounded-lg shadow-xl z-50 p-3 space-y-2.5">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-semibold text-foreground">Local Agents</span>
            {state === "connected" ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400">Connected</span>
            ) : (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/15 text-red-400">Offline</span>
            )}
          </div>

          {state === "disconnected" ? (
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Companion app not running. Start <code className="text-[10px] bg-secondary px-1 rounded">companion.py</code> or double-click the Desktop shortcut.
            </p>
          ) : health && (
            <div className="space-y-1.5">
              <AgentRow
                name="yt-dlp"
                detail={health.ytdlp_ok ? `v${health.ytdlp_version}` : "Not installed"}
                ok={!!health.ytdlp_ok}
                description="YouTube search & video download"
              />
              <AgentRow
                name="ffmpeg"
                detail={health.ffmpeg_ok ? (health.ffmpeg_version || "installed") : "Not installed"}
                ok={!!health.ffmpeg_ok}
                description="Video clipping & audio extraction"
              />
              <AgentRow
                name="Whisper"
                detail={health.whisper_ok ? "base model" : "Not installed"}
                ok={!!health.whisper_ok}
                description="Local speech-to-text transcription"
              />
              <AgentRow
                name={health.matcher_model?.split(":")[0] || "Qwen3"}
                detail={
                  health.model_loaded
                    ? health.matcher_model || "qwen3:8b"
                    : health.ollama_server === "running"
                      ? "Model not pulled"
                      : "Ollama not running"
                }
                ok={!!health.model_loaded}
                description="Local LLM for timestamp matching ($0)"
              />
              {health.cookie_status && health.cookie_status !== "disabled" && (
                <div className="pt-1 border-t border-border/50">
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    <span>Cookies: {health.cookie_status}</span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AgentRow({ name, detail, ok, description }: {
  name: string
  detail: string
  ok: boolean
  description: string
}) {
  return (
    <div className="flex items-center gap-2.5 py-1">
      <div className={cn(
        "w-2 h-2 rounded-full shrink-0",
        ok ? "bg-emerald-400" : "bg-red-400/60"
      )} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-medium text-foreground">{name}</span>
          <span className={cn(
            "text-[10px] font-mono",
            ok ? "text-muted-foreground" : "text-red-400/80"
          )}>
            {detail}
          </span>
        </div>
        <p className="text-[10px] text-muted-foreground/60 leading-tight">{description}</p>
      </div>
    </div>
  )
}

export function AgentOnboardingBanner() {
  const [visible, setVisible] = useState(false)
  const [companionConnected, setCompanionConnected] = useState(false)

  useEffect(() => {
    const dismissed = localStorage.getItem("agent-banner-dismissed")
    if (dismissed) {
      const dismissedAt = parseInt(dismissed, 10)
      if (Date.now() - dismissedAt < 7 * 24 * 60 * 60 * 1000) return
    }
    checkAndShow()
  }, [])

  async function checkAndShow() {
    try {
      const resp = await fetch(`${COMPANION_URL}/health`, { mode: "cors" })
      const data = await resp.json()
      if (data.status === "ok") {
        setCompanionConnected(true)
        setVisible(false)
        return
      }
    } catch { /* not running */ }
    setVisible(true)
  }

  function dismiss() {
    localStorage.setItem("agent-banner-dismissed", Date.now().toString())
    setVisible(false)
  }

  if (!visible || companionConnected) return null

  return (
    <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-4 mb-6 relative">
      <button
        onClick={dismiss}
        className="absolute top-3 right-3 text-muted-foreground hover:text-foreground"
      >
        <X className="w-4 h-4" />
      </button>
      <div className="flex items-start gap-3">
        <Download className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
        <div className="space-y-2">
          <p className="text-sm font-medium">
            One-time setup: Install the B-Roll Scout companion app
          </p>
          <p className="text-xs text-muted-foreground">
            This small background app runs yt-dlp searches on your machine so YouTube
            doesn&apos;t block the server. Without it, searches are limited to ~3-4 jobs
            per day by the YouTube API quota.
          </p>
          <div className="flex gap-3 mt-2">
            <a
              href="https://github.com/jayasim-labs/BRoll-Scout/tree/main/broll-companion"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs bg-primary text-primary-foreground px-3 py-1.5 rounded-md hover:bg-primary/90 transition-colors"
            >
              Setup Instructions
            </a>
            <button
              onClick={dismiss}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Skip for now
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export function useAgentLoop(jobActive: boolean) {
  const runningRef = useRef(false)
  const jobActiveRef = useRef(jobActive)

  useEffect(() => {
    jobActiveRef.current = jobActive
  }, [jobActive])

  useEffect(() => {
    if (runningRef.current) return
    runningRef.current = true
    let cancelled = false

    async function loop() {
      while (!cancelled) {
        try {
          let companionOk = false
          try {
            const hResp = await fetch(`${COMPANION_URL}/health`, { mode: "cors" })
            const hData = await hResp.json()
            companionOk = hData.status === "ok"
          } catch { /* not running */ }

          if (!companionOk) {
            await sleep(POLL_INTERVAL_IDLE)
            continue
          }

          const pollResp = await fetch("/api/v1/agent/poll", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ agent_id: "browser-agent" }),
          })

          if (!pollResp.ok) {
            await sleep(jobActiveRef.current ? POLL_INTERVAL_ACTIVE : POLL_INTERVAL_IDLE)
            continue
          }

          const { tasks } = await pollResp.json()

          if (!tasks || tasks.length === 0) {
            await sleep(jobActiveRef.current ? POLL_INTERVAL_ACTIVE : POLL_INTERVAL_IDLE)
            continue
          }

          // Execute tasks sequentially to avoid overwhelming the single-threaded companion.
          // Whisper/clip tasks can take minutes, so running them in parallel blocks everything.
          for (const task of tasks) {
            try {
              const execResp = await fetch(`${COMPANION_URL}/execute`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(task),
                mode: "cors",
              })
              const execData = await execResp.json()

              await fetch("/api/v1/agent/result", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  task_id: task.task_id,
                  status: "completed",
                  result: execData.results || [],
                }),
              })
            } catch (e) {
              console.error("Agent task failed:", e)
              await fetch("/api/v1/agent/result", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  task_id: task.task_id as string,
                  status: "failed",
                  result: [],
                }),
              }).catch(() => {})
            }
          }
        } catch (e) {
          console.error("Agent loop error:", e)
          await sleep(POLL_INTERVAL_IDLE)
        }
      }
    }

    loop()
    return () => { cancelled = true; runningRef.current = false }
  }, [])
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}
