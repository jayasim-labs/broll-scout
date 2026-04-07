import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const resp = await fetch(backendUrl("/api/v1/agent/result"), {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ ok: false }, { status: 502 })
  }
}
