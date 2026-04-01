"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Video, Settings, Home } from "lucide-react"
import { cn } from "@/lib/utils"

export function Navbar() {
  const pathname = usePathname()

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
