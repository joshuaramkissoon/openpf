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
from app.services import book_service as bs
from app.services import plays_service as ps

PLAYS_MCP_TOOLS = [
    "mcp__plays__list_plays",
    "mcp__plays__get_play",
    "mcp__plays__draft_play",
    "mcp__plays__update_play",
    "mcp__plays__stage_play_tranche",
    "mcp__plays__get_book_by_play",
    "mcp__plays__assign_owner",
    "mcp__plays__add_related",
    "mcp__plays__remove_related",
    "mcp__plays__add_bench",
    "mcp__plays__remove_bench",
    "mcp__plays__suggest_plays",
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
        "sizes it against their book). Staging a tranche only creates proposals the user must approve. "
        "Every holding has one owner — a play, Core or Unsorted (get_book_by_play); plays also list related "
        "holdings owned elsewhere and a bench of not-held ideas. suggest_plays groups Unsorted holdings "
        "into suggestions the user accepts in the app; assign_owner only when the user agrees."
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
            try:
                r = bs.play_rings(db, p)
                out["rings"] = {
                    "owned_weight": r["weight"], "effective_weight": r["effective_weight"],
                    "owned": [_brief_holding(h) for h in r["owned"]],
                    "related": [{"ticker": x["ticker"], "owner": x["owner"]["title"], "why": x["why"]} for x in r["related"]],
                    "bench": [{"ticker": b["ticker"], "in_plan": b["in_plan"], "why": b["why"]} for b in r["bench"]],
                    "needs": [n["text"] for n in r.get("needs") or []],
                }
            except Exception as exc:  # noqa: BLE001
                out["rings_error"] = str(exc)
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
    if len(legs) < 2:
        return _fmt({"error": "a play needs at least 2 legs"})
    init_db()
    with SessionLocal() as db:
        try:
            p = ps.create_play(db, {
                "title": title, "thesis": thesis, "legs": legs, "kill_criteria": kills,
                "what_would_change_my_mind": what_would_change_my_mind, "conviction": conviction,
                "horizon_months": horizon_months, "account_kind": account_kind, "budget_gbp": budget_gbp,
            })
        except ValueError as exc:
            return _fmt({"error": str(exc)})
        missing = ps.unknown_tickers(db, p.legs or [])
        if missing:
            ps.delete_play(db, p)
            return _fmt({"error": f"Not on Trading 212: {', '.join(missing)}. Use search_instruments to find listed "
                                  "alternatives (London-listed UCITS ETFs end in .L), then draft again."})
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


# ── The book by play: ownership, rings, suggestions ──────────────────────────

def _brief_holding(h: dict[str, Any]) -> dict[str, Any]:
    return {"ticker": h["ticker"], "account": h["account_kind"], "value_gbp": round(h["value_gbp"]),
            "weight": round(h["weight"], 4)}


@mcp.tool()
def get_book_by_play() -> str:
    """The whole book grouped by owner: each play, Core and Unsorted with value, weight of book,
    P&L, needs-you cues, effective exposure (incl. related holdings) and per-theme concentration.
    Every holding has exactly one owner, so the groups (plus cash) sum to 100%."""
    init_db()
    with SessionLocal() as db:
        try:
            book = bs.compute_book(db)
        except Exception as exc:  # noqa: BLE001
            return _fmt({"error": f"portfolio unavailable: {exc}"})
        groups = []
        for g in book["groups"]:
            groups.append({
                "kind": g["kind"], "play_id": g.get("play_id"), "title": g["title"], "status": g.get("status"),
                "value_gbp": round(g["value_gbp"]), "weight": round(g["weight"], 4),
                "ppl_gbp": round(g["ppl_gbp"]), "effective_weight": g.get("effective_weight"),
                "target_pct": g.get("target_pct"), "needs": [n["text"] for n in g.get("needs") or []],
                "owned": [_brief_holding(h) for h in g["owned"]],
                "related": [{"ticker": r["ticker"], "owner": r["owner"]["title"], "why": r["why"]} for r in g.get("related") or []],
                "bench": [{"ticker": b["ticker"], "in_plan": b["in_plan"], "why": b["why"]} for b in g.get("bench") or []],
            })
        return _fmt({"total_gbp": round(book["total_gbp"]), "cash_gbp": round(book["cash_gbp"]), "groups": groups,
                     "theme_risk": [r["text"] for r in book["theme_risk"]]})


@mcp.tool()
def assign_owner(ticker: str, owner: str, account_kind: str = "") -> str:
    """Set which play owns a holding. Owner: a play id, "core" (long-term / index / no thesis) or
    "unsorted". Moves the holding in every account that holds it unless account_kind is given.
    Changes bookkeeping only — never trades. Only do this when the user asked or agreed."""
    init_db()
    with SessionLocal() as db:
        try:
            rows = bs.assign_owner(db, ticker, owner, account_kind=account_kind or None, source="archie")
        except ValueError as exc:
            return _fmt({"error": str(exc)})
        return _fmt({"ok": True, "moved": rows})


@mcp.tool()
def add_related(play_id: str, ticker: str, why: str) -> str:
    """Mark a holding owned elsewhere (another play or Core) as related to this play's theme, with a
    one-line why (e.g. MSFT owned by Core, related to "AI needs power"). Related holdings are shown on
    the play and count toward its effective exposure, but never toward its size."""
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        try:
            p = bs.add_related(db, p, ticker, why)
        except ValueError as exc:
            return _fmt({"error": str(exc)})
        return _fmt({"ok": True, "related": ps.serialize_play(p)["related"]})


@mcp.tool()
def remove_related(play_id: str, ticker: str) -> str:
    """Stop showing a holding as related to a play (hides basket legs owned elsewhere, too)."""
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        p = bs.remove_related(db, p, ticker)
        return _fmt({"ok": True, "related": ps.serialize_play(p)["related"]})


@mcp.tool()
def add_bench(play_id: str, ticker: str, why: str, name: str = "") -> str:
    """Add a not-held candidate to a play's bench (an idea for a later tranche), with a one-line why.
    The ticker must be on Trading 212 and not already held."""
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        try:
            p = bs.add_bench(db, p, ticker, why, name)
        except ValueError as exc:
            return _fmt({"error": str(exc)})
        return _fmt({"ok": True, "bench": p.bench})


@mcp.tool()
def remove_bench(play_id: str, ticker: str) -> str:
    """Remove an idea from a play's bench."""
    init_db()
    with SessionLocal() as db:
        p = ps.get_play(db, play_id)
        if not p:
            return _fmt({"error": "not found"})
        p = bs.remove_bench(db, p, ticker)
        return _fmt({"ok": True, "bench": p.bench})


@mcp.tool()
def suggest_plays(suggestions_json: str = "") -> str:
    """Group the user's Unsorted holdings into suggested plays or Core. Two steps:

    1. Call with no arguments → the Unsorted holdings, existing plays, and a rules-based first-pass
       grouping for you to refine.
    2. Call with suggestions_json → saves your grouping for the user to accept / edit / dismiss in the
       app (Home → "Sort with Archie"). Nothing is applied until they accept.

    suggestions_json: JSON list of {kind: "play" | "core" | "existing", title, tickers: [...], why: one
    sentence, thesis?: 1-2 sentences, play_id?: for kind "existing" (join an existing play)}.
    Only Unsorted tickers are kept; each ticker at most once.
    """
    init_db()
    with SessionLocal() as db:
        try:
            if not suggestions_json.strip():
                unsorted, _ = bs.unsorted_holdings(db)
                return _fmt({
                    "unsorted": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in h.items()} for h in unsorted],
                    "existing_plays": [{"play_id": p.id, "title": p.title, "status": p.status,
                                        "legs": [l.get("ticker") for l in p.legs or []]} for p in bs.owning_plays(db)],
                    "first_pass": bs.deterministic_suggestions(unsorted),
                    "next": "Refine this and call suggest_plays(suggestions_json=...) to show it to the user.",
                })
            items = json.loads(suggestions_json)
            if not isinstance(items, list):
                return _fmt({"error": "suggestions_json must be a JSON list"})
            out = bs.save_agent_suggestions(db, items)
        except json.JSONDecodeError as exc:
            return _fmt({"error": f"invalid JSON: {exc}"})
        except ValueError as exc:
            return _fmt({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return _fmt({"error": f"portfolio unavailable: {exc}"})
        return _fmt({"ok": True, "saved": [{"id": i["id"], "kind": i["kind"], "title": i["title"], "tickers": i["tickers"]}
                                           for i in out["items"]],
                     "note": "Saved as suggestions. Ask the user to review them on Home (Sort with Archie)."})


if __name__ == "__main__":
    mcp.run()
