import { NextRequest, NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const resp = await fetch(`${BACKEND}/api/v1/settings/resolve-channel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ resolved: false, channels: [] }, { status: 200 })
  }
}
