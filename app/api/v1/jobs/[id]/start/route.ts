import { NextRequest, NextResponse } from "next/server"

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  return NextResponse.json({ status: "ok", message: "Job starts automatically on creation" })
}
