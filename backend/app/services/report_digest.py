"""Digest mode for "gather fixed data, then write" reports.

Agent mode hands Archie a recipe and lets him call tools for a dozen turns on a
big model — expensive for reports whose inputs never change shape (morning
brief, weekly review, …). Digest mode gathers those inputs deterministically
from the app's own services and makes ONE ``fast_llm`` call to write them up in
the report frame (``## Key numbers`` first, a closing ```summary block).

Recipes that need to investigate keep agent mode (capped turns/budget). A
recipe can opt in or out via ``meta.execution_mode`` ("digest" | "agent").
Digest runs never propose trades or edit anything except, for the watchlist
review, raising the Attention flags the model asks for.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── small helpers ───────────────────────────────────────────────────────
def _safe(fn: Callable[[], Any], what: str) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a missing section is noted, never fatal
        logger.info("digest: %s unavailable: %s", what, exc)
        return {"unavailable": f"{what} unavailable ({type(exc).__name__})"}


def _r(v: Any, d: int = 2) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, d) if f == f else None


def _pmap(fn: Callable[[Any], Any], items: list[Any], workers: int = 6) -> list[Any]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def _memory(*names: str, limit: int = 2500) -> dict[str, str]:
    from app.services.claude_sdk_config import memory_dir

    out: dict[str, str] = {}
    for name in names:
        try:
            p = memory_dir() / name
            if p.is_file():
                out[name] = p.read_text(encoding="utf-8")[:limit]
        except OSError:
            continue
    return out


def _news(ticker: str, since_days: int, limit: int = 3) -> list[dict[str, Any]]:
    from app.services.intel_service import get_company_news

    items = get_company_news(ticker, since_days=since_days, limit=limit) or []
    return [{"headline": i.get("headline"), "source": i.get("source"), "when": i.get("datetime")} for i in items[:limit] if i.get("headline")]


def _next_earnings(ticker: str) -> dict[str, Any] | None:
    from app.services.intel_service import get_earnings

    e = get_earnings(ticker) or {}
    if e.get("next"):
        return e["next"]
    try:
        from app.services.fundamentals_service import get_earnings_calendar

        cal = get_earnings_calendar(ticker) or {}
        nxt = cal.get("next_earnings_date") or cal.get("next")
        if nxt:
            d = date.fromisoformat(str(nxt)[:10])
            return {"date": d.isoformat(), "days_away": (d - date.today()).days}
    except Exception:  # noqa: BLE001
        pass
    return None


def _change(ticker: str, period: str = "5d") -> float | None:
    from app.services.leveraged_market import get_price_history

    candles = [c for c in get_price_history(ticker, period=period, interval="1d") if float(c.get("close") or 0) > 0]
    if len(candles) < 2:
        return None
    return _r(float(candles[-1]["close"]) / float(candles[0]["close"]) - 1, 4)


# ── shared sections ─────────────────────────────────────────────────────
def _book(db: Session) -> dict[str, Any]:
    """Accounts + holdings merged across accounts (display ccy), with today's move."""
    from app.services import day_move
    from app.services.portfolio_service import get_portfolio_snapshot

    snap = get_portfolio_snapshot(db)
    acct = snap.get("account") or {}
    rows: dict[str, dict[str, Any]] = {}
    for p in snap.get("positions") or []:
        t = p.get("display_ticker") or p.get("ticker")
        r = rows.setdefault(t, {"ticker": t, "name": p.get("name"), "yf": p.get("yfinance_ticker") or t, "accounts": [],
                                "value": 0.0, "cost": 0.0, "ppl": 0.0})
        r["accounts"].append(p.get("account_kind"))
        r["value"] += float(p.get("value") or 0)
        r["cost"] += float(p.get("total_cost") or 0)
        r["ppl"] += float(p.get("ppl") or 0)
    holdings = sorted(rows.values(), key=lambda r: -r["value"])
    day_move.attach(holdings, symbol_keys=("yf", "ticker"))
    total = float(acct.get("total") or 0) or 1.0
    for h in holdings:
        h["weight"] = _r(h["value"] / total, 4)
        h["ppl_pct"] = _r(h["ppl"] / h["cost"], 4) if h["cost"] else None
        for k in ("value", "cost", "ppl"):
            h[k] = _r(h[k])
    today = day_move.summarize(holdings)
    return {
        "currency": acct.get("currency") or "GBP",
        "total": _r(acct.get("total")),
        "invested": _r(acct.get("invested")),
        "cash": _r(acct.get("free_cash")),
        "unrealised_pl": _r(acct.get("ppl")),
        "today": today,
        "accounts": [{"account": a.get("account_kind"), "total": _r(a.get("total")), "cash": _r(a.get("free_cash"))}
                     for a in snap.get("accounts") or []],
        "holdings": holdings,
    }


def _slim(h: dict[str, Any]) -> dict[str, Any]:
    return {k: h.get(k) for k in ("ticker", "name", "accounts", "value", "weight", "ppl_pct", "day_change", "day_change_pct")}


def _plays(db: Session) -> list[dict[str, Any]]:
    from app.models.entities import Play
    from app.services.book_service import compute_book

    book = compute_book(db)
    out = []
    for g in book.get("groups") or []:
        if g.get("kind") != "play":
            continue
        play = db.get(Play, g.get("play_id")) if g.get("play_id") else None
        out.append({
            "title": g.get("title"), "status": g.get("status"), "weight": _r(g.get("weight"), 4),
            "target_pct": _r(g.get("target_pct"), 4), "value": _r(g.get("value_gbp")), "ppl_pct": _r(g.get("ppl_pct"), 4),
            "needs": [{"kind": n.get("kind"), "text": n.get("text") or n.get("title")} for n in g.get("needs") or []],
            "holdings": [h.get("ticker") for h in g.get("owned") or []],
            "thesis": (play.thesis or "")[:600] if play else "",
            "kill_rules": [k.get("text") for k in (play.kill_criteria or [])] if play else [],
            "what_would_change_my_mind": (play.what_would_change_my_mind or "")[:300] if play else "",
            "link": f"#/plays/{play.id}" if play else None,
        })
    return out


# ── gatherers per built-in recipe ───────────────────────────────────────
def gather_morning_brief(db: Session, ctx: str) -> dict[str, Any]:
    from app.services.intel_service import get_macro_snapshot, get_market_news
    from app.services.macro_calendar import upcoming_events

    book = _book(db)
    top = book["holdings"][:8]
    news = _pmap(lambda h: _safe(lambda: _news(h["yf"], 1, 3), f"news {h['ticker']}"), top)
    earnings = _pmap(lambda h: _safe(lambda: _next_earnings(h["yf"]), f"earnings {h['ticker']}"), top)
    return {
        "book": {k: v for k, v in book.items() if k != "holdings"},
        "top_holdings": [{**_slim(h), "news_24h": n, "next_earnings": e if isinstance(e, dict) and e.get("days_away", 99) <= 7 else None}
                         for h, n, e in zip(top, news, earnings)],
        "market_news": _safe(lambda: [{"headline": i.get("headline"), "source": i.get("source")} for i in (get_market_news(limit=10) or [])[:10]], "market news"),
        "macro": _safe(lambda: get_macro_snapshot().get("series"), "macro snapshot"),
        "macro_events_7d": _safe(lambda: upcoming_events(within_days=7), "macro calendar"),
        "plays": _safe(lambda: _plays(db), "plays"),
        "memory": _memory("preferences.md", "market_views.md"),
    }


def gather_weekly_review(db: Session, ctx: str) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.entities import TradeIntent
    from app.services.portfolio_service import portfolio_history

    book = _book(db)
    top = book["holdings"][:10]
    week = _pmap(lambda h: _safe(lambda: _change(h["yf"], "5d"), f"week {h['ticker']}"), top)
    bench = {t: _safe(lambda t=t: _change(t, "5d"), f"week {t}") for t in ("SPY", "QQQ")}

    def history() -> dict[str, Any]:
        hist = portfolio_history(db, days=8)
        pts = hist.get("points") or []
        return {"start_value": _r(pts[0]["total"]) if pts else None, "end_value": _r(pts[-1]["total"]) if pts else None,
                "gain_net_of_deposits": _r(pts[-1]["gain"] - pts[0]["gain"]) if len(pts) >= 2 else None,
                "return_pct": hist.get("return_pct")}

    def proposals() -> list[dict[str, Any]]:
        since = datetime.utcnow() - timedelta(days=7)
        rows = db.execute(select(TradeIntent).where(TradeIntent.created_at >= since).order_by(TradeIntent.created_at.desc())).scalars().all()
        return [{"id": r.id, "symbol": r.symbol, "side": r.side, "status": r.status, "notional": _r(r.estimated_notional),
                 "created": r.created_at.date().isoformat(), "rationale": (r.rationale or "")[:160]} for r in rows[:20]]

    return {
        "book": {k: v for k, v in book.items() if k != "holdings"},
        "week": _safe(history, "equity history"),
        "top_holdings": [{**_slim(h), "week_change_pct": w if not isinstance(w, dict) else None} for h, w in zip(top, week)],
        "benchmarks_week_change_pct": bench,
        "concentration_over_20pct": [h["ticker"] for h in book["holdings"] if (h.get("weight") or 0) > 0.2],
        "proposals_last_7d": _safe(proposals, "proposals"),
        "plays": _safe(lambda: _plays(db), "plays"),
        "memory": _memory("preferences.md", "goals.md"),
    }


def gather_watchlist_review(db: Session, ctx: str) -> dict[str, Any]:
    from app.services import watchlist_service as wl

    items = wl.list_items(db, status="watching")

    def one(it: Any) -> dict[str, Any]:
        sym = it.symbol
        return {
            "symbol": sym, "name": it.name, "note": (it.note or "")[:500], "conviction": it.conviction,
            "target_price": it.target_price, "target_direction": it.target_direction,
            "day_change_pct": _safe(lambda: _change(sym, "2d"), f"price {sym}"),
            "week_change_pct": _safe(lambda: _change(sym, "5d"), f"price {sym}"),
            "news_24h": _safe(lambda: _news(sym, 1, 3), f"news {sym}"),
            "next_earnings": _safe(lambda: _next_earnings(sym), f"earnings {sym}"),
        }

    return {"watchlist": _pmap(one, list(items)[:25]), "count": len(items)}


def gather_play_check_in(db: Session, ctx: str) -> dict[str, Any]:
    plays = [p for p in _plays(db) if p.get("status") in (None, "active")]
    book = _book(db)
    by_t = {h["ticker"]: h for h in book["holdings"]}
    for p in plays:
        legs = []
        for t in p["holdings"]:
            h = by_t.get(t) or {"ticker": t, "yf": t}
            wk = _safe(lambda h=h: _change(h.get("yf") or t, "5d"), f"week {t}")
            leg = {**_slim(h), "week_change_pct": wk if not isinstance(wk, dict) else None}
            if isinstance(leg["week_change_pct"], float) and abs(leg["week_change_pct"]) > 0.05:
                leg["news_7d"] = _safe(lambda h=h: _news(h.get("yf") or t, 7, 3), f"news {t}")
            legs.append(leg)
        p["legs"] = legs
    return {"plays": plays, "book_total": book["total"], "currency": book["currency"]}


def gather_earnings_prep(db: Session, ctx: str) -> dict[str, Any]:
    from app.services.fundamentals_service import get_valuation_ratios

    book = _book(db)
    top = book["holdings"][:15]
    nexts = _pmap(lambda h: _safe(lambda: _next_earnings(h["yf"]), f"earnings {h['ticker']}"), top)
    reporting = [(h, e) for h, e in zip(top, nexts) if isinstance(e, dict) and e.get("date") and 0 <= (e.get("days_away") or 99) <= 7]

    def one(pair: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        h, e = pair
        val = _safe(lambda: get_valuation_ratios(h["yf"]), f"valuation {h['ticker']}")
        return {**_slim(h), "earnings": e,
                "valuation": {k: val.get(k) for k in ("trailing_pe", "forward_pe", "peg_ratio", "price_to_sales")} if isinstance(val, dict) and "unavailable" not in val else val,
                "news_14d": _safe(lambda: _news(h["yf"], 14, 4), f"news {h['ticker']}")}

    return {"reporting_within_7d": _pmap(one, reporting), "checked": [h["ticker"] for h in top], "book_total": book["total"]}


def gather_isa_allowance(db: Session, ctx: str) -> dict[str, Any]:
    from app.services.cashflow_service import CONTRIBUTION_TYPES, get_cashflows

    today = date.today()
    start = date(today.year if today >= date(today.year, 4, 6) else today.year - 1, 4, 6)
    deadline = date(start.year + 1, 4, 5)

    def contributions() -> dict[str, Any]:
        rows = [f for f in get_cashflows(db, "stocks_isa") if f.type in CONTRIBUTION_TYPES and f.occurred_at.date() >= start]
        dep = sum(float(f.amount or 0) for f in rows if float(f.amount or 0) > 0)
        return {"deposits_since_6_april": _r(dep), "events": len(rows),
                "note": "From the Trading 212 transactions feed; transfers in from another ISA may not count towards the allowance."}

    book = _book(db)
    isa = [h for h in book["holdings"] if "stocks_isa" in (h.get("accounts") or [])]
    contrib = _safe(contributions, "ISA contributions")
    used = contrib.get("deposits_since_6_april") if isinstance(contrib, dict) else None
    return {
        "tax_year": {"start": start.isoformat(), "deadline": deadline.isoformat(), "days_left": (deadline - today).days},
        "allowance_gbp": 20000, "contributions": contrib,
        "remaining_estimate_gbp": _r(20000 - used) if used is not None else None,
        "cash_by_account": book["accounts"],
        "isa_top_holdings": [_slim(h) for h in isa[:6]],
        "invest_holdings_for_bed_and_isa": [_slim(h) for h in book["holdings"] if h.get("accounts") == ["invest"]][:6],
    }


GATHERERS: dict[str, Callable[[Session, str], dict[str, Any]]] = {
    "morning-brief": gather_morning_brief,
    "weekly-review": gather_weekly_review,
    "watchlist-review": gather_watchlist_review,
    "play-check-in": gather_play_check_in,
    "earnings-prep": gather_earnings_prep,
    "isa-allowance": gather_isa_allowance,
}


def mode_for(recipe: Any) -> str:
    """'digest' for built-ins we can gather for (unless the recipe opts out)."""
    explicit = str(((getattr(recipe, "meta", None) or {}).get("execution_mode")) or "").lower()
    if explicit == "agent":
        return "agent"
    return "digest" if getattr(recipe, "slug", "") in GATHERERS else "agent"


# ── the write-up call ───────────────────────────────────────────────────
_SCHEMA = {
    "type": "object",
    "properties": {
        "report": {"type": "string", "description": "The markdown report, starting with '## Key numbers'. No summary block."},
        "summary": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "points": {"type": "array", "items": {"type": "string"}},
                "action": {"type": ["string", "null"]},
                "signal": {"type": "string", "enum": ["none", "low", "high"]},
            },
            "required": ["headline", "points", "action", "signal"],
        },
        "flags": {
            "type": "array",
            "description": "Watchlist review only: items that are PLAYING OUT or BREAKING relative to their note.",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"}, "title": {"type": "string"}, "detail": {"type": "string"},
                    "consider": {"type": ["string", "null"]}, "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                },
                "required": ["symbol", "title", "detail", "severity"],
            },
        },
    },
    "required": ["report", "summary"],
}


def _system(recipe: Any) -> str:
    from app.services.task_scheduler_service import REPORT_FORMAT

    return (
        f"You are Archie, a UK investor's portfolio copilot, writing the report for the recipe \"{recipe.title}\".\n"
        "All the data has ALREADY been gathered for you and is in the user message as JSON. You cannot call tools: "
        "where the recipe's steps mention tools, read the matching section of the data instead. Use only numbers "
        "from the data; never invent prices, dates or news. If a section says 'unavailable', say so in one short "
        "'Notes' line at the end. Money is in the book's display currency. Percent fields are fractions (0.012 = 1.2%).\n"
        "You can't propose trades in this run: phrase actions as 'consider: …'.\n\n"
        f"{REPORT_FORMAT}\n\n"
        "Return JSON: 'report' is the markdown (WITHOUT the summary block), 'summary' is the summary object, "
        "'flags' only for a watchlist review.\n\n"
        f"=== RECIPE ===\n{(recipe.body or '').strip()}\n=== END RECIPE ==="
    )


def run_digest(db: Session, recipe: Any, *, trigger_context: str = "") -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Gather → one fast_llm call → (output markdown incl. summary block, meta, cost_info)."""
    from app.services import fast_llm
    from app.services.artifact_service import normalise_summary

    started = time.monotonic()
    data = GATHERERS[recipe.slug](db, trigger_context)
    gathered_ms = int((time.monotonic() - started) * 1000)
    payload = {
        "as_of": datetime.now(tz=timezone.utc).strftime("%A %d %B %Y %H:%M UTC"),
        "trigger_context": trigger_context or None,
        "data": data,
    }

    def validate(d: dict[str, Any]) -> dict[str, Any]:
        if not str(d.get("report") or "").strip():
            raise ValueError("empty report")
        return d

    res = fast_llm.complete_json_sync(
        "report_digest", system=_system(recipe), user=json.dumps(payload, default=str, separators=(",", ":")),
        schema=_SCHEMA, validate=validate, max_tokens=3500, usage_id=recipe.slug,
        record_usage=False,  # the scheduler records this run's cost (with the model) like any other run
    )
    report = str(res.data["report"]).strip()
    summary = normalise_summary(res.data.get("summary"))
    output = report + ("\n\n```summary\n" + json.dumps(summary, ensure_ascii=False) + "\n```\n" if summary else "\n")

    meta: dict[str, Any] = {"mode": "digest", "provider": res.provider, "model": res.model, "gather_ms": gathered_ms,
                            "llm_ms": res.duration_ms, "tokens": {"in": res.usage.input_tokens, "out": res.usage.output_tokens}}
    if summary:
        meta["summary"] = summary
    if recipe.slug == "watchlist-review":
        meta["flags_raised"] = _raise_flags(db, res.data.get("flags") or [])
    cost_info = {"total_cost_usd": res.cost_usd, "duration_ms": int((time.monotonic() - started) * 1000), "num_turns": 1,
                 "model": f"{res.provider}:{res.model}"}
    return output, meta, cost_info


def _raise_flags(db: Session, flags: list[dict[str, Any]]) -> int:
    from app.services import watchlist_service as wl

    watched = {i.symbol.upper(): i for i in wl.list_items(db, status="watching")}
    raised = 0
    for f in flags[:10]:
        sym = str(f.get("symbol") or "").upper().strip()
        if sym not in watched or not str(f.get("title") or "").strip():
            continue
        if wl.raise_flag(db, sym, title=str(f["title"]), detail=str(f.get("detail") or ""), consider=f.get("consider") or None,
                         severity=str(f.get("severity") or "info"), source="watchlist_review"):
            raised += 1
    for it in watched.values():
        try:
            wl.touch_reviewed(db, it.id)
        except Exception:  # noqa: BLE001
            pass
    return raised


# ── report summary extraction (agent-mode reports that forgot the block) ──
_SUMMARY_ONLY = {"type": "object", "properties": {"summary": _SCHEMA["properties"]["summary"]}, "required": ["summary"]}


def summarize_report(text: str) -> dict[str, Any] | None:
    """One fast call to pull the summary out of a finished report. Best-effort."""
    from app.services import fast_llm
    from app.services.artifact_service import normalise_summary
    from app.services.task_scheduler_service import SUMMARY_RULES

    if not (text or "").strip():
        return None
    try:
        res = fast_llm.complete_json_sync(
            "report_summary",
            system="Read this portfolio report and return its summary as JSON {\"summary\": {...}}. " + SUMMARY_RULES,
            user=text[:16000], schema=_SUMMARY_ONLY, max_tokens=400, usage_id="report",
        )
        return normalise_summary(res.data.get("summary"))
    except Exception as exc:  # noqa: BLE001
        logger.info("report summary extraction failed: %s", exc)
        return None
