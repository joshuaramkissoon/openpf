"""Schemas for the Instrument Spotlight: a typeahead search row and the unified
per-instrument detail payload that aggregates position, signals, watchlist
context, open attention flags, related theses, and a live-price verdict.

Deliberately a *cheap* bundle — price/signals/position/watchlist/alerts/theses are
all DB-local or short-cached. The slow/rate-limited surfaces (Kronos forecast,
broker order history) stay on their own lazy endpoints (``/charts/forecast``,
``/orders/history``) so opening the sheet never blocks on them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class InstrumentSearchRow(BaseModel):
    instrument_code: str | None = None
    ticker: str
    # Real market ticker for display (T212's shortName), falling back to ``ticker``
    # when there's no distinct market symbol. ``ticker`` stays canonical for keys.
    display_ticker: str | None = None
    name: str = ""
    currency: str | None = None


class InstrumentSearchResponse(BaseModel):
    results: list[InstrumentSearchRow] = Field(default_factory=list)


class InstrumentSignals(BaseModel):
    momentum_63d: float | None = None
    rsi_14: float | None = None
    trend_score: float | None = None
    volatility_30d: float | None = None
    risk_flag: str | None = None
    # Fallback for non-held / outside-top-12 names where the snapshot carries no
    # computed signal: a coarse uptrend/downtrend/mixed read from technicals.
    trend_direction: str | None = None


class InstrumentPosition(BaseModel):
    """Holding aggregated across accounts. Money fields are in the requested
    display currency; ``ppl_pct`` is currency-agnostic (ppl / cost)."""

    account_kind: str
    accounts: list[str] = Field(default_factory=list)
    quantity: float
    average_price: float
    current_price: float
    total_cost: float
    value: float
    ppl: float
    ppl_pct: float | None = None
    weight: float


class InstrumentWatchlist(BaseModel):
    id: str
    conviction: str | None = None
    status: str
    note: str = ""
    target_price: float | None = None
    target_direction: str | None = None
    monitor: bool = False


class InstrumentAlert(BaseModel):
    id: str
    created_at: str | None = None
    category: str
    severity: str
    title: str
    detail: str = ""
    consider: str | None = None
    ticker: str | None = None
    status: str
    source: str = ""


class InstrumentThesis(BaseModel):
    id: str
    title: str
    status: str
    confidence: float = 0.0
    invalidation: str = ""


class InstrumentDetail(BaseModel):
    # Identity
    ticker: str
    # Real market ticker for display; ``ticker`` stays canonical for resolution.
    display_ticker: str | None = None
    instrument_code: str | None = None
    name: str | None = None
    yfinance_ticker: str | None = None
    # Header price (display currency when held, else instrument currency).
    currency: str | None = None
    is_minor_unit: bool = False
    price: float | None = None
    change_pct: float | None = None
    # Aggregates
    held: bool = False
    position: InstrumentPosition | None = None
    signals: InstrumentSignals = Field(default_factory=InstrumentSignals)
    watchlist: InstrumentWatchlist | None = None
    alerts: list[InstrumentAlert] = Field(default_factory=list)
    theses: list[InstrumentThesis] = Field(default_factory=list)
    # Verdict line: distance of live price from the watchlist target (computed in
    # the instrument's native quote so the units match the user-entered target).
    target_price: float | None = None
    target_direction: str | None = None
    target_distance_pct: float | None = None
    display_currency: str = "GBP"
