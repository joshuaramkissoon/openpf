from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from app.services.claude_sdk_config import parse_setting_sources, resolve_sdk_cwd, runtime_info as sdk_runtime_info

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
}


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


def _friendly_tool_name(raw: str) -> str:
    return _TOOL_LABELS.get(raw, raw)


def _build_sdk_env() -> dict[str, str]:
    """Collect T212 credentials to pass via the SDK env field.

    Prefers ARCHIE_T212_* keys (unrestricted, read-only) over the
    backend's IP-restricted keys. Only non-empty values are included.
    Credentials live in subprocess memory only — never written to disk
    or exposed to file-reading tools.
    """
    env: dict[str, str] = {}

    def _pick(key: str, archie_val: str, fallback_val: str) -> None:
        val = (archie_val or fallback_val or "").strip()
        if val:
            env[key] = val

    env["T212_BASE_ENV"] = settings.t212_base_env

    # Invest account — prefer Archie's unrestricted keys
    _pick("T212_API_KEY_INVEST", settings.archie_t212_api_key_invest, settings.t212_api_key_invest)
    _pick("T212_API_SECRET_INVEST", settings.archie_t212_api_secret_invest, settings.t212_api_secret_invest)
    _pick("T212_INVEST_API_KEY", settings.archie_t212_api_key_invest, settings.t212_invest_api_key)
    _pick("T212_INVEST_API_SECRET", settings.archie_t212_api_secret_invest, settings.t212_invest_api_secret)

    # Stocks ISA — prefer Archie's unrestricted keys
    _pick("T212_API_KEY_STOCKS_ISA", settings.archie_t212_api_key_stocks_isa, settings.t212_api_key_stocks_isa)
    _pick("T212_API_SECRET_STOCKS_ISA", settings.archie_t212_api_secret_stocks_isa, settings.t212_api_secret_stocks_isa)
    _pick("T212_STOCKS_ISA_API_KEY", settings.archie_t212_api_key_stocks_isa, settings.t212_stocks_isa_api_key)
    _pick("T212_STOCKS_ISA_API_SECRET", settings.archie_t212_api_secret_stocks_isa, settings.t212_stocks_isa_api_secret)

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
class _RuntimeSession:
    client: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connected: bool = False
    last_used: float = field(default_factory=monotonic)


class ClaudeChatRuntime:
    def __init__(self) -> None:
        self._sessions: dict[str, _RuntimeSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._info: dict[str, Any] = {}
        self._options = self._build_options()

    def _build_options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions

        env_key = (settings.anthropic_api_key or "").strip()
        if env_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", env_key)

        sdk_cwd = resolve_sdk_cwd()
        setting_sources = parse_setting_sources(settings.claude_setting_sources, require_project=True)
        runtime = sdk_runtime_info()

        allowed_tools = ["Skill", "Read", "Glob", "Grep", "WebSearch", "WebFetch"]
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
                    "You have MCP market data tools for spot/historical/technical data and "
                    "scheduler tools for cron task management. "
                    "Do not claim a capability is unavailable before checking available tools."
                ),
            },
            model=settings.claude_model,
            cwd=str(sdk_cwd),
            max_turns=max(4, min(settings.agent_max_turns, 12)),
            allowed_tools=allowed_tools,
            setting_sources=setting_sources,
            include_partial_messages=True,
            permission_mode="acceptEdits" if settings.claude_chat_allow_writes else None,
            env=_build_sdk_env(),
            mcp_servers=mcp_servers if mcp_servers else {},
        )

    async def _get_session(self, chat_session_id: str) -> _RuntimeSession:
        existing = self._sessions.get(chat_session_id)
        if existing:
            return existing

        from claude_agent_sdk import ClaudeSDKClient

        async with self._sessions_lock:
            current = self._sessions.get(chat_session_id)
            if current:
                return current
            created = _RuntimeSession(client=ClaudeSDKClient(options=self._options))
            self._sessions[chat_session_id] = created
            return created

    async def stream_reply(
        self,
        chat_session_id: str,
        prompt: str,
        on_delta: Callable[[str], Awaitable[None]],
        on_status: Callable[[str, str, dict | None], Awaitable[None]] | None = None,
    ) -> str:
        state = await self._get_session(chat_session_id)

        async with state.lock:
            state.last_used = monotonic()
            last_status: tuple[str, str] | None = None

            async def emit_status(phase: str, message: str, tool_input: dict | None = None) -> None:
                nonlocal last_status
                if not on_status:
                    return
                marker = (phase, message)
                if marker == last_status:
                    return
                last_status = marker
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
            seen_tool_ids: set[str] = set()
            seen_tool_result_ids: set[str] = set()
            tool_name_by_id: dict[str, str] = {}
            tool_input_by_id: dict[str, dict] = {}

            try:
                async for message in state.client.receive_response():
                    if _contains_thinking(message):
                        await emit_status("thinking", "Thinking...")

                    for tool_id, tool_name, tool_input in _extract_tool_events(message):
                        key = tool_id or f"name:{tool_name}"
                        if key in seen_tool_ids:
                            continue
                        seen_tool_ids.add(key)
                        if tool_id:
                            tool_name_by_id[tool_id] = tool_name
                            tool_input_by_id[tool_id] = tool_input
                        friendly = _friendly_tool_name(tool_name)
                        await emit_status("tool_start", friendly, tool_input)

                    for tool_id, is_error in _extract_tool_results(message):
                        if tool_id in seen_tool_result_ids:
                            continue
                        seen_tool_result_ids.add(tool_id)
                        tool_name = tool_name_by_id.get(tool_id, "tool step")
                        friendly = _friendly_tool_name(tool_name)
                        if is_error:
                            await emit_status("tool_result", f"{friendly} — hit a snag")
                        else:
                            await emit_status("tool_result", f"{friendly} — done")

                    delta = _extract_stream_delta(message)
                    if delta:
                        streamed = True
                        chunks.append(delta)
                        await on_delta(delta)
                        continue

                    if streamed:
                        continue

                    text = _extract_text_from_sdk_message(message)
                    if text:
                        chunks.append(text)
                        await on_delta(text)
            except Exception:
                # If we already collected some text, return what we have
                # rather than losing the partial response.
                if not chunks:
                    raise

            out = "".join(chunks).strip()
            return out or "No response generated."

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
