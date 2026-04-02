import { NextRequest, NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 60_000)

    const resp = await fetch(`${BACKEND}/api/v1/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    clearTimeout(timeout)
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    const msg =
      error instanceof DOMException && error.name === "AbortError"
        ? "Backend timed out — Lambda may be cold-starting. Please retry."
        : "Backend unavailable"
    return NextResponse.json({ detail: msg }, { status: 502 })
  }
}

export async function GET() {
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 30_000)

    const resp = await fetch(`${BACKEND}/api/v1/jobs`, {
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
