"""Recipes MCP server for Archie — he builds and maintains his own harness.

A recipe is a skill (steps, tools to use, output shape) + a trigger (manual,
schedule, or an event like "earnings week") + delivery (Home, optionally
Telegram). "Every Friday check my plays against their kill rules" becomes a
recipe Archie writes himself; "shorter briefs" refines one.

Safety: recipes are instructions only (validated: no tool grants, no
frontmatter beyond name/description, no shell, no trade execution). Runs use
the scheduled-task context, so they can at most create proposals. Calls from
an unattended context (``OPENPF_MCP_CONTEXT`` != chat) never apply directly:
they become suggestions the user approves, so text in a news article can't
rewrite the harness.

Tool names for wiring: see ``RECIPES_MCP_TOOLS``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.services import recipes_service as svc
from app.services import recipes_validate as rv

RECIPES_MCP_TOOLS = [
    "mcp__recipes__list_recipes",
    "mcp__recipes__get_recipe",
    "mcp__recipes__create_recipe",
    "mcp__recipes__update_recipe",
    "mcp__recipes__set_recipe_enabled",
]

_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("recipes-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "recipes.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "recipes",
    instructions=(
        "Recipes are the user's automations, and you write them. When the user asks for something "
        "recurring or conditional ('every Friday…', 'each morning…', 'when a holding drops 5%…', "
        "'before earnings…'), create_recipe with clear step-by-step instructions naming the tools to use, "
        "an Output section (headline first) and Rules. When they give feedback on a recipe's output "
        "('shorter', 'skip crypto'), update_recipe. Recipes can only read data and propose trades — never "
        "execute, approve or change risk rails; if the user asks for auto-trading, explain that and offer a "
        "recipe that proposes instead. Every change is versioned and revertable on the Recipes page "
        "(/recipes/<slug>). Prefer recipes over raw scheduled tasks for anything the user asks for."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _context() -> str:
    # Fail safe: anything that isn't explicitly the chat context is unattended.
    return "chat" if os.environ.get("OPENPF_MCP_CONTEXT", "").strip().lower() == "chat" else "scheduled"


def _trigger(schedule: str, timezone: str, event: str, big_move_pct: float, manual: bool) -> dict[str, Any] | None:
    if event.strip():
        trig: dict[str, Any] = {"kind": "event", "event": event.strip().lower(), "timezone": timezone or rv.DEFAULT_TZ}
        if big_move_pct:
            trig["threshold_pct"] = big_move_pct
        return trig
    if schedule.strip():
        return {"kind": "schedule", "cron": schedule.strip(), "timezone": timezone or rv.DEFAULT_TZ}
    if manual:
        return {"kind": "manual"}
    return None


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    last = r.get("last_run") or {}
    return {
        "slug": r["slug"], "title": r["title"], "summary": r["summary"], "enabled": r["enabled"],
        "when": r["trigger_summary"], "source": r["source"], "version": r["current_version"],
        "telegram": r["delivery"]["telegram"], "last_run": {"at": last.get("at"), "status": last.get("status"),
                                                            "summary": last.get("summary")} if last else None,
        "path": f"/recipes/{r['slug']}",
    }


def _result(out: dict[str, Any], verb: str) -> dict[str, Any]:
    if out.get("applied"):
        r = out["recipe"]
        return {
            "ok": True, "applied": True, "recipe": _brief(r),
            "message": f"{verb} '{r['title']}' (v{r['current_version']}) — {r['trigger_summary'].lower()}. "
                       f"It's on the Recipes page at /recipes/{r['slug']}; every version can be diffed and reverted.",
        }
    s = out["suggestion"]
    return {
        "ok": True, "applied": False, "suggestion_id": s["id"],
        "message": "This run is unattended, so the change was saved as a SUGGESTION for the user to review on "
                   "the Recipes page. It is not live — say it's suggested, not done.",
    }


def _call(fn) -> str:
    init_db()
    db = SessionLocal()
    try:
        return _fmt(fn(db))
    except rv.RecipeValidationError as exc:
        return _fmt({"ok": False, "error": str(exc)})
    except svc.RecipeNotFound as exc:
        return _fmt({"ok": False, "error": f"no recipe '{exc}' — call list_recipes"})
    finally:
        db.close()


@mcp.tool()
def list_recipes() -> str:
    """List the user's recipes (built-in and custom): what each does, when it runs, last result."""
    return _call(lambda db: {"ok": True, "recipes": [_brief(r) for r in svc.list_recipes(db)]})


@mcp.tool()
def get_recipe(slug: str) -> str:
    """Full detail for one recipe, including its current instructions."""
    def run(db):
        r = svc.serialize(db, svc.get(db, slug), runs=5)
        return {"ok": True, "recipe": {**_brief(r), "instructions": r["body"], "trigger": r["trigger"],
                                       "recent_runs": svc.recipe_runs(db, svc.get(db, slug), limit=5)}}
    return _call(run)


@mcp.tool()
def create_recipe(
    title: str,
    summary: str,
    instructions: str,
    schedule: str = "",
    timezone: str = "Europe/London",
    event: str = "",
    big_move_pct: float = 0,
    telegram: bool = False,
    reason: str = "",
) -> str:
    """Create a recipe (a skill Archie runs on a trigger).

    Args:
        title: Short name, e.g. "Friday play check".
        summary: One plain-English sentence of what it does, for the user.
        instructions: Markdown body: a short intro, "## Steps" (numbered, naming the tools to call),
            "## Output" (start with a one-line headline) and "## Rules". Read memory/preferences.md first.
        schedule: 5-field cron in `timezone` (at most hourly), e.g. "0 17 * * 5" = Fridays 17:00.
            Leave empty for manual-only or when using `event`.
        timezone: IANA timezone (default Europe/London).
        event: Optional event trigger instead of a schedule: "earnings_week" (Mondays, when a holding
            reports that week) or "big_move" (a holding moves past big_move_pct in a day).
        big_move_pct: Threshold for big_move (2-30, default 5).
        telegram: Also send each result to Telegram.
        reason: Why you're creating it (shown in version history).
    """
    logger.info("create_recipe title=%r context=%s", title, _context())
    screen = rv.screen_request(f"{title}. {summary}")
    if not screen.ok and screen.kind in {"auto_trade", "rails", "unrealistic", "too_often"}:
        return _fmt({"ok": False, "error": screen.reason, "alternative": screen.alternative})
    fields = {
        "title": title, "summary": summary, "body": instructions,
        "trigger": _trigger(schedule, timezone, event, big_move_pct, manual=True),
        "delivery": {"telegram": bool(telegram)},
    }
    return _call(lambda db: _result(svc.request_change(db, action="create", context=_context(), fields=fields,
                                                       reason=reason or "Created by Archie"), "Created"))


@mcp.tool()
def update_recipe(
    slug: str,
    reason: str,
    instructions: str = "",
    title: str = "",
    summary: str = "",
    schedule: str = "",
    timezone: str = "",
    event: str = "",
    big_move_pct: float = 0,
    manual_only: bool = False,
    telegram: str = "",
) -> str:
    """Change a recipe — creates a new version the user can diff and revert.

    Pass only what changes. Use this when the user gives feedback on a recipe's output
    ("shorter briefs", "skip crypto", "run it at 8 instead").

    Args:
        slug: Recipe slug from list_recipes.
        reason: One line on why (shown in version history), e.g. "User asked for shorter briefs".
        instructions: The full new markdown body (replaces the old one) — get_recipe first and edit it.
        title / summary: New title or one-line summary.
        schedule: New cron (at most hourly). timezone: IANA timezone.
        event: Switch to an event trigger ("earnings_week" | "big_move"); big_move_pct its threshold.
        manual_only: True to make it run only when the user presses Run now.
        telegram: "on" or "off" to change Telegram delivery.
    """
    logger.info("update_recipe slug=%s context=%s", slug, _context())

    def run(db):
        current = svc.get(db, slug)
        fields: dict[str, Any] = {"title": title, "summary": summary, "body": instructions}
        trig = _trigger(schedule, timezone or (current.trigger or {}).get("timezone") or rv.DEFAULT_TZ,
                        event, big_move_pct, manual_only)
        if trig:
            fields["trigger"] = trig
        if telegram.strip().lower() in {"on", "off", "true", "false"}:
            fields["delivery"] = {"telegram": telegram.strip().lower() in {"on", "true"}}
        return _result(svc.request_change(db, action="update", context=_context(), slug=slug, fields=fields,
                                          reason=reason), "Updated")

    return _call(run)


@mcp.tool()
def set_recipe_enabled(slug: str, enabled: bool, reason: str = "") -> str:
    """Pause (enabled=false) or resume (enabled=true) a recipe."""
    logger.info("set_recipe_enabled slug=%s enabled=%s context=%s", slug, enabled, _context())
    return _call(lambda db: _result(svc.request_change(db, action="enable" if enabled else "disable",
                                                       context=_context(), slug=slug, reason=reason),
                                    "Resumed" if enabled else "Paused"))


if __name__ == "__main__":
    mcp.run()
