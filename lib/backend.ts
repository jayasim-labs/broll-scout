const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"
const API_KEY = process.env.BACKEND_API_KEY || ""

export function backendUrl(path: string): string {
  return `${BACKEND}${path}`
}

export function backendHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  if (API_KEY) headers["X-API-Key"] = API_KEY
  return headers
}
