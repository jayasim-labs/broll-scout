/*
 * Web Worker for agent polling.
 *
 * Web Workers are NOT subject to Chrome's background-tab throttling —
 * setTimeout/setInterval run at full speed even when the tab is hidden.
 * This guarantees the backend always sees recent polls and never
 * triggers the agent-gone timeout during long pipeline runs.
 *
 * Protocol (main thread ↔ worker):
 *   main → worker:  { type: "start", backendOrigin, companionUrl }
 *   main → worker:  { type: "config", jobActive: boolean }
 *   main → worker:  { type: "stop" }
 *   worker → main:  { type: "tasks", tasks: [...] }
 *   main → worker:  { type: "taskResult", task_id, status, result }
 */

let backendOrigin = ""
let companionUrl = ""
let jobActive = false
let running = false
let pollTimer = null

const POLL_ACTIVE_MS = 800
const POLL_IDLE_MS = 10000
const HEARTBEAT_MS = 8000

let heartbeatTimer = null
let executingTasks = false

async function sendPoll() {
  try {
    const resp = await fetch(`${backendOrigin}/api/v1/agent/poll`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: "browser-agent" }),
    })
    if (!resp.ok) return
    const data = await resp.json()
    if (data.tasks && data.tasks.length > 0) {
      executingTasks = true
      startHeartbeat()
      for (const task of data.tasks) {
        await executeTask(task)
      }
      executingTasks = false
      stopHeartbeat()
    }
  } catch { /* network error, retry on next tick */ }
}

async function executeTask(task) {
  const payload = task.payload || {}
  const jobId = payload.job_id || null

  if (jobId) {
    try {
      const st = await fetch(`${backendOrigin}/api/v1/jobs/${jobId}/status`)
      if (st.ok) {
        const d = await st.json()
        if (d.status === "cancelled") {
          await submitResult(task.task_id, "failed", [])
          return
        }
      }
    } catch { /* ignore */ }
  }

  const isWhisper = task.task_type === "whisper"
  const timeoutMs = isWhisper ? 30 * 60000 : 5 * 60000

  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)

    const execResp = await fetch(`${companionUrl}/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(task),
      signal: controller.signal,
    })
    clearTimeout(timer)
    const execData = await execResp.json()
    await submitResult(task.task_id, "completed", execData.results || [])
  } catch {
    await submitResult(task.task_id, "failed", [])
  }
}

async function submitResult(taskId, status, result) {
  try {
    await fetch(`${backendOrigin}/api/v1/agent/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, status, result }),
    })
  } catch { /* ignore */ }
}

async function sendHeartbeat() {
  try {
    await fetch(`${backendOrigin}/api/v1/agent/poll`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: "browser-agent" }),
    })
  } catch { /* ignore */ }
}

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS)
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

async function pollLoop() {
  while (running) {
    await sendPoll()
    const delay = jobActive ? POLL_ACTIVE_MS : POLL_IDLE_MS
    await new Promise(r => setTimeout(r, delay))
  }
}

self.onmessage = (e) => {
  const msg = e.data
  if (msg.type === "start") {
    backendOrigin = msg.backendOrigin || ""
    companionUrl = msg.companionUrl || "http://localhost:9876"
    jobActive = msg.jobActive || false
    if (!running) {
      running = true
      pollLoop()
    }
  } else if (msg.type === "config") {
    if (msg.jobActive !== undefined) jobActive = msg.jobActive
  } else if (msg.type === "stop") {
    running = false
    stopHeartbeat()
  }
}
