import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json()
    const resp = await fetch(backendUrl("/api/v1/settings/bulk"), {
      method: "PUT",
      headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    })
    let data
    try {
      data = await resp.json()
    } catch {
      const text = await resp.text()
      return NextResponse.json(
        { detail: `Backend error: ${text.slice(0, 200)}` },
        { status: resp.status || 502 },
      )
    }
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
