import { NextRequest, NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  try {
    const resp = await fetch(backendUrl(`/api/v1/jobs/${id}/cancel`), {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
