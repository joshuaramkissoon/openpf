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
from app.services.claude_sdk_config import (
    build_security_hooks, build_subagents, configure_sdk_auth,
    parse_setting_sources, project_root, resolve_sdk_cwd, resolve_t212_env,
    _T212_MCP_TOOLS, _MARKET_MCP_TOOLS, _SCHEDULER_MCP_TOOLS,
    _FUNDAMENTALS_MCP_TOOLS,
)
from app.services import costs_service
from app.services.config_store import ConfigStore
from app.services.leveraged_service import monitor_open_trades, run_leveraged_cycle, scan_signals, update_policy

settings = get_settings()

_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_servers"

_DEFAULT_TASKS: list[dict[str, Any]] = [
    {
        "name": "lev_morning_scan",
        "cron_expr": "30 7 * * 1-5",
        "timezone": "Europe/London",
        "model": settings.claude_agent_model,
        "enabled": True,
        "meta": {
            "task_kind": "leveraged_cycle",
            "description": "Morning leveraged cycle (monitor + scan) → markdown report",
        },
        # NOTE: leveraged_cycle is a deterministic engine run; this prompt text
        # is NOT sent to an LLM. The scheduler renders the engine output as a
        # human-readable markdown report (see _render_leveraged_cycle_md), so a
        # readable artifact is produced even when no setups qualify.
        "prompt": (
            "Deterministic leveraged morning cycle: monitor open trades against stop/take-profit/time rules, "
            "then scan the configured universe for new setups within rails. Entries execute only if "
            "auto-execute is enabled and rails permit; otherwise setups are logged as proposals. "
            "Output is a rendered markdown report (monitor + scan), not raw JSON."
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
        "model": settings.claude_agent_model,
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
        "model": settings.claude_agent_model,
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
    {
        "name": "daily_alpha_goal",
        "cron_expr": "45 7 * * 1-5",
        "timezone": "Europe/London",
        "model": settings.claude_agent_model,
        "enabled": False,
        "meta": {
            "task_kind": "claude_with_goal",
            "description": "Daily alpha goal — scan + propose entries toward a £/day target",
            "goal": {"target_gbp": 40.0, "loss_limit_gbp": 60.0, "max_trades": 3, "window": "day"},
        },
        "prompt": (
            "Pursue today's profit target within the GOAL CONTEXT and risk rails above. Steps:\n"
            "1. Check today's realized P&L, open positions, and trades placed so far (T212 + marketdata tools).\n"
            "2. If the target is already hit or a limit is breached, STOP — report status only.\n"
            "3. Otherwise scan the leveraged universe with the marketdata tools (technicals, risk metrics, "
            "compare_assets) and a Kronos forecast on the top candidates. Delegate heavier quant work to the "
            "'quant' subagent.\n"
            "4. Rank the best 1-2 entries with hypothesis, forecast cone, sizing within rails, and invalidation. "
            "Propose them as intents — DO NOT execute unless auto-execute is enabled and rails permit.\n"
            "Output a concise markdown report ending with a JSON block {\"proposals\": [...]}."
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
    """T212 creds for the MCP subprocesses, DB-sourced (in sync with the
    dashboard). Credentials live in subprocess memory only."""
    return resolve_t212_env()


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


def _run_claude_prompt(task: ScheduledTask, goal_context: str = "") -> tuple[str, dict[str, Any], dict]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    configure_sdk_auth()

    cwd = resolve_sdk_cwd()
    setting_sources = parse_setting_sources(settings.claude_setting_sources, require_project=True)

    allowed_tools = ["Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "Task"]
    if settings.agent_allow_bash:
        allowed_tools.append("Bash")

    mcp_servers: dict[str, Any] = {}
    t212_script = _MCP_SERVER_DIR / "t212.py"
    yfinance_script = _MCP_SERVER_DIR / "marketdata.py"
    scheduler_script = _MCP_SERVER_DIR / "scheduler.py"
    fundamentals_script = _MCP_SERVER_DIR / "fundamentals.py"
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
    if fundamentals_script.is_file():
        mcp_servers["fundamentals"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(fundamentals_script)],
            "env": _mcp_env,
        }
        allowed_tools.extend(_FUNDAMENTALS_MCP_TOOLS)

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
        hooks=build_security_hooks(),
        agents=build_subagents(),
    )

    async def _run() -> tuple[str, dict]:
        # Only keep the *last* assistant text block — earlier blocks are
        # intermediate reasoning ("Let me search for …") emitted between
        # tool calls, not the polished final report.
        last_text = ""
        cost_info: dict = {}
        _prompt = f"{goal_context.strip()}\n\n{task.prompt}" if goal_context.strip() else task.prompt
        async for message in query(prompt=_prompt, options=options):
            if isinstance(message, ResultMessage):
                cost_info = {
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "duration_ms": getattr(message, "duration_ms", None),
                    "num_turns": getattr(message, "num_turns", None),
                }
            text = _extract_text_from_sdk_message(message)
            if text:
                last_text = text
        return last_text.strip(), cost_info

    output, cost_info = anyio.run(_run)
    meta: dict[str, Any] = {}
    parsed = _extract_json_block(output)
    if parsed:
        meta["json"] = parsed
    return output, meta, cost_info


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


def _build_goal_context(db: Session, task: ScheduledTask) -> str:
    """Build a GOAL CONTEXT block for ``claude_with_goal`` scheduled tasks.

    Combines the leveraged session rails (config) with any per-task goal
    override in ``task.meta['goal']`` and instructs the agent to reason about
    trajectory toward the target and respect hard daily limits.
    """
    try:
        policy = ConfigStore(db).get_leveraged()
    except Exception:  # noqa: BLE001
        policy = {}
    goal = (task.meta or {}).get("goal") or {}
    if not isinstance(goal, dict):
        goal = {}

    def _pick_num(*vals: Any) -> float:
        for v in vals:
            try:
                if v is not None and float(v) != 0.0:
                    return float(v)
            except (TypeError, ValueError):
                continue
        return 0.0

    target = _pick_num(goal.get("target_gbp"), policy.get("daily_profit_target_gbp"))
    loss_limit = _pick_num(goal.get("loss_limit_gbp"), policy.get("daily_loss_limit_gbp"))
    try:
        max_trades = int(goal.get("max_trades") or policy.get("max_daily_trades") or 0)
    except (TypeError, ValueError):
        max_trades = 0
    window = str(goal.get("window") or "day").strip() or "day"
    notes = str(goal.get("notes") or "").strip()

    per_pos = float(policy.get("per_position_notional", 200.0) or 200.0)
    max_exp = float(policy.get("max_total_exposure", 600.0) or 600.0)
    max_open = int(policy.get("max_open_positions", 3) or 3)
    tp = float(policy.get("take_profit_pct", 0.08) or 0.08)
    sl = float(policy.get("stop_loss_pct", 0.05) or 0.05)
    today = datetime.now(tz=timezone.utc).strftime("%A %d %B %Y")

    lines = [
        "## GOAL CONTEXT — read before acting",
        f"Today: {today}. This is a goal-driven session: capture small, consistent alpha within hard rails.",
    ]
    if target > 0:
        lines.append(
            f"- PROFIT TARGET: £{target:,.2f} per {window}. Once today's REALIZED P&L >= this, STOP opening "
            "new positions (managing/closing existing ones is fine)."
        )
    else:
        lines.append("- PROFIT TARGET: none set — optimise risk-adjusted return without overtrading.")
    if loss_limit > 0:
        lines.append(
            f"- LOSS LIMIT: £{loss_limit:,.2f} per {window}. If today's REALIZED P&L <= -£{loss_limit:,.2f}, "
            "STOP for the day — no new entries."
        )
    if max_trades > 0:
        lines.append(f"- MAX NEW TRADES today: {max_trades}. Do not exceed.")
    lines.append(
        f"- EXPOSURE RAILS: <= £{max_exp:,.0f} total, <= £{per_pos:,.0f} per position, <= {max_open} open at "
        f"once. Entries use +{tp * 100:.0f}% take-profit / -{sl * 100:.0f}% stop-loss."
    )
    lines.append(
        "Before deciding: use your tools to determine TODAY's realized P&L, current open positions, and how "
        "many trades you have already placed today. Reason about TRAJECTORY toward the target, not statelessly. "
        "If any limit is already hit, do NOT open new positions — just report status."
    )
    lines.append(
        "At the end, append ONE status line (date | realized P&L | trades today | open exposure | action taken) "
        "to memory/leveraged/daily-goal.md (create it if absent) so future runs have continuity."
    )
    if notes:
        lines.append(f"Operator notes: {notes}")
    return "\n".join(lines)


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_gbp(value: Any) -> str:
    try:
        return f"£{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _render_leveraged_monitor_md(result: dict[str, Any]) -> str:
    """Render monitor_open_trades output as a human-readable markdown report."""
    checked = int(result.get("checked", 0) or 0)
    closed = int(result.get("closed", 0) or 0)
    items = result.get("items") or []
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Leveraged Monitor",
        f"_{stamp}_",
        "",
        f"**Open trades checked:** {checked} · **Closed this run:** {closed}",
        "",
    ]
    if not items:
        lines.append("No open leveraged positions to monitor. Nothing to do.")
        return "\n".join(lines) + "\n"

    lines.append("| Symbol | Current price | Return | Action |")
    lines.append("|---|---|---|---|")
    for it in items:
        reason = it.get("close_reason")
        action = f"CLOSED ({reason})" if reason else "held"
        price = it.get("current_price")
        price_str = f"{float(price):,.2f}" if isinstance(price, (int, float)) else "—"
        lines.append(
            f"| {it.get('symbol', '—')} | {price_str} | {_fmt_pct(it.get('return_pct'))} | {action} |"
        )
    return "\n".join(lines) + "\n"


def _render_leveraged_scan_md(result: dict[str, Any]) -> str:
    """Render scan_signals output as a human-readable markdown report.

    Always produces a readable artifact — even when zero setups qualify, it
    states what was checked and why nothing was proposed (the recurring
    lessons.md complaint about raw-JSON 'nothing found' dumps).
    """
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    created = int(result.get("created", 0) or 0)
    executed = int(result.get("executed", 0) or 0)
    signals = result.get("signals") or []
    failures = result.get("failures") or []
    reason = result.get("reason")
    policy = result.get("policy") or {}

    lines = [
        "# Leveraged Scan",
        f"_{stamp}_",
        "",
        f"**New setups proposed:** {created} · **Auto-executed:** {executed} · "
        f"**Open positions:** {result.get('open_positions', 0)} · "
        f"**Open exposure:** {_fmt_gbp(result.get('open_exposure'))}",
        "",
    ]

    if reason:
        lines.append(f"> No new entries: {reason}.")
        lines.append("")

    if signals:
        lines.append("## Proposed setups")
        lines.append("")
        lines.append("| Symbol | Direction | Ref price | Notional | Conf. | Exp. edge | SL / TP |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in signals:
            sl_tp = f"{_fmt_pct(-abs(float(s.get('stop_loss_pct') or 0)))} / {_fmt_pct(s.get('take_profit_pct'))}"
            ref = s.get("reference_price")
            ref_str = f"{float(ref):,.2f}" if isinstance(ref, (int, float)) else "—"
            lines.append(
                f"| {s.get('symbol', '—')} | {s.get('direction', '—')} | {ref_str} | "
                f"{_fmt_gbp(s.get('target_notional'))} | "
                f"{float(s.get('confidence') or 0):.0%} | {_fmt_pct(s.get('expected_edge'))} | {sl_tp} |"
            )
            rationale = str(s.get("rationale") or "").strip()
            if rationale:
                lines.append(f"|  | _{rationale}_ |  |  |  |  |  |")
        lines.append("")
    elif not reason:
        lines.append(
            "No setups qualified this run — the scanned universe showed no entries meeting the "
            "momentum/technical thresholds. Nothing proposed."
        )
        lines.append("")

    if failures:
        lines.append("## Data / execution issues")
        for f in failures:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("## Rails in force")
    lines.append(
        f"- Per-position {_fmt_gbp(policy.get('per_position_notional'))} · "
        f"max total {_fmt_gbp(policy.get('max_total_exposure'))} · "
        f"max open {policy.get('max_open_positions', '—')} · "
        f"auto-execute {'ON' if policy.get('auto_execute_enabled') else 'OFF'}"
    )
    return "\n".join(lines) + "\n"


def _render_leveraged_cycle_md(result: dict[str, Any]) -> str:
    """Render run_leveraged_cycle (monitor + scan) as one markdown report."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    monitor = result.get("monitor") or {}
    scan = result.get("scan") or {}

    parts = [
        "# Leveraged Morning Cycle",
        f"_{stamp}_",
        "",
        "Deterministic engine run: monitor open trades, then scan for new setups within rails. "
        "Entries execute only if auto-execute is enabled and rails permit; otherwise they are proposals.",
        "",
        "---",
        "",
        _render_leveraged_monitor_md(monitor),
        "",
        "---",
        "",
        _render_leveraged_scan_md(scan),
    ]
    return "\n".join(parts)


def _run_task_impl(db: Session, task: ScheduledTask) -> tuple[str, dict[str, Any], str | None, dict]:
    kind = str((task.meta or {}).get("task_kind") or "claude").strip().lower()
    description = str((task.meta or {}).get("description") or task.name)

    if kind == "leveraged_cycle":
        result = run_leveraged_cycle(db, source_task_id=task.id)
        content = _render_leveraged_cycle_md(result)
        path = _cron_log_path(task.name, content, task_kind=kind, description=description)
        return "ok", {"result": result}, path, {}

    if kind == "leveraged_scan":
        result = scan_signals(db, source_task_id=task.id)
        content = _render_leveraged_scan_md(result)
        path = _cron_log_path(task.name, content, task_kind=kind, description=description)
        return "ok", {"result": result}, path, {}

    if kind == "leveraged_monitor":
        result = monitor_open_trades(db)
        content = _render_leveraged_monitor_md(result)
        path = _cron_log_path(task.name, content, task_kind=kind, description=description)
        return "ok", {"result": result}, path, {}

    goal_context = _build_goal_context(db, task) if kind == "claude_with_goal" else ""
    output, meta, cost_info = _run_claude_prompt(task, goal_context=goal_context)
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

    return "ok", payload, path, cost_info


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
        status, payload, output_path, cost_info = _run_task_impl(db, task)
        _record_log(
            db,
            task,
            status=status,
            message="task completed" if status == "ok" else "task finished with errors",
            payload=payload,
            output_path=output_path,
        )
        if cost_info.get("total_cost_usd") is not None or cost_info.get("duration_ms") is not None:
            costs_service.record(
                db,
                source="scheduled",
                source_id=task.name,
                model=task.model or settings.claude_model,
                total_cost_usd=cost_info.get("total_cost_usd"),
                duration_ms=cost_info.get("duration_ms"),
                num_turns=cost_info.get("num_turns"),
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
                status, payload, output_path, cost_info = _run_task_impl(bg_db, bg_task)
                _record_log(
                    bg_db,
                    bg_task,
                    status=status,
                    message="task completed" if status == "ok" else "task finished with errors",
                    payload=payload,
                    output_path=output_path,
                )
                if cost_info.get("total_cost_usd") is not None or cost_info.get("duration_ms") is not None:
                    costs_service.record(
                        bg_db,
                        source="scheduled",
                        source_id=bg_task.name,
                        model=bg_task.model or settings.claude_model,
                        total_cost_usd=cost_info.get("total_cost_usd"),
                        duration_ms=cost_info.get("duration_ms"),
                        num_turns=cost_info.get("num_turns"),
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
