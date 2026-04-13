"""
In-memory task queue for local companion agents.

Tasks are ephemeral (5–600 second lifetime). No persistence needed.
The EC2 pipeline creates tasks scoped to a job_id, then awaits completion.
Each editor's browser agent polls for tasks belonging to ITS job only.

MULTI-USER ISOLATION:
- Tasks are created with a job_id.
- poll_tasks() only returns tasks matching the caller's job_id.
- Agent-gone detection is per-job: if Editor A's agent disappears,
  only Editor A's tasks are failed — Editor B's pipeline is unaffected.
- _active_agents is keyed by (agent_id, job_id) so each editor's
  heartbeat is tracked independently.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_pending: dict[str, dict] = {}
_completed: dict[str, dict] = {}
_events: dict[str, asyncio.Event] = {}
# Keyed by (agent_id, job_id) → last poll timestamp
_active_agents: dict[tuple[str, str | None], float] = {}
_lock = asyncio.Lock()

TASK_TIMEOUT = 600

# Agent-gone detection: polls + heartbeats run inside a Web Worker
# (immune to Chrome's background-tab throttling). The heartbeat fires
# every 8s regardless of whether a companion task is running.
# A gap > 5 min for a SPECIFIC job reliably means the user closed the tab.
AGENT_GONE_THRESHOLD = 5 * 60


async def create_task(
    task_type: str, payload: dict, job_id: str | None = None,
) -> str:
    task_id = str(uuid4())
    merged_payload = dict(payload)
    if job_id is not None:
        merged_payload.setdefault("job_id", job_id)
    async with _lock:
        _pending[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "payload": merged_payload,
            "job_id": job_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _events[task_id] = asyncio.Event()
    return task_id


async def wait_for_result(task_id: str, timeout: float = TASK_TIMEOUT) -> list[dict[str, Any]]:
    event = _events.get(task_id)
    if not event:
        return []

    task_info = _pending.get(task_id, {})
    task_job_id = task_info.get("job_id")

    deadline = time.time() + timeout
    check_interval = 5.0
    while time.time() < deadline:
        remaining = min(check_interval, deadline - time.time())
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
            async with _lock:
                result = _completed.pop(task_id, {})
                _events.pop(task_id, None)
            status = result.get("status")
            if status in ("failed", "cancelled"):
                if status == "failed":
                    logger.warning("Agent task %s failed", task_id)
                else:
                    logger.info("Agent task %s cancelled", task_id)
                return []
            return result.get("result", [])
        except asyncio.TimeoutError:
            pass

        # Per-job agent-gone check: only look at agents polling for THIS job
        gap = _seconds_since_last_poll_for_job(task_job_id)
        task_type = task_info.get("task_type", "?")
        task_status = task_info.get("status", "?")
        if gap > AGENT_GONE_THRESHOLD:
            logger.warning(
                "Agent task %s (%s, status=%s, job=%s) aborted — no agent poll "
                "for this job in %.0fs (threshold %ds)",
                task_id, task_type, task_status, task_job_id, gap, AGENT_GONE_THRESHOLD,
            )
            await _fail_pending_for_job(task_job_id, "agent_gone")
            return []
        elif gap > 60:
            logger.info(
                "Agent task %s (%s, job=%s): poll gap %.0fs — still within threshold %ds",
                task_id, task_type, task_job_id, gap, AGENT_GONE_THRESHOLD,
            )

    logger.warning("Agent task %s timed out after %.0fs", task_id, timeout)
    async with _lock:
        _pending.pop(task_id, None)
        _events.pop(task_id, None)
    return []


async def cancel_tasks_for_job(job_id: str) -> list[str]:
    """Mark pending/claimed agent tasks for this job as cancelled and wake waiters."""
    events_to_set: list[asyncio.Event] = []
    cancelled_ids: list[str] = []
    async with _lock:
        for tid, t in list(_pending.items()):
            if t.get("job_id") != job_id:
                continue
            cancelled_ids.append(tid)
            _completed[tid] = {"status": "cancelled", "result": []}
            _pending.pop(tid, None)
            ev = _events.pop(tid, None)
            if ev:
                events_to_set.append(ev)
    for ev in events_to_set:
        ev.set()
    if cancelled_ids:
        logger.info(
            "Cancelled %d agent task(s) for job %s",
            len(cancelled_ids), job_id,
        )
    return cancelled_ids


_poll_count = 0
_poll_log_interval = 50


async def heartbeat(agent_id: str, job_id: str | None = None) -> None:
    """Record agent liveness without claiming any tasks."""
    async with _lock:
        _active_agents[(agent_id, job_id)] = time.time()
        _active_agents[(agent_id, None)] = time.time()


async def poll_tasks(agent_id: str, max_tasks: int = 2, job_id: str | None = None) -> list[dict]:
    """Return pending tasks matching the agent's job scope.

    - If job_id is set: only claim tasks for that specific job (multi-user safe).
    - If job_id is None: claim ANY pending task (backward compat with old Workers).
    """
    global _poll_count
    async with _lock:
        _active_agents[(agent_id, job_id)] = time.time()
        _active_agents[(agent_id, None)] = time.time()
        _poll_count += 1

        if job_id is not None:
            pending_for_job = [
                t for t in _pending.values()
                if t["status"] == "pending" and t.get("job_id") == job_id
            ]
        else:
            # Legacy: no job_id means claim any pending task
            pending_for_job = [
                t for t in _pending.values()
                if t["status"] == "pending"
            ]

        claimed: list[dict] = []
        for task in pending_for_job:
            task["status"] = "claimed"
            task["claimed_at"] = datetime.now(timezone.utc).isoformat()
            task["agent_id"] = agent_id
            claimed.append({
                "task_id": task["task_id"],
                "task_type": task["task_type"],
                "payload": task["payload"],
            })
            if len(claimed) >= max_tasks:
                break

    if claimed:
        logger.info(
            "Agent poll #%d (job=%s): claimed %d task(s) [%s]",
            _poll_count, job_id,
            len(claimed),
            ", ".join(f"{c['task_type']}" for c in claimed),
        )
    elif _poll_count % _poll_log_interval == 0:
        total_pending = sum(1 for t in _pending.values() if t["status"] == "pending")
        total_claimed = sum(1 for t in _pending.values() if t["status"] == "claimed")
        logger.debug(
            "Agent poll #%d (job=%s): heartbeat (total pending=%d, claimed=%d)",
            _poll_count, job_id, total_pending, total_claimed,
        )
    return claimed


async def submit_result(task_id: str, status: str, result: list) -> bool:
    async with _lock:
        if task_id not in _pending and task_id not in _events:
            logger.debug("Late result for task %s (already timed out or completed) — discarding", task_id)
            return True
        _completed[task_id] = {"status": status, "result": result}
        _pending.pop(task_id, None)
        event = _events.get(task_id)
    if event:
        event.set()
    return True


async def get_queue_status() -> dict:
    async with _lock:
        now = time.time()
        active_count = sum(1 for t in _active_agents.values() if now - t < 30)
        pending_count = sum(1 for t in _pending.values() if t["status"] == "pending")
        claimed_count = sum(1 for t in _pending.values() if t["status"] == "claimed")
        return {
            "pending_tasks": pending_count,
            "claimed_tasks": claimed_count,
            "completed_tasks": len(_completed),
            "agents_active": active_count,
        }


def is_agent_available(job_id: str | None = None) -> bool:
    """Check if an agent is available for the given job (or any job if None).

    Graceful fallback: if no agent has polled with this specific job_id,
    check if ANY agent polled recently (covers old Workers that don't send
    job_id, or the brief window before the Worker learns the new job_id).
    """
    now = time.time()
    if job_id is not None:
        for (aid, jid), ts in _active_agents.items():
            if jid == job_id and now - ts < 30:
                return True
        has_claimed = any(
            t["status"] == "claimed" and t.get("job_id") == job_id
            for t in _pending.values()
        )
        if has_claimed:
            return True

    # Fallback: any agent active recently (handles old Workers or pre-job polls)
    if any(now - t < 30 for t in _active_agents.values()):
        return True
    has_claimed = any(t["status"] == "claimed" for t in _pending.values())
    return has_claimed


def _seconds_since_last_poll_for_job(job_id: str | None) -> float:
    """How long since an agent last polled for this specific job."""
    relevant = [
        ts for (aid, jid), ts in _active_agents.items()
        if jid == job_id
    ]
    if not relevant:
        # Fall back to global check — maybe the agent hasn't sent a job_id yet
        all_ts = list(_active_agents.values())
        if not all_ts:
            return float("inf")
        return time.time() - max(all_ts)
    return time.time() - max(relevant)


def seconds_since_last_poll() -> float:
    """How long since any agent last polled. Returns inf if never polled."""
    if not _active_agents:
        return float("inf")
    return time.time() - max(_active_agents.values())


async def _fail_pending_for_job(job_id: str | None, reason: str = "agent_gone") -> int:
    """Fail pending/claimed tasks for ONE job only. Other jobs are unaffected."""
    failed = 0
    events_to_set: list[asyncio.Event] = []
    async with _lock:
        for tid in list(_pending):
            task = _pending[tid]
            if task.get("job_id") != job_id:
                continue
            _completed[tid] = {"status": "failed", "result": []}
            _pending.pop(tid)
            ev = _events.pop(tid, None)
            if ev:
                events_to_set.append(ev)
            failed += 1
    for ev in events_to_set:
        ev.set()
    if failed:
        logger.warning("Failed %d pending tasks for job %s: %s", failed, job_id, reason)
    return failed


async def fail_all_pending(reason: str = "agent_gone") -> int:
    """Fail ALL pending tasks across all jobs. Use _fail_pending_for_job() instead when possible."""
    failed = 0
    events_to_set: list[asyncio.Event] = []
    async with _lock:
        for tid in list(_pending):
            _completed[tid] = {"status": "failed", "result": []}
            _pending.pop(tid)
            ev = _events.pop(tid, None)
            if ev:
                events_to_set.append(ev)
            failed += 1
    for ev in events_to_set:
        ev.set()
    if failed:
        logger.warning("Failed %d pending tasks (ALL jobs): %s", failed, reason)
    return failed
