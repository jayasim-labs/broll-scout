"""
In-memory task queue for the local yt-dlp agent.

Tasks are ephemeral (5–45 second lifetime). No persistence needed.
The EC2 pipeline creates tasks, then awaits their completion.
A browser-based or CLI agent polls for pending tasks, runs yt-dlp
locally, and posts results back.
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
_active_agents: dict[str, float] = {}
_lock = asyncio.Lock()

TASK_TIMEOUT = 600

# Agent-gone detection: polls now run inside a Web Worker (immune to
# Chrome's background-tab throttling), so a gap > 2 min reliably means
# the user closed the tab or navigated away. 5 min is a safe threshold.
AGENT_GONE_THRESHOLD = 5 * 60   # 5 min — Worker guarantees sub-second polls
_agent_gone_notified = False


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

    # Instead of a single long wait, check periodically so we can
    # detect when the agent has gone away and bail early.
    deadline = time.time() + timeout
    check_interval = 5.0  # seconds between agent-alive checks
    while time.time() < deadline:
        remaining = min(check_interval, deadline - time.time())
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
            # Event was set — result is ready
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

        # Check if agent has genuinely disappeared (user closed tab).
        # Chrome background throttling can pause polls for 5-15 min,
        # so we use a very generous threshold (30 min) to avoid killing
        # tasks during normal tab backgrounding.
        gap = seconds_since_last_poll()
        if gap > AGENT_GONE_THRESHOLD:
            logger.warning(
                "Agent task %s aborted — no agent poll for %.0fs (threshold %ds)",
                task_id, gap, AGENT_GONE_THRESHOLD,
            )
            await fail_all_pending("agent_gone")
            return []

    logger.warning("Agent task %s timed out after %.0fs", task_id, timeout)
    async with _lock:
        _pending.pop(task_id, None)
        _events.pop(task_id, None)
    return []


async def cancel_tasks_for_job(job_id: str) -> list[str]:
    """Mark pending/claimed agent tasks for this job as cancelled and wake waiters.

    Returns task_ids so the browser can ask the companion to abort in-flight work.
    """
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


async def poll_tasks(agent_id: str, max_tasks: int = 2) -> list[dict]:
    async with _lock:
        _active_agents[agent_id] = time.time()
        claimed: list[dict] = []
        for task_id, task in list(_pending.items()):
            if task["status"] == "pending":
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


def is_agent_available() -> bool:
    """Check if an agent is available: polled recently OR actively processing tasks."""
    now = time.time()
    if any(now - t < 30 for t in _active_agents.values()):
        return True
    has_claimed = any(t["status"] == "claimed" for t in _pending.values())
    return has_claimed


def seconds_since_last_poll() -> float:
    """How long since any agent last polled. Returns inf if never polled."""
    if not _active_agents:
        return float("inf")
    return time.time() - max(_active_agents.values())


async def fail_all_pending(reason: str = "agent_gone") -> int:
    """Fail all pending/claimed tasks immediately so waiters stop blocking."""
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
        logger.warning("Failed %d pending tasks: %s", failed, reason)
    return failed


