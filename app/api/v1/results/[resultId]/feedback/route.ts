import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ resultId: string }> }
) {
  try {
    const { resultId } = await params
    const jobId = request.nextUrl.searchParams.get("job_id")
    const body = await request.json()
    const resp = await fetch(
      backendUrl(`/api/v1/results/${resultId}/feedback?job_id=${jobId}`),
      {
        method: "POST",
        headers: backendHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      }
    )
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
