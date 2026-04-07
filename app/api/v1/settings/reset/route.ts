import { NextResponse } from "next/server"
import { backendUrl, backendHeaders } from "@/lib/backend"

export async function POST() {
  try {
    const resp = await fetch(backendUrl("/api/v1/settings/reset"), {
      method: "POST",
      headers: backendHeaders(),
    })
    const data = await resp.json()
    return NextResponse.json(data, { status: resp.status })
  } catch (error) {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 })
  }
}
