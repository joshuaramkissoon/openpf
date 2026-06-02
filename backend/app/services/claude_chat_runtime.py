from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Awaitable, Callable

from claude_agent_sdk import CanUseTool, PermissionResultAllow, PermissionResultDeny, ResultMessage

from app.core.config import get_settings
from app.services.claude_sdk_config import (
    build_security_hooks, build_subagents, configure_sdk_auth, parse_setting_sources, resolve_sdk_cwd,
    resolve_t212_env,
    runtime_info as sdk_runtime_info,
    _T212_MCP_TOOLS, _MARKET_MCP_TOOLS, _SCHEDULER_MCP_TOOLS, _FORECAST_MCP_TOOLS,
    _FUNDAMENTALS_MCP_TOOLS, _INTEL_MCP_TOOLS, _WATCHLIST_MCP_TOOLS,
)

settings = get_settings()

_TOOL_LABELS: dict[str, str] = {
    "Read": "Reading files",
    "Write": "Writing a file",
    "Edit": "Editing a file",
    "Bash": "Running a command",
    "WebSearch": "Searching the web",
    "WebFetch": "Fetching a page",
    "Grep": "Searching code",
    "Glob": "Finding files",
    "Skill": "Running a skill",
    "Task": "Delegating to subagent",
    # T212 MCP tools
    "mcp__trading212__get_account_summary": "Checking account summary",
    "mcp__trading212__get_positions": "Fetching positions",
    "mcp__trading212__get_pending_orders": "Checking pending orders",
    "mcp__trading212__place_market_order": "Placing market order",
    "mcp__trading212__place_limit_order": "Placing limit order",
    "mcp__trading212__place_stop_order": "Placing stop order",
    "mcp__trading212__place_stop_limit_order": "Placing stop-limit order",
    "mcp__trading212__cancel_order": "Cancelling order",
    "mcp__trading212__search_instruments": "Searching instruments",
    "mcp__trading212__get_exchanges": "Checking exchanges",
    "mcp__trading212__get_order_history": "Fetching order history",
    "mcp__trading212__get_dividend_history": "Fetching dividend history",
    "mcp__trading212__get_transaction_history": "Fetching transactions",
    "mcp__trading212__request_csv_export": "Requesting CSV export",
    "mcp__trading212__get_csv_export_status": "Checking export status",
    # Market data MCP tools
    "mcp__marketdata__get_price_snapshot": "Checking latest price",
    "mcp__marketdata__get_price_history_rows": "Fetching price history",
    "mcp__marketdata__get_technical_snapshot": "Computing technicals",
    "mcp__marketdata__get_indicator_series": "Computing indicator series",
    "mcp__marketdata__get_risk_metrics": "Analyzing risk metrics",
    "mcp__marketdata__get_correlation_matrix": "Computing correlations",
    "mcp__marketdata__compare_assets": "Comparing assets",
    # Forecast MCP tools
    "mcp__forecast__forecast_prices": "Forecasting prices (Kronos)",
    "mcp__forecast__forecast_status": "Checking forecast model",
    # Fundamentals MCP tools
    "mcp__fundamentals__get_fundamentals": "Fetching fundamentals",
    "mcp__fundamentals__get_valuation": "Checking valuation ratios",
    "mcp__fundamentals__get_financial_statements": "Reading financial statements",
    "mcp__fundamentals__get_earnings_calendar": "Checking earnings calendar",
    # Scheduler MCP tools
    "mcp__scheduler__list_scheduled_tasks": "Listing scheduled tasks",
    "mcp__scheduler__create_scheduled_task": "Creating scheduled task",
    "mcp__scheduler__pause_scheduled_task": "Pausing scheduled task",
    "mcp__scheduler__resume_scheduled_task": "Resuming scheduled task",
    "mcp__scheduler__delete_scheduled_task": "Deleting scheduled task",
    "mcp__scheduler__run_scheduled_task_now": "Running task now",
    "mcp__scheduler__get_scheduled_task_logs": "Reading task logs",
    "mcp__scheduler__run_due_scheduled_tasks": "Running due tasks",
    "mcp__scheduler__seed_default_scheduled_tasks": "Seeding default tasks",
    # Watchlist MCP tools
    "mcp__watchlist__list_watchlist": "Reading the watchlist",
    "mcp__watchlist__add_to_watchlist": "Adding to the watchlist",
    "mcp__watchlist__update_watchlist_item": "Updating a watchlist item",
    "mcp__watchlist__remove_from_watchlist": "Removing from the watchlist",
    "mcp__watchlist__flag_watchlist_item": "Flagging a watchlist item",
}


_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_servers"


def _friendly_tool_name(raw: str) -> str:
    return _TOOL_LABELS.get(raw, raw)


def _build_sdk_env() -> dict[str, str]:
    """T212 creds (DB-sourced, in sync with the dashboard) + PYTHONPATH for the
    MCP subprocesses. Credentials live in subprocess memory only."""
    env = resolve_t212_env()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent)
    return env


def _extract_text_from_sdk_message(message: Any) -> str:
    if message is None:
        return ""

    if isinstance(message, str):
        return message

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)

    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text

    return ""


def _extract_stream_delta(message: Any) -> str:
    event = getattr(message, "event", None)
    if not isinstance(event, dict):
        return ""

    # Anthropic stream payload shape.
    delta = event.get("delta")
    if isinstance(delta, dict):
        text = delta.get("text")
        if isinstance(text, str):
            return text
    text_delta = event.get("text")
    if isinstance(text_delta, str):
        return text_delta
    return ""


def _extract_tool_events(message: Any) -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            tool_name = getattr(block, "name", None)
            tool_id = getattr(block, "id", None)
            tool_input = getattr(block, "input", None) or {}
            if not isinstance(tool_input, dict):
                tool_input = {}
            if isinstance(tool_name, str):
                out.append((str(tool_id or ""), tool_name, tool_input))
                continue
            if isinstance(block, dict):
                block_type = str(block.get("type", ""))
                if block_type == "tool_use":
                    name = block.get("name")
                    tool_use_id = block.get("id")
                    input_data = block.get("input") or {}
                    if not isinstance(input_data, dict):
                        input_data = {}
                    if isinstance(name, str):
                        out.append((str(tool_use_id or ""), name, input_data))

    event = getattr(message, "event", None)
    if isinstance(event, dict):
        evt_type = str(event.get("type", ""))
        if evt_type == "content_block_start":
            content_block = event.get("content_block")
            if isinstance(content_block, dict) and str(content_block.get("type", "")) == "tool_use":
                name = content_block.get("name")
                tool_use_id = content_block.get("id")
                input_data = content_block.get("input") or {}
                if not isinstance(input_data, dict):
                    input_data = {}
                if isinstance(name, str):
                    out.append((str(tool_use_id or ""), name, input_data))
    return out


def _extract_tool_results(message: Any) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            tool_use_id = getattr(block, "tool_use_id", None)
            if isinstance(tool_use_id, str):
                out.append((tool_use_id, bool(getattr(block, "is_error", False))))
                continue
            if isinstance(block, dict):
                block_type = str(block.get("type", ""))
                if block_type == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if isinstance(tool_use_id, str):
                        out.append((tool_use_id, bool(block.get("is_error", False))))

    event = getattr(message, "event", None)
    if isinstance(event, dict):
        evt_type = str(event.get("type", ""))
        if evt_type == "content_block_start":
            content_block = event.get("content_block")
            if isinstance(content_block, dict) and str(content_block.get("type", "")) == "tool_result":
                tool_use_id = content_block.get("tool_use_id")
                if isinstance(tool_use_id, str):
                    out.append((tool_use_id, bool(content_block.get("is_error", False))))
    return out


def _extract_input_json_delta(message: Any) -> tuple[int | None, str]:
    """Extract (content_block_index, partial_json) from input_json_delta events."""
    event = getattr(message, "event", None)
    if not isinstance(event, dict):
        return None, ""
    if str(event.get("type", "")) != "content_block_delta":
        return None, ""
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return None, ""
    if str(delta.get("type", "")) != "input_json_delta":
        return None, ""
    idx = event.get("index")
    partial = delta.get("partial_json", "")
    return idx, partial


def _contains_thinking(message: Any) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            if getattr(block, "thinking", None):
                return True
            if isinstance(block, dict) and str(block.get("type", "")) == "thinking":
                return True

    event = getattr(message, "event", None)
    if isinstance(event, dict):
        evt_type = str(event.get("type", ""))
        if evt_type == "content_block_start":
            content_block = event.get("content_block")
            if isinstance(content_block, dict) and str(content_block.get("type", "")) == "thinking":
                return True
        if evt_type == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and str(delta.get("type", "")) in {"thinking_delta", "signature_delta"}:
                return True
    return False


@dataclass
class ReplyResult:
    """Result from a chat reply including stop metadata."""
    text: str
    stop_reason: str | None = None
    result_subtype: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None


# Asks the user a clarifying question (the AskUserQuestion tool's `questions`
# payload) and resolves to the `answers` dict ({question_text: label | [labels]}).
# Set per-turn by stream_reply; the per-session can_use_tool callback reads it.
QuestionAsker = Callable[[dict], Awaitable[dict | None]]

# Returned to Claude when AskUserQuestion can't be served interactively (no live
# UI channel) so it falls back to asking the question in plain text instead of
# the tool hard-erroring.
_ASK_FALLBACK_MSG = (
    "Interactive questions aren't available right now. Ask your clarifying "
    "question directly in your text reply — state the options clearly — and the "
    "user will answer in their next message."
)


@dataclass
class _RuntimeSession:
    client: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connected: bool = False
    last_used: float = field(default_factory=monotonic)
    # Active clarifying-question channel for the in-flight turn (None when idle).
    question_asker: QuestionAsker | None = None


class ClaudeChatRuntime:
    def __init__(self) -> None:
        self._sessions: dict[str, _RuntimeSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._info: dict[str, Any] = {}
        self._options = self._build_options()

    def _build_options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions

        configure_sdk_auth()

        sdk_cwd = resolve_sdk_cwd()
        setting_sources = parse_setting_sources(settings.claude_setting_sources, require_project=True)
        runtime = sdk_runtime_info()

        allowed_tools = ["Skill", "Read", "Glob", "Grep", "WebSearch", "WebFetch", "Task"]
        if settings.claude_chat_allow_writes:
            allowed_tools.extend(["Write", "Edit"])
        if settings.agent_allow_bash:
            allowed_tools.append("Bash")

        # MCP server config — T212 runs locally so HTTP calls use the
        # user's IP, bypassing Cloudflare datacenter-IP blocks.
        mcp_servers: dict[str, Any] = {}
        t212_script = _MCP_SERVER_DIR / "t212.py"
        market_script = _MCP_SERVER_DIR / "marketdata.py"
        scheduler_script = _MCP_SERVER_DIR / "scheduler.py"
        forecast_script = _MCP_SERVER_DIR / "forecast.py"
        fundamentals_script = _MCP_SERVER_DIR / "fundamentals.py"
        intel_script = _MCP_SERVER_DIR / "intel.py"
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

        # Resolve a possibly-relative SQLite DATABASE_URL to an absolute
        # path so MCP subprocesses (which may run with a different CWD)
        # open the *same* database file as the main app.
        _db_url = settings.database_url
        if _db_url.startswith("sqlite:///./") or _db_url.startswith("sqlite:///mypf"):
            _rel = _db_url.replace("sqlite:///", "", 1)
            _abs = str((Path(_backend_root) / _rel).resolve())
            _db_url = f"sqlite:///{_abs}"
        _mcp_env = {"PYTHONPATH": _backend_root, "DATABASE_URL": _db_url}

        if market_script.is_file():
            mcp_servers["marketdata"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(market_script)],
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

        # Forecast MCP server (Kronos). Uses the same env so `app` and
        # `vendor` are importable; torch loads lazily inside the subprocess
        # only when a forecast is actually requested.
        if forecast_script.is_file():
            mcp_servers["forecast"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(forecast_script)],
                "env": _mcp_env,
            }
            allowed_tools.extend(_FORECAST_MCP_TOOLS)

        # Fundamentals MCP server (yfinance). Uses the same env so `app` is
        # importable as a stdio subprocess.
        if fundamentals_script.is_file():
            mcp_servers["fundamentals"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fundamentals_script)],
                "env": _mcp_env,
            }
            allowed_tools.extend(_FUNDAMENTALS_MCP_TOOLS)

        # Intel MCP (Finnhub news + FRED macro). Reads provider keys from the DB
        # (ConfigStore data_providers) via DATABASE_URL in _mcp_env.
        if intel_script.is_file():
            mcp_servers["intel"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(intel_script)],
                "env": _mcp_env,
            }
            allowed_tools.extend(_INTEL_MCP_TOOLS)

        # Watchlist MCP — Archie CRUDs the tracked-ideas board + flags into Attention.
        watchlist_script = _MCP_SERVER_DIR / "watchlist.py"
        if watchlist_script.is_file():
            mcp_servers["watchlist"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(watchlist_script)],
                "env": _mcp_env,
            }
            allowed_tools.extend(_WATCHLIST_MCP_TOOLS)

        self._info = {
            **runtime,
            "allowed_tools": allowed_tools,
            "runtime": "chat",
            "permission_mode": "acceptEdits" if settings.claude_chat_allow_writes else "default",
            "mcp_servers": list(mcp_servers.keys()),
        }

        return ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": (
                    "You are Archie, Josh's portfolio copilot on the MyPF dashboard. "
                    "Explain clearly, highlight risk, and give actionable next steps. "
                    "Your identity and memory guidelines are in your project CLAUDE.md. "
                    "You have MCP market data tools for spot/historical/technical data, "
                    "fundamentals tools (get_fundamentals, get_valuation, get_financial_statements, "
                    "get_earnings_calendar) for company facts, valuation ratios, statements, and earnings, "
                    "scheduler tools for cron task management, watchlist tools "
                    "(list/add/update/remove + flag) for Josh's tracked-ideas board, and a Kronos forecasting "
                    "tool (forecast_prices) that projects a holding's close price over a "
                    "future horizon with p10/p50/p90 uncertainty bands. Treat forecasts as "
                    "probabilistic analysis with clear uncertainty, never as certainties or "
                    "executed trades. "
                    "There is also a leveraged trading engine (3x ISA ETPs, long via products like "
                    "3PLT and downside via INVERSE ETPs — T212 has no short selling) governed by "
                    "hard-enforced daily risk rails (profit target, loss limit, max trades, exposure/"
                    "per-position caps), and an autonomous daily scheduled loop (morning scan, midday/"
                    "EOD monitors, weekly review) that scans and proposes leveraged entries. You can "
                    "inspect and manage these via the scheduler tools. Leveraged products are identified "
                    "LIVE from T212 instrument metadata — the name encodes factor, direction, and underlying "
                    "(e.g. 'Leverage Shares 3x Long SanDisk SNDK'), and get_positions returns a `leverage` "
                    "field — trust that over any cached list. Never imply a rail can be bypassed. "
                    "To show a price chart, emit a fenced code block tagged `chart` whose body is JSON, e.g. "
                    "{\"ticker\": \"MU\", \"period\": \"6mo\", \"indicators\": [\"sma20\", \"sma50\"], \"forecast\": true}; "
                    "the dashboard renders it as an interactive candlestick chart with SMAs and the Kronos forecast "
                    "cone. NEVER use matplotlib, Python, or image files to draw charts. "
                    "When you recommend a name worth tracking (not an immediate trade), add it to the watchlist "
                    "with add_to_watchlist and a concise note of WHY — that note is the condition the watch "
                    "service later checks, so the idea resurfaces instead of being forgotten. "
                    "Do not claim a capability is unavailable before checking available tools."
                ),
            },
            model=settings.claude_chat_model,
            cwd=str(sdk_cwd),
            max_turns=max(4, min(settings.agent_max_turns, 12)),
            allowed_tools=allowed_tools,
            setting_sources=setting_sources,
            include_partial_messages=True,
            permission_mode="acceptEdits" if settings.claude_chat_allow_writes else None,
            env=_build_sdk_env(),
            mcp_servers=mcp_servers if mcp_servers else {},
            hooks=build_security_hooks(),
            agents=build_subagents(),
        )

    def _build_can_use_tool(self, chat_session_id: str) -> CanUseTool:
        """Permission callback for one session's SDK client.

        Only fires for tools that aren't auto-approved by `allowed_tools` /
        settings — in practice just AskUserQuestion (and any tool the model tries
        that we don't allow). We serve AskUserQuestion by routing its questions to
        the live turn's UI channel and feeding the user's selections back as the
        tool's `updated_input`; everything else is denied (the prior, no-callback
        behaviour for non-allowed tools)."""

        async def can_use_tool(tool_name: str, input_data: dict, context: Any):
            if tool_name != "AskUserQuestion":
                return PermissionResultDeny(
                    message="This tool isn't available in chat."
                )

            state = self._sessions.get(chat_session_id)
            asker = state.question_asker if state else None
            if asker is None:
                return PermissionResultDeny(message=_ASK_FALLBACK_MSG)

            try:
                answers = await asker(input_data)
            except Exception:
                return PermissionResultDeny(message=_ASK_FALLBACK_MSG)

            if not answers:
                # User dismissed the question without answering.
                return PermissionResultDeny(
                    message="The user dismissed the question. Continue with your best judgement or ask again in plain text."
                )

            return PermissionResultAllow(
                updated_input={
                    "questions": input_data.get("questions", []),
                    "answers": answers,
                }
            )

        return can_use_tool

    async def _get_session(self, chat_session_id: str) -> _RuntimeSession:
        existing = self._sessions.get(chat_session_id)
        if existing:
            return existing

        from claude_agent_sdk import ClaudeSDKClient

        async with self._sessions_lock:
            current = self._sessions.get(chat_session_id)
            if current:
                return current
            # Per-session options so each client's permission callback can route
            # AskUserQuestion to that session's live UI channel.
            options = replace(self._options, can_use_tool=self._build_can_use_tool(chat_session_id))
            created = _RuntimeSession(client=ClaudeSDKClient(options=options))
            self._sessions[chat_session_id] = created
            return created

    async def stream_reply(
        self,
        chat_session_id: str,
        prompt: str,
        on_delta: Callable[[str], Awaitable[None]],
        on_status: Callable[[str, str, dict | None], Awaitable[None]] | None = None,
        on_question: QuestionAsker | None = None,
    ) -> ReplyResult:
        state = await self._get_session(chat_session_id)

        async with state.lock:
            state.last_used = monotonic()
            # Expose this turn's clarifying-question channel to the session's
            # can_use_tool callback; clear it when the turn ends.
            state.question_asker = on_question
            last_status: tuple[str, str] | None = None

            async def emit_status(phase: str, message: str, tool_input: dict | None = None) -> None:
                nonlocal last_status
                if not on_status:
                    return
                # Only dedup ambient status phases (thinking, query, runtime).
                # Tool/subagent events represent distinct actions and must
                # always be emitted even if the label text is identical.
                if phase in ("thinking", "query", "runtime"):
                    marker = (phase, message)
                    if marker == last_status:
                        return
                    last_status = marker
                else:
                    last_status = None
                await on_status(phase, message, tool_input)

            if not state.connected:
                await emit_status("runtime", "Waking up...")
                await state.client.connect()
                state.connected = True

            await emit_status("query", "Looking at your portfolio...")
            await emit_status("thinking", "Thinking...")

            await state.client.query(prompt, session_id=chat_session_id)

            chunks: list[str] = []
            streamed = False
            # Snapshot of tool activity (start + result count) at the moment text
            # was last appended. When text resumes after intervening tool calls we
            # insert a paragraph break so the persisted reply keeps the visual
            # separation the streaming view gets for free (text runs split on tool
            # boundaries). Without this, "...the order." + "I'll get the FX rate..."
            # join with no separator and render as one mashed-together block.
            tool_marker_at_last_text = 0
            seen_tool_ids: set[str] = set()
            seen_tool_result_ids: set[str] = set()
            tool_name_by_id: dict[str, str] = {}
            tool_input_by_id: dict[str, dict] = {}
            stop_reason: str | None = None
            result_subtype: str | None = None
            cost_usd: float | None = None
            duration_ms_val: int | None = None
            num_turns_val: int | None = None
            active_subagents: dict[str, str] = {}  # task tool_use_id → subagent_type
            # Track Task tool calls whose input hasn't streamed yet.
            # Maps tool_use_id → accumulated partial JSON string.
            pending_task_inputs: dict[str, str] = {}
            # Map content block index → tool_use_id for correlating input_json_delta events.
            block_index_to_tool_id: dict[int, str] = {}

            async def _maybe_emit_subagent_start(tool_id: str, partial_json: str) -> None:
                """Try to extract subagent_type from accumulated JSON and emit subagent_start."""
                # Try full parse first
                subagent_type: str | None = None
                try:
                    parsed = json.loads(partial_json)
                    subagent_type = parsed.get("subagent_type")
                except (json.JSONDecodeError, TypeError):
                    pass
                # Fall back to regex on partial JSON
                if not subagent_type:
                    m = re.search(r'"subagent_type"\s*:\s*"([^"]+)"', partial_json)
                    if m:
                        subagent_type = m.group(1)
                if not subagent_type:
                    # Also try to find a 'description' field for a friendlier label
                    m = re.search(r'"description"\s*:\s*"([^"]+)"', partial_json)
                    if m:
                        subagent_type = m.group(1)
                if subagent_type:
                    pending_task_inputs.pop(tool_id, None)
                    active_subagents[tool_id] = subagent_type
                    await emit_status(
                        "subagent_start",
                        f"Delegating to {subagent_type}",
                        {"subagent_id": tool_id, "subagent_type": subagent_type},
                    )

            def _append_reply_text(text: str) -> None:
                """Append assistant text to `chunks`, inserting a paragraph break
                when the text resumes after intervening tool activity."""
                nonlocal tool_marker_at_last_text
                marker = len(seen_tool_ids) + len(seen_tool_result_ids)
                if chunks and marker > tool_marker_at_last_text:
                    chunks.append("\n\n")
                tool_marker_at_last_text = marker
                chunks.append(text)

            try:
                async for message in state.client.receive_response():
                    # Detect ResultMessage (final message in stream)
                    if isinstance(message, ResultMessage):
                        stop_reason = getattr(message, "stop_reason", None)
                        result_subtype = getattr(message, "subtype", None)
                        cost_usd = getattr(message, "total_cost_usd", None)
                        duration_ms_val = getattr(message, "duration_ms", None)
                        num_turns_val = getattr(message, "num_turns", None)
                        result_text = getattr(message, "result", None)
                        if isinstance(result_text, str) and result_text.strip() and not chunks:
                            chunks.append(result_text.strip())
                            await on_delta(result_text.strip())
                        continue

                    # Accumulate input_json_delta for pending Task tools
                    delta_idx, delta_json = _extract_input_json_delta(message)
                    if delta_idx is not None and delta_json:
                        tool_id_for_delta = block_index_to_tool_id.get(delta_idx)
                        if tool_id_for_delta and tool_id_for_delta in pending_task_inputs:
                            pending_task_inputs[tool_id_for_delta] += delta_json
                            await _maybe_emit_subagent_start(
                                tool_id_for_delta,
                                pending_task_inputs.get(tool_id_for_delta, ""),
                            )

                    # Detect if this message originates from within a subagent
                    parent_id = getattr(message, "parent_tool_use_id", None)
                    is_subagent_msg = parent_id is not None and parent_id in active_subagents

                    # If parent is still pending (subagent_start not yet emitted),
                    # force-emit with a fallback label so nested events aren't lost.
                    if parent_id and parent_id in pending_task_inputs and parent_id not in active_subagents:
                        pending_task_inputs.pop(parent_id, None)
                        active_subagents[parent_id] = "Subagent"
                        await emit_status(
                            "subagent_start",
                            "Delegating to Subagent",
                            {"subagent_id": parent_id, "subagent_type": "Subagent"},
                        )
                        is_subagent_msg = True

                    if is_subagent_msg:
                        # Process nested tool events (emit as subagent_tool_* phases)
                        for tool_id2, tool_name2, tool_input2 in _extract_tool_events(message):
                            key2 = tool_id2 or f"name:{tool_name2}"
                            if key2 in seen_tool_ids:
                                continue
                            seen_tool_ids.add(key2)
                            if tool_id2:
                                tool_name_by_id[tool_id2] = tool_name2
                            friendly2 = _friendly_tool_name(tool_name2)
                            await emit_status("subagent_tool_start", friendly2, {"subagent_id": parent_id})
                        for tool_id2, is_error2 in _extract_tool_results(message):
                            if tool_id2 in seen_tool_result_ids:
                                continue
                            seen_tool_result_ids.add(tool_id2)
                            tool_name2 = tool_name_by_id.get(tool_id2, "tool step")
                            friendly2 = _friendly_tool_name(tool_name2)
                            result_msg2 = f"{friendly2} — {'hit a snag' if is_error2 else 'done'}"
                            await emit_status("subagent_tool_result", result_msg2, {"subagent_id": parent_id})
                        # Skip regular processing for subagent messages (no text deltas from subagent go to user)
                        continue

                    if _contains_thinking(message):
                        await emit_status("thinking", "Thinking...")

                    for tool_id, tool_name, tool_input in _extract_tool_events(message):
                        key = tool_id or f"name:{tool_name}"
                        if key in seen_tool_ids:
                            continue
                        seen_tool_ids.add(key)

                        # Track block index → tool_id for input_json_delta correlation
                        event = getattr(message, "event", None)
                        if isinstance(event, dict) and tool_id:
                            block_idx = event.get("index")
                            if isinstance(block_idx, int):
                                block_index_to_tool_id[block_idx] = tool_id

                        if tool_name == "Task":
                            # Check if we already have subagent_type in the input
                            subagent_type = (tool_input or {}).get("subagent_type")
                            if subagent_type:
                                # Full input available (non-streaming or complete message)
                                if tool_id:
                                    active_subagents[tool_id] = subagent_type
                                await emit_status(
                                    "subagent_start",
                                    f"Delegating to {subagent_type}",
                                    {"subagent_id": tool_id or "", "subagent_type": subagent_type},
                                )
                            elif tool_id:
                                # Streaming: input is empty, defer until we get input_json_delta
                                pending_task_inputs[tool_id] = ""
                        else:
                            if tool_id:
                                tool_name_by_id[tool_id] = tool_name
                                tool_input_by_id[tool_id] = tool_input
                            # AskUserQuestion is surfaced as its own interactive
                            # card (via can_use_tool), not the tool-activity trail.
                            if tool_name != "AskUserQuestion":
                                friendly = _friendly_tool_name(tool_name)
                                await emit_status("tool_start", friendly, tool_input)

                    for tool_id, is_error in _extract_tool_results(message):
                        if tool_id in seen_tool_result_ids:
                            continue
                        seen_tool_result_ids.add(tool_id)
                        # Handle pending tasks that never got subagent_start emitted
                        if tool_id in pending_task_inputs and tool_id not in active_subagents:
                            pending_task_inputs.pop(tool_id, None)
                            active_subagents[tool_id] = "Subagent"
                            await emit_status(
                                "subagent_start",
                                "Delegating to Subagent",
                                {"subagent_id": tool_id, "subagent_type": "Subagent"},
                            )
                        if tool_id in active_subagents:
                            # Subagent completed
                            subagent_type = active_subagents.pop(tool_id)
                            result_msg = f"{subagent_type} — {'hit a snag' if is_error else 'done'}"
                            await emit_status(
                                "subagent_result",
                                result_msg,
                                {"subagent_id": tool_id, "subagent_type": subagent_type},
                            )
                        else:
                            tool_name = tool_name_by_id.get(tool_id, "tool step")
                            if tool_name == "AskUserQuestion":
                                continue  # represented by the question card
                            friendly = _friendly_tool_name(tool_name)
                            if is_error:
                                await emit_status("tool_result", f"{friendly} — hit a snag")
                            else:
                                await emit_status("tool_result", f"{friendly} — done")

                    delta = _extract_stream_delta(message)
                    if delta:
                        streamed = True
                        _append_reply_text(delta)
                        await on_delta(delta)
                        continue

                    if streamed:
                        continue

                    text = _extract_text_from_sdk_message(message)
                    if text:
                        _append_reply_text(text)
                        await on_delta(text)
            except Exception:
                # If we already collected some text, return what we have
                # rather than losing the partial response.
                if not chunks:
                    raise
            finally:
                # The question channel is turn-scoped; never let it leak to the
                # next turn (or fire when no turn is active).
                state.question_asker = None

            out = "".join(chunks).strip()
            return ReplyResult(
                text=out or "No response generated.",
                stop_reason=stop_reason,
                result_subtype=result_subtype,
                cost_usd=cost_usd,
                duration_ms=duration_ms_val,
                num_turns=num_turns_val,
            )

    async def shutdown(self) -> None:
        async with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for state in sessions:
            if not state.connected:
                continue
            try:
                await state.client.disconnect()
            except Exception:
                continue

    async def drop_session(self, chat_session_id: str) -> None:
        async with self._sessions_lock:
            state = self._sessions.pop(chat_session_id, None)

        if not state or not state.connected:
            return

        async with state.lock:
            try:
                await state.client.disconnect()
            except Exception:
                return

    async def interrupt(self, chat_session_id: str) -> bool:
        """Stop the in-flight turn for a session.

        Must be safe to call *concurrently* while `stream_reply` holds
        `state.lock` and is iterating `receive_response()`. We therefore do
        NOT take `state.lock` — we operate directly on the cached client.

        Preferred path: the SDK's `ClaudeSDKClient.interrupt()` (0.2.87),
        which sends a control "interrupt" request over the streaming
        transport, terminating the current turn while keeping the session
        connected for the next message. If that isn't available or fails,
        fall back to `drop_session` (disconnect the client, which kills the
        query); the next message transparently re-connects.

        Returns True if an interrupt/disconnect was attempted, False if
        there was no active connected session to stop.
        """
        state = self._sessions.get(chat_session_id)
        if not state or not state.connected:
            return False

        interrupt_fn = getattr(state.client, "interrupt", None)
        if callable(interrupt_fn):
            try:
                await interrupt_fn()
                return True
            except Exception:
                # interrupt() is only valid in streaming mode with an
                # in-flight turn; if it raises (e.g. nothing to interrupt,
                # or the control channel rejected it), fall back to a hard
                # disconnect so the turn is guaranteed to stop.
                pass

        await self.drop_session(chat_session_id)
        return True

    def runtime_info(self) -> dict[str, Any]:
        return dict(self._info)

    async def check_mcp_health(self) -> dict[str, dict[str, str]]:
        """Spawn each configured MCP server and check if it stays alive.

        A stdio MCP server that starts successfully will block on stdin.
        If it crashes on startup it will exit within a few seconds.
        """
        servers: dict[str, Any] = self._options.mcp_servers or {}
        if not servers:
            return {}

        timeout_secs = 3
        results: dict[str, dict[str, str]] = {}

        async def _probe(name: str, cfg: dict[str, Any]) -> dict[str, str]:
            cmd = [cfg["command"]] + cfg.get("args", [])
            env = {**os.environ, **cfg.get("env", {})}
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except Exception as exc:
                return {"status": "error", "detail": f"spawn failed: {exc}"}

            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_secs)
                # Process exited within the timeout — it crashed.
                stderr_bytes = await proc.stderr.read() if proc.stderr else b""
                detail = stderr_bytes.decode(errors="replace").strip()
                return {
                    "status": "error",
                    "detail": detail or f"exited with code {proc.returncode}",
                }
            except asyncio.TimeoutError:
                # Still alive after timeout — healthy.
                return {"status": "ok", "detail": "server started and listening"}
            finally:
                # Clean up the subprocess.
                if proc.returncode is None:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=2)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass

        tasks = {name: _probe(name, cfg) for name, cfg in servers.items()}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for (name, _), result in zip(tasks.items(), gathered):
            if isinstance(result, Exception):
                results[name] = {"status": "error", "detail": str(result)}
            else:
                results[name] = result

        return results


claude_chat_runtime = ClaudeChatRuntime()
