import { NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function GET() {
  try {
    const resp = await fetch(backendUrl("/api/v1/library/stats"), {
      headers: backendHeaders(),
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
