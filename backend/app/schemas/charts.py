from __future__ import annotations

from pydantic import BaseModel


class CandleItem(BaseModel):
    time: str | float  # "YYYY-MM-DD" for daily, Unix ts for intraday
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorPoint(BaseModel):
    time: str | float
    value: float


class MACDPoint(BaseModel):
    time: str | float
    macd: float
    signal: float
    histogram: float


class ChartMarker(BaseModel):
    time: str | float
    position: str  # "aboveBar" | "belowBar"
    color: str
    shape: str  # "arrowUp" | "arrowDown" | "circle"
    text: str


class ChartResponse(BaseModel):
    ok: bool
    ticker: str
    yfinance_ticker: str
    period: str
    interval: str
    candles: list[CandleItem]
    overlays: dict[str, list[IndicatorPoint]]
    panels: dict[str, list[IndicatorPoint] | list[MACDPoint]]
    markers: list[ChartMarker]


class ChartErrorResponse(BaseModel):
    ok: bool = False
    error: str


# ── Kronos forecast schemas ───────────────────────────────────────────────


class ForecastHistoryPoint(BaseModel):
    date: str
    close: float


class ForecastBandPoint(BaseModel):
    date: str
    p10: float
    p50: float
    p90: float


class ForecastSummary(BaseModel):
    median_terminal_close: float
    expected_return_pct: float
    prob_up: float
    terminal_spread_pct: float


class ForecastResponse(BaseModel):
    ok: bool
    symbol: str
    yfinance_ticker: str
    model: str
    device: str | None = None
    generated_at: str
    horizon: int
    lookback: int
    samples: int
    last_close: float
    last_date: str
    summary: ForecastSummary
    history: list[ForecastHistoryPoint]
    forecast: list[ForecastBandPoint]
