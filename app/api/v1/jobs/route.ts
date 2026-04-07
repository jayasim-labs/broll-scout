import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 60_000)

    const resp = await fetch(backendUrl("/api/v1/jobs"), {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    clearTimeout(timeout)
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    const msg =
      error instanceof DOMException && error.name === "AbortError"
        ? "Backend timed out — please retry in a moment."
        : "Backend unavailable"
    return NextResponse.json({ detail: msg }, { status: 502 })
  }
}

export async function GET() {
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 30_000)

    const resp = await fetch(backendUrl("/api/v1/jobs"), {
      headers: backendHeaders(),
      cache: "no-store",
      signal: controller.signal,
    })
    clearTimeout(timeout)
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ jobs: [] }, { status: 200 })
  }
}
