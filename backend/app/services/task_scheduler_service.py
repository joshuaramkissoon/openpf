from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import threading

import anyio
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ScheduledTask, ScheduledTaskLog
from app.services.claude_sdk_config import parse_setting_sources, project_root, resolve_sdk_cwd
from app.services.config_store import ConfigStore
from app.services.leveraged_service import monitor_open_trades, run_leveraged_cycle, scan_signals, update_policy

settings = get_settings()

_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_servers"

_T212_MCP_TOOLS = [
    "mcp__trading212__get_account_summary",
    "mcp__trading212__get_positions",
    "mcp__trading212__get_pending_orders",
    "mcp__trading212__place_market_order",
    "mcp__trading212__place_limit_order",
    "mcp__trading212__place_stop_order",
    "mcp__trading212__place_stop_limit_order",
    "mcp__trading212__cancel_order",
    "mcp__trading212__search_instruments",
    "mcp__trading212__get_exchanges",
    "mcp__trading212__get_order_history",
    "mcp__trading212__get_dividend_history",
    "mcp__trading212__get_transaction_history",
    "mcp__trading212__request_csv_export",
    "mcp__trading212__get_csv_export_status",
]

_MARKET_MCP_TOOLS = [
    "mcp__marketdata__get_price_snapshot",
    "mcp__marketdata__get_price_history_rows",
    "mcp__marketdata__get_technical_snapshot",
]

_SCHEDULER_MCP_TOOLS = [
    "mcp__scheduler__list_scheduled_tasks",
    "mcp__scheduler__create_scheduled_task",
    "mcp__scheduler__pause_scheduled_task",
    "mcp__scheduler__resume_scheduled_task",
    "mcp__scheduler__delete_scheduled_task",
    "mcp__scheduler__run_scheduled_task_now",
    "mcp__scheduler__get_scheduled_task_logs",
    "mcp__scheduler__run_due_scheduled_tasks",
    "mcp__scheduler__seed_default_scheduled_tasks",
]

_DEFAULT_TASKS: list[dict[str, Any]] = [
    {
        "name": "lev_morning_scan",
        "cron_expr": "30 7 * * 1-5",
        "timezone": "Europe/London",
        "model": settings.claude_model,
        "enabled": True,
        "meta": {
            "task_kind": "leveraged_cycle",
            "description": "Morning leveraged scan + entries",
        },
        "prompt": (
            "Run leveraged cycle: monitor open trades, scan new setups, and execute only within configured rails. "
            "If policy rails need adjustment, include JSON {\"policy_updates\": {...}} in your output."
        ),
    },
    {
        "name": "lev_midday_check",
        "cron_expr": "0 12 * * 1-5",
        "timezone": "Europe/London",
        "model": "claude-haiku-4-5",
        "enabled": True,
        "meta": {
            "task_kind": "leveraged_monitor",
            "description": "Midday risk/exit check",
        },
        "prompt": "Monitor open leveraged trades and enforce stop-loss/take-profit/time-stop rules.",
    },
    {
        "name": "lev_eod_close",
        "cron_expr": "30 15 * * 1-5",
        "timezone": "Europe/London",
        "model": settings.claude_model,
        "enabled": True,
        "meta": {
            "task_kind": "leveraged_monitor",
            "description": "End-of-day close workflow",
        },
        "prompt": "Run open-trade monitor and enforce close-time rules for non-overnight leveraged positions.",
    },
    {
        "name": "weekly_review",
        "cron_expr": "0 10 * * 0",
        "timezone": "Europe/London",
        "model": settings.claude_model,
        "enabled": True,
        "meta": {
            "task_kind": "claude",
            "description": "Weekly strategy review and policy suggestions",
        },
        "prompt": (
            "Produce a weekly strategy review artifact. Follow these steps:\n\n"
            "1. **Portfolio snapshot**: Use T212 MCP tools to pull current ISA positions and account summary "
            "(balances, holdings). For current prices and technicals of held tickers, use marketdata MCP tools. "
            "Summarise total value, cash available, and top holdings.\n\n"
            "2. **Leveraged positions**: List any open leveraged positions with current P&L, "
            "entry price, and days held. Use marketdata MCP for current prices. If none, state that clearly.\n\n"
            "3. **Market context**: Use marketdata MCP to get price and technicals for key indices "
            "(SPY, QQQ) and any leveraged products held. Summarise trend, RSI, and notable moves.\n\n"
            "4. **Trade log review**: Read memory/decisions/ for this month's trade decisions. "
            "Summarise wins, losses, and patterns. If no trades yet, note that.\n\n"
            "5. **Lessons & recommendations**: Based on the above, provide 2-3 actionable takeaways "
            "and any suggested changes to position sizing, stop-loss levels, or strategy.\n\n"
            "6. **Policy updates** (optional): If risk rails or trading parameters should change, "
            "include a JSON block with key policy_updates.\n\n"
            "Format your response as a clean, readable markdown report with headers and tables. "
            "This is your final artifact — make it polished and information-dense, not a thinking log."
        ),
    },
]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _next_run_utc(cron_expr: str, tz_name: str, now_utc: datetime | None = None) -> datetime:
    now = _aware_utc(now_utc or _utcnow())
    tz = ZoneInfo(tz_name)
    trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
    next_local = trigger.get_next_fire_time(previous_fire_time=None, now=now.astimezone(tz))
    if next_local is None:
        raise RuntimeError(f"invalid next run for cron '{cron_expr}'")
    return next_local.astimezone(timezone.utc).replace(tzinfo=None)


def _serialize_task(task: ScheduledTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "cron_expr": task.cron_expr,
        "timezone": task.timezone,
        "model": task.model,
        "prompt": task.prompt,
        "enabled": task.enabled,
        "next_run_at": task.next_run_at,
        "last_run_at": task.last_run_at,
        "last_status": task.last_status,
        "run_count": task.run_count,
        "failure_count": task.failure_count,
        "meta": task.meta or {},
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def list_tasks(db: Session) -> list[dict[str, Any]]:
    rows = list(db.execute(select(ScheduledTask).order_by(ScheduledTask.name.asc())).scalars().all())
    return [_serialize_task(row) for row in rows]


def list_task_logs(db: Session, task_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            select(ScheduledTaskLog)
            .where(ScheduledTaskLog.task_id == task_id)
            .order_by(desc(ScheduledTaskLog.created_at))
            .limit(max(1, min(limit, 200)))
        ).scalars().all()
    )
    return [
        {
            "id": row.id,
            "task_id": row.task_id,
            "created_at": row.created_at,
            "status": row.status,
            "message": row.message,
            "output_path": row.output_path,
            "payload": row.payload or {},
        }
        for row in rows
    ]


def _build_sdk_env() -> dict[str, str]:
    env: dict[str, str] = {}

    def _pick(key: str, archie_val: str, fallback_val: str) -> None:
        val = (archie_val or fallback_val or "").strip()
        if val:
            env[key] = val

    env["T212_BASE_ENV"] = settings.t212_base_env
    _pick("T212_API_KEY_INVEST", settings.archie_t212_api_key_invest, settings.t212_api_key_invest)
    _pick("T212_API_SECRET_INVEST", settings.archie_t212_api_secret_invest, settings.t212_api_secret_invest)
    _pick("T212_INVEST_API_KEY", settings.archie_t212_api_key_invest, settings.t212_invest_api_key)
    _pick("T212_INVEST_API_SECRET", settings.archie_t212_api_secret_invest, settings.t212_invest_api_secret)
    _pick("T212_API_KEY_STOCKS_ISA", settings.archie_t212_api_key_stocks_isa, settings.t212_api_key_stocks_isa)
    _pick("T212_API_SECRET_STOCKS_ISA", settings.archie_t212_api_secret_stocks_isa, settings.t212_api_secret_stocks_isa)
    _pick("T212_STOCKS_ISA_API_KEY", settings.archie_t212_api_key_stocks_isa, settings.t212_stocks_isa_api_key)
    _pick("T212_STOCKS_ISA_API_SECRET", settings.archie_t212_api_secret_stocks_isa, settings.t212_stocks_isa_api_secret)
    return env


def _extract_text_from_sdk_message(message: Any) -> str:
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                out.append(text)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                out.append(item["text"])
        return "\n".join(out)
    if isinstance(content, str):
        return content
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    return ""


def _extract_json_block(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        try:
            payload = json.loads(fenced.group(1))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    for match in re.finditer(r"\{[\s\S]*\}", text):
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    return None


def _run_claude_prompt(task: ScheduledTask) -> tuple[str, dict[str, Any]]:
    from claude_agent_sdk import ClaudeAgentOptions, query

    env_key = (settings.anthropic_api_key or "").strip()
    if env_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", env_key)

    cwd = resolve_sdk_cwd()
    setting_sources = parse_setting_sources(settings.claude_setting_sources, require_project=True)

    allowed_tools = ["Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"]
    if settings.agent_allow_bash:
        allowed_tools.append("Bash")

    mcp_servers: dict[str, Any] = {}
    t212_script = _MCP_SERVER_DIR / "t212.py"
    yfinance_script = _MCP_SERVER_DIR / "marketdata.py"
    scheduler_script = _MCP_SERVER_DIR / "scheduler.py"
    if t212_script.is_file():
        mcp_servers["trading212"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(t212_script)],
            "env": _build_sdk_env(),
        }
        allowed_tools.extend(_T212_MCP_TOOLS)

    # The marketdata + scheduler MCP servers import from the `app`
    # package, so the backend root must be on PYTHONPATH when they
    # are launched as stdio subprocesses by the SDK.
    _backend_root = str(_MCP_SERVER_DIR.parent)

    # The scheduler (and marketdata) MCP servers use the same SQLite
    # database as the main app.  Because the default DATABASE_URL is a
    # relative path (sqlite:///./mypf.db) and MCP subprocesses may run
    # with a different CWD, we resolve it to an absolute path and pass
    # it explicitly so every process opens the *same* database file.
    _db_url = settings.database_url
    if _db_url.startswith("sqlite:///./") or _db_url.startswith("sqlite:///mypf"):
        _rel = _db_url.replace("sqlite:///", "", 1)
        _abs = str((Path(_backend_root) / _rel).resolve())
        _db_url = f"sqlite:///{_abs}"
    _mcp_env = {"PYTHONPATH": _backend_root, "DATABASE_URL": _db_url}

    if yfinance_script.is_file():
        mcp_servers["marketdata"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(yfinance_script)],
            "env": _mcp_env,
        }
        allowed_tools.extend(_MARKET_MCP_TOOLS)
    if scheduler_script.is_file():
        mcp_servers["scheduler"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(scheduler_script)],
            "env": _mcp_env,
        }
        allowed_tools.extend(_SCHEDULER_MCP_TOOLS)

    options = ClaudeAgentOptions(
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": (
                "You are Archie, Josh's portfolio copilot and leveraged strategy operator. "
                "Always respect configured risk rails. "
                f"Today is {datetime.now(tz=timezone.utc).strftime('%A %d %B %Y %H:%M UTC')}. "
                "Tool routing: "
                "Use mcp__marketdata__ tools (get_price_snapshot, get_price_history_rows, get_technical_snapshot) "
                "for ALL price quotes, historical candles, and technical analysis — these are yfinance-backed "
                "with no rate limits. "
                "Use mcp__trading212__ tools ONLY for account-specific operations: positions, balances, "
                "orders, execution, dividends, and transaction history. "
                "Never use T212 tools to look up prices or market data — T212 has strict API rate limits "
                "(1 req/s for positions, 1 req/50s for instrument search)."
            ),
        },
        model=task.model or settings.claude_model,
        cwd=str(cwd),
        setting_sources=setting_sources,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers,
        max_turns=settings.agent_max_turns,
    )

    async def _run() -> str:
        # Only keep the *last* assistant text block — earlier blocks are
        # intermediate reasoning ("Let me search for …") emitted between
        # tool calls, not the polished final report.
        last_text = ""
        async for message in query(prompt=task.prompt, options=options):
            text = _extract_text_from_sdk_message(message)
            if text:
                last_text = text
        return last_text.strip()

    output = anyio.run(_run)
    meta: dict[str, Any] = {}
    parsed = _extract_json_block(output)
    if parsed:
        meta["json"] = parsed
    return output, meta


def _cron_log_path(task_name: str, content: str, *, task_kind: str = "", description: str = "") -> str:
    root = project_root() / ".claude" / "runtime" / "artifacts" / "scheduled" / task_name
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")

    frontmatter = f"""---
type: scheduled
task_name: {task_name}
task_kind: {task_kind}
created_at: {datetime.now(tz=timezone.utc).isoformat()}
title: {description or task_name}
---

"""
    path = root / f"{stamp}.md"
    path.write_text(frontmatter + content, encoding="utf-8")
    return str(path)


def _record_log(db: Session, task: ScheduledTask, *, status: str, message: str, payload: dict[str, Any], output_path: str | None) -> ScheduledTaskLog:
    row = ScheduledTaskLog(
        task_id=task.id,
        status=status,
        message=message,
        payload=payload,
        output_path=output_path,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _run_task_impl(db: Session, task: ScheduledTask) -> tuple[str, dict[str, Any], str | None]:
    kind = str((task.meta or {}).get("task_kind") or "claude").strip().lower()
    description = str((task.meta or {}).get("description") or task.name)

    if kind == "leveraged_cycle":
        result = run_leveraged_cycle(db, source_task_id=task.id)
        content = "# Leveraged Cycle\n\n```json\n" + json.dumps(result, indent=2, default=str) + "\n```\n"
        path = _cron_log_path(task.name, content, task_kind=kind, description=description)
        return "ok", {"result": result}, path

    if kind == "leveraged_scan":
        result = scan_signals(db, source_task_id=task.id)
        content = "# Leveraged Scan\n\n```json\n" + json.dumps(result, indent=2, default=str) + "\n```\n"
        path = _cron_log_path(task.name, content, task_kind=kind, description=description)
        return "ok", {"result": result}, path

    if kind == "leveraged_monitor":
        result = monitor_open_trades(db)
        content = "# Leveraged Monitor\n\n```json\n" + json.dumps(result, indent=2, default=str) + "\n```\n"
        path = _cron_log_path(task.name, content, task_kind=kind, description=description)
        return "ok", {"result": result}, path

    output, meta = _run_claude_prompt(task)
    path = _cron_log_path(task.name, output or "(no output)", task_kind=kind, description=description)

    policy_updates = None
    parsed = meta.get("json") if isinstance(meta.get("json"), dict) else None
    if isinstance(parsed, dict):
        maybe_updates = parsed.get("policy_updates")
        if isinstance(maybe_updates, dict):
            policy_updates = update_policy(db, maybe_updates, actor="archie")

    payload: dict[str, Any] = {"meta": meta}
    if policy_updates is not None:
        payload["policy_updates_applied"] = policy_updates

    return "ok", payload, path


def _touch_task_after_run(db: Session, task: ScheduledTask, *, status: str) -> None:
    now = _utcnow()
    task.last_run_at = now
    task.last_status = status
    task.next_run_at = _next_run_utc(task.cron_expr, task.timezone, now)
    if status == "ok":
        task.run_count = int(task.run_count or 0) + 1
    else:
        task.failure_count = int(task.failure_count or 0) + 1
    db.add(task)
    db.commit()


def run_task_now(db: Session, task_id: str) -> dict[str, Any]:
    task = db.get(ScheduledTask, task_id)
    if not task:
        raise RuntimeError(f"task {task_id} not found")

    try:
        status, payload, output_path = _run_task_impl(db, task)
        _record_log(
            db,
            task,
            status=status,
            message="task completed" if status == "ok" else "task finished with errors",
            payload=payload,
            output_path=output_path,
        )
        _touch_task_after_run(db, task, status=status)
        db.refresh(task)
        return {"task": _serialize_task(task), "status": status, "payload": payload, "output_path": output_path}
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        _record_log(db, task, status="error", message=message, payload={"error": message}, output_path=None)
        _touch_task_after_run(db, task, status="error")
        raise


def start_task_background(db: Session, task_id: str) -> dict[str, Any]:
    """Mark a task as running and execute it in a background thread.

    Returns immediately with the task in ``running`` status.  The actual
    execution happens in a daemon thread that opens its own DB session.
    """
    from app.core.database import SessionLocal

    task = db.get(ScheduledTask, task_id)
    if not task:
        raise RuntimeError(f"task {task_id} not found")

    if task.last_status == "running":
        return {"task": _serialize_task(task), "status": "already_running"}

    # Mark running and commit so callers (and pollers) see the state immediately.
    task.last_status = "running"
    db.add(task)
    db.commit()
    db.refresh(task)
    snapshot = _serialize_task(task)

    def _background() -> None:
        bg_db = SessionLocal()
        try:
            bg_task = bg_db.get(ScheduledTask, task_id)
            if bg_task is None:
                return
            try:
                status, payload, output_path = _run_task_impl(bg_db, bg_task)
                _record_log(
                    bg_db,
                    bg_task,
                    status=status,
                    message="task completed" if status == "ok" else "task finished with errors",
                    payload=payload,
                    output_path=output_path,
                )
                _touch_task_after_run(bg_db, bg_task, status=status)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                _record_log(bg_db, bg_task, status="error", message=message, payload={"error": message}, output_path=None)
                _touch_task_after_run(bg_db, bg_task, status="error")
        finally:
            bg_db.close()

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()

    return {"task": snapshot, "status": "started"}


def run_due_tasks(db: Session) -> list[dict[str, Any]]:
    now = _utcnow()
    due_tasks = list(
        db.execute(
            select(ScheduledTask).where(
                ScheduledTask.enabled.is_(True),
                (ScheduledTask.next_run_at.is_(None)) | (ScheduledTask.next_run_at <= now),
            )
        ).scalars().all()
    )

    results: list[dict[str, Any]] = []
    for task in due_tasks:
        try:
            results.append(run_task_now(db, task.id))
        except Exception as exc:  # noqa: BLE001
            results.append({"task_id": task.id, "status": "error", "error": str(exc)})
    return results


def create_task(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise RuntimeError("task name is required")

    exists = db.execute(select(ScheduledTask).where(ScheduledTask.name == name)).scalar_one_or_none()
    if exists:
        raise RuntimeError(f"task with name '{name}' already exists")

    cron_expr = str(payload.get("cron_expr", "")).strip()
    if not cron_expr:
        raise RuntimeError("cron_expr is required")

    timezone_name = str(payload.get("timezone", "Europe/London") or "Europe/London").strip()
    _ = _next_run_utc(cron_expr, timezone_name)

    task = ScheduledTask(
        name=name,
        cron_expr=cron_expr,
        timezone=timezone_name,
        model=str(payload.get("model") or settings.claude_model),
        prompt=str(payload.get("prompt") or ""),
        enabled=bool(payload.get("enabled", True)),
        next_run_at=_next_run_utc(cron_expr, timezone_name),
        meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        last_status="idle",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


def update_task(db: Session, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    task = db.get(ScheduledTask, task_id)
    if not task:
        raise RuntimeError(f"task {task_id} not found")

    if "name" in patch:
        name = str(patch.get("name") or "").strip()
        if not name:
            raise RuntimeError("task name cannot be empty")
        other = db.execute(
            select(ScheduledTask).where(ScheduledTask.name == name, ScheduledTask.id != task.id)
        ).scalar_one_or_none()
        if other:
            raise RuntimeError(f"task name '{name}' already exists")
        task.name = name

    if "cron_expr" in patch:
        task.cron_expr = str(patch.get("cron_expr") or task.cron_expr).strip()

    if "timezone" in patch:
        task.timezone = str(patch.get("timezone") or task.timezone).strip() or "Europe/London"

    if "model" in patch:
        task.model = str(patch.get("model") or task.model).strip() or task.model

    if "prompt" in patch:
        task.prompt = str(patch.get("prompt") or "")

    if "enabled" in patch:
        task.enabled = bool(patch.get("enabled"))

    if "meta" in patch and isinstance(patch.get("meta"), dict):
        task.meta = patch.get("meta") or {}

    task.next_run_at = _next_run_utc(task.cron_expr, task.timezone)
    task.updated_at = _utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


def delete_task(db: Session, task_id: str) -> bool:
    task = db.get(ScheduledTask, task_id)
    if not task:
        return False

    logs = list(db.execute(select(ScheduledTaskLog).where(ScheduledTaskLog.task_id == task_id)).scalars().all())
    for row in logs:
        db.delete(row)
    db.delete(task)
    db.commit()
    return True


def seed_default_tasks(db: Session) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for item in _DEFAULT_TASKS:
        existing = db.execute(select(ScheduledTask).where(ScheduledTask.name == item["name"])).scalar_one_or_none()
        if existing:
            # Ensure next_run_at is initialized for older rows.
            if existing.next_run_at is None:
                existing.next_run_at = _next_run_utc(existing.cron_expr, existing.timezone)
                db.add(existing)
                db.commit()
            continue
        created.append(create_task(db, item))
    return created
