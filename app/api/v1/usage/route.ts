import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function GET() {
  try {
    const resp = await fetch(backendUrl("/api/v1/usage"), {
      headers: backendHeaders(),
      cache: "no-store",
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({}, { status: 502 })
  }
}

export async function POST(request: NextRequest) {
  try {
    const resp = await fetch(backendUrl("/api/v1/usage/recalculate"), {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
