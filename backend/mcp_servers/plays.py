"""Plays MCP server for Archie.

Lets Archie list, inspect, draft and update theme bets ("Plays"), and stage the
next tranche as PROPOSED trade intents. Nothing here executes an order — staged
tranches land in the user's decision queue for approval. Backed by the same
SQLite tables as the app (DATABASE_URL in the env).

Tool names for runtime wiring: see ``PLAYS_MCP_TOOLS`` below.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.schemas.plays import KillCriterion, PlayLegIn
from app.services import plays_service as ps

PLAYS_MCP_TOOLS = [
    "mcp__plays__list_plays",
    "mcp__plays__get_play",
    "mcp__plays__draft_play",
    "mcp__plays__update_play",
    "mcp__plays__stage_play_tranche",
]

# ── Logging (file-based — stdout is reserved for the MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("plays-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "plays.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "plays",
    instructions=(
        "Plays are theme bets: a thesis turned into a sized basket with staged entry and kill criteria. "
        "When the user wants to bet on a theme, draft_play it (you choose the legs and kill rules; the app "
        "sizes it against their book). Staging a tranche only creates proposals the user must approve."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _summary(p) -> dict[str, Any]:
    d = ps.serialize_play(p)
    return {k: d[k] for k in ("id", "title", "status", "conviction", "account_kind", "budget_gbp",
                              "budget_pct_of_book", "review")} | {
        "legs": [f"{l['ticker']} {l.get('target_weight', 0):.0%}" for l in d["legs"]],
        "link": f"/plays/{p.id}",
    }


@mcp.tool()
def list_plays(status: str = "all") -> str:
    """List the user's Plays.

    Args:
        status: draft | active | paused | closed | all (default all).
    """
    init_db()
    with SessionLocal() as db:
        return _fmt([_summary(p) for p in ps.list_plays(db, status=status)])


@mcp.tool()
def get_play(play_id: str, include_performance: bool = True) -> str:
    """Full detail for one Play: thesis, legs, sizing, tranches, kill criteria, review,
    and (optionally) live performance + kill-rule status."""
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        out: dict[str, Any] = ps.serialize_play(p)
        if include_performance:
            try:
                perf = ps.play_performance(db, p)
                perf.pop("points", None)
                out["performance"] = perf
            except Exception as exc:  # noqa: BLE001
                out["performance_error"] = str(exc)
        return _fmt(out)


@mcp.tool()
def draft_play(
    title: str,
    thesis: str,
    legs_json: str,
    kill_criteria_json: str,
    what_would_change_my_mind: str,
    conviction: str = "medium",
    horizon_months: int = 12,
    account_kind: str = "stocks_isa",
    budget_gbp: float | None = None,
) -> str:
    """Create a DRAFT Play the user can review in the app. The app sizes it
    deterministically (conviction × basket volatility − existing exposure, capped by
    risk rails and 10% of book) and stages entry in tranches.

    Args:
        title: Short name, e.g. "Nuclear renaissance".
        thesis: Markdown, 2-4 short paragraphs.
        legs_json: JSON list of {ticker (Yahoo symbol, e.g. CCJ or NUCG.L), name, role: core|satellite|hedge,
            target_weight (0-1, normalised), rationale}. 3-7 legs. In a Stocks ISA avoid US-domiciled ETFs.
        kill_criteria_json: JSON list of {text, rule?}. rule types: price_below {ticker, value},
            drawdown_pct {value: %}, weight_above {ticker|null, value: fraction of book}, date_after {value: ISO date}.
            Include at least 2 with a rule.
        what_would_change_my_mind: One or two falsifiable sentences.
        conviction: low | medium | high.
        horizon_months: Expected holding horizon.
        account_kind: invest | stocks_isa.
        budget_gbp: Optional explicit budget; omit to let the app size it.
    """
    logger.info("draft_play title=%s", title)
    try:
        legs = [PlayLegIn.model_validate(l).model_dump() for l in json.loads(legs_json)]
        kills = [KillCriterion.model_validate(k).model_dump(exclude_none=True) for k in json.loads(kill_criteria_json)]
    except Exception as exc:  # noqa: BLE001
        return _fmt({"error": f"invalid legs/kill criteria: {exc}"})
    init_db()
    with SessionLocal() as db:
        p = ps.create_play(db, {
            "title": title, "thesis": thesis, "legs": legs, "kill_criteria": kills,
            "what_would_change_my_mind": what_would_change_my_mind, "conviction": conviction,
            "horizon_months": horizon_months, "account_kind": account_kind, "budget_gbp": budget_gbp,
        })
        return _fmt({"ok": True, **_summary(p), "sizing": (p.sizing or {}).get("rationale"),
                     "note": "Draft saved. Ask the user to review and activate it in Plays."})


@mcp.tool()
def update_play(play_id: str, patch_json: str) -> str:
    """Update a Play. patch_json keys (all optional): title, thesis, status (draft|active|paused|closed),
    conviction, horizon_months, account_kind, budget_gbp, legs, kill_criteria, what_would_change_my_mind,
    resize (bool — recompute sizing after edits)."""
    try:
        patch = json.loads(patch_json)
    except json.JSONDecodeError as exc:
        return _fmt({"error": f"invalid JSON: {exc}"})
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        try:
            p = ps.update_play(db, p, patch)
        except ValueError as exc:
            return _fmt({"error": str(exc)})
        return _fmt({"ok": True, **_summary(p)})


@mcp.tool()
def stage_play_tranche(play_id: str, tranche_index: int | None = None) -> str:
    """Stage the next (or given, 0-based) tranche of a Play as PROPOSED buy intents.
    Nothing executes — the user approves each proposal. Reports legs that would breach risk rails."""
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        try:
            return _fmt(ps.stage_tranche(db, p, tranche_index))
        except ValueError as exc:
            return _fmt({"error": str(exc)})


if __name__ == "__main__":
    mcp.run()
