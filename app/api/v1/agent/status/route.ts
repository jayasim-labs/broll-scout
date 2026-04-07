import { NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function GET() {
  try {
    const resp = await fetch(backendUrl("/api/v1/agent/status"), { headers: backendHeaders(), cache: "no-store" })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ agents_active: 0, pending_tasks: 0 }, { status: 502 })
  }
}
