"""Instrument Spotlight — search + unified per-instrument detail.

``GET /instruments/search`` powers the command-palette typeahead. ``GET
/instruments/{ticker}/detail`` assembles everything we already know about one
instrument into a single cheap call: live price + day change, my (cross-account)
position, technical signals, watchlist context, open attention flags, related
theses, and the price-vs-target verdict. Every external lookup is best-effort —
a missing snapshot/price never 500s the view, it just narrows what's returned.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Alert, Thesis
from app.schemas.instruments import (
    InstrumentAlert,
    InstrumentDetail,
    InstrumentPosition,
    InstrumentSearchResponse,
    InstrumentSearchRow,
    InstrumentSignals,
    InstrumentThesis,
    InstrumentWatchlist,
)
from app.services import instrument_insight as instrument_insight_service
from app.services import leveraged_market, manual_trade, portfolio_service, proposal_service, watchlist_service
from app.services.execution_service import ExecutionError
from app.services.t212_client import T212Error
from app.services.t212_errors import classify_t212_error

router = APIRouter(prefix="/instruments", tags=["instruments"])

_OPEN_STATUSES = ["new", "seen"]


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _aggregate_position(rows: list[dict[str, Any]], display_currency: str) -> InstrumentPosition:
    """Combine per-account rows for one ticker into a single holding, mirroring the
    dashboard's client-side aggregation (qty/value/ppl summed, prices re-derived)."""
    qty = sum(float(r.get("quantity") or 0.0) for r in rows)
    value = sum(float(r.get("value") or 0.0) for r in rows)
    ppl = sum(float(r.get("ppl") or 0.0) for r in rows)
    total_cost = sum(float(r.get("total_cost") or 0.0) for r in rows)
    weight = sum(float(r.get("weight") or 0.0) for r in rows)
    accounts = sorted({str(r.get("account_kind")) for r in rows if r.get("account_kind")})

    avg_price = (total_cost / qty) if qty > 0 and total_cost > 0 else float(rows[0].get("average_price") or 0.0)
    current_price = (value / qty) if qty > 0 else float(rows[0].get("current_price") or 0.0)
    ppl_pct = (ppl / total_cost) if total_cost > 0 else None

    return InstrumentPosition(
        account_kind="all" if len(accounts) > 1 else (accounts[0] if accounts else "all"),
        accounts=accounts,
        quantity=qty,
        average_price=avg_price,
        current_price=current_price,
        total_cost=total_cost,
        value=value,
        ppl=ppl,
        ppl_pct=ppl_pct,
        weight=weight,
    )


@router.get("/search", response_model=InstrumentSearchResponse)
def search_instruments(
    q: str = Query("", description="search term (symbol or name)"),
    limit: int = Query(8, ge=1, le=25),
    db: Session = Depends(get_db),
) -> InstrumentSearchResponse:
    rows = portfolio_service.search_instruments(q, db, limit=limit)
    return InstrumentSearchResponse(results=[InstrumentSearchRow(**r) for r in rows])


@router.get("/names")
def instrument_names(
    symbols: str = Query("", description="comma-separated market symbols, e.g. BA.L,RHM.DE"),
    account: str | None = Query(None, description="flag listings this account can't hold"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Company name, clean ticker, exchange, sector, price and whether Trading 212
    lists each symbol. Names are cached; slow lookups finish in the background."""
    from app.services import instrument_names as names

    wanted = [s for s in symbols.split(",") if s.strip()][:40]
    return {"items": names.describe(db, wanted, account_kind=account)}


@router.get("/{ticker}/detail", response_model=InstrumentDetail)
def instrument_detail(
    ticker: str,
    display_currency: str = Query("GBP"),
    db: Session = Depends(get_db),
) -> InstrumentDetail:
    sym = _norm(ticker)
    display_currency = (display_currency or "GBP").upper()

    # ── 1. Identity (best-effort; falls back to the typed term) ──────────────
    ident = portfolio_service.resolve_primary_instrument(ticker, db)
    instrument_code = ident.get("instrument_code") if ident else None
    name = (ident.get("name") if ident else None) or None
    display_symbol = (ident.get("ticker") if ident else None) or sym
    display_ticker = (ident.get("display_ticker") if ident else None) or None
    yf_ticker = ident.get("yfinance_ticker") if ident else None
    native_currency = ident.get("currency") if ident else None

    # ── 2. My position, aggregated across accounts ───────────────────────────
    matched: list[dict[str, Any]] = []
    try:
        snap = portfolio_service.get_portfolio_snapshot(db, "all", display_currency)
        for p in snap.get("positions", []):
            if (
                _norm(p.get("ticker")) in {sym, _norm(display_symbol)}
                or (instrument_code and _norm(p.get("instrument_code")) == _norm(instrument_code))
            ):
                matched.append(p)
    except Exception:  # noqa: BLE001 — snapshot is best-effort
        matched = []

    held = bool(matched)
    position = _aggregate_position(matched, display_currency) if held else None
    if held:
        # The held position is the ground truth of what the user actually owns, so
        # its identity wins over an ambiguous symbol match — e.g. real Nasdaq
        # GOOGL_US_EQ ("Alphabet") vs a GOOGL-symbol leverage ETP. Done BEFORE the
        # price fetch so the chart/price use the corrected instrument.
        first = matched[0]
        name = first.get("name") or name
        instrument_code = first.get("instrument_code") or instrument_code
        # display_ticker, else the held position's own canonical symbol — never the
        # ambiguous ident's (which may be a different same-symbol instrument).
        display_ticker = first.get("display_ticker") or first.get("ticker") or display_ticker
        yf_ticker = first.get("yfinance_ticker") or yf_ticker
        native_currency = first.get("instrument_currency") or native_currency
        if first.get("ticker"):
            display_symbol = first["ticker"]

    # US equities: T212 freezes the code (e.g. SNDK1_US_EQ); the tradable symbol is
    # the shortName-derived display_ticker (SNDK). Prefer it for the chart + price so
    # they don't 404 on the frozen symbol. (LSE ETPs keep structural resolution.)
    if not yf_ticker and instrument_code and instrument_code.upper().endswith("_US_EQ"):
        legacy = instrument_code.upper().split("_", 1)[0]
        if display_ticker and display_ticker.upper() != legacy and "." not in display_ticker:
            yf_ticker = display_ticker.upper()

    # ── 3. Live price in the instrument's native quote (for the verdict math) ─
    price_ticker = yf_ticker or instrument_code or sym
    native_price: float | None = None
    change_pct: float | None = None
    is_minor = False
    try:
        live = leveraged_market.get_price(price_ticker, native_currency)
        native_price = float(live["price"])
        change_pct = float(live["change_pct"])
        native_currency = live.get("currency") or native_currency
        is_minor = bool(live.get("is_minor_unit"))
        yf_ticker = yf_ticker or live.get("yfinance_ticker")
    except Exception:  # noqa: BLE001 — price is best-effort
        pass

    # ── 4. Signals: prefer the held row; fall back to technicals for RSI/trend ─
    sig_row = next((p for p in matched if p.get("rsi_14") is not None), matched[0] if matched else None)
    signals = InstrumentSignals(
        momentum_63d=sig_row.get("momentum_63d") if sig_row else None,
        rsi_14=sig_row.get("rsi_14") if sig_row else None,
        trend_score=sig_row.get("trend_score") if sig_row else None,
        volatility_30d=sig_row.get("volatility_30d") if sig_row else None,
        risk_flag=sig_row.get("risk_flag") if sig_row else None,
    )
    if signals.rsi_14 is None:
        try:
            tech = leveraged_market.get_technicals(price_ticker)
            signals.rsi_14 = tech.get("rsi_14")
            signals.trend_direction = tech.get("trend_direction")
        except Exception:  # noqa: BLE001 — technicals are best-effort
            pass

    syms = {sym, _norm(display_symbol)}

    # ── 5. Watchlist context ─────────────────────────────────────────────────
    wl = watchlist_service.get_active_by_symbol(db, sym) or watchlist_service.get_active_by_symbol(db, display_symbol)
    watchlist_ctx = None
    target_price = None
    target_direction = None
    if wl:
        watchlist_ctx = InstrumentWatchlist(
            id=wl.id,
            conviction=wl.conviction,
            status=wl.status,
            note=wl.note or "",
            target_price=wl.target_price,
            target_direction=wl.target_direction,
            monitor=bool(wl.monitor),
        )
        target_price = wl.target_price
        target_direction = wl.target_direction

    # ── 6. Open attention flags for this ticker ──────────────────────────────
    alert_rows = db.execute(
        select(Alert)
        .where(Alert.ticker.in_(list(syms)), Alert.status.in_(_OPEN_STATUSES))
        .order_by(desc(Alert.created_at))
    ).scalars().all()
    alerts = [
        InstrumentAlert(
            id=a.id,
            created_at=a.created_at.isoformat() if a.created_at else None,
            category=a.category,
            severity=a.severity,
            title=a.title,
            detail=a.detail or "",
            consider=a.consider,
            ticker=a.ticker,
            status=a.status,
            source=a.source or "",
        )
        for a in alert_rows
    ]

    # ── 7. Active theses for this ticker ─────────────────────────────────────
    thesis_rows = db.execute(
        select(Thesis)
        .where(Thesis.symbol.in_(list(syms)), Thesis.status == "active")
        .order_by(desc(Thesis.created_at))
        .limit(3)
    ).scalars().all()
    theses = [
        InstrumentThesis(
            id=t.id,
            title=t.title or "",
            status=t.status,
            confidence=t.confidence,
            invalidation=t.invalidation or "",
        )
        for t in thesis_rows
    ]

    # ── 8. Verdict + header price ────────────────────────────────────────────
    target_distance_pct = None
    if target_price and native_price and native_price > 0:
        target_distance_pct = (target_price - native_price) / native_price

    if held and position:
        header_price = position.current_price
        header_currency = display_currency
        header_minor = False
    else:
        header_price = native_price
        header_currency = native_currency
        header_minor = is_minor

    return InstrumentDetail(
        ticker=display_symbol or sym,
        display_ticker=display_ticker or display_symbol or sym,
        instrument_code=instrument_code,
        name=name,
        yfinance_ticker=yf_ticker,
        currency=header_currency,
        is_minor_unit=header_minor,
        price=header_price,
        change_pct=change_pct,
        held=held,
        position=position,
        signals=signals,
        watchlist=watchlist_ctx,
        alerts=alerts,
        theses=theses,
        target_price=target_price,
        target_direction=target_direction,
        target_distance_pct=target_distance_pct,
        display_currency=display_currency,
    )


# ── Insight: the stock sheet's single aggregated payload ─────────────────────


@router.get("/{ticker}/insight")
def instrument_insight(
    ticker: str,
    wait: float = Query(instrument_insight_service.DEFAULT_WAIT_S, ge=0.0, le=8.0, description="seconds to wait for slow sections"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Everything the instrument sheet shows, section by section. Every section
    is optional (null/empty when its source is missing); sections still loading
    are named in ``pending`` — re-request to pick them up from the cache."""
    try:
        return instrument_insight_service.build_insight(ticker, db, wait_s=wait)
    except instrument_insight_service.UnknownInstrument:
        raise HTTPException(status_code=404, detail=f"Unknown instrument {ticker!r}")


class ProposeFromSheet(BaseModel):
    side: str
    account_kind: str
    notional_gbp: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    rationale: str = ""


@router.post("/{ticker}/propose")
def propose_from_sheet(ticker: str, body: ProposeFromSheet, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create a *proposed* trade intent from the sheet's sizer. Never executes:
    it goes through the same validation and rails as Archie's proposals and
    waits for a human to approve it in Decisions."""
    ident = instrument_insight_service.resolve(ticker, db)
    rationale = body.rationale.strip() or f"Sized by hand from the {ident['display_ticker']} sheet."
    return proposal_service.propose_trade(
        db,
        side=body.side,
        account_kind=body.account_kind,
        rationale=rationale,
        symbol=None if ident.get("instrument_code") else ident["display_ticker"],
        instrument_code=ident.get("instrument_code"),
        quantity=body.quantity,
        notional_gbp=body.notional_gbp,
        source="instrument_sheet",
    )


# ── Manual trades: the user trades from the sheet; no proposal ──────────────


class ManualTradeAmount(BaseModel):
    side: str
    account_kind: str
    notional_gbp: float = Field(gt=0)


class UserAction(BaseModel):
    """Proof the request came from a person pressing Trade in the review step.
    Agents have no route here at all (no MCP tool; the app's own API is blocked
    by their hooks); this makes the intent explicit in every request too."""

    kind: Literal["manual_trade"]
    reviewed: Literal[True]


class ManualTradeRequest(ManualTradeAmount):
    user_action: UserAction
    live_confirmed: bool = False
    note: str = Field(default="", max_length=500)


@router.post("/{ticker}/trade/preview")
def manual_trade_preview(ticker: str, body: ManualTradeAmount, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The review step: size, fees and every rail for a hand-placed order. Records nothing."""
    ident = instrument_insight_service.resolve(ticker, db)
    try:
        return manual_trade.preview(db, ident, side=body.side, account_kind=body.account_kind, notional_gbp=body.notional_gbp)
    except manual_trade.ManualTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{ticker}/trade")
def manual_trade_place(ticker: str, body: ManualTradeRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Place the user's own order: recorded with source "user", approved as the
    user, then executed through ``execute_intent`` (every rail re-checked)."""
    ident = instrument_insight_service.resolve(ticker, db)
    try:
        return manual_trade.place(
            db, ident, side=body.side, account_kind=body.account_kind, notional_gbp=body.notional_gbp,
            live_confirmed=body.live_confirmed, note=body.note,
        )
    except manual_trade.ManualTradeError as exc:
        status = 409 if exc.code == "needs_live_confirmation" else 400
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc), "violations": exc.violations}) from exc
    except (ExecutionError, T212Error) as exc:
        classified = classify_t212_error(exc, account_kind=body.account_kind)
        raise HTTPException(status_code=classified.status_code, detail=classified.as_detail()) from exc
