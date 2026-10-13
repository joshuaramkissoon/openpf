"""Opt-in eval: ask Archie the standard quant questions through the real Claude CLI.

Not part of pytest — it spends model budget. It refuses to run outside a
sandbox instance. Start a sandbox backend first (it serves the mock broker the
trading212 MCP server reads), then run this with the SAME sandbox env vars so
the tools see the same database:

    OPENPF_SANDBOX=1 DATABASE_URL=sqlite:////tmp/x/q.db OPENPF_RUNTIME_DIR=/tmp/x/runtime \\
    VIEWS_DATA_DIR=... RECIPES_DATA_DIR=... OPENPF_SANDBOX_STATE=... PORT=<sandbox port> \\
    CLAUDE_CODE_OAUTH_TOKEN=... python -m evals.archie_tools.run [--only id1,id2] [--model ...]

Each case uses the chat runtime's own options (MCP servers, hooks, subagents,
skills, tool search) in a fresh session. Results go to
``evals/archie_tools/results/<timestamp>.json`` and a summary table is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from evals.archie_tools.cases import CASES, Case, Run, ToolCall  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _parse_output(content: Any) -> Any:
    if isinstance(content, list):
        text = "".join(c.get("text", "") if isinstance(c, dict) else str(getattr(c, "text", c)) for c in content)
    else:
        text = str(content or "")
    try:
        out = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    # FastMCP structured output wraps a str return as {"result": "<json>"}.
    if isinstance(out, dict) and set(out) == {"result"} and isinstance(out["result"], str):
        try:
            return json.loads(out["result"])
        except json.JSONDecodeError:
            return out["result"]
    return out


async def run_case(case: Case, *, model: str | None, budget: float) -> Run:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        PermissionResultDeny,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    from app.core.database import SessionLocal, init_db
    from app.services.chat_service import build_prompt_for_session, create_session
    from app.services.claude_chat_runtime import ClaudeChatRuntime

    init_db()
    with SessionLocal() as db:
        session = create_session(db, title=f"eval: {case.id}")
        prompt = build_prompt_for_session(db, session, case.prompt, "all", "GBP")

    async def deny(tool_name: str, input_data: dict, context: Any):
        return PermissionResultDeny(message="Not available in this eval run; continue without it.")

    options = replace(ClaudeChatRuntime()._build_options(), can_use_tool=deny, max_budget_usd=budget,
                      include_partial_messages=False)
    if model:
        options = replace(options, model=model)

    calls: dict[str, ToolCall] = {}
    order: list[str] = []
    texts: list[str] = []
    cost = None
    started = time.monotonic()
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt, session_id=session.id)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            calls[block.id] = ToolCall(block.name, dict(block.input or {}), None)
                            order.append(block.id)
                        elif isinstance(block, TextBlock) and not getattr(msg, "parent_tool_use_id", None):
                            texts.append(block.text)
                elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock) and block.tool_use_id in calls:
                            calls[block.tool_use_id].output = _parse_output(block.content)
                            calls[block.tool_use_id].is_error = bool(block.is_error)
                elif isinstance(msg, ResultMessage):
                    cost = getattr(msg, "total_cost_usd", None)
    except Exception as exc:  # noqa: BLE001
        return Run("\n\n".join(texts), [calls[i] for i in order], cost, time.monotonic() - started, error=repr(exc)[:300])
    return Run("\n\n".join(texts), [calls[i] for i in order], cost, time.monotonic() - started)


def grade(case: Case, run: Run) -> dict[str, Any]:
    shorts = [c.short for c in run.calls]
    results: list[dict[str, Any]] = []
    for group in case.expect:
        ok = any(g in shorts for g in group)
        results.append({"check": f"calls {' or '.join(group)}", "ok": ok})
    bad = [s for s in shorts if s in case.forbid]
    results.append({"check": "no unrequested side effects", "ok": not bad, "detail": ", ".join(bad) or None})
    quant_errors = [c.short for c in run.calls if c.name.startswith("mcp__quant__") and isinstance(c.output, dict) and not c.output.get("ok")]
    results.append({"check": "quant tools succeeded", "ok": not quant_errors, "detail": ", ".join(quant_errors) or None})
    for chk in case.checks:
        try:
            ok, detail = chk(run)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"check crashed: {exc}"
        results.append({"check": detail, "ok": bool(ok)})
    if run.error:
        results.append({"check": "run completed", "ok": False, "detail": run.error})
    return {
        "id": case.id,
        "prompt": case.prompt,
        "passed": all(r["ok"] for r in results),
        "score": f"{sum(r['ok'] for r in results)}/{len(results)}",
        "checks": results,
        "tools": shorts,
        "cost_usd": run.cost_usd,
        "duration_s": round(run.duration_s or 0, 1),
        "reply": run.reply,
        "calls": [asdict(c) | {"output": c.output if isinstance(c.output, (dict, list)) else str(c.output)[:2000]} for c in run.calls],
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--model", help="override the chat model")
    ap.add_argument("--budget", type=float, default=1.5, help="max USD per case (default 1.5)")
    args = ap.parse_args()

    if os.environ.get("OPENPF_SANDBOX", "").lower() not in {"1", "true", "yes", "on"}:
        print("Refusing to run: set OPENPF_SANDBOX=1 and point the env at a sandbox instance.", file=sys.stderr)
        return 2
    from app.core.sandbox import is_sandbox, verify_isolation

    assert is_sandbox()
    verify_isolation()

    only = set(filter(None, (args.only or "").split(",")))
    cases = [c for c in CASES if not only or c.id in only]
    graded = []
    for case in cases:
        print(f"→ {case.id} …", flush=True)
        run = await run_case(case, model=args.model, budget=args.budget)
        g = grade(case, run)
        graded.append(g)
        mark = "PASS" if g["passed"] else "FAIL"
        cost = f"${g['cost_usd']:.2f}" if g["cost_usd"] is not None else "$?"
        print(f"  {mark} {g['score']}  tools={','.join(g['tools'])}  {cost}  {g['duration_s']}s", flush=True)
        for r in g["checks"]:
            if not r["ok"]:
                print(f"     ✗ {r['check']}" + (f" — {r['detail']}" if r.get("detail") else ""), flush=True)

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"cases": graded, "model": args.model}, indent=2, default=str))
    passed = sum(g["passed"] for g in graded)
    total_cost = sum(g["cost_usd"] or 0 for g in graded)
    checks = [r for g in graded for r in g["checks"]]
    print(f"\n{passed}/{len(graded)} cases passed · {sum(r['ok'] for r in checks)}/{len(checks)} checks · ${total_cost:.2f} · {out}")
    return 0 if passed == len(graded) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
