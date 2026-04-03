import { NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET() {
  try {
    const resp = await fetch(`${BACKEND}/api/v1/library/stats`, {
      cache: "no-store",
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json(
      { videos_indexed: 0, clips_found: 0, transcripts_cached: 0, editor_rated: 0 },
      { status: 200 },
    )
  }
}
