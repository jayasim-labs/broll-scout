import { NextRequest, NextResponse } from "next/server"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string; segmentId: string }> }
) {
  const { id, segmentId } = await params
  try {
    const resp = await fetch(
      `${BACKEND}/api/v1/jobs/${id}/segments/${segmentId}/expand-progress`,
      { cache: "no-store" }
    )
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ phase: "unknown", log: [] }, { status: 502 })
  }
}
