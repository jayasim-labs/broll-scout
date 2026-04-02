"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Wifi, WifiOff, Download, X } from "lucide-react"
import { cn } from "@/lib/utils"

const COMPANION_URL = "http://localhost:9876"
const POLL_INTERVAL_ACTIVE = 500    // fast polling when job is running
const POLL_INTERVAL_IDLE = 10000    // slow polling when no job
const HEALTH_CHECK_INTERVAL = 15000

type AgentState = "connected" | "disconnected" | "checking"

interface CompanionHealth {
  status: string
  ytdlp_version?: string
}

export function AgentStatusBadge() {
  const [state, setState] = useState<AgentState>("checking")
  const [version, setVersion] = useState<string>("")

  useEffect(() => {
    checkCompanion()
    const interval = setInterval(checkCompanion, HEALTH_CHECK_INTERVAL)
    return () => clearInterval(interval)
  }, [])

  async function checkCompanion() {
    try {
      const resp = await fetch(`${COMPANION_URL}/health`, { mode: "cors" })
      const data: CompanionHealth = await resp.json()
      if (data.status === "ok") {
        setState("connected")
        setVersion(data.ytdlp_version || "")
      } else {
        setState("disconnected")
      }
    } catch {
      setState("disconnected")
    }
  }

  if (state === "checking") return null

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 text-xs px-2 py-1 rounded-full",
        state === "connected"
          ? "bg-emerald-500/10 text-emerald-400"
          : "bg-muted text-muted-foreground"
      )}
      title={
        state === "connected"
          ? `Local agent connected (yt-dlp ${version})`
          : "Local agent not detected"
      }
    >
      {state === "connected" ? (
        <Wifi className="w-3 h-3" />
      ) : (
        <WifiOff className="w-3 h-3" />
      )}
      <span className="hidden sm:inline">
        {state === "connected" ? "Agent" : "No agent"}
      </span>
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

          await Promise.all(tasks.map(async (task: Record<string, unknown>) => {
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
                  task_id: task.task_id,
                  status: "failed",
                  result: [],
                }),
              }).catch(() => {})
            }
          }))
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
