"""Views MCP server for Archie.

Lets Archie build and change the user's Views (agent-built, sandboxed pages)
from chat: "build me a view of my dividend income by month". Builds run the
same guarded builder the app uses (one draft file, read-only data, no network)
and land in the same storage, so the new page appears in the sidebar.

Tool names for wiring: see ``VIEWS_MCP_TOOLS``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.services import views_builder as builder
from app.services import views_store as store

VIEWS_MCP_TOOLS = [
    "mcp__views__list_views",
    "mcp__views__create_view",
    "mcp__views__update_view",
]

_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("views-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "views.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "views",
    instructions=(
        "Views are pages in the OpenPF app built on request from the user's live data "
        "(e.g. 'dividend income by month', 'drift vs my target weights'). Use create_view when the "
        "user asks for a page, dashboard, tracker or chart they want to keep; update_view to change "
        "one. Builds take ~30-90s. Afterwards tell the user it's pinned in the sidebar and give the "
        "link path (/views/<id>). Views are read-only: they cannot trade."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _queue(prompt: str, view_id: str | None) -> dict[str, Any]:
    queued = builder.enqueue(prompt, view_id=view_id)
    return {
        "ok": True,
        "status": "building",
        "view_id": queued["view_id"],
        "path": queued["path"],
        "message": (
            "The view is being built now (usually under a minute). It appears in the "
            "sidebar under Views when ready; tell the user where to find it."
        ),
    }


@mcp.tool()
def list_views() -> str:
    """List the user's views (id, title, description, pinned, current version)."""
    views = store.list_views()
    return _fmt([{k: v[k] for k in ("id", "title", "description", "pinned", "current_version", "updated_at")} for v in views])


@mcp.tool()
def create_view(prompt: str, title: str = "") -> str:
    """Build a new view from a description of what the user wants to see.

    Args:
        prompt: What the page should show, in the user's words plus any detail you know
            (e.g. "monthly dividend income for the last 12 months, plus which holdings pay most").
        title: Optional short title hint.
    """
    logger.info("create_view prompt=%r", prompt[:200])
    full = f"{prompt}\n\n(Title suggestion: {title})" if title.strip() else prompt
    try:
        return _fmt(_queue(full, None))
    except ValueError as exc:
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def update_view(view_id: str, instruction: str) -> str:
    """Change an existing view (creates a new version; the user can revert).

    Args:
        view_id: The view id from list_views.
        instruction: What to change, e.g. "add a line for the 12-month average".
    """
    logger.info("update_view id=%s instruction=%r", view_id, instruction[:200])
    try:
        return _fmt(_queue(instruction, view_id))
    except store.ViewNotFound:
        return _fmt({"ok": False, "error": f"no view with id '{view_id}'"})
    except (ValueError, builder.BuildConflict) as exc:
        return _fmt({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    mcp.run()
