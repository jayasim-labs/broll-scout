import { NextRequest, NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET(request: NextRequest) {
  try {
    const url = new URL(request.url)
    const resp = await fetch(
      `${BACKEND}/api/v1/library/search${url.search}`,
      { cache: "no-store" }
    )
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ results: [], total_count: 0 }, { status: 200 })
  }
}
