import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function GET(request: NextRequest) {
  try {
    const url = new URL(request.url)
    const resp = await fetch(
      backendUrl(`/api/v1/library/search${url.search}`),
      { headers: backendHeaders(), cache: "no-store" }
    )
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ results: [], total_count: 0 }, { status: 200 })
  }
}
