"""Deterministic gates for event-triggered recipes.

An event recipe is checked on a fixed cadence; the check is cheap and has no
LLM in it. Archie only runs when the gate passes, and the gate's findings are
handed to the run as context ("NVDA reports Wed after close").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

EARNINGS_WINDOW_DAYS = 7
TOP_HOLDINGS = 15


def _holdings(db: Session) -> list[dict[str, Any]]:
    from app.services.watch_service import _holdings as top_holdings

    return top_holdings(db, top=TOP_HOLDINGS)


def _next_earnings(symbol: str) -> dict[str, Any] | None:
    # The same lookup the earnings-prep report uses (Finnhub, then the
    # fundamentals calendar), on the market symbol. The gate used to ask Finnhub
    # alone with the raw T212 symbol, so it could say "no holdings report this
    # week" while the report found ASML and TSM.
    from app.services.report_digest import next_earnings

    return next_earnings(symbol)


def _change_pct(symbol: str) -> float | None:
    from app.services.leveraged_market import get_price

    try:
        return float(get_price(symbol).get("change_pct") or 0.0)
    except Exception:  # noqa: BLE001 — a missing quote just means "no signal"
        return None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_event(db: Session, trigger: dict[str, Any], state: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Evaluate an event trigger.

    Returns ``{"fire": bool, "context": str, "reason": str, "state": dict}``;
    ``state`` is the recipe's updated dedupe memory (persist it when firing).
    ``force`` (Run now) skips dedupe but still gathers context.
    """
    event = trigger.get("event")
    state = dict(state or {})
    try:
        holdings = _holdings(db)
    except Exception as exc:  # noqa: BLE001 — no portfolio, no event
        return {"fire": force, "context": "", "reason": f"couldn't read holdings ({exc})", "state": state}

    if event == "earnings_week":
        hits: list[str] = []
        for p in holdings:
            tk = p.get("display_ticker") or p.get("ticker")
            sym = p.get("yfinance_ticker") or p.get("ticker")
            if not tk or not sym:
                continue
            try:
                nxt = _next_earnings(sym)
            except Exception:  # noqa: BLE001
                continue
            if not nxt or nxt.get("days_away") is None:
                continue
            if 0 <= int(nxt["days_away"]) <= EARNINGS_WINDOW_DAYS:
                when = {"bmo": "before open", "amc": "after close", "dmh": "during market hours"}.get(
                    str(nxt.get("hour") or "").lower(), "time TBD")
                hits.append(f"{tk} reports {nxt.get('date')} ({when}), £{float(p.get('value') or 0):,.0f} held")
        if not hits:
            return {"fire": force, "context": "", "reason": "no holdings report in the next 7 days", "state": state}
        key = "earnings:" + "|".join(sorted(hits))
        if not force and state.get("last_earnings_key") == key:
            return {"fire": False, "context": "", "reason": "already prepared for these reports", "state": state}
        state["last_earnings_key"] = key
        return {"fire": True, "context": "Holdings reporting in the next 7 days:\n- " + "\n- ".join(hits), "reason": "", "state": state}

    if event == "big_move":
        threshold = float(trigger.get("threshold_pct") or 5) / 100.0
        today = _today()
        seen = set((state.get("moves") or {}).get(today, []))
        movers: list[tuple[str, float, float]] = []
        for p in holdings:
            tk = p.get("ticker")
            sym = p.get("yfinance_ticker") or tk
            if not tk or not sym:
                continue
            chg = _change_pct(sym)
            if chg is None or abs(chg) < threshold:
                continue
            if tk in seen and not force:
                continue
            movers.append((tk, chg, float(p.get("value") or 0)))
        if not movers:
            return {"fire": force, "context": "", "reason": "no holding has moved past the threshold today", "state": state}
        state["moves"] = {today: sorted(seen | {m[0] for m in movers})}
        lines = [f"{tk} {chg * 100:+.1f}% today (£{v:,.0f} held)" for tk, chg, v in movers]
        return {"fire": True, "context": "Big moves on holdings:\n- " + "\n- ".join(lines), "reason": "", "state": state}

    return {"fire": False, "context": "", "reason": f"unknown event {event!r}", "state": state}
