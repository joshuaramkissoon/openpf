"""Today's move — the ONE definition, shared by Home and the Views data source.

Per holding: the change from the previous session's close to the latest close
(yfinance daily candles, the same prices the sandbox and the dashboard use),
applied to the holding's current value in the display currency:

    day_change = value - value / (1 + pct)

The book's move is the sum over holdings; its percentage is relative to the
holdings' value at the previous close. Cash doesn't move. Before a market opens
the "latest session" is the previous trading day, so the result carries the
session date and Home labels it accordingly.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Iterable

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_lock = threading.Lock()
_TTL_S = 60.0


def _round(v: float | None, digits: int = 2) -> float | None:
    return round(v, digits) if v is not None and v == v else None


def session_move(symbol: str) -> dict[str, Any] | None:
    """{pct, session} for one ticker, or None when there's no data. Cached 60s."""
    sym = (symbol or "").strip()
    if not sym:
        return None
    now = time.time()
    with _lock:
        hit = _cache.get(sym)
        if hit and now - hit[0] < _TTL_S:
            return hit[1]
    out: dict[str, Any] | None = None
    try:
        from app.services.leveraged_market import get_price_history

        candles = [c for c in get_price_history(sym, period="5d", interval="1d") if float(c.get("close") or 0) > 0]
        if len(candles) >= 2:
            last, prev = candles[-1], candles[-2]
            out = {"pct": float(last["close"]) / float(prev["close"]) - 1.0, "session": str(last.get("date") or "")[:10] or None}
    except Exception:  # noqa: BLE001 — no data → no move for this holding
        out = None
    with _lock:
        _cache[sym] = (now, out)
    return out


def attach(positions: list[dict[str, Any]], *, symbol_keys: Iterable[str] = ("yf", "yfinance_ticker", "ticker")) -> None:
    """Set ``day_change`` / ``day_change_pct`` / ``day_session`` on each row
    (``value`` must be in the display currency)."""
    keys = tuple(symbol_keys)

    def sym(row: dict[str, Any]) -> str:
        return next((str(row[k]) for k in keys if row.get(k)), "")

    with ThreadPoolExecutor(max_workers=8) as pool:
        moves = list(pool.map(session_move, [sym(p) for p in positions]))
    for row, mv in zip(positions, moves):
        pct = mv["pct"] if mv else None
        value = float(row.get("value") or 0.0)
        row["day_change_pct"] = _round(pct, 5)
        row["day_change"] = _round(value - value / (1 + pct)) if pct is not None and pct > -1 else None
        row["day_session"] = mv["session"] if mv else None


def summarize(positions: list[dict[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    """The book's move from rows that already carry ``day_change``."""
    counted = [p for p in positions if p.get("day_change") is not None]
    change = sum(float(p["day_change"]) for p in counted)
    value = sum(float(p.get("value") or 0.0) for p in counted)
    base = value - change
    sessions = sorted({p["day_session"] for p in counted if p.get("day_session")})
    session = sessions[-1] if sessions else None
    t = (today or date.today()).isoformat()
    return {
        "day_change": round(change, 2) if counted else None,
        "day_change_pct": round(change / base, 5) if counted and base > 0 else None,
        "session": session,
        "is_today": session == t if session else None,
        "holdings_counted": len(counted),
        "holdings_missing": len(positions) - len(counted),
    }


def today_move(db: Any, account_kind: str = "all", currency: str | None = None) -> dict[str, Any]:
    """Home's number: the same per-holding moves the Views data source shows, summed."""
    from app.services.portfolio_service import get_portfolio_snapshot

    snap = get_portfolio_snapshot(db, account_kind=account_kind, display_currency=currency)  # type: ignore[arg-type]
    rows = [{"value": p.get("value"), "yfinance_ticker": p.get("yfinance_ticker"), "ticker": p.get("ticker")}
            for p in snap.get("positions") or []]
    attach(rows, symbol_keys=("yfinance_ticker", "ticker"))
    return {"account_kind": account_kind, "currency": (snap.get("account") or {}).get("currency"), **summarize(rows)}
