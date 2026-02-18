from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.claude_agent_runtime import run_sandboxed_python
from app.services.research_service import fetch_news, fetch_x_posts, web_search

settings = get_settings()


class ToolPolicyError(RuntimeError):
    pass


def _workspace_root() -> Path:
    root = Path(settings.agent_workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_resolve(path: str) -> Path:
    root = _workspace_root()
    candidate = (root / path).resolve()
    if root not in candidate.parents and candidate != root:
        raise ToolPolicyError("filesystem access outside agent workspace is blocked")
    return candidate


def read_workspace_file(path: str) -> str:
    target = _safe_resolve(path)
    if not target.exists() or not target.is_file():
        return ""
    return target.read_text(encoding="utf-8")[:200_000]


def write_workspace_file(path: str, content: str) -> str:
    target = _safe_resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


def run_quant_code(code: str, input_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_sandboxed_python(code, input_payload=input_payload, timeout_seconds=15)


def tool_web_search(query: str, max_results: int = 5) -> list[dict]:
    return web_search(query, max_results=max_results)


def tool_news_search(query: str, max_results: int = 8) -> list[dict]:
    return fetch_news(query, max_results=max_results)


def tool_x_search(query: str, max_results: int = 8) -> list[dict]:
    return fetch_x_posts(query, max_results=max_results)


def summarize_workspace_files(max_files: int = 20) -> list[dict[str, Any]]:
    root = _workspace_root()
    results: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        results.append(
            {
                "path": rel,
                "size": path.stat().st_size,
            }
        )
        if len(results) >= max_files:
            break
    return results


def quant_snapshot_digest(portfolio_snapshot: dict[str, Any]) -> dict[str, Any]:
    code = (
        "positions = INPUT.get('positions', [])\n"
        "weights = [max(float(p.get('weight', 0.0)), 0.0) for p in positions]\n"
        "weights = sorted(weights, reverse=True)\n"
        "top5 = sum(weights[:5])\n"
        "import json\n"
        "print(json.dumps({'positions': len(positions), 'top5_weight': top5, 'sum_weights': sum(weights)}))\n"
    )

    result = run_quant_code(code, {"positions": portfolio_snapshot.get("positions", [])})
    if result.get("exit_code") != 0:
        return {"error": result.get("stderr", "quant digest failed")}

    stdout = str(result.get("stdout", "")).strip()
    try:
        return json.loads(stdout.splitlines()[-1]) if stdout else {}
    except json.JSONDecodeError:
        return {"raw": stdout}
