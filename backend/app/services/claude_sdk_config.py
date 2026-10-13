from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.mcp_registry import tool_ids

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

    READ keys only. Execution (write) keys are never handed to any agent
    subprocess — live orders go through ``execution_service.execute_intent``.
    """
    from app.core.sandbox import is_sandbox, sandbox_base_url

    if is_sandbox():
        # Only the mock broker: its URL and its fake read keys. Real keys (env
        # or DB) never reach a sandbox subprocess.
        from app.services.sandbox_broker import SANDBOX_CREDENTIALS

        return {
            "T212_BASE_ENV": "sandbox",
            "T212_SANDBOX_URL": sandbox_base_url(),
            "OPENPF_SANDBOX": "1",
            "T212_API_KEY_INVEST": SANDBOX_CREDENTIALS["invest"]["t212_api_key"],
            "T212_API_SECRET_INVEST": SANDBOX_CREDENTIALS["invest"]["t212_api_secret"],
            "T212_API_KEY_STOCKS_ISA": SANDBOX_CREDENTIALS["stocks_isa"]["t212_api_key"],
            "T212_API_SECRET_STOCKS_ISA": SANDBOX_CREDENTIALS["stocks_isa"]["t212_api_secret"],
        }

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
    explicit = str(settings.openpf_runtime_dir or "").strip()
    raw = explicit or str(settings.claude_project_cwd or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()

    # CLAUDE_PROJECT_CWD stays scoped to this repo tree; an explicit
    # OPENPF_RUNTIME_DIR (per-instance data folder) may live anywhere.
    if not explicit:
        try:
            candidate.relative_to(root)
        except ValueError:
            candidate = root

    return candidate


def runtime_dir() -> Path:
    """This instance's Archie runtime folder (his cwd; holds artifacts/ and
    memory/). Every artifact/memory path in the backend derives from here so a
    sandbox instance and a live one never see each other's files."""
    return resolve_sdk_cwd()


def artifacts_dir() -> Path:
    return runtime_dir() / "artifacts"


def memory_dir() -> Path:
    return runtime_dir() / "memory"


def _seed_runtime_from_template(cwd: Path) -> None:
    """A non-default runtime folder gets Archie's tracked identity (CLAUDE.md)
    and skills from the template ``.claude/runtime/.claude`` — never its
    artifacts/ or memory/ (those are per-instance and may hold real data).
    CLAUDE.md is refreshed when the template is newer."""
    from app.core.sandbox import live_runtime_dir

    template = live_runtime_dir() / ".claude"
    dst = cwd / ".claude"
    dst.mkdir(parents=True, exist_ok=True)
    src_md, dst_md = template / "CLAUDE.md", dst / "CLAUDE.md"
    if src_md.is_file() and (not dst_md.exists() or src_md.stat().st_mtime > dst_md.stat().st_mtime):
        shutil.copy2(src_md, dst_md)
    src_skills = template / "skills"
    if src_skills.is_dir():
        shutil.copytree(src_skills, dst / "skills", dirs_exist_ok=True)


def resolve_sdk_cwd() -> Path:
    from app.core.sandbox import live_runtime_dir

    root = project_root()
    cwd = _resolve_cwd_candidate()
    cwd.mkdir(parents=True, exist_ok=True)
    if cwd != root and cwd.resolve() != live_runtime_dir().resolve():
        _seed_runtime_from_template(cwd)
        return cwd

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
    "`artifacts/` in your working directory; never write anywhere else."
)

_QUANT_PROMPT = (
    "You are Archie's quantitative analysis specialist. You perform technical "
    "analysis, write and run Python scripts for data analysis, compute risk "
    "metrics and indicators, and do statistical/portfolio modelling. Work from "
    "the instruments, date ranges, and analysis goals you are given.\n\n"
    "Start with the quant tools — tested maths on the user's real book in GBP with their risk rails: "
    "size_position, analyse_risk, what_if, rebalance_plan, deploy_cash, placement. Only write your own "
    "code for analysis those don't cover, and then build on `app.quant` (covariance shrinkage, risk "
    "contributions, HRP, drawdowns, Kelly) rather than re-deriving formulas.\n\n"
    "Other tools: the marketdata tools (get_price_snapshot, get_price_history_rows, "
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

def build_subagents() -> dict[str, Any]:
    """Construct Archie's subagent roster.

    Returns a ``dict[str, AgentDefinition]`` suitable for
    ``ClaudeAgentOptions(agents=...)``. There is deliberately no execution
    subagent: no agent can place or cancel orders — the main agent drafts trades
    with ``propose_trade`` and a human approves them.
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
        tools=["WebSearch", "WebFetch", "Read", "Glob", "Grep", "Write", *tool_ids("marketdata", "fundamentals", "intel")],
        model="sonnet",
    )

    quant = AgentDefinition(
        description=(
            "Quantitative analysis specialist for bespoke work the quant tools "
            "don't cover: backtests, custom statistics, scripted data analysis, "
            "multi-step studies. Don't delegate routine sizing/risk/rebalance/"
            "what-if questions — call the quant tools yourself. Provide: "
            "instruments, date ranges, and what analysis is needed."
        ),
        prompt=_QUANT_PROMPT,
        tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash",
               *tool_ids("quant", "marketdata", "forecast", "fundamentals")],
        model="claude-opus-4-8",
    )

    return {"researcher": researcher, "quant": quant}


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


# The trading surface. The agent can only *propose* trades (propose_trade); these
# guards stop it routing around that gate via Bash/WebFetch: calling the app's own
# API (where a human approves/executes), hitting the broker directly, or poking
# the execution code / exec credentials / DB from a shell.
_API_PORT = str(os.environ.get("OPENPF_API_PORT") or os.environ.get("API_PORT") or "8000")
_NETWORK_DENY = [
    (
        re.compile(
            r"(?:\blocalhost\b|\b127(?:\.\d{1,3}){3}\b|\b0\.0\.0\.0\b|\[::1\]|\bhost\.docker\.internal\b|\b2130706433\b)",
            re.IGNORECASE,
        ),
        "requests to the app's own host (localhost/loopback) are blocked",
    ),
    (re.compile(rf"\bbackend:{_API_PORT}\b|:{_API_PORT}/api\b", re.IGNORECASE), "requests to the app's own API are blocked"),
    (
        re.compile(r"/api/(?:agent/intents|orders|leveraged|config)\b", re.IGNORECASE),
        "trading/config API endpoints are human-only",
    ),
    (re.compile(r"trading212\.com", re.IGNORECASE), "direct Trading 212 API access is blocked — use the read-only trading212 tools"),
]
_BASH_TRADING_DENY = [
    (
        re.compile(
            r"\b(?:execute_intent|approve_intent|place_(?:market|limit|stop|stop_limit)_order|cancel_order"
            r"|build_t212_client|get_account_exec_credentials|exec_api_(?:key|secret)|T212_EXEC|update_policy"
            r"|set_leveraged|auto_execute|execute_signal|ConfigStore|app_config|sqlite3?)\b",
            re.IGNORECASE,
        ),
        "trade execution, broker credentials and the app database are off-limits — propose trades with propose_trade",
    ),
    (re.compile(r"\.(?:db|sqlite3?)\b", re.IGNORECASE), "the app database is off-limits"),
]
# Write/Edit may only touch agent-owned state under .claude/ in this repo, never
# the app's code or config (which would let the agent rewrite its own gate).
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _network_violation(text: str) -> str | None:
    for pattern, reason in _NETWORK_DENY:
        if pattern.search(text):
            return reason
    return None


_OTHER_INSTANCE_REASON = "another OpenPF instance's data (sandbox and live are kept apart)"


def _other_instance_roots() -> tuple[list[Path], str]:
    """(folders this instance must never touch, substring marker for Bash)."""
    from app.core.sandbox import demo_dir, live_runtime_dir

    runtime = runtime_dir().resolve()
    live = live_runtime_dir().resolve()
    if runtime == live:
        return [demo_dir().resolve()], ".demo"
    # A sandbox/custom instance: the live runtime (memory, artifacts) is off-limits.
    return [live], ".claude/runtime"


def _touches_other_instance(paths: list[str], cwd: str | None) -> bool:
    roots, _ = _other_instance_roots()
    base = Path(cwd or runtime_dir())
    for raw in paths:
        if not raw:
            continue
        p = Path(raw).expanduser()
        p = (p if p.is_absolute() else base / p).resolve()
        if any(p == r or r in p.parents for r in roots):
            return True
    return False


# Recipes (Archie's own harness) change only through the versioned recipes tools,
# so every edit has a diff the user can see and revert — never via raw file
# writes into the skills plugin.
_RECIPES_DENY_REASON = "recipes are edited only through the recipes tools (versioned and reviewable)"
_BASH_RECIPES_DENY = re.compile(r"data/recipes|recipes/plugin|\.claude-plugin|recipes_service|RecipeVersion", re.IGNORECASE)


def _recipes_dir() -> Path | None:
    try:
        from app.services.recipes_service import data_dir

        return data_dir()
    except Exception:  # noqa: BLE001
        return None


def _in_recipes_dir(raw_path: str, cwd: str | None) -> bool:
    root = _recipes_dir()
    if not raw_path or root is None:
        return False
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(cwd or resolve_sdk_cwd()) / path
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def recipe_plugins() -> list[dict[str, str]]:
    """``ClaudeAgentOptions(plugins=...)`` for the user's recipes (as skills)."""
    try:
        from app.services.recipes_service import plugins_option

        return plugins_option()
    except Exception:  # noqa: BLE001 — a recipes problem must never stop a runtime
        return []


async def _bash_guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    command = str((input_data.get("tool_input") or {}).get("command", ""))
    _, marker = _other_instance_roots()
    if marker in command:
        return _deny(_OTHER_INSTANCE_REASON)
    for pattern, reason in _BASH_DENY:
        if pattern.search(command):
            return _deny(reason)
    root = _recipes_dir()
    if _BASH_RECIPES_DENY.search(command) or (root is not None and str(root) in command):
        return _deny(_RECIPES_DENY_REASON)
    for pattern, reason in _SECRET_PATTERNS:
        if pattern.search(command):
            return _deny(reason)
    for pattern, reason in _BASH_TRADING_DENY:
        if pattern.search(command):
            return _deny(reason)
    reason = _network_violation(command)
    if reason:
        return _deny(reason)
    return {}


async def _web_fetch_guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    url = str((input_data.get("tool_input") or {}).get("url", ""))
    reason = _network_violation(url)
    if reason:
        return _deny(reason)
    return {}


def _outside_agent_area(raw_path: str, cwd: str | None) -> bool:
    if not raw_path:
        return False
    root = project_root()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(cwd or resolve_sdk_cwd()) / path
    path = path.resolve()
    runtime = runtime_dir().resolve()
    if path == runtime or runtime in path.parents:
        return False  # this instance's own runtime folder (artifacts, memory)
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False  # outside the repo: left to the SDK's own permission checks
    return not rel.parts or rel.parts[0] != ".claude"


async def _file_guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    tool_input = input_data.get("tool_input") or {}
    target = " ".join(
        str(tool_input.get(k, ""))
        for k in ("file_path", "path", "pattern", "notebook_path")
    )
    for pattern, reason in _SECRET_PATTERNS:
        if pattern.search(target):
            return _deny(reason)
    if re.search(r"\.(?:db|sqlite3?)(?:-wal|-shm|-journal)?\b", target, re.IGNORECASE):
        return _deny("the app database is off-limits")
    _, marker = _other_instance_roots()
    if marker in target or _touches_other_instance(
        [str(tool_input.get(k, "")) for k in ("file_path", "path", "notebook_path")], input_data.get("cwd")
    ):
        return _deny(_OTHER_INSTANCE_REASON)
    if str(input_data.get("tool_name", "")) in _WRITE_TOOLS:
        write_target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if _in_recipes_dir(write_target, input_data.get("cwd")):
            return _deny(_RECIPES_DENY_REASON)
        if _outside_agent_area(write_target, input_data.get("cwd")):
            return _deny("writes are limited to agent state under .claude/ — app code and config are read-only")
    return {}


def build_security_hooks() -> dict[str, Any]:
    """Build PreToolUse security hooks for ClaudeAgentOptions(hooks=...).

    Returns ``dict[HookEvent, list[HookMatcher]]``. Blocks dangerous Bash
    commands, reads/writes that touch secrets or the app DB, writes outside
    agent state, and any Bash/WebFetch route around the trade-proposal gate
    (self-API calls, trading212.com, execution code/credentials).
    """
    from claude_agent_sdk import HookMatcher

    return {
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[_bash_guard]),
            HookMatcher(matcher="WebFetch", hooks=[_web_fetch_guard]),
            HookMatcher(matcher="Read|Edit|MultiEdit|Write|NotebookEdit|Glob|Grep", hooks=[_file_guard]),
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
