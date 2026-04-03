"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Video, Settings, Home, FolderOpen, DollarSign, Cloud, Cpu, Film } from "lucide-react"
import { cn } from "@/lib/utils"
import { AgentStatusBadge } from "@/components/agent-status"
import { useEffect, useState, useCallback } from "react"

export function Navbar() {
  const pathname = usePathname()
  const [apiCost, setApiCost] = useState<number | null>(null)
  const [awsCost, setAwsCost] = useState<number | null>(null)

  const fetchCost = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/usage", { cache: "no-store" })
      if (resp.ok) {
        const data = await resp.json()
        setApiCost(data.current_month?.estimated_cost_usd ?? null)
        setAwsCost(data.aws_cost?.mtd ?? null)
      }
    } catch {
      /* silent */
    }
  }, [])

  useEffect(() => {
    fetchCost()
    const iv = setInterval(fetchCost, 60_000)
    return () => clearInterval(iv)
  }, [fetchCost])

  return (
    <nav className="bg-card border-b border-border sticky top-0 z-50">
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <Link href="/" className="flex items-center gap-3 hover:opacity-80 transition-opacity">
            <Video className="w-8 h-8 text-primary" />
            <div>
              <span className="text-xl font-bold">B-Roll Scout</span>
              <span className="hidden sm:inline text-xs text-muted-foreground ml-2">
                Beyond the Obvious
              </span>
            </div>
          </Link>

          <div className="flex items-center gap-4">
            <AgentStatusBadge />
            <Link
              href="/"
              className={cn(
                "text-muted-foreground hover:text-foreground transition-colors flex items-center gap-2",
                pathname === "/" && "text-foreground"
              )}
            >
              <Home className="w-5 h-5" />
              <span className="hidden sm:inline">Scout</span>
            </Link>
            <Link
              href="/library"
              className={cn(
                "text-muted-foreground hover:text-foreground transition-colors flex items-center gap-2",
                pathname === "/library" && "text-foreground"
              )}
            >
              <Film className="w-5 h-5" />
              <span className="hidden sm:inline">Library</span>
            </Link>
            <Link
              href="/projects"
              className={cn(
                "text-muted-foreground hover:text-foreground transition-colors flex items-center gap-2",
                pathname === "/projects" && "text-foreground"
              )}
            >
              <FolderOpen className="w-5 h-5" />
              <span className="hidden sm:inline">Projects</span>
            </Link>
            <Link
              href="/usage"
              className={cn(
                "text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5",
                pathname === "/usage" && "text-foreground"
              )}
            >
              <DollarSign className="w-5 h-5" />
              <span className="hidden sm:flex items-center gap-2 text-xs">
                {awsCost !== null && (
                  <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-400 font-medium" title="AWS infra (EC2 + DynamoDB) — this month">
                    <Cloud className="w-3 h-3" />${awsCost.toFixed(2)}
                  </span>
                )}
                {apiCost !== null && (
                  <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-medium" title="AI API (OpenAI GPT-4o, GPT-4o-mini, Whisper) — this month">
                    <Cpu className="w-3 h-3" />${apiCost.toFixed(2)}
                  </span>
                )}
                {awsCost === null && apiCost === null && "Usage"}
              </span>
            </Link>
            <Link
              href="/settings"
              className={cn(
                "text-muted-foreground hover:text-foreground transition-colors flex items-center gap-2",
                pathname === "/settings" && "text-foreground"
              )}
            >
              <Settings className="w-5 h-5" />
              <span className="hidden sm:inline">Settings</span>
            </Link>
          </div>
        </div>
      </div>
    </nav>
  )
}
