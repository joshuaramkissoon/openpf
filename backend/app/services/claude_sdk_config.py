from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from app.core.config import get_settings

settings = get_settings()

_SETTING_SOURCE_ALLOWED = {"user", "project", "local"}


def configure_sdk_auth() -> None:
    """Set the correct env var for Claude SDK authentication.

    Prefers OAuth token over API key. When OAuth is present, API key is
    explicitly removed — the SDK treats API key as higher priority, which
    would silently bypass subscription billing.

    Token source: ``CLAUDE_CODE_OAUTH_TOKEN`` in .env (generated via
    ``claude setup-token``). Falls back to ``ANTHROPIC_API_KEY``.
    """
    oauth = (settings.claude_code_oauth_token or "").strip()
    if oauth:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = oauth
        os.environ.pop("ANTHROPIC_API_KEY", None)
        return

    api_key = (settings.anthropic_api_key or "").strip()
    if api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)


def resolve_t212_env() -> dict[str, str]:
    """T212 credential env vars for the MCP subprocesses, sourced from the DB
    (ConfigStore) — the SAME credentials the dashboard uses — so Archie never
    drifts from the keys you manage. Falls back to the regular ``T212_*`` .env
    settings if the DB has none.

    Note: deliberately does NOT prefer the legacy ``ARCHIE_T212_*`` keys — those
    being stale (while the regular keys were updated) is exactly what caused
    Archie's 401s. Credentials live in subprocess memory only.
    """
    env: dict[str, str] = {"T212_BASE_ENV": settings.t212_base_env}
    invest = ("", "")
    isa = ("", "")
    try:
        from app.core.database import SessionLocal
        from app.services.config_store import ConfigStore

        with SessionLocal() as db:
            store = ConfigStore(db)
            env["T212_BASE_ENV"] = str(store.get_broker().get("t212_base_env") or settings.t212_base_env)
            ic = store.get_account_credentials("invest")
            sc = store.get_account_credentials("stocks_isa")
            invest = (str(ic.get("t212_api_key", "")).strip(), str(ic.get("t212_api_secret", "")).strip())
            isa = (str(sc.get("t212_api_key", "")).strip(), str(sc.get("t212_api_secret", "")).strip())
    except Exception:  # noqa: BLE001 — fall back to .env below
        pass

    if not all(invest):
        invest = (
            (settings.t212_invest_api_key or settings.t212_api_key_invest or settings.t212_api_key or "").strip(),
            (settings.t212_invest_api_secret or settings.t212_api_secret_invest or settings.t212_api_secret or "").strip(),
        )
    if not all(isa):
        isa = (
            (settings.t212_stocks_isa_api_key or settings.t212_api_key_stocks_isa or "").strip(),
            (settings.t212_stocks_isa_api_secret or settings.t212_api_secret_stocks_isa or "").strip(),
        )

    if all(invest):
        env["T212_API_KEY_INVEST"], env["T212_API_SECRET_INVEST"] = invest
    if all(isa):
        env["T212_API_KEY_STOCKS_ISA"], env["T212_API_SECRET_STOCKS_ISA"] = isa
    return env


def project_root() -> Path:
    # In Docker the directory layout differs (no `backend/` prefix),
    # so allow an explicit override via PROJECT_ROOT env var.
    env = os.environ.get("PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    # backend/app/services -> backend -> repo root
    return Path(__file__).resolve().parents[3]


def parse_setting_sources(raw: str | None, *, require_project: bool = True) -> list[str]:
    if raw is None:
        raw = settings.claude_setting_sources

    values = [v.strip().lower() for v in str(raw).split(",") if v.strip()]
    picked = [v for v in values if v in _SETTING_SOURCE_ALLOWED]

    if not picked:
        picked = ["project"] if require_project else []

    if require_project and "project" not in picked:
        picked.append("project")

    return picked


def _resolve_cwd_candidate() -> Path:
    root = project_root()
    raw = str(settings.claude_project_cwd or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()

    # Keep SDK cwd scoped to this repo tree.
    try:
        candidate.relative_to(root)
    except ValueError:
        candidate = root

    return candidate


def resolve_sdk_cwd() -> Path:
    root = project_root()
    cwd = _resolve_cwd_candidate()
    cwd.mkdir(parents=True, exist_ok=True)

    src_hidden = root / ".claude"
    dst_hidden = cwd / ".claude"
    src_skills = src_hidden / "skills"
    dst_skills = dst_hidden / "skills"
    dst_skills.mkdir(parents=True, exist_ok=True)

    # If SDK cwd differs from repo root, seed project skills/CLAUDE.md there.
    if cwd != root and src_skills.exists():
        for skill_dir in src_skills.iterdir():
            if not skill_dir.is_dir():
                continue
            if not (skill_dir / "SKILL.md").exists():
                continue
            target = dst_skills / skill_dir.name
            if target.exists():
                continue
            shutil.copytree(skill_dir, target)

    src_claude_md = src_hidden / "CLAUDE.md"
    dst_claude_md = dst_hidden / "CLAUDE.md"
    if src_claude_md.exists() and not dst_claude_md.exists():
        dst_claude_md.parent.mkdir(parents=True, exist_ok=True)
        dst_claude_md.write_text(src_claude_md.read_text(encoding="utf-8"), encoding="utf-8")

    return cwd


def list_skill_files(cwd: Path | None = None) -> list[str]:
    base = cwd or resolve_sdk_cwd()
    out: list[str] = []
    skills_root = base / ".claude" / "skills"
    if not skills_root.exists():
        return out

    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        try:
            out.append(str(skill_md.relative_to(base)))
        except ValueError:
            out.append(str(skill_md))
    return out


# ──────────────────────────────────────────────────────────────────────────
# MCP tool allow-lists
#
# Centralised here (per docs/plans/2026-02-19-archie-subagents-design.md) so
# the chat/agent/scheduler runtimes and build_subagents() share one source of
# truth and avoid circular imports.
# ──────────────────────────────────────────────────────────────────────────

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
    "mcp__marketdata__get_indicator_series",
    "mcp__marketdata__get_risk_metrics",
    "mcp__marketdata__get_correlation_matrix",
    "mcp__marketdata__compare_assets",
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

_FORECAST_MCP_TOOLS = [
    "mcp__forecast__forecast_prices",
    "mcp__forecast__forecast_status",
]

_FUNDAMENTALS_MCP_TOOLS = [
    "mcp__fundamentals__get_fundamentals",
    "mcp__fundamentals__get_valuation",
    "mcp__fundamentals__get_financial_statements",
    "mcp__fundamentals__get_earnings_calendar",
]

_INTEL_MCP_TOOLS = [
    "mcp__intel__get_company_news",
    "mcp__intel__get_market_news",
    "mcp__intel__get_macro_snapshot",
    "mcp__intel__get_earnings",
]

_WATCHLIST_MCP_TOOLS = [
    "mcp__watchlist__list_watchlist",
    "mcp__watchlist__add_to_watchlist",
    "mcp__watchlist__update_watchlist_item",
    "mcp__watchlist__remove_from_watchlist",
    "mcp__watchlist__flag_watchlist_item",
]

# Narrow T212 subset the execution subagent is allowed to use — no CSV
# export, no dividend/transaction history.
_EXECUTION_T212_TOOLS = [
    "mcp__trading212__get_account_summary",
    "mcp__trading212__get_positions",
    "mcp__trading212__get_pending_orders",
    "mcp__trading212__search_instruments",
    "mcp__trading212__place_market_order",
    "mcp__trading212__place_limit_order",
    "mcp__trading212__place_stop_order",
    "mcp__trading212__place_stop_limit_order",
    "mcp__trading212__cancel_order",
    "mcp__trading212__get_order_history",
]


# ──────────────────────────────────────────────────────────────────────────
# Subagents
# ──────────────────────────────────────────────────────────────────────────

_RESEARCHER_PROMPT = (
    "You are Archie's financial research specialist. You handle web research "
    "on markets, companies, macro events, financial news, and documentation. "
    "Work from the specific questions and portfolio context you are given — "
    "you have no memory of the main conversation.\n\n"
    "Tools you have: WebSearch / WebFetch for news, catalysts, and primary sources; "
    "the marketdata tools (get_price_snapshot, get_price_history_rows, get_technical_snapshot, "
    "get_indicator_series, get_risk_metrics, get_correlation_matrix, compare_assets) for live "
    "prices, technicals, risk, and correlation; and the fundamentals tools (get_fundamentals, "
    "get_valuation, get_financial_statements, get_earnings_calendar) for company facts, valuation "
    "ratios, financial statements, and earnings dates. Prefer these over web pages for any numeric "
    "market/financial data — never quote a price or metric you did not fetch this run.\n\n"
    "Data quality: if any series looks synthetic or implausible (e.g. a smooth random-walk with no "
    "gaps/weekends, round-number prices, zero volume, or values that contradict a reliable source), "
    "FLAG it explicitly rather than reporting it as fact. Distinguish what you verified from what you "
    "could not. Return structured, clearly sourced findings. You may write artifacts, but ONLY under "
    "`.claude/runtime/artifacts/`; never write anywhere else."
)

_QUANT_PROMPT = (
    "You are Archie's quantitative analysis specialist. You perform technical "
    "analysis, write and run Python scripts for data analysis, compute risk "
    "metrics and indicators, and do statistical/portfolio modelling. Work from "
    "the instruments, date ranges, and analysis goals you are given.\n\n"
    "Tools you have: the marketdata tools (get_price_snapshot, get_price_history_rows, "
    "get_technical_snapshot, get_indicator_series, get_risk_metrics, get_correlation_matrix, "
    "compare_assets) for live data and risk/correlation; the fundamentals tools (get_fundamentals, "
    "get_valuation, get_financial_statements, get_earnings_calendar); and the Kronos forecast tool "
    "(forecast_prices) returning a p10/p50/p90 price cone — treat it as a probabilistic distribution, "
    "never a point certainty. You also have Bash: you can `import app.quant` (the project's quant "
    "library, with PYTHONPATH set to the backend root) to reuse vetted indicator/risk/backtest code "
    "instead of re-deriving it — prefer it over ad-hoc reimplementations.\n\n"
    "ALWAYS show your method (data source, window, formula or library call) and quantify uncertainty "
    "(confidence intervals, sample size, lookback, sensitivity) — point estimates without uncertainty "
    "are not acceptable. If input data looks synthetic or implausible (smooth random-walk, no "
    "weekend gaps, zero volume, values that contradict a snapshot), FLAG it and do not silently build "
    "on it. Return concise, quantified results."
)

_EXECUTION_PROMPT = (
    "You are Archie's trade execution specialist. You place and cancel orders "
    "via the Trading 212 tools, using only the account balance, positions, and "
    "exact order instructions passed to you. Do not improvise sizing or "
    "instruments. Always finish with a single JSON block matching the execution "
    "schema: {\"trades\": [...], \"errors\": [...], \"commentary\": \"...\"}. "
    "Return structured JSON only — never unstructured prose as the final answer."
)


def build_subagents() -> dict[str, Any]:
    """Construct Archie's subagent roster.

    Returns a ``dict[str, AgentDefinition]`` suitable for
    ``ClaudeAgentOptions(agents=...)``. See
    docs/plans/2026-02-19-archie-subagents-design.md for the roster spec.
    """
    from claude_agent_sdk import AgentDefinition

    researcher = AgentDefinition(
        description=(
            "Financial research specialist. Delegate here: web research on "
            "markets, companies, macro events, financial news, documentation. "
            "Provide specific questions and relevant portfolio context. Returns "
            "structured research findings and writes artifacts when useful."
        ),
        prompt=_RESEARCHER_PROMPT,
        tools=["WebSearch", "WebFetch", "Read", "Glob", "Grep", "Write", *_MARKET_MCP_TOOLS, *_FUNDAMENTALS_MCP_TOOLS, *_INTEL_MCP_TOOLS],
        model="sonnet",
    )

    quant = AgentDefinition(
        description=(
            "Quantitative analysis specialist. Delegate here: technical "
            "analysis, writing and running Python scripts for data analysis, "
            "risk calculations, statistical modelling, portfolio metrics, "
            "indicator computation. Provide: instruments, date ranges, and what "
            "analysis is needed."
        ),
        prompt=_QUANT_PROMPT,
        tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash", *_MARKET_MCP_TOOLS, *_FORECAST_MCP_TOOLS, *_FUNDAMENTALS_MCP_TOOLS],
        model="claude-opus-4-8",
    )

    execution = AgentDefinition(
        description=(
            "Trade execution specialist. Delegate ALL order placement and "
            "cancellation here. Always pass: current account balance, relevant "
            "positions, and exact order instructions (symbol, type, qty, limit "
            "price if applicable). Returns a typed list of trade results — do "
            "not attempt T212 tool calls yourself."
        ),
        prompt=_EXECUTION_PROMPT,
        tools=list(_EXECUTION_T212_TOOLS),
        model="haiku",
    )

    return {"researcher": researcher, "quant": quant, "execution": execution}


# ──────────────────────────────────────────────────────────────────────────
# Security hooks
#
# PreToolUse guards that block destructive shell commands (rm, fork bombs,
# disk writes) and access to secrets (.env, keys). Applied to the parent
# ClaudeAgentOptions; the SDK propagates hooks to subagents.
# ──────────────────────────────────────────────────────────────────────────

# Bash commands that must never run.
_BASH_DENY = [
    (re.compile(r"\brm\b"), "destructive file removal (rm) is blocked"),
    (re.compile(r"\bmkfs\b"), "filesystem formatting is blocked"),
    (re.compile(r"\bdd\b\s+.*\bif="), "raw disk writes (dd) are blocked"),
    (re.compile(r">\s*/dev/(sd|nvme|disk)"), "writes to raw devices are blocked"),
    (re.compile(r":\s*\(\s*\)\s*\{.*\|.*&\s*\}"), "fork bombs are blocked"),
    (re.compile(r"\bgit\b.*\bpush\b.*--force"), "force pushes are blocked"),
]

# Paths/secrets that must never be read or written by any tool.
_SECRET_PATTERNS = [
    (re.compile(r"(^|/)\.env(\.|$|\b)"), ".env files contain secrets"),
    (re.compile(r"\bid_rsa\b|\.pem\b|\.key\b"), "private keys are protected"),
    (re.compile(r"\b(secrets?|credentials?)\b", re.IGNORECASE), "credential files are protected"),
]


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Blocked by security policy: {reason}",
        }
    }


async def _bash_guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    command = str((input_data.get("tool_input") or {}).get("command", ""))
    for pattern, reason in _BASH_DENY:
        if pattern.search(command):
            return _deny(reason)
    for pattern, reason in _SECRET_PATTERNS:
        if pattern.search(command):
            return _deny(reason)
    return {}


async def _file_guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    tool_input = input_data.get("tool_input") or {}
    target = " ".join(
        str(tool_input.get(k, ""))
        for k in ("file_path", "path", "pattern", "notebook_path")
    )
    for pattern, reason in _SECRET_PATTERNS:
        if pattern.search(target):
            return _deny(reason)
    return {}


def build_security_hooks() -> dict[str, Any]:
    """Build PreToolUse security hooks for ClaudeAgentOptions(hooks=...).

    Returns ``dict[HookEvent, list[HookMatcher]]``. Blocks dangerous Bash
    commands and reads/writes that touch secrets.
    """
    from claude_agent_sdk import HookMatcher

    return {
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[_bash_guard]),
            HookMatcher(matcher="Read|Edit|Write|Glob|Grep", hooks=[_file_guard]),
        ],
    }


def runtime_info() -> dict[str, Any]:
    cwd = resolve_sdk_cwd()
    source_memory_file = project_root() / ".claude" / "CLAUDE.md"
    runtime_memory_file = cwd / ".claude" / "CLAUDE.md"
    return {
        "project_root": str(project_root()),
        "cwd": str(cwd),
        "setting_sources": parse_setting_sources(settings.claude_setting_sources, require_project=True),
        "skills_dir": str(cwd / ".claude" / "skills"),
        "skill_files": list_skill_files(cwd),
        "claude_model": settings.claude_model,
        "claude_chat_model": settings.claude_chat_model,
        "claude_agent_model": settings.claude_agent_model,
        "claude_memory_model": settings.claude_memory_model,
        "memory_file": str(runtime_memory_file),
        "memory_source_file": str(source_memory_file),
        "memory_strategy": settings.claude_memory_strategy,
    }
