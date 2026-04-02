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

TASK_TIMEOUT = 45


async def create_task(task_type: str, payload: dict) -> str:
    task_id = str(uuid4())
    async with _lock:
        _pending[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "payload": payload,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _events[task_id] = asyncio.Event()
    return task_id


async def wait_for_result(task_id: str, timeout: float = TASK_TIMEOUT) -> list[dict[str, Any]]:
    event = _events.get(task_id)
    if not event:
        return []
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        async with _lock:
            result = _completed.pop(task_id, {})
            _events.pop(task_id, None)
        if result.get("status") == "failed":
            logger.warning("Agent task %s failed", task_id)
            return []
        return result.get("result", [])
    except asyncio.TimeoutError:
        logger.warning("Agent task %s timed out after %.0fs", task_id, timeout)
        async with _lock:
            _pending.pop(task_id, None)
            _events.pop(task_id, None)
        return []


async def poll_tasks(agent_id: str, max_tasks: int = 3) -> list[dict]:
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
            return False
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
    """Check if any agent has polled within the last 30 seconds."""
    now = time.time()
    return any(now - t < 30 for t in _active_agents.values())


async def cleanup_stale(max_age: float = 120) -> None:
    """Remove tasks older than max_age seconds that were never completed."""
    async with _lock:
        now = time.time()
        stale = [
            tid for tid, t in _pending.items()
            if now - datetime.fromisoformat(t["created_at"]).timestamp() > max_age
        ]
        for tid in stale:
            _pending.pop(tid, None)
            event = _events.pop(tid, None)
            if event:
                event.set()
