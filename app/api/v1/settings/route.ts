import { NextRequest, NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET() {
  try {
    const resp = await fetch(`${BACKEND}/api/v1/settings`, { cache: "no-store" })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ settings: {} }, { status: 200 })
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json()
    const resp = await fetch(`${BACKEND}/api/v1/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
