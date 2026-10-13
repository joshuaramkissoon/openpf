"""Whitelisted, read-only data sources for Views.

This is the ONLY data a view can reach: the sandboxed iframe asks the parent
page, the parent calls ``POST /api/views/data``, and that resolves here. The
builder agent's ``sample_data`` tool uses the same resolver, so views are always
built against the exact shapes they will receive at runtime.

Every source returns plain JSON with stable, documented field names (see
``app/views_sdk/VIEW_SDK.md``) — curated from the internal services so views
don't couple to internal schemas. Nothing here writes, trades or exposes keys.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ACCOUNTS = ("all", "invest", "stocks_isa")
HISTORY_RANGES = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "2y": 731, "5y": 1827, "all": 8000}
PRICE_PERIODS = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max"}
PRICE_INTERVALS = {"1d", "1wk", "1mo"}
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=_]{1,24}$")


class DataSourceError(ValueError):
    """Bad source or params (maps to HTTP 400)."""


# ── tiny TTL cache (keeps views snappy and the broker's rate limits happy) ──
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ── param helpers ──
def _account(params: dict[str, Any]) -> str:
    acct = str(params.get("account") or "all").lower()
    if acct not in ACCOUNTS:
        raise DataSourceError(f"account must be one of {', '.join(ACCOUNTS)}")
    return acct


def _currency(params: dict[str, Any]) -> str:
    cur = str(params.get("currency") or "GBP").upper()
    return cur if cur in ("GBP", "USD") else "GBP"


def _ticker(value: Any) -> str:
    t = str(value or "").strip()
    if not _TICKER_RE.match(t):
        raise DataSourceError("ticker is required (e.g. 'AAPL', 'VUSA.L')")
    return t


def _r(value: Any, digits: int = 2) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if f == f else None


def _strip_code(code: str) -> str:
    """``AAPL_US_EQ`` → ``AAPL``; ``VUSAl_EQ`` → ``VUSA``."""
    base = (code or "").split("_")[0]
    return re.sub(r"[a-z]+$", "", base) or code


# ── sources ──
def _portfolio(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.portfolio_service import get_portfolio_snapshot

    account, currency = _account(params), _currency(params)
    combine = params.get("combine", True) is not False
    snap = _cached(("portfolio", account, currency), 30, lambda: get_portfolio_snapshot(db, account_kind=account, display_currency=currency))
    acct = snap.get("account", {})
    total = float(acct.get("total") or 0.0)

    rows: dict[str, dict[str, Any]] = {}
    for p in snap.get("positions", []):
        ticker = p.get("display_ticker") or _strip_code(p.get("ticker") or "")
        key = ticker if combine else f"{ticker}:{p.get('account_kind')}"
        value = float(p.get("value") or 0.0)
        cost = float(p.get("total_cost") or 0.0)
        ppl = float(p.get("ppl") or 0.0)
        row = rows.get(key)
        if row is None:
            rows[key] = {
                "ticker": ticker,
                "name": p.get("name") or ticker,
                "account": p.get("account_kind") if not combine else None,
                "accounts": [p.get("account_kind")],
                "yf": p.get("yfinance_ticker") or ticker,
                "instrument_currency": p.get("instrument_currency"),
                # Listing currency with pence folded into pounds (GBX → GBP).
                "exposure_currency": {"GBX": "GBP", "GBP_PENCE": "GBP", "GBp": "GBP"}.get(str(p.get("instrument_currency") or ""), p.get("instrument_currency") or "GBP"),
                "quantity": float(p.get("quantity") or 0.0),
                "price": _r(p.get("current_price"), 4),
                "avg_price": _r(p.get("average_price"), 4),
                "value": value,
                "cost": cost,
                "ppl": ppl,
                "momentum_63d": _r(p.get("momentum_63d"), 4),
                "rsi_14": _r(p.get("rsi_14"), 1),
                "volatility_30d": _r(p.get("volatility_30d"), 4),
                "risk_flag": p.get("risk_flag"),
            }
        else:
            row["quantity"] += float(p.get("quantity") or 0.0)
            row["value"] += value
            row["cost"] += cost
            row["ppl"] += ppl
            row["accounts"].append(p.get("account_kind"))

    positions = []
    for row in rows.values():
        row["ppl_pct"] = _r(row["ppl"] / row["cost"], 4) if row["cost"] else None
        row["weight"] = _r(row["value"] / total, 5) if total else None
        for k in ("value", "cost", "ppl"):
            row[k] = _r(row[k])
        row["quantity"] = _r(row["quantity"], 6)
        positions.append(row)
    positions.sort(key=lambda r: r["value"] or 0, reverse=True)
    _attach_day_change(positions)

    def _acct(a: dict[str, Any]) -> dict[str, Any]:
        return {
            "account": a.get("account_kind"),
            "total": _r(a.get("total")),
            "invested": _r(a.get("invested")),
            "free_cash": _r(a.get("free_cash")),
            "ppl": _r(a.get("ppl")),
        }

    m = snap.get("metrics", {})
    return {
        "currency": acct.get("currency") or currency,
        "as_of": str(acct.get("fetched_at") or ""),
        "account": _acct(acct),
        "accounts": [_acct(a) for a in snap.get("accounts", [])],
        "positions": positions,
        "metrics": {
            "total_value": _r(m.get("total_value")),
            "free_cash": _r(m.get("free_cash")),
            "cash_ratio": _r(m.get("cash_ratio"), 4),
            "concentration_hhi": _r(m.get("concentration_hhi"), 4),
            "top_position_weight": _r(m.get("top_position_weight"), 4),
            "estimated_beta": _r(m.get("estimated_beta"), 3),
            "estimated_volatility": _r(m.get("estimated_volatility"), 4),
        },
    }


def _history(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.portfolio_service import portfolio_history

    account, currency = _account(params), _currency(params)
    rng = str(params.get("range") or "1y").lower()
    if rng == "ytd":
        days = max(1, (date.today() - date(date.today().year, 1, 1)).days + 1)
    elif rng in HISTORY_RANGES:
        days = HISTORY_RANGES[rng]
    else:
        raise DataSourceError(f"range must be one of ytd, {', '.join(HISTORY_RANGES)}")
    raw = _cached(("history", account, currency, days), 120, lambda: portfolio_history(db, account_kind=account, display_currency=currency, days=days))
    return {
        "currency": raw.get("currency") or currency,
        "range": rng,
        "points": [
            {"date": p.get("date"), "total": _r(p.get("total")), "invested": _r(p.get("invested")), "free_cash": _r(p.get("free_cash")), "gain": _r(p.get("gain"))}
            for p in raw.get("points", [])
        ],
        "return_pct": _r(raw.get("return_pct"), 4),
        "net_contributed": _r(raw.get("net_contributed")),
        "start_value": _r(raw.get("start_value")),
        "end_value": _r(raw.get("end_value")),
    }


def _orders(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.api.orders import order_history

    account = _account(params)
    ticker = params.get("ticker")
    ticker = _ticker(ticker) if ticker else None
    limit = max(1, min(int(params.get("limit") or 50), 50))

    def fetch() -> dict[str, Any]:
        resp = order_history(account=account, ticker=ticker, limit=limit, cursor=None, db=db)
        return resp.model_dump()

    raw = _cached(("orders", account, ticker, limit), 300, fetch)
    orders = []
    for o in raw.get("orders", []):
        orders.append({
            "id": o.get("order_id"),
            "account": o.get("account_kind"),
            "ticker": _strip_code(o.get("ticker") or ""),
            "name": o.get("name"),
            "side": o.get("side"),
            "type": o.get("type"),
            "status": o.get("status"),
            "quantity": _r(o.get("filled_quantity") or o.get("quantity"), 6),
            "fill_price": _r(o.get("fill_price"), 4),
            "value": _r(o.get("value")),
            "created_at": o.get("created_at"),
        })
    return {"orders": orders, "errors": [e.get("message") for e in raw.get("errors", [])]}


def _dividends(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.config_store import ConfigStore
    from app.services.t212_client import build_t212_client

    account = _account(params)
    kinds = ["invest", "stocks_isa"] if account == "all" else [account]

    def fetch(kind: str) -> list[dict[str, Any]]:
        client = build_t212_client(ConfigStore(db), account_kind=kind)
        return list(client.get_dividends(max_pages=40, page_sleep=1.0))

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for kind in kinds:
        try:
            raw = _cached(("dividends", kind), 1800, lambda kind=kind: fetch(kind))
        except Exception as exc:  # noqa: BLE001 — report per account, keep the rest
            errors.append(f"{kind}: {exc}")
            continue
        for d in raw:
            items.append({
                "account": kind,
                "ticker": _strip_code(str(d.get("ticker") or "")),
                "amount": _r(d.get("amount")),
                "currency": d.get("currency") or "GBP",
                "paid_on": str(d.get("paidOn") or "")[:10],
                "quantity": _r(d.get("quantity"), 6),
                "type": d.get("type"),
            })
    items.sort(key=lambda d: d["paid_on"], reverse=True)
    monthly: dict[str, float] = defaultdict(float)
    by_ticker: dict[str, float] = defaultdict(float)
    for d in items:
        monthly[d["paid_on"][:7]] += d["amount"] or 0.0
        by_ticker[d["ticker"]] += d["amount"] or 0.0
    return {
        "currency": items[0]["currency"] if items else "GBP",
        "items": items,
        "monthly": [{"month": k, "amount": _r(v)} for k, v in sorted(monthly.items())],
        "by_ticker": sorted(({"ticker": k, "amount": _r(v)} for k, v in by_ticker.items()), key=lambda r: -(r["amount"] or 0)),
        "total": _r(sum(d["amount"] or 0.0 for d in items)),
        "errors": errors,
    }


def _prices(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.api.charts import get_candles

    ticker = _ticker(params.get("ticker"))
    period = str(params.get("range") or params.get("period") or "6mo")
    interval = str(params.get("interval") or "1d")
    if period not in PRICE_PERIODS:
        raise DataSourceError(f"range must be one of {', '.join(sorted(PRICE_PERIODS))}")
    if interval not in PRICE_INTERVALS:
        raise DataSourceError(f"interval must be one of {', '.join(sorted(PRICE_INTERVALS))}")
    try:
        resp = get_candles(ticker=ticker, period=period, interval=interval, indicators="")
    except HTTPException as exc:
        raise DataSourceError(str(exc.detail)) from exc
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    return {
        "ticker": ticker,
        "range": period,
        "interval": interval,
        "candles": [
            {"date": str(c.get("time"))[:10], "open": _r(c.get("open"), 4), "high": _r(c.get("high"), 4), "low": _r(c.get("low"), 4), "close": _r(c.get("close"), 4), "volume": c.get("volume")}
            for c in data.get("candles", [])
        ],
    }


def _attach_day_change(positions: list[dict[str, Any]]) -> None:
    """Today's move per holding, so views never have to derive money from quotes
    (quote prices are in the instrument's currency, not the display currency)."""
    from concurrent.futures import ThreadPoolExecutor

    from app.services.leveraged_market import get_price

    def _pct(sym: str) -> float | None:
        try:
            q = _cached(("quote", sym), 60, lambda sym=sym: get_price(sym) or {})
            v = q.get("change_pct")
            return float(v) if v is not None else None
        except Exception:  # noqa: BLE001
            return None

    syms = [str(p.get("yf") or p.get("ticker") or "") for p in positions]
    with ThreadPoolExecutor(max_workers=8) as pool:
        pcts = list(pool.map(_pct, syms))
    for row, pct in zip(positions, pcts):
        row["day_change_pct"] = _r(pct, 5) if pct is not None else None
        value = row.get("value") or 0.0
        row["day_change"] = _r(value - value / (1 + pct)) if pct is not None and pct > -1 else None


def _quotes(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.leveraged_market import get_price

    tickers = params.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [t for t in tickers.split(",") if t.strip()]
    if not isinstance(tickers, list) or not tickers:
        raise DataSourceError("tickers must be a non-empty list")
    if len(tickers) > 30:
        raise DataSourceError("at most 30 tickers per call")
    out = []
    for t in tickers:
        sym = _ticker(t)
        try:
            q = _cached(("quote", sym), 60, lambda sym=sym: get_price(sym) or {})
        except Exception:  # noqa: BLE001
            q = {}
        out.append({"ticker": sym, "price": _r(q.get("price"), 4), "change_pct": _r(q.get("change_pct"), 5), "currency": q.get("currency")})
    return {"quotes": out}


def _fundamentals(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.fundamentals_service import get_fundamentals_snapshot

    ticker = _ticker(params.get("ticker"))
    data = {k: v for k, v in get_fundamentals_snapshot(ticker).items() if not isinstance(v, (dict, list))}
    # yfinance reports dividendYield in percent (0.73 = 0.73%); views use fractions everywhere.
    if data.get("dividend_yield") is not None:
        data["dividend_yield"] = _r(float(data["dividend_yield"]) / 100.0, 5)
    return data


def _watchlist(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services import watchlist_service as wl

    status = str(params.get("status") or "watching")
    if status not in ("watching", "acted", "archived", "all"):
        raise DataSourceError("status must be watching | acted | archived | all")
    rows = wl.list_items(db, status=None if status == "all" else status)
    items = []
    for row in rows:
        s = wl.serialize(row)
        items.append({k: s.get(k) for k in ("id", "symbol", "name", "note", "conviction", "status", "target_price", "target_direction", "created_at")})
    return {"items": items}


def _proposals(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.execution_service import list_intents

    limit = max(1, min(int(params.get("limit") or 50), 200))
    items = []
    for r in list_intents(db, limit=limit):
        meta = r.meta if isinstance(r.meta, dict) else {}
        items.append({
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": r.status,
            "ticker": r.symbol,
            "side": r.side,
            "quantity": r.quantity,
            "notional": _r(r.estimated_notional),
            "confidence": _r(r.confidence, 3),
            "rationale": (r.rationale or "")[:600],
            "account": meta.get("account_kind"),
            "executed_at": r.executed_at.isoformat() if r.executed_at else None,
        })
    return {"proposals": items}


def play_rings(play: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Owned / related / bench groups for one serialized play, valued from the live book.

    ADAPTER — the one place that knows the play "rings" shape. When plays carry
    explicit ring fields (``owned`` / ``related`` / ``bench`` lists of tickers or
    ``{ticker}`` dicts, top-level or under ``rings``), those win. Otherwise it
    falls back to today's shape: legs held in the book are *owned*,
    ``meta.overlap`` names held outside the legs are *related*, and legs not yet
    bought are the *bench*. Values always come from ``positions`` (display
    currency), never from price x quantity, so views stay inside the money rule.
    """
    by_ticker = {str(p.get("ticker") or "").upper(): p for p in positions}

    def keys(item: Any) -> set[str]:
        if isinstance(item, str):
            raw = [item]
        elif isinstance(item, dict):
            raw = [item.get("ticker"), item.get("instrument_code"), item.get("symbol")]
        else:
            raw = []
        out: set[str] = set()
        for r in raw:
            if r:
                out.add(str(r).upper())
                out.add(_strip_code(str(r)).upper())
        return out

    def held(item: Any) -> dict[str, Any] | None:
        return next((by_ticker[k] for k in sorted(keys(item)) if k in by_ticker), None)

    def entry(item: Any, role: str | None = None) -> dict[str, Any]:
        pos = held(item)
        d = item if isinstance(item, dict) else {}
        ticker = (pos or {}).get("ticker") or (_strip_code(str(d.get("ticker") or item)) if (d.get("ticker") or isinstance(item, str)) else "")
        return {
            "ticker": ticker,
            "name": d.get("name") or (pos or {}).get("name") or ticker,
            "role": role or d.get("role"),
            "target_weight": _r(d.get("target_weight"), 4),
            "held": pos is not None,
            "value": (pos or {}).get("value"),
            "ppl": (pos or {}).get("ppl"),
            "day_change": (pos or {}).get("day_change"),
            "weight": (pos or {}).get("weight"),
        }

    rings = play.get("rings") if isinstance(play.get("rings"), dict) else play
    explicit = any(isinstance(rings.get(k), list) for k in ("owned", "related", "bench"))
    if explicit:
        owned = [entry(x, "owned") for x in rings.get("owned") or []]
        related = [entry(x, "related") for x in rings.get("related") or []]
        bench = [entry(x, "bench") for x in rings.get("bench") or []]
    else:
        legs = [leg for leg in play.get("legs") or [] if isinstance(leg, dict)]
        owned = [entry(leg) for leg in legs if held(leg)]
        bench = [entry(leg) for leg in legs if not held(leg)]
        leg_keys: set[str] = set().union(*(keys(leg) for leg in legs)) if legs else set()
        meta = play.get("meta") if isinstance(play.get("meta"), dict) else {}
        related = [entry(o, "related") for o in meta.get("overlap") or [] if isinstance(o, dict) and not (keys(o) & leg_keys) and held(o)]
    exposure = rings.get("effective_exposure", play.get("effective_exposure"))
    if not isinstance(exposure, (int, float)):
        exposure = sum(e["value"] or 0.0 for e in owned)
    return {"owned": owned, "related": related, "bench": bench, "exposure": _r(exposure), "explicit": explicit}


def _plays(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Theme bets (thesis, basket legs, sizing, review verdict) plus their live
    owned / related / bench groups and which play owns each holding."""
    try:
        from app.services.plays_service import list_plays, serialize_play
    except ImportError:
        return {"available": False, "plays": [], "owners": []}
    status = params.get("status")
    if status is not None and status not in ("draft", "active", "paused", "closed"):
        raise DataSourceError("status must be draft | active | paused | closed")
    rows = list_plays(db, status=status)
    positions: list[dict[str, Any]] = []
    if rows:
        try:
            positions = _portfolio(db, {"currency": params.get("currency")})["positions"]
        except Exception:  # noqa: BLE001 — plays still render without live values
            logger.warning("plays source: portfolio unavailable for rings", exc_info=True)
    plays = []
    owner: dict[str, str] = {}
    for p in rows:
        s = serialize_play(p)
        out = {k: s.get(k) for k in ("id", "title", "thesis", "status", "conviction", "horizon_months", "account_kind", "budget_gbp", "legs", "kill_criteria", "review", "created_at", "activated_at")}
        meta = s.get("meta") if isinstance(s.get("meta"), dict) else {}
        out["benchmark"] = meta.get("benchmark")
        out["key_risks"] = meta.get("key_risks") or []
        rings = play_rings(s, positions)
        out["rings"] = rings
        out["exposure"] = rings["exposure"]
        out["owned_count"] = len(rings["owned"])
        out["bench_count"] = len(rings["bench"])
        plays.append(out)
        if s.get("status") in ("active", "paused", "draft"):
            for e in rings["owned"]:
                owner.setdefault(e["ticker"], s.get("id"))
    owners = [{"ticker": pos["ticker"], "play_id": owner.get(pos["ticker"]), "value": pos.get("value")} for pos in positions]
    return {"available": True, "plays": plays, "owners": owners}


def _ticker_list(params: dict[str, Any], cap: int) -> list[str]:
    tickers = params.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [t for t in tickers.split(",") if t.strip()]
    if not isinstance(tickers, list) or not tickers:
        raise DataSourceError("tickers must be a non-empty list")
    if len(tickers) > cap:
        raise DataSourceError(f"at most {cap} tickers per call")
    return [_ticker(t) for t in tickers]


def _profiles(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Classification + dividend facts for many tickers at once (parallel, cached)."""
    from concurrent.futures import ThreadPoolExecutor

    from app.services.fundamentals_service import get_fundamentals_snapshot

    syms = _ticker_list(params, 40)
    fields = ("long_name", "sector", "industry", "country", "quote_type", "currency", "dividend_yield", "dividend_rate", "ex_dividend_date", "beta")

    def one(sym: str) -> dict[str, Any]:
        try:
            data = _cached(("profile", sym), 6 * 3600, lambda: get_fundamentals_snapshot(sym))
        except Exception:  # noqa: BLE001
            data = {}
        row = {"ticker": sym} | {k: data.get(k) for k in fields}
        if row.get("dividend_yield") is not None:
            row["dividend_yield"] = _r(float(row["dividend_yield"]) / 100.0, 5)
        return row

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {"profiles": list(pool.map(one, syms))}


def _fx(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Spot FX from the display currency: ``rates.USD`` = USD per 1 unit of base."""
    from app.services.fx import get_fx_rate

    base = _currency(params)
    rates: dict[str, float | None] = {}
    for quote in ("GBP", "USD", "EUR"):
        try:
            rates[quote] = _r(_cached(("fx", base, quote), 3600, lambda q=quote: get_fx_rate(base, q)), 6)
        except Exception:  # noqa: BLE001
            rates[quote] = None
    return {"base": base, "rates": rates}


def uk_tax_year(today: date | None = None) -> tuple[date, date]:
    """UK tax year containing ``today``: 6 April to 5 April."""
    today = today or date.today()
    start_year = today.year if (today.month, today.day) >= (4, 6) else today.year - 1
    return date(start_year, 4, 6), date(start_year + 1, 4, 5)


ISA_ALLOWANCE_GBP = 20_000.0


def _tax_year_summary(start: date, end: date, deposits: dict[str, float], items: list[dict[str, Any]]) -> dict[str, Any]:
    """This UK tax year's ISA subscriptions. Trading 212's ISA is flexible, so
    money withdrawn this tax year can be paid back in without using allowance:
    used = deposits − withdrawals (never below 0)."""
    today = date.today()
    withdrawn = sum(
        abs(float(i["amount"] or 0.0))
        for i in items
        if i["account"] == "stocks_isa" and i["type"] in ("WITHDRAW", "WITHDRAWAL") and i["date"] and start.isoformat() <= i["date"] <= end.isoformat()
    )
    deposited = deposits.get("stocks_isa", 0.0)
    used = max(0.0, deposited - withdrawn)
    days_left = max(0, (end - today).days + 1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "deposits": {k: _r(v) for k, v in deposits.items()},
        "isa_allowance": ISA_ALLOWANCE_GBP,
        "isa_deposited": _r(deposited),
        "isa_withdrawn": _r(withdrawn),
        "isa_used": _r(used),
        "isa_remaining": _r(max(0.0, ISA_ALLOWANCE_GBP - used)),
        "isa_used_pct": _r(used / ISA_ALLOWANCE_GBP, 4),
        "days_left": days_left,
        "year_elapsed_pct": _r(1 - days_left / max(1, (end - start).days + 1), 4),
        "monthly_to_max": _r(max(0.0, ISA_ALLOWANCE_GBP - used) / max(1.0, days_left / 30.44)),
    }


def _cashflows(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Deposits / withdrawals per account (from the broker's transactions feed)."""
    from app.services.cashflow_service import get_cashflows, maybe_sync_all

    account = _account(params)
    try:
        _cached(("cashflow-sync",), 1800, lambda: maybe_sync_all(db) or True)
    except Exception:  # noqa: BLE001 — serve what's stored
        logger.warning("cashflow sync failed", exc_info=True)
    start, end = uk_tax_year()
    items = []
    tax_year: dict[str, float] = defaultdict(float)
    for ev in get_cashflows(db, account_kind=account):
        when = ev.occurred_at.date() if ev.occurred_at else None
        items.append({"account": ev.account_kind, "type": ev.type, "amount": _r(ev.amount), "currency": ev.currency, "date": when.isoformat() if when else None})
        if when and start <= when <= end and ev.type == "DEPOSIT":
            tax_year[ev.account_kind] += float(ev.amount or 0.0)
    items.sort(key=lambda r: r["date"] or "", reverse=True)
    return {
        "items": items[:500],
        "tax_year": _tax_year_summary(start, end, tax_year, items),
    }


def _news(db: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Recent headlines for a few tickers (text only — views cannot open links)."""
    from concurrent.futures import ThreadPoolExecutor

    from app.services.intel_service import get_company_news

    if params.get("movers"):
        # Headlines for today's biggest movers in the book (by |day_change_pct|).
        n = max(1, min(int(params.get("movers") or 5), 8))
        positions = _portfolio(db, {"currency": params.get("currency")})["positions"]
        ranked = sorted((p for p in positions if p.get("day_change_pct") is not None), key=lambda p: -abs(p["day_change_pct"]))
        params = {**params, "tickers": [p.get("yf") or p["ticker"] for p in ranked[:n]]}
        if not params["tickers"]:
            return {"items": []}
    syms = _ticker_list(params, 10)
    days = max(1, min(int(params.get("days") or 2), 7))
    limit = max(1, min(int(params.get("limit") or 3), 5))

    def one(sym: str) -> list[dict[str, Any]]:
        try:
            raw = get_company_news(sym, since_days=days, limit=limit)
        except Exception:  # noqa: BLE001
            return []
        return [
            {"ticker": sym, "title": (n.get("headline") or "")[:240], "source": n.get("source") or "", "published_at": n.get("datetime"), "summary": (n.get("summary") or "")[:400]}
            for n in raw[:limit]
            if n.get("headline")
        ]

    with ThreadPoolExecutor(max_workers=6) as pool:
        items = [item for chunk in pool.map(one, syms) for item in chunk]
    return {"items": items}


SOURCES: dict[str, Callable[[Session, dict[str, Any]], dict[str, Any]]] = {
    "portfolio": _portfolio,
    "history": _history,
    "orders": _orders,
    "dividends": _dividends,
    "prices": _prices,
    "quotes": _quotes,
    "fundamentals": _fundamentals,
    "watchlist": _watchlist,
    "proposals": _proposals,
    "plays": _plays,
    "profiles": _profiles,
    "fx": _fx,
    "cashflows": _cashflows,
    "news": _news,
}


def resolve(db: Session, source: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a whitelisted source. Raises ``DataSourceError`` for unknown sources/bad params."""
    fn = SOURCES.get(str(source or ""))
    if fn is None:
        raise DataSourceError(f"unknown source '{source}'. Available: {', '.join(SOURCES)}")
    if params is not None and not isinstance(params, dict):
        raise DataSourceError("params must be an object")
    return fn(db, dict(params or {}))


def truncate_sample(value: Any, *, max_items: int = 4, depth: int = 0) -> Any:
    """Shrink a payload for the builder: keep shape, drop bulk."""
    if isinstance(value, list):
        head = [truncate_sample(v, max_items=max_items, depth=depth + 1) for v in value[:max_items]]
        if len(value) > max_items:
            head.append(f"… {len(value) - max_items} more items")
        return head
    if isinstance(value, dict):
        return {k: truncate_sample(v, max_items=max_items, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "…"
    if isinstance(value, datetime):
        return value.isoformat()
    return value
