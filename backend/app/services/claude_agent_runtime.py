from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.claude_sdk_config import (
    build_security_hooks, build_subagents, configure_sdk_auth,
    parse_setting_sources, project_root, resolve_sdk_cwd, resolve_t212_env,
    _T212_MCP_TOOLS, _MARKET_MCP_TOOLS, _SCHEDULER_MCP_TOOLS, _FORECAST_MCP_TOOLS,
    _FUNDAMENTALS_MCP_TOOLS,
)
from app.services.research_service import fetch_news, fetch_x_posts, web_search

settings = get_settings()

_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_servers"


def _build_sdk_env() -> dict[str, str]:
    """T212 creds (DB-sourced, in sync with the dashboard) + PYTHONPATH for the
    MCP subprocesses. Credentials live in subprocess memory only."""
    env = resolve_t212_env()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent)
    return env


def _ensure_workspace() -> Path:
    raw = Path(settings.agent_workspace).expanduser()
    if not raw.is_absolute():
        raw = (project_root() / raw).resolve()
    workspace = raw.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def run_sandboxed_python(code: str, input_payload: dict[str, Any] | None = None, timeout_seconds: int = 15) -> dict[str, Any]:
    workspace = _ensure_workspace()
    with tempfile.TemporaryDirectory(dir=workspace) as temp_dir:
        temp_path = Path(temp_dir)
        script_path = temp_path / "analysis.py"
        input_path = temp_path / "input.json"

        payload = input_payload or {}
        input_path.write_text(json.dumps(payload), encoding="utf-8")

        wrapper = (
            "import json\n"
            "from pathlib import Path\n"
            "INPUT = json.loads(Path('input.json').read_text())\n"
            + code
        )
        script_path.write_text(wrapper, encoding="utf-8")

        proc = subprocess.run(
            ["python3", str(script_path)],
            cwd=temp_path,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent.parent)},
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }


def _extract_text_from_sdk_message(message: Any) -> str:
    # SDK message shape can vary by version; handle defensively.
    if message is None:
        return ""

    if isinstance(message, str):
        return message

    if isinstance(message, dict):
        # common nested formats
        if isinstance(message.get("text"), str):
            return message["text"]
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "\n".join(parts)

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        if parts:
            return "\n".join(parts)

    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text

    return ""


def _extract_json_block(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None

    fenced = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        candidate = fenced.group(1)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    for match in re.finditer(r"\{[\s\S]*\}", text):
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None


def _build_research_context(snapshot: dict[str, Any], watchlist: list[str]) -> dict[str, Any]:
    top_symbols = [row["ticker"] for row in sorted(snapshot.get("positions", []), key=lambda x: x.get("weight", 0.0), reverse=True)[:6]]
    query = " ".join(top_symbols[:4]) if top_symbols else "equity market AI stocks"

    web = web_search(f"{query} market outlook", max_results=6)
    news = fetch_news(query, max_results=8)
    x_posts = fetch_x_posts(query, max_results=8)

    quant_code = (
        "positions = INPUT.get('positions', [])\\n"
        "weights = sorted([max(float(p.get('weight', 0.0)), 0.0) for p in positions], reverse=True)\\n"
        "mom = [float(p.get('momentum_63d') or 0.0) for p in positions]\\n"
        "import json\\n"
        "print(json.dumps({"
        "'positions': len(positions), "
        "'top3_weight': sum(weights[:3]), "
        "'avg_momentum_63d': (sum(mom)/len(mom) if mom else 0.0)"
        "}))\\n"
    )
    quant = run_sandboxed_python(quant_code, input_payload={"positions": snapshot.get("positions", [])}, timeout_seconds=10)

    workspace = _ensure_workspace()
    files = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        files.append({"path": str(path.relative_to(workspace)), "size": path.stat().st_size})
        if len(files) >= 20:
            break

    return {
        "top_symbols": top_symbols,
        "watchlist": watchlist,
        "web": web,
        "news": news,
        "x_posts": x_posts,
        "quant_digest": quant,
        "workspace_files": files,
    }


def run_claude_analyst_cycle(snapshot: dict[str, Any], watchlist: list[str], risk_config: dict[str, Any]) -> dict[str, Any] | None:
    if settings.agent_provider != "claude":
        return None

    workspace = _ensure_workspace()
    research = _build_research_context(snapshot, watchlist)

    prompt_payload = {
        "portfolio": snapshot,
        "watchlist": watchlist,
        "risk": risk_config,
        "research": research,
        "instructions": {
            "goal": "Produce quant-led investment insights and concrete next actions.",
            "output": {
                "summary_markdown": "string",
                "intents": [
                    {
                        "symbol": "string",
                        "account_kind": "invest|stocks_isa",
                        "side": "buy|sell",
                        "order_type": "market",
                        "confidence": "0..1",
                        "expected_edge": "0..1",
                        "risk_score": "0..1",
                        "rationale": "string",
                        "target_notional": "number"
                    }
                ],
                "theses": [
                    {
                        "symbol": "string",
                        "title": "string",
                        "thesis": "string",
                        "catalysts": ["string"],
                        "invalidation": "string",
                        "confidence": "0..1"
                    }
                ]
            },
            "constraints": [
                "Respect provided risk rails.",
                "Do not assume missing prices — always fetch live prices via marketdata MCP tools.",
                "Portfolio context contains cost basis only (quantity, average_price, total_cost), NOT current prices.",
                "Focus on actionable, concise, evidence-backed recommendations.",
                "Return JSON only."
            ],
        },
    }

    sdk_error: str | None = None
    response_text = ""

    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

        sdk_cwd = resolve_sdk_cwd()
        setting_sources = parse_setting_sources(settings.claude_setting_sources, require_project=True)

        allowed_tools = ["Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "Task"]
        if settings.agent_allow_bash:
            allowed_tools.append("Bash")

        mcp_servers: dict[str, Any] = {}
        t212_script = _MCP_SERVER_DIR / "t212.py"
        market_script = _MCP_SERVER_DIR / "marketdata.py"
        scheduler_script = _MCP_SERVER_DIR / "scheduler.py"
        fundamentals_script = _MCP_SERVER_DIR / "fundamentals.py"

        # Resolve a possibly-relative SQLite DATABASE_URL to an absolute
        # path so MCP subprocesses (which may run with a different CWD)
        # open the *same* database file as the main app.
        _backend_root = str(_MCP_SERVER_DIR.parent)
        _db_url = settings.database_url
        if _db_url.startswith("sqlite:///./") or _db_url.startswith("sqlite:///mypf"):
            _rel = _db_url.replace("sqlite:///", "", 1)
            _abs = str((Path(_backend_root) / _rel).resolve())
            _db_url = f"sqlite:///{_abs}"
        _mcp_env = {"PYTHONPATH": _backend_root, "DATABASE_URL": _db_url}

        if t212_script.is_file():
            mcp_servers["trading212"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(t212_script)],
                "env": _build_sdk_env(),
            }
            allowed_tools.extend(_T212_MCP_TOOLS)
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
        if fundamentals_script.is_file():
            mcp_servers["fundamentals"] = {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fundamentals_script)],
                "env": _mcp_env,
            }
            allowed_tools.extend(_FUNDAMENTALS_MCP_TOOLS)

        options = ClaudeAgentOptions(
            system_prompt=(
                "You are MyPF's portfolio analyst agent — Archie's autonomous research cycle. "
                "Your job is risk-aware, evidence-based, high-signal recommendations, never generic advice.\n\n"
                "ESTABLISH THE MARKET REGIME FIRST — before any single-name work. Pull live data with the "
                "marketdata tools and state the regime explicitly: SPY and QQQ vs their SMA50/SMA200 "
                "(use get_technical_snapshot / get_indicator_series), and VIX level/trend "
                "(get_price_snapshot on ^VIX). Classify as risk-on / neutral / risk-off and say so in one line. "
                "Every recommendation downstream must be consistent with this regime — long-tilted in risk-on, "
                "defensive (cash, trims, or ISA-only INVERSE ETPs) in risk-off. Note: T212 has NO short selling; "
                "'downside' exposure is achieved via inverse ETPs in the ISA only.\n\n"
                "TOOLS — never quote a price you did not fetch live this run:\n"
                "- marketdata: get_price_snapshot, get_price_history_rows, get_technical_snapshot, "
                "get_indicator_series, get_risk_metrics, get_correlation_matrix, compare_assets.\n"
                "- fundamentals: get_fundamentals, get_valuation, get_financial_statements, get_earnings_calendar "
                "(use for valuation, profitability, growth, financial health, and earnings-date risk).\n"
                "- forecast: forecast_prices (Kronos p10/p50/p90) for a probabilistic price cone — treat as "
                "uncertainty, never a certainty.\n"
                "- Subagents via Task: delegate news/catalyst/web research to the 'researcher' subagent and "
                "heavier statistics / Python / app.quant work to the 'quant' subagent.\n\n"
                "EVERY recommendation MUST include: (1) position sizing within the provided risk rails, "
                "(2) an explicit invalidation level (the price/condition that proves it wrong), and "
                "(3) a regime caveat tying it back to the regime you established. "
                "Flag concentration, liquidity, and downside risk. "
                "This is ANALYSIS ONLY — never execute and never imply a trade has been executed; "
                "intents are proposals for Josh to approve."
            ),
            model=settings.claude_agent_model,
            cwd=str(sdk_cwd),
            add_dirs=[str(workspace)],
            max_turns=settings.agent_max_turns,
            allowed_tools=allowed_tools,
            setting_sources=setting_sources,
            mcp_servers=mcp_servers if mcp_servers else {},
            hooks=build_security_hooks(),
            agents=build_subagents(),
        )

        async def _run_query() -> tuple[str, dict]:
            chunks: list[str] = []
            cost_info: dict = {}
            async with ClaudeSDKClient(options=options) as client:
                await client.query(json.dumps(prompt_payload))
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        cost_info = {
                            "total_cost_usd": getattr(message, "total_cost_usd", None),
                            "duration_ms": getattr(message, "duration_ms", None),
                            "num_turns": getattr(message, "num_turns", None),
                            "session_id": getattr(message, "session_id", None),
                        }
                    text = _extract_text_from_sdk_message(message)
                    if text:
                        chunks.append(text)
            return "\n".join(chunks), cost_info

        import anyio

        configure_sdk_auth()

        response_text, cost_info = anyio.run(_run_query)

        if cost_info.get("total_cost_usd") is not None or cost_info.get("duration_ms") is not None:
            from app.services import costs_service
            from app.core.database import SessionLocal
            _source_id = cost_info.get("session_id") or "unknown"
            with SessionLocal() as _cost_db:
                costs_service.record(
                    _cost_db,
                    source="agent_run",
                    source_id=_source_id,
                    model=settings.claude_agent_model,
                    total_cost_usd=cost_info.get("total_cost_usd"),
                    duration_ms=cost_info.get("duration_ms"),
                    num_turns=cost_info.get("num_turns"),
                )

    except Exception as exc:
        sdk_error = str(exc)

    if not response_text:
        if sdk_error:
            return {
                "provider": "claude",
                "ok": False,
                "error": sdk_error,
                "summary_markdown": "Claude runtime unavailable, using rule-based fallback.",
                "intents": [],
                "theses": [],
                "research": research,
            }
        return None

    parsed = _extract_json_block(response_text)
    if parsed is None:
        return {
            "provider": "claude",
            "ok": True,
            "summary_markdown": response_text[:8000],
            "intents": [],
            "theses": [],
            "research": research,
            "raw": response_text[:12000],
        }

    parsed.setdefault("summary_markdown", "")
    parsed.setdefault("intents", [])
    parsed.setdefault("theses", [])

    return {
        "provider": "claude",
        "ok": True,
        "summary_markdown": str(parsed.get("summary_markdown", ""))[:12000],
        "intents": parsed.get("intents", []) if isinstance(parsed.get("intents"), list) else [],
        "theses": parsed.get("theses", []) if isinstance(parsed.get("theses"), list) else [],
        "research": research,
        "raw": response_text[:12000],
    }


def run_research_request(
    *,
    objective: str,
    subject: str = "",
    hypothesis: str = "",
    horizon_days: int = 30,
    portfolio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an interactive, agent-driven research/analysis request (Research Desk).

    Archie orchestrates the researcher + quant subagents over the market-data,
    forecast, and read-only T212 tools to evaluate ``objective``/``hypothesis``,
    returning a structured verdict + a markdown report and writing a persistent
    artifact under .claude/runtime/artifacts/research/.
    """
    objective = (objective or "").strip()
    subject = (subject or "").strip()
    hypothesis = (hypothesis or "").strip()
    horizon_days = max(1, min(int(horizon_days or 30), 365))

    if settings.agent_provider != "claude":
        return {"ok": False, "error": "Claude agent provider is not enabled.",
                "markdown": "", "verdict": None, "confidence": None, "artifact_path": None}
    if not objective:
        return {"ok": False, "error": "An objective is required.",
                "markdown": "", "verdict": None, "confidence": None, "artifact_path": None}

    request_block = {
        "subject": subject or "(none — general / new idea described in the objective)",
        "objective": objective,
        "hypothesis": hypothesis or "(none stated — form and test your own)",
        "horizon_days": horizon_days,
        "portfolio_context": portfolio_context or {},
    }

    system_prompt = (
        "You are Archie's Research Desk analyst — rigorous, quantified, and honest about uncertainty. "
        "Evaluate the ANALYSIS REQUEST (provided as JSON) and produce a verdict. Protocol:\n"
        "1) Restate or form the hypothesis.\n"
        "2) Gather LIVE evidence via tools — never assume prices. Use the marketdata tools "
        "(get_price_snapshot, get_technical_snapshot, get_risk_metrics, get_indicator_series, "
        "compare_assets, get_correlation_matrix), the fundamentals tools (get_fundamentals, "
        "get_valuation, get_financial_statements, get_earnings_calendar) for company facts, "
        "valuation ratios, financial statements, and earnings, and the Kronos forecast tool "
        f"(forecast_prices, ~{horizon_days}-day horizon, p10/p50/p90). Delegate news/catalysts to "
        "the 'researcher' subagent and heavier statistics to the 'quant' subagent.\n"
        "3) Weigh evidence FOR vs AGAINST the hypothesis.\n"
        "4) State invalidation conditions (what would prove it wrong).\n"
        "5) Give a suggested action — ANALYSIS ONLY; never execute or imply executed trades. "
        "Fundamental and valuation data ARE available via the fundamentals tools "
        "(get_fundamentals, get_valuation, get_financial_statements, get_earnings_calendar) — "
        "use them whenever the question involves valuation, profitability, growth, or financial health.\n\n"
        "Write a clean markdown report (headers, a metrics table, the forecast read, evidence for/against, "
        "verdict, invalidation, suggested action) and SAVE it with the Write tool to a file named "
        "artifacts/research/<concise-slug>.md.\n\n"
        "Your FINAL message must be the markdown report followed by EXACTLY one fenced JSON block:\n"
        "```json\n"
        "{\"verdict\": \"support|refute|mixed\", \"confidence\": 0.0, \"summary\": \"one-sentence takeaway\", "
        "\"suggested_action\": \"string\", \"invalidation\": \"string\", "
        "\"artifact_path\": \"artifacts/research/<slug>.md\"}\n"
        "```"
    )

    sdk_error: str | None = None
    response_text = ""
    cost_info: dict = {}

    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

        sdk_cwd = resolve_sdk_cwd()
        setting_sources = parse_setting_sources(settings.claude_setting_sources, require_project=True)

        allowed_tools = ["Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "Task"]
        if settings.agent_allow_bash:
            allowed_tools.append("Bash")

        _backend_root = str(_MCP_SERVER_DIR.parent)
        _db_url = settings.database_url
        if _db_url.startswith("sqlite:///./") or _db_url.startswith("sqlite:///mypf"):
            _rel = _db_url.replace("sqlite:///", "", 1)
            _abs = str((Path(_backend_root) / _rel).resolve())
            _db_url = f"sqlite:///{_abs}"
        _mcp_env = {"PYTHONPATH": _backend_root, "DATABASE_URL": _db_url}

        mcp_servers: dict[str, Any] = {}
        for name, script, env, tools in (
            ("trading212", _MCP_SERVER_DIR / "t212.py", _build_sdk_env(), _T212_MCP_TOOLS),
            ("marketdata", _MCP_SERVER_DIR / "marketdata.py", _mcp_env, _MARKET_MCP_TOOLS),
            ("forecast", _MCP_SERVER_DIR / "forecast.py", _mcp_env, _FORECAST_MCP_TOOLS),
            ("fundamentals", _MCP_SERVER_DIR / "fundamentals.py", _mcp_env, _FUNDAMENTALS_MCP_TOOLS),
        ):
            if script.is_file():
                mcp_servers[name] = {"type": "stdio", "command": sys.executable, "args": [str(script)], "env": env}
                allowed_tools.extend(tools)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=settings.claude_agent_model,
            cwd=str(sdk_cwd),
            max_turns=30,
            allowed_tools=allowed_tools,
            setting_sources=setting_sources,
            mcp_servers=mcp_servers if mcp_servers else {},
            hooks=build_security_hooks(),
            agents=build_subagents(),
        )

        async def _run_query() -> tuple[str, dict]:
            # Keep only the LAST assistant text block — earlier blocks are
            # intermediate reasoning/delegation emitted between tool calls,
            # not the polished final report + JSON verdict.
            last_text = ""
            info: dict = {}
            async with ClaudeSDKClient(options=options) as client:
                await client.query(json.dumps(request_block))
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        info = {
                            "total_cost_usd": getattr(message, "total_cost_usd", None),
                            "duration_ms": getattr(message, "duration_ms", None),
                            "num_turns": getattr(message, "num_turns", None),
                            "session_id": getattr(message, "session_id", None),
                        }
                    text = _extract_text_from_sdk_message(message)
                    if text:
                        last_text = text
            return last_text.strip(), info

        import anyio

        configure_sdk_auth()
        response_text, cost_info = anyio.run(_run_query)

        if cost_info.get("total_cost_usd") is not None or cost_info.get("duration_ms") is not None:
            from app.services import costs_service
            from app.core.database import SessionLocal
            with SessionLocal() as _cost_db:
                costs_service.record(
                    _cost_db,
                    source="research",
                    source_id=cost_info.get("session_id") or "research",
                    model=settings.claude_agent_model,
                    total_cost_usd=cost_info.get("total_cost_usd"),
                    duration_ms=cost_info.get("duration_ms"),
                    num_turns=cost_info.get("num_turns"),
                )
    except Exception as exc:  # noqa: BLE001
        sdk_error = str(exc)

    if not response_text:
        return {"ok": False, "error": sdk_error or "No response from the analyst.",
                "markdown": "", "verdict": None, "confidence": None, "artifact_path": None}

    parsed = _extract_json_block(response_text) or {}
    # Strip the trailing machine-readable JSON block from the displayed report.
    display_md = re.sub(r"```json\s*\{[\s\S]*?\}\s*```\s*$", "", response_text).strip()

    confidence: float | None
    try:
        confidence = float(parsed["confidence"]) if parsed.get("confidence") is not None else None
    except (TypeError, ValueError, KeyError):
        confidence = None

    return {
        "ok": True,
        "markdown": display_md[:16000],
        "verdict": (str(parsed.get("verdict")).lower() if parsed.get("verdict") else None),
        "confidence": confidence,
        "summary": (str(parsed.get("summary")).strip()[:600] or None) if parsed.get("summary") else None,
        "suggested_action": (str(parsed.get("suggested_action")).strip()[:1200] or None) if parsed.get("suggested_action") else None,
        "invalidation": (str(parsed.get("invalidation")).strip()[:1200] or None) if parsed.get("invalidation") else None,
        "artifact_path": (str(parsed.get("artifact_path")).strip() or None) if parsed.get("artifact_path") else None,
        "raw": response_text[:16000],
    }
