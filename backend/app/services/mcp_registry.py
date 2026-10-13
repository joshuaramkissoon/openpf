"""Single registry of the MCP servers Archie can use, per runtime context.

Every Claude runtime (interactive chat, the analyst cycle, the research runner
and scheduled tasks) builds its ``mcp_servers`` / ``allowed_tools`` from here
instead of hand-wiring each server, so the safety invariants hold everywhere:

* No server exposes broker order placement or cancellation. The agent can only
  *propose* trades (``proposals`` server → a ``proposed`` TradeIntent); live
  orders go through the approval-gated ``execution_service.execute_intent``.
* No subprocess ever receives T212 execution (write) keys or the app's API
  token. Only the ``trading212`` server gets the read-only T212 keys.
* No subprocess ever receives crypto exchange keys. The ``crypto`` server
  reads balances from the backend's stored snapshots and prices from public
  market data; inside any MCP process ``crypto_service`` blanks the stored
  key and refuses to build a keyed exchange client.

Adding a server is a one-entry change to ``MCP_SERVERS``::

    McpServerSpec(
        name="plays",                     # -> tool ids mcp__plays__<tool>
        script="plays.py",                # file in backend/mcp_servers/
        tools=("list_plays", "create_play"),
        contexts=frozenset({CHAT, SCHEDULED}),
    )

Contexts: ``CHAT`` (dashboard chat / Telegram), ``AGENT`` (analyst cycle),
``RESEARCH`` (single-thesis research runner), ``SCHEDULED`` (cron tasks).
Never register a tool that places, modifies or cancels broker orders, or that
changes risk rails / auto-execute policy — ``tests/test_execution_gate.py``
enforces this.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings

CHAT = "chat"
AGENT = "agent"
RESEARCH = "research"
SCHEDULED = "scheduled"
ALL_CONTEXTS = frozenset({CHAT, AGENT, RESEARCH, SCHEDULED})

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_DIR = BACKEND_ROOT / "mcp_servers"


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    script: str
    tools: tuple[str, ...]
    contexts: frozenset[str]
    # Only the broker read server gets (read-only) T212 credentials.
    needs_t212_read_keys: bool = False
    # Tool navigation (see docs/archie-toolkit.md). With tool search on, every
    # server's tools are deferred behind ToolSearch except ``always_load`` ones —
    # the few Archie needs on most turns.
    always_load: bool = False
    # Tools a context should not see at all ({context: (tool, ...)}): left out of
    # allowed_tools and unregistered by the server itself (OPENPF_MCP_HIDE_TOOLS).
    hidden: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def tool_ids(self) -> list[str]:
        return [f"mcp__{self.name}__{tool}" for tool in self.tools]

    def hidden_in(self, context: str) -> tuple[str, ...]:
        return next((tools for ctx, tools in self.hidden if ctx == context), ())

    def tool_ids_for(self, context: str) -> list[str]:
        hide = set(self.hidden_in(context))
        return [f"mcp__{self.name}__{tool}" for tool in self.tools if tool not in hide]

    @property
    def script_path(self) -> Path:
        return MCP_SERVER_DIR / self.script


MCP_SERVERS: tuple[McpServerSpec, ...] = (
    McpServerSpec(
        name="trading212",
        script="t212.py",
        tools=(
            "get_account_summary",
            "get_positions",
            "get_pending_orders",
            "search_instruments",
            "get_exchanges",
            "get_order_history",
            "get_dividend_history",
            "get_transaction_history",
            "request_csv_export",
            "get_csv_export_status",
        ),
        contexts=ALL_CONTEXTS,
        needs_t212_read_keys=True,
        always_load=True,
    ),
    McpServerSpec(
        name="marketdata",
        script="marketdata.py",
        tools=(
            "get_price_snapshot",
            "get_price_history_rows",
            "get_technical_snapshot",
            "get_indicator_series",
            "get_risk_metrics",
            "get_correlation_matrix",
            "compare_assets",
        ),
        contexts=ALL_CONTEXTS,
        always_load=True,
    ),
    # Tested portfolio maths: sizing, risk, what-if, rebalancing, deploying cash,
    # ISA/Invest placement. Read-only — it never proposes or places anything.
    McpServerSpec(
        name="quant",
        script="quant.py",
        tools=("size_position", "analyse_risk", "what_if", "rebalance_plan", "deploy_cash", "placement"),
        contexts=frozenset({CHAT, RESEARCH, SCHEDULED}),
        always_load=True,
    ),
    McpServerSpec(
        name="scheduler",
        script="scheduler.py",
        tools=(
            "list_scheduled_tasks",
            "create_scheduled_task",
            "pause_scheduled_task",
            "resume_scheduled_task",
            "delete_scheduled_task",
            "run_scheduled_task_now",
            "get_scheduled_task_logs",
            "run_due_scheduled_tasks",
            "seed_default_scheduled_tasks",
        ),
        contexts=frozenset({CHAT, AGENT, SCHEDULED}),
        # Maintenance ops: a chat or a scheduled run has no business running every
        # due task or re-seeding defaults (user routines are recipes).
        hidden=(
            (CHAT, ("run_due_scheduled_tasks", "seed_default_scheduled_tasks")),
            (SCHEDULED, ("run_due_scheduled_tasks", "seed_default_scheduled_tasks")),
        ),
    ),
    McpServerSpec(
        name="forecast",
        script="forecast.py",
        tools=("forecast_prices", "forecast_status"),
        contexts=frozenset({CHAT, RESEARCH}),
    ),
    McpServerSpec(
        name="fundamentals",
        script="fundamentals.py",
        tools=(
            "get_fundamentals",
            "get_valuation",
            "get_financial_statements",
            "get_earnings_calendar",
        ),
        contexts=ALL_CONTEXTS,
    ),
    McpServerSpec(
        name="intel",
        script="intel.py",
        tools=("get_company_news", "get_market_news", "get_macro_snapshot", "get_earnings"),
        contexts=frozenset({CHAT, AGENT, SCHEDULED}),
    ),
    # Smart money: disclosed insider / 13F / Congress trades + trend detector.
    # Read-only over the local disclosures table.
    McpServerSpec(
        name="smart_money",
        script="smart_money.py",
        tools=("get_smart_money", "get_disclosure_trends"),
        contexts=frozenset({CHAT, AGENT, SCHEDULED}),
    ),
    McpServerSpec(
        name="watchlist",
        script="watchlist.py",
        tools=(
            "list_watchlist",
            "add_to_watchlist",
            "update_watchlist_item",
            "remove_from_watchlist",
            "flag_watchlist_item",
        ),
        contexts=frozenset({CHAT, SCHEDULED}),
    ),
    # The ONLY way the agent can express a trade: it records a 'proposed'
    # TradeIntent for a human to approve. It cannot execute anything.
    McpServerSpec(
        name="proposals",
        script="proposals.py",
        tools=("propose_trade", "list_proposals", "get_track_record", "suggest_permission"),
        contexts=frozenset({CHAT, SCHEDULED}),
        always_load=True,
    ),
    # Theme bets. Staging a tranche only creates 'proposed' intents.
    McpServerSpec(
        name="plays",
        script="plays.py",
        tools=("list_plays", "get_play", "draft_play", "update_play", "stage_play_tranche",
               "get_book_by_play", "assign_owner", "add_related", "remove_related", "add_bench",
               "remove_bench", "suggest_plays"),
        contexts=frozenset({CHAT, SCHEDULED}),
        always_load=True,
    ),
    # Crypto exchange, read-only and keyless: balances/positions from the
    # backend's snapshots, public prices, fee schedule, risk. The only write is
    # drafting the Starter crypto play (a draft — no orders).
    McpServerSpec(
        name="crypto",
        script="crypto.py",
        tools=("get_crypto_account", "get_crypto_prices", "get_crypto_fees", "get_crypto_risk",
               "draft_starter_crypto_play"),
        contexts=frozenset({CHAT, SCHEDULED}),
    ),
    # Agent-built pages. Generated code runs in a sandboxed iframe and can only
    # read whitelisted data — it can't trade or reach the network.
    McpServerSpec(
        name="views",
        script="views.py",
        tools=("list_views", "create_view", "update_view"),
        contexts=frozenset({CHAT}),
    ),
    # Recipes: Archie's own harness (skills + triggers). Instructions only — no
    # tool grants, no rails. From SCHEDULED (unattended) runs, create/update/
    # enable only record a suggestion the user must approve (see
    # recipes_service.request_change; the server reads OPENPF_MCP_CONTEXT).
    McpServerSpec(
        name="recipes",
        script="recipes.py",
        tools=("list_recipes", "get_recipe", "create_recipe", "update_recipe", "set_recipe_enabled"),
        contexts=frozenset({CHAT, SCHEDULED}),
    ),
)


def server_spec(name: str) -> McpServerSpec:
    for spec in MCP_SERVERS:
        if spec.name == name:
            return spec
    raise KeyError(name)


def tool_ids(*names: str) -> list[str]:
    """Allowed-tool ids for the named servers (used for subagent tool lists)."""
    out: list[str] = []
    for name in names:
        out.extend(server_spec(name).tool_ids)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Subprocess environments
# ──────────────────────────────────────────────────────────────────────────

# Env vars that must never reach the Claude CLI or any MCP subprocess other than
# via an explicit per-server injection (read keys → trading212 only): broker
# keys/secrets (incl. execution keys), crypto exchange keys (never injected
# anywhere) and the app's own API/bot tokens, so Bash/WebFetch can't reuse
# them. Non-secret settings (T212_BASE_ENV, CRYPTO_EXCHANGE) pass through.
_SENSITIVE_NAMES = {"OPENPF_API_TOKEN", "TELEGRAM_BOT_TOKEN"}
_SENSITIVE_PREFIXES = ("T212_", "ARCHIE_T212_", "CRYPTO_", "KRAKEN_", "COINBASE_", "BINANCE_", "CCXT_")


def _is_sensitive(key: str) -> bool:
    if key in _SENSITIVE_NAMES:
        return True
    return key.startswith(_SENSITIVE_PREFIXES) and ("KEY" in key or "SECRET" in key)


def _scrubbed_inherited_env() -> dict[str, str]:
    """Blank every sensitive var the backend process inherited.

    The SDK merges ``{**os.environ, **options.env}`` for the CLI, and the CLI
    passes its env on to stdio MCP servers, so overriding with "" is what keeps
    a secret exported in the backend's shell out of every child process.
    """
    return {key: "" for key in os.environ if _is_sensitive(key)}


def resolved_database_url() -> str:
    """DATABASE_URL with a relative SQLite path made absolute (backend root), so
    MCP subprocesses — which may run with another CWD — open the same file."""
    url = get_settings().database_url
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////"):
        rel = url[len(prefix):]
        if rel and rel != ":memory:" and not Path(rel).is_absolute():
            return f"{prefix}{(BACKEND_ROOT / rel).resolve()}"
    return url


def sdk_env() -> dict[str, str]:
    """Env overrides for the Claude CLI subprocess itself (passed as
    ``ClaudeAgentOptions(env=...)``). Contains no broker credentials."""
    env = _scrubbed_inherited_env()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    # Tool search: defer every MCP tool except always_load servers' behind the
    # CLI's ToolSearch (true | false | auto | auto:N; see docs/archie-toolkit.md).
    mode = str(getattr(get_settings(), "claude_tool_search", "") or "").strip()
    if mode:
        env["ENABLE_TOOL_SEARCH"] = mode
    return env


def mcp_server_env(spec: McpServerSpec) -> dict[str, str]:
    env = _scrubbed_inherited_env()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["DATABASE_URL"] = resolved_database_url()
    # Pin every per-instance folder explicitly so a child process can't fall
    # back to another instance's defaults (sandbox vs live).
    from app.core.sandbox import is_sandbox
    from app.services.claude_sdk_config import runtime_dir

    env["OPENPF_RUNTIME_DIR"] = str(runtime_dir())
    views_dir = Path(get_settings().views_data_dir).expanduser()
    env["VIEWS_DATA_DIR"] = str(views_dir if views_dir.is_absolute() else (BACKEND_ROOT / views_dir).resolve())
    env["OPENPF_SANDBOX"] = "1" if is_sandbox() else ""
    if spec.needs_t212_read_keys:
        from app.services.claude_sdk_config import resolve_t212_env

        env.update(resolve_t212_env())
    return env


def build_mcp_servers(context: str) -> tuple[dict[str, Any], list[str]]:
    """``(mcp_servers, allowed_tool_ids)`` for one runtime context.

    Servers whose script is missing are skipped (and their tools not allowed).
    """
    if context not in ALL_CONTEXTS:
        raise ValueError(f"unknown MCP context {context!r}")
    servers: dict[str, Any] = {}
    allowed: list[str] = []
    for spec in MCP_SERVERS:
        if context not in spec.contexts or not spec.script_path.is_file():
            continue
        env = mcp_server_env(spec)
        # Lets a server tell attended (chat) from unattended (scheduled) calls.
        env["OPENPF_MCP_CONTEXT"] = context
        hidden = spec.hidden_in(context)
        if hidden:
            env["OPENPF_MCP_HIDE_TOOLS"] = ",".join(hidden)
        servers[spec.name] = {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(spec.script_path)],
            "env": env,
        }
        if spec.always_load:
            servers[spec.name]["alwaysLoad"] = True
        allowed.extend(spec.tool_ids_for(context))
    if tool_search_enabled():
        allowed.append("ToolSearch")  # how deferred MCP tools are found and loaded
    return servers, allowed


def tool_search_enabled() -> bool:
    mode = str(getattr(get_settings(), "claude_tool_search", "") or "").strip().lower()
    return mode not in ("", "false", "0", "off")
