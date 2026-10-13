"""Deterministic trend detector over normalised disclosure rows.

Pure functions: rows (dicts) + the user's context in, scored detections out.
Each detection says how strong the evidence is (``score`` 0–100 →
weak/moderate/strong), which parts of the user's book it touches, and the
honest caveats (reporting delay, sample size, sales being weak evidence).

Scores reflect *evidence quality*, not a recommendation. What reaches the
user's signals is decided separately (:func:`should_alert`).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

CAVEAT_SALES = "Sales are weak evidence: insiders and members sell for taxes, diversification or personal reasons."
CAVEAT_13F = "13F is a quarter-end snapshot filed up to 45 days later — the fund may already have changed the position. Long positions only; hedges and shorts aren't shown."
CAVEAT_CONGRESS = "Congress trades are reported up to 45 days late and only as wide dollar ranges; evidence that they predict returns is mixed."
CAVEAT_FORM4 = "Form 4s are filed within ~2 business days, so this is recent."


@dataclass
class Context:
    today: date
    held: set[str] = field(default_factory=set)
    plays: dict[str, list[str]] = field(default_factory=dict)  # ticker → play titles
    watchlist: set[str] = field(default_factory=set)

    def touches(self, ticker: str | None) -> list[str]:
        if not ticker:
            return []
        t = ticker.upper()
        out = []
        if t in self.held:
            out.append("holding")
        out += [f"play: {p}" for p in self.plays.get(t, [])]
        if t in self.watchlist:
            out.append("watchlist")
        return out

    @property
    def book_tickers(self) -> set[str]:
        return set(self.held) | set(self.plays) | set(self.watchlist)


def strength(score: float) -> str:
    return "strong" if score >= 70 else "moderate" if score >= 45 else "weak"


def _in_window(r: dict[str, Any], today: date, days: int, field_name: str = "trade_date") -> bool:
    d = r.get(field_name)
    return isinstance(d, date) and today - timedelta(days=days) <= d <= today


def _evidence(rows: Iterable[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda r: (r.get("trade_date") or date.min), reverse=True)
    return [
        {
            "source": r.get("source"), "actor": r.get("actor"), "role": r.get("actor_role"), "side": r.get("side"),
            "kind": r.get("kind"), "size": r.get("size_label"),
            "trade_date": r.get("trade_date").isoformat() if r.get("trade_date") else None,
            "disclosed_date": r.get("disclosed_date").isoformat() if r.get("disclosed_date") else None,
            "delay_days": r.get("delay_days"), "url": r.get("url"),
        }
        for r in rows[:limit]
    ]


def _actor_key(r: dict[str, Any]) -> str:
    meta = r.get("meta") or {}
    return str(meta.get("owner_cik") or meta.get("cik") or r.get("actor") or "").lower()


def _median_delay(rows: Iterable[dict[str, Any]]) -> int | None:
    vals = [r["delay_days"] for r in rows if isinstance(r.get("delay_days"), int)]
    return int(statistics.median(vals)) if vals else None


def _total(rows: Iterable[dict[str, Any]]) -> float:
    return sum(float(r.get("size_low") or 0) for r in rows)


def _usd(v: float) -> str:
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K" if v >= 1e3 else f"${v:,.0f}"


def _detection(kind: str, *, ctx: Context, score: float, title: str, detail: str, rows: list[dict[str, Any]],
               caveats: list[str], ticker: str | None = None, sector: str | None = None, window: int = 90,
               actors: int = 0) -> dict[str, Any]:
    dates = [r["trade_date"] for r in rows if r.get("trade_date")]
    first = min(dates) if dates else None
    subject = ticker or sector or "market"
    score = round(max(0.0, min(100.0, score)))
    urls = [r.get("url") for r in sorted(rows, key=lambda r: float(r.get("size_low") or 0), reverse=True) if r.get("url")]
    return {
        "id": f"sm:{kind}:{subject}:{first.isoformat() if first else 'na'}:{actors}",
        "kind": kind,
        "ticker": ticker,
        "sector": sector,
        "title": title,
        "detail": detail,
        "score": score,
        "strength": strength(score),
        "touches": ctx.touches(ticker) if ticker else [],
        "window_days": window,
        "actors": actors,
        "first_trade": first.isoformat() if first else None,
        "last_trade": max(dates).isoformat() if dates else None,
        "median_delay_days": _median_delay(rows),
        "sources": sorted({r.get("source") for r in rows if r.get("source")}),
        "url": urls[0] if urls else None,
        "caveats": caveats,
        "evidence": _evidence(rows),
    }


def _by_ticker(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("ticker"):
            out.setdefault(r["ticker"].upper(), []).append(r)
    return out


def detect(rows: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
    """All detections, strongest first. Only informative rows count."""
    today = ctx.today
    rows = [r for r in rows if r.get("informative")]
    out: list[dict[str, Any]] = []
    covered: set[str] = set()

    # ── 1. Insider cluster buys / CEO-CFO open-market buys (30d) ──
    f4_buys = [r for r in rows if r["source"] == "form4" and r.get("side") == "buy" and r.get("kind") == "open_market" and _in_window(r, today, 30)]
    for tk, grp in _by_ticker(f4_buys).items():
        insiders = {_actor_key(r) for r in grp}
        execs = [r for r in grp if (r.get("meta") or {}).get("is_exec")]
        total = _total(grp)
        size_bonus = 10 if total >= 1e6 else 5 if total >= 250e3 else 0
        n = len(insiders)
        if n >= 2:
            score = 40 + 15 * (n - 2) + (15 if execs else 0) + size_bonus
            caveats = [CAVEAT_FORM4]
            if n == 2:
                caveats.append("Only two insiders — a small sample.")
            if total < 100e3:
                caveats.append(f"Small amounts in total ({_usd(total)}).")
            who = ", ".join(sorted({f"{r['actor']} ({r.get('actor_role')})" for r in grp})[:4])
            out.append(_detection(
                "insider_cluster_buy", ctx=ctx, ticker=tk, window=30, actors=n, rows=grp, caveats=caveats,
                score=score,
                title=f"{n} insiders bought {tk} in 30 days" + (" incl. CEO/CFO" if execs else ""),
                detail=f"Open-market purchases totalling {_usd(total)}: {who}.",
            ))
            covered.add(tk)
        elif execs and _total(execs) >= 50e3:
            e = execs[0]
            score = 45 + (20 if total >= 1e6 else 10 if total >= 250e3 else 0)
            out.append(_detection(
                "insider_exec_buy", ctx=ctx, ticker=tk, window=30, actors=1, rows=grp,
                caveats=[CAVEAT_FORM4, "A single insider — one person's view."], score=score,
                title=f"{tk}: {e.get('actor_role')} bought {_usd(total)} on the open market",
                detail=f"{e['actor']} ({e.get('actor_role')}) bought with their own money on {e['trade_date']}.",
            ))
            covered.add(tk)

    # ── 2. Insider selling on the user's names (context only, weak) ──
    f4_sells = [r for r in rows if r["source"] == "form4" and r.get("side") == "sell" and _in_window(r, today, 30)]
    for tk, grp in _by_ticker(f4_sells).items():
        if tk not in ctx.book_tickers:
            continue
        n = len({_actor_key(r) for r in grp})
        if n < 3:
            continue
        out.append(_detection(
            "insider_selling", ctx=ctx, ticker=tk, window=30, actors=n, rows=grp, caveats=[CAVEAT_SALES, CAVEAT_FORM4],
            score=min(50, 30 + 5 * (n - 3)),
            title=f"{n} insiders sold {tk} in 30 days (not plan sales)",
            detail=f"Discretionary open-market sales totalling {_usd(_total(grp))}. 10b5-1 plan sales are excluded.",
        ))

    # ── 3. Congress: several members buying the same name (90d) ──
    cg_buys = [r for r in rows if r["source"] == "congress" and r.get("side") == "buy" and _in_window(r, today, 90)]
    for tk, grp in _by_ticker(cg_buys).items():
        members = {_actor_key(r) for r in grp}
        n = len(members)
        if n < 2:
            continue
        delay = _median_delay(grp)
        out.append(_detection(
            "congress_cluster", ctx=ctx, ticker=tk, window=90, actors=n, rows=grp, caveats=[CAVEAT_CONGRESS],
            score=min(70, 30 + 10 * (n - 2)),
            title=f"{n} members of Congress bought {tk} in 90 days",
            detail=f"{', '.join(sorted({r['actor'] for r in grp})[:4])}" + (f" — disclosed ~{delay} days after trading." if delay is not None else "."),
        ))

    # ── 4. 13F consensus adds (latest quarter on file) ──
    f13 = [r for r in rows if r["source"] == "13f"]
    if f13:
        latest_period = max((r["trade_date"] for r in f13 if r.get("trade_date")), default=None)
        f13_buys = [r for r in f13 if r.get("side") == "buy" and latest_period and r.get("trade_date") and (latest_period - r["trade_date"]).days <= 31]
        for tk, grp in _by_ticker(f13_buys).items():
            funds = {_actor_key(r) for r in grp}
            n = len(funds)
            if n < 2:
                continue
            new = [r for r in grp if r.get("kind") == "new"]
            out.append(_detection(
                "fund_consensus_add", ctx=ctx, ticker=tk, window=120, actors=n, rows=grp, caveats=[CAVEAT_13F],
                score=min(75, 35 + 10 * (n - 2) + (5 if new else 0)),
                title=f"{n} followed funds added {tk} last quarter",
                detail="; ".join(f"{r['actor']}: {r.get('size_label')}" for r in grp[:4]) + ".",
            ))

    # ── 5. Different groups converging on one name (90d; 13F within ~6 months) ──
    in_win = [r for r in rows if _in_window(r, today, 90) or (r["source"] == "13f" and _in_window(r, today, 200))]
    sellers_by_ticker = {tk: {_actor_key(r) for r in g if r.get("side") == "sell"} for tk, g in _by_ticker(in_win).items()}
    recent = [r for r in in_win if r.get("side") == "buy"]
    for tk, grp in _by_ticker(recent).items():
        groups = {r["actor_type"] for r in grp}
        actors = len({_actor_key(r) for r in grp})
        sellers = len(sellers_by_ticker.get(tk, set()))
        if len(groups) < 2 or actors < 3 or sellers >= actors:
            continue
        names = {"insider": "insiders", "fund": "funds", "congress": "Congress"}
        caveats = []
        if "fund" in groups:
            caveats.append(CAVEAT_13F)
        if "congress" in groups:
            caveats.append(CAVEAT_CONGRESS)
        out.append(_detection(
            "cross_group_buying", ctx=ctx, ticker=tk, window=90, actors=actors, rows=grp, caveats=caveats or [CAVEAT_FORM4],
            score=min(80, (45 if len(groups) == 2 else 65) + 3 * max(0, actors - 3)),
            title=f"{tk}: {' + '.join(names[g] for g in sorted(groups))} buying",
            detail=f"{actors} separate buyers across {len(groups)} disclosure types in the last 90 days"
                   + (f"; {sellers} sold in the same window." if sellers else "."),
        ))

    # ── 6. Sector flows ──
    sec_rows = [r for r in rows if r.get("sector") and _in_window(r, today, 90) and r["source"] != "13f"]
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for r in sec_rows:
        by_sector.setdefault(r["sector"], []).append(r)
    for sector, grp in by_sector.items():
        buys = {(_actor_key(r), r.get("ticker")) for r in grp if r.get("side") == "buy"}
        sells = {(_actor_key(r), r.get("ticker")) for r in grp if r.get("side") == "sell"}
        total = len(buys) + len(sells)
        cg_members = {_actor_key(r) for r in grp if r["source"] == "congress" and r.get("side") == "buy"}
        if len(buys) >= 6 and len(buys) / total >= 0.7:
            names = sorted({r["ticker"] for r in grp if r.get("side") == "buy" and r.get("ticker")})
            out.append(_detection(
                "sector_inflow", ctx=ctx, sector=sector, window=90, actors=len(buys),
                rows=[r for r in grp if r.get("side") == "buy"], caveats=[CAVEAT_CONGRESS if cg_members else CAVEAT_FORM4],
                score=min(45, 20 + len(buys)),
                title=f"Disclosed buying is concentrated in {sector}",
                detail=f"{len(buys)} buys vs {len(sells)} sells in 90 days across {', '.join(names[:6])}.",
            ))
        elif len(sells) >= 8 and len(sells) / total >= 0.8:
            out.append(_detection(
                "sector_outflow", ctx=ctx, sector=sector, window=90, actors=len(sells),
                rows=[r for r in grp if r.get("side") == "sell"], caveats=[CAVEAT_SALES],
                score=min(35, 15 + len(sells) // 2),
                title=f"Disclosed selling is concentrated in {sector}",
                detail=f"{len(sells)} sells vs {len(buys)} buys in 90 days.",
            ))
        if len(cg_members) >= 3:
            out.append(_detection(
                "congress_sector", ctx=ctx, sector=sector, window=90, actors=len(cg_members),
                rows=[r for r in grp if r["source"] == "congress" and r.get("side") == "buy"], caveats=[CAVEAT_CONGRESS],
                score=min(55, 30 + 5 * (len(cg_members) - 3)),
                title=f"{len(cg_members)} members of Congress bought {sector} names",
                detail="Across " + ", ".join(sorted({r['ticker'] for r in grp if r['source'] == 'congress' and r.get('side') == 'buy' and r.get('ticker')})[:6]) + ".",
            ))

    # ── 7. Anything else touching the user's names (context for the sheet / Archie) ──
    flagged = {d["ticker"] for d in out if d.get("ticker")}
    for tk, grp in _by_ticker([r for r in rows if _in_window(r, today, 90) or (r["source"] == "13f" and _in_window(r, today, 200))]).items():
        if tk in flagged or tk not in ctx.book_tickers:
            continue
        buys = [r for r in grp if r.get("side") == "buy"]
        sells = [r for r in grp if r.get("side") == "sell"]
        bits = []
        for src, label in (("form4", "insiders"), ("13f", "funds"), ("congress", "Congress members")):
            b = len({_actor_key(r) for r in buys if r["source"] == src})
            s = len({_actor_key(r) for r in sells if r["source"] == src})
            if b or s:
                bits.append(f"{label}: {b} buying, {s} selling")
        caveats = []
        if any(r["source"] == "congress" for r in grp):
            caveats.append(CAVEAT_CONGRESS)
        if any(r["source"] == "13f" for r in grp):
            caveats.append(CAVEAT_13F)
        if sells and not buys:
            caveats.append(CAVEAT_SALES)
        out.append(_detection(
            "book_activity", ctx=ctx, ticker=tk, window=90, actors=len({_actor_key(r) for r in grp}), rows=grp,
            caveats=caveats or [CAVEAT_FORM4], score=min(40, 15 + 8 * len(buys)),
            title=f"Disclosed activity in {tk}", detail="; ".join(bits) + ".",
        ))

    out.sort(key=lambda d: (-d["score"], -len(d["touches"])))
    return out


def should_alert(d: dict[str, Any]) -> bool:
    """High signal only: it touches the user's book (holdings/plays/watchlist)
    with at least moderate evidence, or it is unusually strong anywhere.
    Sector-level and 'book_activity' summaries never become alerts."""
    if d["kind"] in ("book_activity", "sector_outflow"):
        return False
    if d["touches"] and d["score"] >= 45:
        return True
    return d["score"] >= 75


def alert_severity(d: dict[str, Any]) -> str:
    return "warning" if "holding" in d["touches"] and d["score"] >= 70 else "info"


# ── Ticker summary (stock sheet line) ───────────────────────────────────────

def ticker_summary(ticker: str, rows: list[dict[str, Any]], fund_snapshots: list[dict[str, Any]], today: date) -> dict[str, Any]:
    t = ticker.upper()
    rows = [r for r in rows if (r.get("ticker") or "").upper() == t]
    inf = [r for r in rows if r.get("informative")]
    ins_buys = {_actor_key(r) for r in inf if r["source"] == "form4" and r["side"] == "buy" and _in_window(r, today, 30)}
    ins_sells = {_actor_key(r) for r in inf if r["source"] == "form4" and r["side"] == "sell" and _in_window(r, today, 30)}
    plan_sales = sum(1 for r in rows if r["source"] == "form4" and r.get("kind") == "plan_sale" and _in_window(r, today, 30))
    cg = [r for r in inf if r["source"] == "congress" and _in_window(r, today, 90)]
    cg_buys = [r for r in cg if r["side"] == "buy"]
    cg_sells = [r for r in cg if r["side"] == "sell"]
    holders = []
    for snap in fund_snapshots:
        h = (snap.get("holdings") or {}).get(t)
        if h:
            holders.append({"fund": snap.get("name"), "weight": h.get("weight"), "value": h.get("value"), "period": snap.get("period")})
    changes = [r for r in inf if r["source"] == "13f" and _in_window(r, today, 200)]

    parts: list[str] = []
    if ins_buys:
        parts.append(f"{len(ins_buys)} insider{'s' if len(ins_buys) != 1 else ''} bought in 30 days")
    if ins_sells:
        parts.append(f"{len(ins_sells)} insider{'s' if len(ins_sells) != 1 else ''} sold (excl. plan sales)" if not ins_buys
                     else f"{len(ins_sells)} sold (excl. plan sales)")
    if holders:
        parts.append(f"held by {len(holders)} fund{'s' if len(holders) != 1 else ''} you follow")
    if cg:
        delay = _median_delay(cg)
        bits = []
        if cg_buys:
            bits.append(f"{len(cg_buys)} buy{'s' if len(cg_buys) != 1 else ''}")
        if cg_sells:
            bits.append(f"{len(cg_sells)} sell{'s' if len(cg_sells) != 1 else ''}")
        parts.append(f"Congress: {', '.join(bits)}" + (f", disclosed {delay} days late" if delay is not None else ""))
    return {
        "ticker": t,
        "line": " · ".join(parts),
        "insiders": {"buyers_30d": len(ins_buys), "sellers_30d": len(ins_sells), "plan_sales_30d": plan_sales},
        "funds": {"holders": holders, "changes": _evidence(changes)},
        "congress": {"buys_90d": len(cg_buys), "sells_90d": len(cg_sells), "median_delay_days": _median_delay(cg)},
        "recent": _evidence(sorted(inf, key=lambda r: r.get("disclosed_date") or date.min, reverse=True), limit=12),
    }
