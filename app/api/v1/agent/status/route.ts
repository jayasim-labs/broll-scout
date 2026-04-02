import { NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET() {
  try {
    const resp = await fetch(`${BACKEND}/api/v1/agent/status`)
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ agents_active: 0, pending_tasks: 0 }, { status: 502 })
  }
}
