"""Backfill the portfolio equity curve from full T212 history.

T212 exposes no historical net-asset-value series, so we reconstruct one:

    1. Pull full order history (signed fills) + dividends per account.
    2. Key every instrument by ISIN (stable across ticker renames, e.g. the
       FB→META rebrand shares one ISIN) and resolve it to a *current* yfinance
       ticker via live T212 metadata, so a renamed symbol still prices off its
       full history.
    3. Fetch split-adjusted daily closes (yfinance, auto_adjust) per instrument
       and historical GBP/USD FX, then hand normalized inputs to the pure
       ``equity_reconstruction`` engine, which walks share counts day-by-day and
       anchors each instrument to today's real holding (this is what backs out
       stock splits — the anchor factor IS the split ratio for a held position).
    4. Persist the daily series to ``reconstructed_equity_daily``.

History endpoints are aggressively rate-limited and yfinance is slow, so this is
a one-time/occasional background job, not a request-path operation.
"""
from __future__ import annotations

import bisect
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ReconstructedEquityDaily
from app.services.cashflow_service import CONTRIBUTION_TYPES, get_cashflows
from app.services.config_store import ConfigStore
from app.services.equity_reconstruction import CashEvent, Fill, normalize_split_basis, reconstruct_daily_equity
from app.services.fx import get_fx_rate
from app.services.historical_fx import ensure_history, load_fx_history
from app.services.leveraged_market import resolve_yfinance_ticker
from app.services.market_data import fetch_history
from app.services.portfolio_service import _latest_accounts, _latest_positions
from app.services.t212_client import T212Error, build_t212_client

logger = logging.getLogger(__name__)
settings = get_settings()


# ── value helpers ────────────────────────────────────────────────────────────

def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_day(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _quote_ccy(yf_ticker: str, t212_ccy: str | None) -> str:
    """Currency the yfinance close is quoted in. LSE (.L) prints in pence (GBX)
    regardless of T212's tag; everything else follows the instrument currency."""
    if yf_ticker.upper().endswith(".L"):
        return "GBX"
    return (t212_ccy or "USD").upper()


class _PriceSeries:
    """Sorted daily closes with nearest-prior lookup. Returns None *before* the
    first close (instrument not yet listed/held) so the engine skips it."""

    def __init__(self, rows: list[tuple[date, float]]):
        rows = sorted(rows)
        self._dates = [r[0] for r in rows]
        self._closes = [r[1] for r in rows]

    def on(self, d: date) -> float | None:
        if not self._dates:
            return None
        i = bisect.bisect_right(self._dates, d)
        if i == 0:
            return None
        return self._closes[i - 1]


def _to_today_basis(rows: list[tuple[date, float]], splits: list[tuple[date, float]]) -> _PriceSeries:
    """Convert *unadjusted* closes to today's split basis using ONLY the splits
    we can actually see, so price and share normalization stay consistent.

    A close on date d is divided by the product of split ratios dated after d
    (a 4:1 split → later prices unchanged, earlier prices ÷4). Crucially, when a
    consolidation isn't in the split feed (common for leveraged ETPs), we fall
    back to the *real* traded price rather than yfinance's silently back-adjusted
    one — which is what caused the 2020 value to explode."""
    if not splits:
        return _PriceSeries(rows)
    sdates = sorted(splits)
    out: list[tuple[date, float]] = []
    for d, close in rows:
        factor = 1.0
        for sd, ratio in sdates:
            if sd > d and ratio > 0:
                factor /= ratio
        out.append((d, close * factor))
    return _PriceSeries(out)


def _make_fx_lookup(fx_hist):
    def fx(src: str, tgt: str, on: date) -> float:
        s = (src or "GBP").upper()
        t = (tgt or "GBP").upper()
        if s in ("GBX", "GBP_PENCE", "GBPENCE"):  # pence → GBP is /100
            return fx_hist.rate("GBP", t, on) * 0.01
        return fx_hist.rate(s, t, on)
    return fx


# ── T212 → engine inputs ──────────────────────────────────────────────────────

def _instrument_meta_by_code(client) -> dict[str, dict]:
    """{CODE: {currency, name}} from the bulk metadata (one cached call), keyed by
    the upper-cased T212 instrument code — the stable join key between historical
    orders and current positions (T212 keeps the code across ticker rebrands)."""
    out: dict[str, dict] = {}
    try:
        for inst in client.get_instruments_metadata():
            if not isinstance(inst, dict):
                continue
            code = str(inst.get("ticker") or "").strip()
            if not code:
                continue
            out[code.upper()] = {
                "currency": str(inst.get("currencyCode") or "").strip() or None,
                "name": inst.get("name") or inst.get("shortName"),
            }
    except Exception as exc:  # noqa: BLE001 — metadata is best-effort
        logger.warning("equity_backfill: metadata fetch failed: %s", exc)
    return out


def _normalize_orders(orders: list[dict], account_ccy_default: str) -> tuple[list[Fill], dict[str, dict]]:
    """Turn raw T212 orders into engine Fills keyed by the T212 instrument CODE.

    ISIN is unreliable in the orders feed (empty for most US equities), so it
    can't be the join key — the code is, and it matches current positions
    exactly. Also returns a CODE→{code, currency, name} map for price resolution.
    """
    fills: list[Fill] = []
    seen: dict[str, dict] = {}
    for item in orders:
        order = item.get("order") or {}
        fill = item.get("fill") or {}
        if str(order.get("status") or "").upper() not in ("FILLED", ""):
            # Only filled orders move shares; cancelled/rejected don't.
            if str(order.get("status") or "").upper() not in ("",) and not fill:
                continue
        inst = order.get("instrument") or {}
        code = str(inst.get("ticker") or order.get("ticker") or "").strip()
        key = code.upper()
        if not key:
            continue

        qty_raw = _f(fill.get("quantity"))
        if qty_raw == 0.0:
            continue
        side = str(order.get("side") or "").upper()
        # Trust `side` for sign (T212's quantity sign has been seen both ways).
        signed_qty = -abs(qty_raw) if side == "SELL" else abs(qty_raw)

        wi = fill.get("walletImpact") or {}
        net = abs(_f(wi.get("netValue"), _f(order.get("filledValue"))))
        cash_impact = net if side == "SELL" else -net
        account_ccy = str(wi.get("currency") or order.get("currency") or account_ccy_default).upper()

        on = _parse_day(fill.get("filledAt") or order.get("createdAt"))
        if on is None:
            continue

        instr_ccy = str(inst.get("currency") or "USD").upper()
        seen.setdefault(key, {"code": code, "currency": instr_ccy, "name": inst.get("name")})
        fills.append(Fill(
            on=on, key=key, qty=signed_qty, cash_impact=cash_impact,
            account_ccy=account_ccy, instrument_ccy=instr_ccy,
            price=abs(_f(fill.get("price"))),
        ))
    return fills, seen


def _normalize_dividends(divs: list[dict]) -> list[CashEvent]:
    events: list[CashEvent] = []
    for d in divs:
        on = _parse_day(d.get("paidOn"))
        amount = _f(d.get("amount"))
        if on is None or amount == 0.0:
            continue
        events.append(CashEvent(on=on, amount=amount, ccy=str(d.get("currency") or "USD").upper()))
    return events


def _cashflow_events(db: Session, account_kind: str) -> list[CashEvent]:
    return [
        CashEvent(on=f.occurred_at.date(), amount=_f(f.amount), ccy=(f.currency or "USD").upper())
        for f in get_cashflows(db, account_kind)
        if f.type in CONTRIBUTION_TYPES
    ]


def _current_holdings(db: Session, account_kind: str) -> tuple[dict[str, float], float]:
    """Today's share count per instrument CODE + free cash (GBP) for one account,
    from the latest live snapshot. Used to anchor the reconstruction's right edge
    (and to back out splits — the anchor factor is the split ratio)."""
    positions = [p for p in _latest_positions(db) if p.account_kind == account_kind]
    current_qty: dict[str, float] = {}
    for p in positions:
        key = (p.instrument_code or "").upper()
        current_qty[key] = current_qty.get(key, 0.0) + _f(p.quantity)

    cash_gbp = 0.0
    for a in _latest_accounts(db):
        if a.account_kind == account_kind:
            cash_gbp += _f(a.free_cash) * get_fx_rate((a.currency or "GBP").upper(), "GBP")
    return current_qty, cash_gbp


# ── price loading ──────────────────────────────────────────────────────────────

def _fetch_splits(yf_ticker: str) -> list[tuple[date, float]]:
    """yfinance split calendar for a ticker: [(date, ratio)] (4:1 → 4.0, 1:50
    reverse → 0.02). Used to normalize as-executed fills to today's basis so they
    line up with split-adjusted prices. Best-effort — empty on any failure."""
    try:
        import yfinance as yf

        out: list[tuple[date, float]] = []
        for idx, ratio in yf.Ticker(yf_ticker).splits.items():
            d = idx.date() if hasattr(idx, "date") else None
            r = float(ratio)
            if d is not None and r and r > 0:
                out.append((d, r))
        return out
    except Exception:  # noqa: BLE001
        return []


def _fill_price_series(fill_prices: list[tuple[date, float]]) -> _PriceSeries:
    """Step-function price from the user's actual fill prices, carried forward.
    Ground-truth fallback for instruments yfinance prices badly (delisted /
    recycled tickers). Also split-robust: a split preserves shares×price, and we
    only reset the carried price when a new fill is observed."""
    return _PriceSeries([(d, p) for d, p in fill_prices if p and p > 0])


def _load_prices(
    keys_meta: dict[str, dict],
    meta_by_code: dict[str, dict],
    span_days: int,
    fill_prices_by_key: dict[str, list[tuple[date, float]]] | None = None,
):
    """Build a daily price series per instrument key, in today's split basis.

    Uses unadjusted yfinance closes + the visible split calendar, but FIRST
    sanity-checks yfinance against the user's own fill price on the trade date.
    If they diverge wildly (recycled/delisted ticker → wrong company's prices),
    we fall back to carrying the real fill price forward — which never explodes
    and is split-robust. Returns (price_lookup, instr_ccy_override, unpriced,
    splits_by_key, fill_fallback)."""
    fill_prices_by_key = fill_prices_by_key or {}
    series_by_key: dict[str, _PriceSeries] = {}
    instr_ccy: dict[str, str] = {}
    splits_by_key: dict[str, list[tuple[date, float]]] = {}
    unpriced: list[str] = []
    fill_fallback: list[str] = []
    lookback = max(span_days + 7, 60)

    for key, info in keys_meta.items():
        meta = meta_by_code.get(key)
        code = info.get("code") or key
        ccy = info.get("currency") or (meta or {}).get("currency")
        try:
            yf_ticker = resolve_yfinance_ticker(code, ccy)
        except Exception:  # noqa: BLE001
            yf_ticker = code
        instr_ccy[key] = _quote_ccy(yf_ticker, ccy)
        fps = sorted(fill_prices_by_key.get(key, []))

        rows: list[tuple[date, float]] = []
        try:
            # Unadjusted (real) closes — we apply splits ourselves so prices and
            # share counts use the SAME (visible) split data and stay consistent.
            frame = fetch_history(yf_ticker, lookback_days=lookback, auto_adjust=False)
            rows = [(r.date, float(r.close)) for r in frame.itertuples() if r.close and r.close > 0]
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not abort the backfill
            logger.warning("equity_backfill: no price history for %s (%s): %s", code, yf_ticker, exc)

        # Sanity-check yfinance against the earliest fill price (same day, same
        # basis): if it's off by >4× either way, it's the wrong security's data.
        trustworthy = bool(rows)
        if rows and fps:
            unadj = _PriceSeries(rows)
            fill_d, fill_p = fps[0]
            yp = unadj.on(fill_d)
            if yp is not None and fill_p > 0 and not (0.25 <= yp / fill_p <= 4.0):
                trustworthy = False

        if trustworthy:
            splits = _fetch_splits(yf_ticker)
            if splits:
                splits_by_key[key] = splits
            series_by_key[key] = _to_today_basis(rows, splits)
        elif fps:
            # Bad/again no market data, but we have real fill prices → carry them.
            series_by_key[key] = _fill_price_series(fps)
            fill_fallback.append(code)
        else:
            unpriced.append(code)
            continue

    def price_lookup(key: str, on: date) -> float | None:
        s = series_by_key.get(key)
        return s.on(on) if s else None

    return price_lookup, instr_ccy, unpriced, splits_by_key, fill_fallback


# ── orchestration ────────────────────────────────────────────────────────────

def backfill_account(
    db: Session,
    account_kind: str,
    *,
    target_ccy: str = "GBP",
    orders: list[dict] | None = None,
    raw_dividends: list[dict] | None = None,
) -> dict:
    """Reconstruct and persist the daily equity curve for one account.

    ``orders`` / ``raw_dividends`` let a caller supply pre-fetched payloads
    (offline re-runs / tests) instead of hitting the rate-limited T212 API."""
    config = ConfigStore(db)
    try:
        client = build_t212_client(config, account_kind=account_kind)
    except T212Error as exc:
        return {"account_kind": account_kind, "ok": False, "error": str(exc), "points": 0}

    account_ccy_default = "GBP" if account_kind == "stocks_isa" else "USD"

    if orders is None:
        logger.info("equity_backfill[%s]: fetching order history…", account_kind)
        orders = client.get_order_history()
    fills, order_meta = _normalize_orders(orders, account_ccy_default)
    if not fills:
        return {"account_kind": account_kind, "ok": True, "points": 0, "note": "no fills"}

    if raw_dividends is None:
        logger.info("equity_backfill[%s]: fetching dividends…", account_kind)
        try:
            raw_dividends = client.get_dividends()
        except T212Error as exc:
            logger.warning("equity_backfill[%s]: dividends fetch failed: %s", account_kind, exc)
            raw_dividends = []
    dividends = _normalize_dividends(raw_dividends)

    cash_events = _cashflow_events(db, account_kind) + dividends

    meta_by_code = _instrument_meta_by_code(client)
    current_qty, current_cash = _current_holdings(db, account_kind)

    start = min(f.on for f in fills)
    end = date.today()
    span_days = (end - start).days

    # Historical FX across the span (point-in-time GBP/USD).
    try:
        ensure_history(db, start - timedelta(days=7), end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("equity_backfill[%s]: FX backfill failed: %s", account_kind, exc)
    fx_hist = load_fx_history(db)
    fx_lookup = _make_fx_lookup(fx_hist)

    logger.info("equity_backfill[%s]: pricing %d instruments over %d days…",
                account_kind, len({f.key for f in fills}), span_days)
    keys_meta = {f.key: order_meta.get(f.key, {}) for f in fills}
    # Per-instrument fill prices (ground truth) for the yfinance sanity check.
    fill_prices_by_key: dict[str, list[tuple[date, float]]] = {}
    for f in fills:
        if f.price > 0:
            fill_prices_by_key.setdefault(f.key, []).append((f.on, f.price))
    price_lookup, instr_ccy_override, unpriced, splits_by_key, fill_fallback = _load_prices(
        keys_meta, meta_by_code, span_days, fill_prices_by_key
    )

    # Normalize as-executed fills to today's split basis (so shares line up with
    # split-adjusted prices), then re-tag with the resolved quote currency (e.g.
    # GBX for LSE) so the engine's FX conversion matches the price's currency.
    # Fill-fallback keys carry no splits entry, so they stay in raw units —
    # which is correct, since their price series is the (split-robust) fill price.
    fills = normalize_split_basis(fills, splits_by_key)
    fills = [
        Fill(on=f.on, key=f.key, qty=f.qty, cash_impact=f.cash_impact,
             account_ccy=f.account_ccy, instrument_ccy=instr_ccy_override.get(f.key, f.instrument_ccy),
             price=f.price)
        for f in fills
    ]

    series = reconstruct_daily_equity(
        fills=fills, cash_events=cash_events, price_lookup=price_lookup,
        fx_lookup=fx_lookup, current_qty=current_qty, current_cash_base=current_cash,
        target_ccy=target_ccy, start=start, end=end,
    )

    # Persist (idempotent: replace this account's reconstructed rows).
    db.execute(delete(ReconstructedEquityDaily).where(ReconstructedEquityDaily.account_kind == account_kind))
    for p in series:
        db.add(ReconstructedEquityDaily(
            account_kind=account_kind, date=date.fromisoformat(p["date"]),
            total=p["total"], invested=p["holdings"], cash=p["cash"], currency=target_ccy,
        ))
    db.commit()

    return {
        "account_kind": account_kind, "ok": True, "points": len(series),
        "start": start.isoformat(), "end": end.isoformat(),
        "instruments": len({f.key for f in fills}), "unpriced": unpriced,
        "fill_fallback": fill_fallback,
        "end_value": series[-1]["total"] if series else 0.0,
    }


def backfill_all(db: Session, *, target_ccy: str = "GBP") -> dict:
    config = ConfigStore(db)
    results = [backfill_account(db, kind, target_ccy=target_ccy) for kind in config.enabled_account_kinds()]
    return {"accounts": results, "points": sum(r.get("points", 0) for r in results)}


def get_reconstructed_history(db: Session, account_kind: str = "all") -> list[ReconstructedEquityDaily]:
    q = select(ReconstructedEquityDaily).order_by(ReconstructedEquityDaily.date.asc())
    if account_kind != "all":
        q = q.where(ReconstructedEquityDaily.account_kind == account_kind)
    return list(db.execute(q).scalars().all())
