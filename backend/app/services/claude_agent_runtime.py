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
    build_security_hooks, build_subagents, parse_setting_sources, project_root, resolve_sdk_cwd,
    _T212_MCP_TOOLS, _MARKET_MCP_TOOLS, _SCHEDULER_MCP_TOOLS,
)
from app.services.research_service import fetch_news, fetch_x_posts, web_search

settings = get_settings()

_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_servers"


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

    _backend_root = str(Path(__file__).resolve().parent.parent.parent)
    env["PYTHONPATH"] = _backend_root

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
                "Do not assume missing prices.",
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

        options = ClaudeAgentOptions(
            system_prompt=(
                "You are MyPF's portfolio analyst agent. "
                "Prioritize risk-aware, evidence-based, high-signal recommendations."
            ),
            model=settings.claude_model,
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

        env_key = (settings.anthropic_api_key or "").strip()
        if env_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", env_key)

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
                    model=settings.claude_model,
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
