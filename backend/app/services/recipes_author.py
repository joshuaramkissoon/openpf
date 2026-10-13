"""Turns a sentence into a recipe draft ("Ask Archie to make one"), and a
sentence of feedback into a revised recipe ("make it shorter").

Two layers, so garbage never reaches the user as a broken automation:

1. ``recipes_validate.screen_request`` — deterministic, instant: empty input,
   auto-trading, rail changes, impossible promises, minute-level schedules,
   data we don't have.
2. A short, tool-less model call with a strict JSON schema. It may refuse
   (``ok: false`` + a reason + an alternative) for anything the screen missed;
   whatever it returns is then validated by the same rules as every other
   recipe write. Nothing is saved here — the UI previews the draft (with a
   diff for revisions) and the user saves it.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from app.core.config import get_settings
from app.services import recipes_validate as rv
from app.services.recipes_validate import RecipeValidationError

logger = logging.getLogger(__name__)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "reason": {"type": "string"},
        "alternative": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "icon": {"type": "string"},
        "instructions": {"type": "string"},
        "trigger": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["manual", "schedule", "event"]},
                "cron": {"type": "string"},
                "timezone": {"type": "string"},
                "event": {"type": "string", "enum": ["earnings_week", "big_move"]},
                "threshold_pct": {"type": "number"},
            },
            "required": ["kind"],
        },
        "telegram": {"type": "boolean"},
        "change_note": {"type": "string"},
    },
    "required": ["ok"],
}

_TOOLS_DOC = """Tools a recipe can tell Archie to use (read-only unless noted):
- trading212: get_account_summary, get_positions, get_pending_orders, get_order_history, get_dividend_history, get_transaction_history
- marketdata: get_price_snapshot, get_price_history_rows, get_technical_snapshot, get_indicator_series, get_risk_metrics, get_correlation_matrix, compare_assets
- fundamentals: get_fundamentals, get_valuation, get_financial_statements, get_earnings_calendar
- intel: get_company_news, get_market_news, get_macro_snapshot, get_earnings
- watchlist: list_watchlist, add_to_watchlist, update_watchlist_item, flag_watchlist_item (raises an alert)
- plays: list_plays, get_play (incl. kill-rule status), stage_play_tranche (creates proposals)
- proposals: propose_trade (queues a trade for the USER to approve — the only trade action), list_proposals
- web search; memory files (memory/preferences.md, goals.md, market_views.md)
NOT available: placing/approving/cancelling orders, changing risk rails or auto-execute, moving money,
options/order-book/level-2 data, other brokers/bank/pension accounts, email."""

_SYSTEM = f"""You write "recipes" for OpenPF, a personal Trading 212 portfolio app. A recipe is a skill that
Archie (the user's AI portfolio copilot) runs on a trigger, plus where the result goes. Respond ONLY with JSON
matching the schema.

{_TOOLS_DOC}

Triggers: "manual" (Run now only), "schedule" (5-field cron in Europe/London, at most hourly — a specific
minute, never */N minutes), or "event": "earnings_week" (checked Mondays; runs if a holding reports within 7 days)
or "big_move" (checked hourly in market hours; runs when a holding moves more than threshold_pct, 2-30, in a day).

Instructions format (markdown, under 6000 characters): one or two framing sentences, then "## Steps" (numbered,
naming the tools above), "## Output" (always starting with a one-line **Headline**; concise, tables over prose)
and "## Rules" (what to skip, when to stay quiet). Tell Archie to read memory/preferences.md first. Never
include shell commands, frontmatter, or instructions to execute/approve/cancel trades or change risk rails;
trade ideas go through propose_trade for the user to approve.

Refuse with ok=false, a kind one-sentence reason and a concrete alternative the user could ask for instead,
when the request: needs data or actions not listed above; asks to trade automatically; promises returns;
needs a sub-hourly schedule; or is too vague to act on (then suggest a specific version). Otherwise ok=true
with title (≤ 6 words), summary (one plain-English sentence, ≤ 200 chars, says what it does and when),
icon (one lucide icon name, e.g. sunrise, target, coins, calendar-clock, activity, globe, piggy-bank,
binoculars, bell, newspaper, scale, shield), instructions, trigger and telegram (true only if asked).
Today is {date.today().isoformat()}."""


async def _ask_model(prompt: str) -> dict[str, Any] | None:  # pragma: no cover — network; mocked in tests
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    from app.services.claude_sdk_config import configure_sdk_auth, resolve_sdk_cwd

    configure_sdk_auth()
    settings = get_settings()
    options = ClaudeAgentOptions(
        system_prompt=_SYSTEM,
        model=settings.recipes_model,
        cwd=str(resolve_sdk_cwd()),
        setting_sources=[],
        tools=[],
        allowed_tools=[],
        max_turns=3,
        permission_mode="dontAsk",
        output_format={"type": "json_schema", "schema": _SCHEMA},
    )
    structured: Any = None
    texts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            structured = getattr(message, "structured_output", None)
            try:
                from app.core.database import SessionLocal
                from app.services import costs_service

                with SessionLocal() as udb:
                    costs_service.record(udb, source="recipes", source_id="author", model=settings.recipes_model,
                                         total_cost_usd=message.total_cost_usd, duration_ms=message.duration_ms,
                                         num_turns=message.num_turns)
            except Exception:  # noqa: BLE001
                logger.debug("usage record failed", exc_info=True)
        else:
            for block in getattr(message, "content", None) or []:
                if hasattr(block, "text"):
                    texts.append(block.text)
    if isinstance(structured, dict):
        return structured
    raw = structured if isinstance(structured, str) else "\n".join(texts)
    try:
        start, end = raw.index("{"), raw.rindex("}")
        obj = json.loads(raw[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def _refusal(kind: str, reason: str, alternative: str = "") -> dict[str, Any]:
    return {"ok": False, "kind": kind, "reason": reason, "alternative": alternative}


def _to_fields(out: dict[str, Any]) -> dict[str, Any]:
    trigger = dict(out.get("trigger") or {"kind": "manual"})
    trigger.setdefault("timezone", rv.DEFAULT_TZ)
    return {
        "title": out.get("title", ""),
        "summary": out.get("summary", ""),
        "icon": out.get("icon"),
        "body": out.get("instructions", ""),
        "trigger": trigger,
        "delivery": {"telegram": bool(out.get("telegram"))},
    }


def _finish(out: dict[str, Any] | None) -> dict[str, Any]:
    if not out:
        return _refusal("unavailable", "Archie couldn't draft that just now — try again in a moment.")
    if not out.get("ok"):
        return _refusal("declined", str(out.get("reason") or "That isn't something a recipe can do."),
                        str(out.get("alternative") or ""))
    fields = _to_fields(out)
    try:
        clean = {
            "title": rv.clean_title(fields["title"]),
            "summary": rv.clean_summary(fields["summary"]),
            "icon": rv.clean_icon(fields["icon"]),
            "body": rv.clean_body(fields["body"]),
            "trigger": rv.normalise_trigger(fields["trigger"]),
            "delivery": rv.normalise_delivery(fields["delivery"]),
        }
    except RecipeValidationError as exc:
        return _refusal("invalid", f"Archie's draft didn't pass the safety check ({exc}).",
                        "Try rephrasing the request a little more specifically.")
    warnings: list[str] = []
    if clean["trigger"]["kind"] == "schedule":
        warnings = rv.check_cron(clean["trigger"]["cron"], clean["trigger"]["timezone"])["warnings"]
    return {"ok": True, "draft": {**clean, "trigger_summary": rv.describe_trigger(clean["trigger"])},
            "note": str(out.get("change_note") or "")[:240], "warnings": warnings}


async def draft(prompt: str) -> dict[str, Any]:
    screen = rv.screen_request(prompt)
    if not screen.ok:
        return screen.public()
    return _finish(await _ask_model(f"Write a recipe for this request from the user:\n\n{prompt.strip()}"))


async def revise(current: dict[str, Any], instruction: str) -> dict[str, Any]:
    screen = rv.screen_request(instruction)
    if not screen.ok and screen.kind != "unrealistic":
        return screen.public()
    payload = {
        "title": current["title"], "summary": current["summary"], "instructions": current["body"],
        "trigger": current["trigger"], "telegram": current["delivery"].get("telegram", False),
    }
    prompt = (
        "Here is an existing recipe (JSON). Apply the user's change and return the COMPLETE updated recipe "
        "(all fields, even unchanged ones), with change_note = a short past-tense description of what you "
        "changed. Keep everything the user didn't ask to change. If the change is impossible, ok=false.\n\n"
        f"Recipe:\n{json.dumps(payload, indent=1)}\n\nChange requested by the user:\n{instruction.strip()}"
    )
    out = _finish(await _ask_model(prompt))
    if out.get("ok") and "icon" not in instruction.lower():
        out["draft"]["icon"] = current.get("icon") or out["draft"]["icon"]  # keep the user's icon stable
    return out
