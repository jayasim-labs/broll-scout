import { NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function POST() {
  try {
    const resp = await fetch(backendUrl("/api/v1/settings/migrate-channels"), {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
