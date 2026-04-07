import { NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function GET() {
  try {
    const resp = await fetch(backendUrl("/api/v1/settings/channels"), { headers: backendHeaders(), cache: "no-store" })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch {
    return NextResponse.json({ groups: {} }, { status: 200 })
  }
}
