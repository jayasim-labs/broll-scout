import { NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function POST() {
  try {
    const resp = await fetch(`${BACKEND}/api/v1/settings/migrate-channels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
