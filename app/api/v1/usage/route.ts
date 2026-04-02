import { NextRequest, NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET() {
  try {
    const resp = await fetch(`${BACKEND}/api/v1/usage`, {
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
    const resp = await fetch(`${BACKEND}/api/v1/usage/recalculate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
