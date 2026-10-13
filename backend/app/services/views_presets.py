"""Preset views: hand-made viz specs (instant, no model) ranked for this portfolio.

Each preset lives in ``app/views_sdk/presets/<slug>.json``. ``rank_presets``
orders them by what's relevant to the user's actual book (lots of USD →
currency first; idle cash → cash drag) with a one-line why. ``closest_presets``
maps a free-text request to the nearest presets — used when a request can't be
built (declined, no model available) so the user always has a next step.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.views_sdk import preset_specs

logger = logging.getLogger(__name__)

# Display order when nothing is known, plus words that point at each preset.
CATALOG: dict[str, dict[str, Any]] = {
    "today": {"keywords": "today moved move movers daily day change why news headlines up down performance gainers fallers", "base": 50},
    "risk": {"keywords": "risk concentration concentrated diversified diversification sector sectors country countries exposure top weight allocation spread", "base": 40},
    "winners-losers": {"keywords": "winners losers gain gains loss losses profit return returns best worst since bought purchase performance unrealised p&l pnl", "base": 35},
    "income": {"keywords": "dividend dividends income yield paid pays payout passive upcoming ex-dividend monthly", "base": 30},
    "currency": {"keywords": "currency currencies fx usd dollar dollars gbp pound sterling euro forex exchange rate hedge", "base": 25},
    "cash-drag": {"keywords": "cash idle uninvested drag opportunity cost free cash money sitting", "base": 25},
    "isa-allowance": {"keywords": "isa allowance tax year subscription deposit deposits contribution 20000 20k limit wrapper", "base": 20},
    "plays-map": {"keywords": "theme themes play plays thesis basket map grouped group bets", "base": 15},
}


def _money(v: float, currency: str) -> str:
    sym = "$" if currency == "USD" else "£"
    return f"{'-' if v < 0 else ''}{sym}{abs(v):,.0f}"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def list_presets() -> list[dict[str, Any]]:
    specs = preset_specs()
    order = list(CATALOG) + [s for s in specs if s not in CATALOG]
    out = []
    for slug in order:
        spec = specs.get(slug)
        if not spec:
            continue
        out.append({"slug": slug, "title": spec["title"], "description": spec.get("description", ""), "icon": spec.get("icon") or "layout-dashboard"})
    return out


def _signals(db: Session, currency: str) -> dict[str, tuple[float, str]]:
    """slug → (score boost, why). Best effort: any failure just means no boost."""
    from app.services import views_data

    out: dict[str, tuple[float, str]] = {}
    try:
        pf = views_data.resolve(db, "portfolio", {"currency": currency})
    except Exception:  # noqa: BLE001
        logger.info("preset ranking: portfolio unavailable", exc_info=True)
        return out
    positions = pf.get("positions") or []
    acct = pf.get("account") or {}
    total = float(acct.get("total") or 0.0)
    invested = sum(float(p.get("value") or 0.0) for p in positions)
    if not positions or not total:
        return out

    dc = sum(float(p.get("day_change") or 0.0) for p in positions)
    up = sum(1 for p in positions if (p.get("day_change_pct") or 0) > 0)
    down = sum(1 for p in positions if (p.get("day_change_pct") or 0) < 0)
    day_pct = dc / (invested - dc) if invested - dc else 0.0
    movers = sorted((p for p in positions if p.get("day_change_pct") is not None), key=lambda p: -abs(p["day_change_pct"]))
    lead = f"; {movers[0]['ticker']} {movers[0]['day_change_pct'] * 100:+.1f}%" if movers else ""
    out["today"] = (min(30.0, abs(day_pct) * 1500), f"Your holdings are {day_pct * 100:+.1f}% today ({up} up, {down} down){lead}")

    weights = sorted((float(p.get("value") or 0.0) / total for p in positions), reverse=True)
    top5 = sum(weights[:5])
    out["risk"] = (max(0.0, (top5 - 0.3) * 80), f"Your top five holdings are {_pct(top5)} of everything; the largest is {_pct(weights[0])}")

    foreign = sum(float(p.get("value") or 0.0) for p in positions if (p.get("exposure_currency") or "GBP") != "GBP")
    if foreign / total > 0.15:
        out["currency"] = (foreign / total * 60, f"{_pct(foreign / total)} of your portfolio is priced outside sterling — a 5% pound move is about {_money(foreign * 0.0476, currency)}")

    cash = float(acct.get("free_cash") or 0.0)
    if cash > 0:
        ratio = cash / total
        out["cash-drag"] = (min(45.0, ratio * 350), f"{_money(cash, currency)} ({_pct(ratio)}) is sitting in cash — roughly {_money(cash * 0.07, currency)} a year at a 7% return")

    priced = [p for p in positions if p.get("ppl_pct") is not None]
    if len(priced) >= 2:
        best = max(priced, key=lambda p: p["ppl_pct"])
        worst = min(priced, key=lambda p: p["ppl_pct"])
        spread = best["ppl_pct"] - worst["ppl_pct"]
        out["winners-losers"] = (min(25.0, spread * 20), f"{best['ticker']} is {best['ppl_pct'] * 100:+.0f}% since you bought; {worst['ticker']} {worst['ppl_pct'] * 100:+.0f}%")

    if any(a.get("account") == "stocks_isa" for a in pf.get("accounts") or []):
        try:
            from app.services.cashflow_service import get_cashflows

            start, end = views_data.uk_tax_year()
            used = sum(float(e.amount or 0) for e in get_cashflows(db, "stocks_isa") if e.type == "DEPOSIT" and e.occurred_at and start <= e.occurred_at.date() <= end)
            from datetime import date

            days = (end - date.today()).days + 1
            left = max(0.0, views_data.ISA_ALLOWANCE_GBP - used)
            out["isa-allowance"] = ((30.0 if days < 75 and left > 0 else 10.0), f"£{left:,.0f} of this year's £20,000 ISA allowance left, {days} days to go")
        except Exception:  # noqa: BLE001
            pass
    else:
        out["isa-allowance"] = (-15.0, "Track this tax year's ISA allowance")

    try:
        plays = views_data.resolve(db, "plays", {"currency": currency})
        live = [p for p in plays.get("plays") or [] if p.get("status") in ("active", "paused", "draft")]
        owned = sum(float(p.get("exposure") or 0.0) for p in live)
        if live:
            out["plays-map"] = (35.0, f"{len(live)} play{'s' if len(live) != 1 else ''} own {_pct(owned / invested) if invested else '0%'} of your holdings")
    except Exception:  # noqa: BLE001
        pass
    return out


_DEFAULT_WHY = {
    "today": "See today's move by theme and holding, and the headlines behind it",
    "risk": "Check how much rides on a few names, sectors and countries",
    "winners-losers": "What's made and lost you money since you bought",
    "income": "Dividends received, who pays you, and what's coming up",
    "currency": "How exposed you are to the pound and the dollar",
    "cash-drag": "How much cash is idle and what it may cost",
    "isa-allowance": "Track this tax year's ISA allowance",
    "plays-map": "Organise your holdings into theme plays",
}


def rank_presets(db: Session | None, *, currency: str = "GBP", installed: set[str] | None = None) -> list[dict[str, Any]]:
    """Presets ordered by relevance to this portfolio, each with a one-line why."""
    signals = _signals(db, currency) if db is not None else {}
    installed = installed or set()
    ranked = []
    for p in list_presets():
        boost, why = signals.get(p["slug"], (0.0, _DEFAULT_WHY.get(p["slug"], p["description"])))
        score = CATALOG.get(p["slug"], {}).get("base", 10) + boost
        ranked.append(p | {"why": why, "score": round(score, 1), "installed": p["slug"] in installed})
    ranked.sort(key=lambda p: -p["score"])
    for i, p in enumerate(ranked):
        p["suggested"] = i < 3 and not p["installed"]
    return ranked


_WORD_RE = re.compile(r"[a-z0-9&]+")


def closest_presets(prompt: str, n: int = 3) -> list[dict[str, Any]]:
    """Nearest presets to a free-text request (keyword overlap). Always returns
    something: with no overlap, the default top presets."""
    words = set(_WORD_RE.findall((prompt or "").lower()))
    scored = []
    for p in list_presets():
        kw = set(CATALOG.get(p["slug"], {}).get("keywords", "").split()) | set(_WORD_RE.findall(p["title"].lower()))
        hits = len(words & kw)
        scored.append((hits, CATALOG.get(p["slug"], {}).get("base", 0), p))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    best = [dict(p, why=_DEFAULT_WHY.get(p["slug"], p["description"]), match=hits) for hits, _, p in scored[:n]]
    return best


def best_match(prompt: str) -> dict[str, Any] | None:
    """The single preset that clearly matches a request, or None."""
    top = closest_presets(prompt, 1)
    return top[0] if top and top[0].get("match", 0) > 0 else None
