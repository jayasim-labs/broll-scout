/*
 * Web Worker for agent polling.
 *
 * Web Workers are NOT subject to Chrome's background-tab throttling —
 * setTimeout/setInterval run at full speed even when the tab is hidden.
 * This guarantees the backend always sees recent polls and never
 * triggers the agent-gone timeout during long pipeline runs.
 *
 * CRITICAL DESIGN: The heartbeat (poll) loop runs on a FIXED interval,
 * completely independent of task execution. Task execution runs in
 * parallel — a long-running companion /execute call (e.g. Whisper 10m,
 * transcript 429 cooldown 2m) never blocks the heartbeat.
 *
 * Protocol (main thread ↔ worker):
 *   main → worker:  { type: "start", backendOrigin, companionUrl }
 *   main → worker:  { type: "config", jobActive: boolean }
 *   main → worker:  { type: "stop" }
 */

let backendOrigin = ""
let companionUrl = ""
let jobActive = false
let running = false

const POLL_ACTIVE_MS = 800
const POLL_IDLE_MS = 10000
const HEARTBEAT_MS = 8000

let heartbeatTimer = null
let busyExecuting = false

function log(msg) {
  console.log(`[agent-worker] ${msg}`)
}

function logError(msg, err) {
  console.error(`[agent-worker] ${msg}`, err)
}

// ─── Heartbeat: always-on, never blocked by task execution ────────
// Sends a lightweight poll to the backend so it knows the agent is alive.
// This runs on a FIXED setInterval, completely decoupled from task execution.
function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(async () => {
    try {
      await fetch(`${backendOrigin}/api/v1/agent/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: "browser-agent" }),
      })
    } catch (err) {
      logError("heartbeat fetch failed", err)
    }
  }, HEARTBEAT_MS)
  log("heartbeat started (every " + HEARTBEAT_MS + "ms)")
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

// ─── Task execution: runs in parallel, never blocks the poll loop ─
async function executeTask(task) {
  const payload = task.payload || {}
  const jobId = payload.job_id || null
  const taskType = task.task_type || "unknown"
  const taskId = task.task_id

  log(`executing ${taskType} task ${taskId}`)

  if (jobId) {
    try {
      const st = await fetch(`${backendOrigin}/api/v1/jobs/${jobId}/status`)
      if (st.ok) {
        const d = await st.json()
        if (d.status === "cancelled") {
          log(`task ${taskId} skipped — job ${jobId} cancelled`)
          await submitResult(taskId, "failed", [])
          return
        }
      }
    } catch (err) {
      logError(`job status check failed for ${taskId}`, err)
    }
  }

  const isWhisper = taskType === "whisper"
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
    log(`task ${taskId} (${taskType}) completed`)
    await submitResult(taskId, "completed", execData.results || [])
  } catch (err) {
    logError(`task ${taskId} (${taskType}) failed`, err)
    await submitResult(taskId, "failed", [])
  }
}

async function submitResult(taskId, status, result) {
  try {
    await fetch(`${backendOrigin}/api/v1/agent/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, status, result }),
    })
  } catch (err) {
    logError(`submitResult failed for ${taskId}`, err)
  }
}

// ─── Poll loop: lightweight, never blocks ─────────────────────────
// Polls the backend for new tasks. If tasks are returned, they are
// executed in the background (fire-and-forget) so the poll loop
// immediately returns to sleeping and polling again.
async function pollLoop() {
  log("pollLoop started")
  while (running) {
    try {
      const resp = await fetch(`${backendOrigin}/api/v1/agent/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: "browser-agent" }),
      })
      if (resp.ok) {
        const data = await resp.json()
        if (data.tasks && data.tasks.length > 0 && !busyExecuting) {
          busyExecuting = true
          // Fire-and-forget: execute tasks in background, don't block the loop
          ;(async () => {
            try {
              for (const task of data.tasks) {
                await executeTask(task)
              }
            } catch (err) {
              logError("task execution batch error", err)
            } finally {
              busyExecuting = false
            }
          })()
        }
      }
    } catch (err) {
      logError("poll fetch failed", err)
    }

    const delay = jobActive ? POLL_ACTIVE_MS : POLL_IDLE_MS
    await new Promise(r => setTimeout(r, delay))
  }
  log("pollLoop stopped")
}

self.onmessage = (e) => {
  const msg = e.data
  if (msg.type === "start") {
    backendOrigin = msg.backendOrigin || ""
    companionUrl = msg.companionUrl || "http://localhost:9876"
    jobActive = msg.jobActive || false
    log(`start: backend=${backendOrigin}, companion=${companionUrl}, jobActive=${jobActive}`)
    if (!running) {
      running = true
      startHeartbeat()
      pollLoop()
    }
  } else if (msg.type === "config") {
    if (msg.jobActive !== undefined) {
      jobActive = msg.jobActive
      log(`config: jobActive=${jobActive}`)
    }
  } else if (msg.type === "stop") {
    log("stop received")
    running = false
    stopHeartbeat()
  }
}
