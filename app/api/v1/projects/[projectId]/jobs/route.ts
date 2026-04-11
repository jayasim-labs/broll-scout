import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> }
) {
  try {
    const { projectId } = await params
    const resp = await fetch(backendUrl(`/api/v1/projects/${projectId}/jobs`), {
      headers: backendHeaders(),
      cache: "no-store",
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ jobs: [] }, { status: 200 })
  }
}
