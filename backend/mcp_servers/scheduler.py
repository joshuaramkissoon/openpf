"""Scheduler MCP server for Archie.

Exposes cron task management tools backed by the app's SQLite task tables.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.services.task_scheduler_service import (
    create_task,
    delete_task,
    list_task_logs,
    list_tasks,
    run_due_tasks,
    run_task_now,
    seed_default_tasks,
    update_task,
)

# ── Logging (file-based — stdout is reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR", "/app/logs"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("scheduler-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "scheduler.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "scheduler",
    instructions=(
        "Cron scheduler tools for Archie. "
        "Use these tools to list/create/update/delete/run scheduled tasks."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _as_bool(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


@mcp.tool()
def list_scheduled_tasks() -> str:
    """List all configured scheduled tasks."""
    logger.info("list_scheduled_tasks")
    init_db()
    db = SessionLocal()
    try:
        return _fmt({"ok": True, "items": list_tasks(db)})
    finally:
        db.close()


@mcp.tool()
def create_scheduled_task(
    name: str,
    schedule: str,
    prompt: str,
    model: str = "",
    timezone: str = "Europe/London",
    enabled: bool = True,
    task_kind: str = "claude",
    description: str = "",
) -> str:
    """Create a scheduled task.

    Args:
        name: Unique task name.
        schedule: Cron expression, e.g. "30 7 * * 1-5".
        prompt: Claude prompt or task instruction.
        model: Optional Claude model override.
        timezone: IANA timezone, default Europe/London.
        enabled: Whether task is active.
        task_kind: One of claude|leveraged_cycle|leveraged_scan|leveraged_monitor.
        description: Optional metadata description.
    """
    logger.info("create_scheduled_task name=%s schedule=%s", name, schedule)
    init_db()
    db = SessionLocal()
    try:
        payload = {
            "name": name,
            "cron_expr": schedule,
            "timezone": timezone,
            "model": model or "",
            "prompt": prompt,
            "enabled": _as_bool(enabled),
            "meta": {
                "task_kind": str(task_kind or "claude").strip().lower(),
                "description": str(description or "").strip(),
            },
        }
        row = create_task(db, payload)
        return _fmt({"ok": True, "task": row})
    except Exception as exc:  # noqa: BLE001
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def pause_scheduled_task(task_id: str) -> str:
    """Pause a task by ID."""
    logger.info("pause_scheduled_task id=%s", task_id)
    init_db()
    db = SessionLocal()
    try:
        row = update_task(db, task_id, {"enabled": False})
        return _fmt({"ok": True, "task": row})
    except Exception as exc:  # noqa: BLE001
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def resume_scheduled_task(task_id: str) -> str:
    """Resume a task by ID."""
    logger.info("resume_scheduled_task id=%s", task_id)
    init_db()
    db = SessionLocal()
    try:
        row = update_task(db, task_id, {"enabled": True})
        return _fmt({"ok": True, "task": row})
    except Exception as exc:  # noqa: BLE001
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def delete_scheduled_task(task_id: str) -> str:
    """Delete a task by ID."""
    logger.info("delete_scheduled_task id=%s", task_id)
    init_db()
    db = SessionLocal()
    try:
        deleted = delete_task(db, task_id)
        return _fmt({"ok": bool(deleted), "deleted": bool(deleted), "id": task_id})
    finally:
        db.close()


@mcp.tool()
def run_scheduled_task_now(task_id: str) -> str:
    """Run a single task immediately."""
    logger.info("run_scheduled_task_now id=%s", task_id)
    init_db()

    # run_task_now → _run_claude_prompt → anyio.run() needs its own event
    # loop, but this MCP server already runs inside an async loop (FastMCP).
    # Executing in a separate thread gives anyio.run() a clean loop.
    def _in_thread() -> dict:
        db = SessionLocal()
        try:
            return run_task_now(db, task_id)
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_in_thread)
            result = future.result()  # blocks until done
        return _fmt({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def get_scheduled_task_logs(task_id: str, limit: int = 20) -> str:
    """Get recent logs for a task."""
    logger.info("get_scheduled_task_logs id=%s limit=%d", task_id, limit)
    init_db()
    db = SessionLocal()
    try:
        rows = list_task_logs(db, task_id, limit=max(1, min(int(limit), 200)))
        return _fmt({"ok": True, "task_id": task_id, "count": len(rows), "items": rows})
    finally:
        db.close()


@mcp.tool()
def run_due_scheduled_tasks() -> str:
    """Run all due enabled tasks now."""
    logger.info("run_due_scheduled_tasks")
    init_db()

    def _in_thread() -> list:
        db = SessionLocal()
        try:
            return run_due_tasks(db)
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_in_thread)
            rows = future.result()
        return _fmt({"ok": True, "count": len(rows), "items": rows})
    except Exception as exc:  # noqa: BLE001
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def seed_default_scheduled_tasks() -> str:
    """Seed default task set if missing."""
    logger.info("seed_default_scheduled_tasks")
    init_db()
    db = SessionLocal()
    try:
        rows = seed_default_tasks(db)
        return _fmt({"ok": True, "created": len(rows), "items": rows})
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
