"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Video, Settings, Home, FolderOpen, DollarSign } from "lucide-react"
import { cn } from "@/lib/utils"
import { AgentStatusBadge } from "@/components/agent-status"
import { useEffect, useState, useCallback } from "react"

export function Navbar() {
  const pathname = usePathname()
  const [totalCost, setTotalCost] = useState<number | null>(null)

  const fetchCost = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/usage", { cache: "no-store" })
      if (resp.ok) {
        const data = await resp.json()
        setTotalCost(data.all_time?.estimated_cost_usd ?? null)
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
                "text-muted-foreground hover:text-foreground transition-colors flex items-center gap-2",
                pathname === "/usage" && "text-foreground"
              )}
            >
              <DollarSign className="w-5 h-5" />
              <span className="hidden sm:inline">
                {totalCost !== null ? `$${totalCost.toFixed(2)}` : "Usage"}
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
