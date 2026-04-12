/*
 * Web Worker for agent polling — with per-job isolation.
 *
 * MULTI-USER: Each editor's browser sends their current job_id with
 * every poll. The backend only returns tasks for THAT job. This ensures
 * Editor A's agent never steals Editor B's tasks.
 *
 * Web Workers are NOT subject to Chrome's background-tab throttling —
 * setTimeout/setInterval run at full speed even when the tab is hidden.
 *
 * CRITICAL DESIGN: The heartbeat runs on a FIXED interval, completely
 * independent of task execution. Task execution is fire-and-forget.
 *
 * Protocol (main thread <-> worker):
 *   main -> worker:  { type: "start", backendOrigin, companionUrl, jobActive, jobId }
 *   main -> worker:  { type: "config", jobActive, jobId }
 *   main -> worker:  { type: "stop" }
 */

let backendOrigin = ""
let companionUrl = ""
let jobActive = false
let jobId = null
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

function pollBody() {
  const body = { agent_id: "browser-agent" }
  if (jobId) body.job_id = jobId
  return JSON.stringify(body)
}

// --- Heartbeat: always-on, never blocked by task execution ---
function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(async () => {
    try {
      await fetch(`${backendOrigin}/api/v1/agent/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: pollBody(),
      })
    } catch (err) {
      logError("heartbeat fetch failed", err)
    }
  }, HEARTBEAT_MS)
  log("heartbeat started (every " + HEARTBEAT_MS + "ms, job=" + jobId + ")")
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

// --- Task execution: runs in parallel, never blocks the poll loop ---
async function executeTask(task) {
  const payload = task.payload || {}
  const taskJobId = payload.job_id || null
  const taskType = task.task_type || "unknown"
  const taskId = task.task_id

  log(`executing ${taskType} task ${taskId} (job=${taskJobId})`)

  if (taskJobId) {
    try {
      const st = await fetch(`${backendOrigin}/api/v1/jobs/${taskJobId}/status`)
      if (st.ok) {
        const d = await st.json()
        if (d.status === "cancelled") {
          log(`task ${taskId} skipped - job ${taskJobId} cancelled`)
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

// --- Poll loop: lightweight, never blocks ---
async function pollLoop() {
  log("pollLoop started (job=" + jobId + ")")
  while (running) {
    try {
      const resp = await fetch(`${backendOrigin}/api/v1/agent/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: pollBody(),
      })
      if (resp.ok) {
        const data = await resp.json()
        if (data.tasks && data.tasks.length > 0 && !busyExecuting) {
          busyExecuting = true
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
    jobId = msg.jobId || null
    log(`start: backend=${backendOrigin}, companion=${companionUrl}, jobActive=${jobActive}, job=${jobId}`)
    if (!running) {
      running = true
      startHeartbeat()
      pollLoop()
    }
  } else if (msg.type === "config") {
    if (msg.jobActive !== undefined) jobActive = msg.jobActive
    if (msg.jobId !== undefined) {
      const oldJobId = jobId
      jobId = msg.jobId
      if (oldJobId !== jobId) {
        log(`job changed: ${oldJobId} -> ${jobId}`)
        startHeartbeat()
      }
    }
    log(`config: jobActive=${jobActive}, job=${jobId}`)
  } else if (msg.type === "stop") {
    log("stop received")
    running = false
    stopHeartbeat()
  }
}
