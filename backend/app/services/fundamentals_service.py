"""Fundamentals data service powered by yfinance.

Pure functions over ``yf.Ticker(sym)`` exposing company facts, valuation
ratios, financial statements, and the earnings calendar. Mirrors the TTL
cache style in ``leveraged_market.py`` and reuses its symbol normalization
(``to_yfinance_ticker``) and error type (``LeveragedMarketError``).

Fundamentals change slowly, so the cache TTL here is long (6h).
"""

from __future__ import annotations

import logging
import math
import time
from threading import Lock
from typing import Any

import pandas as pd
import yfinance as yf

from app.services.leveraged_market import (
    LeveragedMarketError,
    to_yfinance_ticker,
)

logger = logging.getLogger(__name__)


# ── TTL cache (mirrors leveraged_market.py, but with a 6h TTL) ──────────────
_CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE_MAX_ITEMS = 512
_cache_lock = Lock()
# Keyed by (ticker, fn, args) since fundamentals change slowly.
_fundamentals_cache: dict[tuple, tuple[float, Any]] = {}


def _cache_get(key: tuple) -> Any | None:
    now = time.time()
    with _cache_lock:
        payload = _fundamentals_cache.get(key)
        if not payload:
            return None
        ts, value = payload
        if now - ts > _CACHE_TTL_SECONDS:
            _fundamentals_cache.pop(key, None)
            return None
        return value


def _cache_set(key: tuple, value: Any) -> Any:
    with _cache_lock:
        if len(_fundamentals_cache) >= _CACHE_MAX_ITEMS:
            oldest_key = min(_fundamentals_cache.items(), key=lambda item: item[1][0])[0]
            _fundamentals_cache.pop(oldest_key, None)
        _fundamentals_cache[key] = (time.time(), value)
    return value


def _safe_float(value: Any) -> float | None:
    """Coerce to float, returning None for missing/NaN/inf values."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _round(value: Any, digits: int = 4) -> float | None:
    f = _safe_float(value)
    if f is None:
        return None
    return round(f, digits)


def _get_info(yf_ticker: str) -> dict[str, Any]:
    """Fetch ``.info`` for a ticker, raising a consistent error on failure."""
    try:
        t = yf.Ticker(yf_ticker)
        info = t.info
    except Exception as exc:  # noqa: BLE001
        raise LeveragedMarketError(f"yfinance .info failed for {yf_ticker}: {exc}") from exc
    if not isinstance(info, dict) or not info:
        raise LeveragedMarketError(f"No fundamentals available for {yf_ticker}")
    return info


# ── Public API ──────────────────────────────────────────────────────────────


def get_fundamentals_snapshot(ticker: str) -> dict[str, Any]:
    """Company facts plus a curated set of valuation, profitability, growth,
    and balance-sheet-health metrics from a single ``.info`` call.
    """
    yf_ticker = to_yfinance_ticker(ticker)
    key = (yf_ticker, "snapshot")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    info = _get_info(yf_ticker)

    market_cap = _safe_float(info.get("marketCap"))
    free_cashflow = _safe_float(info.get("freeCashflow"))
    fcf_yield = None
    if free_cashflow is not None and market_cap is not None and market_cap > 0:
        fcf_yield = round(free_cashflow / market_cap, 4)

    payload: dict[str, Any] = {
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": yf_ticker,
        # Company facts
        "long_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency"),
        "market_cap": market_cap,
        "enterprise_value": _safe_float(info.get("enterpriseValue")),
        # Valuation ratios
        "trailing_pe": _round(info.get("trailingPE")),
        "forward_pe": _round(info.get("forwardPE")),
        "price_to_book": _round(info.get("priceToBook")),
        "price_to_sales": _round(info.get("priceToSalesTrailing12Months")),
        "enterprise_to_ebitda": _round(info.get("enterpriseToEbitda")),
        "peg_ratio": _round(info.get("pegRatio") or info.get("trailingPegRatio")),
        # Profitability margins
        "profit_margins": _round(info.get("profitMargins")),
        "gross_margins": _round(info.get("grossMargins")),
        "operating_margins": _round(info.get("operatingMargins")),
        "return_on_equity": _round(info.get("returnOnEquity")),
        "return_on_assets": _round(info.get("returnOnAssets")),
        # Balance sheet health
        "debt_to_equity": _round(info.get("debtToEquity")),
        "total_cash": _safe_float(info.get("totalCash")),
        "total_debt": _safe_float(info.get("totalDebt")),
        "free_cashflow": free_cashflow,
        "operating_cashflow": _safe_float(info.get("operatingCashflow")),
        "fcf_yield": fcf_yield,
        # Revenue + growth
        "total_revenue": _safe_float(info.get("totalRevenue")),
        "revenue_growth": _round(info.get("revenueGrowth")),
        "earnings_growth": _round(info.get("earningsGrowth")),
        # Income/risk
        "dividend_yield": _round(info.get("dividendYield")),
        "beta": _round(info.get("beta"), 3),
    }

    return _cache_set(key, payload)


def get_valuation_ratios(ticker: str) -> dict[str, Any]:
    """Focused valuation block: P/E, fwd P/E, P/B, P/S, EV/EBITDA, PEG,
    FCF yield, and earnings yield.
    """
    yf_ticker = to_yfinance_ticker(ticker)
    key = (yf_ticker, "valuation")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    info = _get_info(yf_ticker)

    market_cap = _safe_float(info.get("marketCap"))
    free_cashflow = _safe_float(info.get("freeCashflow"))
    fcf_yield = None
    if free_cashflow is not None and market_cap is not None and market_cap > 0:
        fcf_yield = round(free_cashflow / market_cap, 4)

    # Earnings yield = inverse of trailing P/E (E/P).
    trailing_pe = _safe_float(info.get("trailingPE"))
    earnings_yield = None
    if trailing_pe is not None and trailing_pe != 0:
        earnings_yield = round(1.0 / trailing_pe, 4)

    payload = {
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": yf_ticker,
        "long_name": info.get("longName") or info.get("shortName"),
        "currency": info.get("currency"),
        "market_cap": market_cap,
        "trailing_pe": _round(info.get("trailingPE")),
        "forward_pe": _round(info.get("forwardPE")),
        "price_to_book": _round(info.get("priceToBook")),
        "price_to_sales": _round(info.get("priceToSalesTrailing12Months")),
        "enterprise_to_ebitda": _round(info.get("enterpriseToEbitda")),
        "peg_ratio": _round(info.get("pegRatio") or info.get("trailingPegRatio")),
        "fcf_yield": fcf_yield,
        "earnings_yield": earnings_yield,
    }

    return _cache_set(key, payload)


# Statement → (yfinance attribute by period) + the line items worth keeping.
_STATEMENT_ATTRS: dict[str, tuple[str, str]] = {
    "income": ("income_stmt", "quarterly_income_stmt"),
    "balance": ("balance_sheet", "quarterly_balance_sheet"),
    "cashflow": ("cashflow", "quarterly_cashflow"),
}

_STATEMENT_LINE_ITEMS: dict[str, list[str]] = {
    "income": [
        "Total Revenue",
        "Cost Of Revenue",
        "Gross Profit",
        "Operating Expense",
        "Operating Income",
        "Net Income",
        "Net Income Common Stockholders",
        "EBITDA",
        "EBIT",
        "Basic EPS",
        "Diluted EPS",
        "Research And Development",
    ],
    "balance": [
        "Total Assets",
        "Total Liabilities Net Minority Interest",
        "Total Equity Gross Minority Interest",
        "Stockholders Equity",
        "Cash And Cash Equivalents",
        "Total Debt",
        "Long Term Debt",
        "Current Assets",
        "Current Liabilities",
        "Working Capital",
        "Retained Earnings",
    ],
    "cashflow": [
        "Operating Cash Flow",
        "Investing Cash Flow",
        "Financing Cash Flow",
        "Free Cash Flow",
        "Capital Expenditure",
        "Repurchase Of Capital Stock",
        "Cash Dividends Paid",
        "Net Income From Continuing Operations",
        "Changes In Cash",
    ],
}


def get_financial_statements(
    ticker: str,
    statement: str = "income",
    period: str = "annual",
    limit: int = 4,
) -> dict[str, Any]:
    """Return the most recent ``limit`` periods of a financial statement.

    Args:
        statement: one of {income, balance, cashflow}.
        period: one of {annual, quarterly}.
        limit: number of most-recent periods to include (default 4).

    Each period is a dict ``{period_end: <date str>, <line_item>: value, ...}``
    keeping only the most useful line items (not the entire frame).
    """
    statement = (statement or "income").strip().lower()
    period = (period or "annual").strip().lower()
    if statement not in _STATEMENT_ATTRS:
        raise LeveragedMarketError(
            f"unknown statement {statement!r}; expected one of "
            f"{sorted(_STATEMENT_ATTRS)}"
        )
    if period not in ("annual", "quarterly"):
        raise LeveragedMarketError(
            f"unknown period {period!r}; expected 'annual' or 'quarterly'"
        )
    try:
        limit = max(1, min(int(limit), 8))
    except (TypeError, ValueError):
        limit = 4

    yf_ticker = to_yfinance_ticker(ticker)
    key = (yf_ticker, "statements", statement, period, limit)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    annual_attr, quarterly_attr = _STATEMENT_ATTRS[statement]
    attr = annual_attr if period == "annual" else quarterly_attr

    try:
        t = yf.Ticker(yf_ticker)
        frame = getattr(t, attr)
    except Exception as exc:  # noqa: BLE001
        raise LeveragedMarketError(
            f"yfinance .{attr} failed for {yf_ticker}: {exc}"
        ) from exc

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise LeveragedMarketError(
            f"No {statement} {period} statement for {ticker} ({yf_ticker})"
        )

    # Columns are period dates (most recent first); rows are line items.
    columns = list(frame.columns)[:limit]
    wanted = _STATEMENT_LINE_ITEMS[statement]
    index_set = set(frame.index)

    periods: list[dict[str, Any]] = []
    for col in columns:
        try:
            period_end = pd.to_datetime(col).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            period_end = str(col)
        row: dict[str, Any] = {"period_end": period_end}
        for item in wanted:
            if item not in index_set:
                continue
            raw = frame.loc[item, col]
            value = _safe_float(raw)
            if value is not None:
                row[item] = value
        periods.append(row)

    payload = {
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": yf_ticker,
        "statement": statement,
        "period": period,
        "count": len(periods),
        "periods": periods,
    }

    return _cache_set(key, payload)


def get_earnings_calendar(ticker: str) -> dict[str, Any]:
    """Next earnings date (if any) plus the last few reported-vs-estimate rows."""
    yf_ticker = to_yfinance_ticker(ticker)
    key = (yf_ticker, "earnings")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        t = yf.Ticker(yf_ticker)
        frame = t.get_earnings_dates(limit=12)
    except Exception as exc:  # noqa: BLE001
        raise LeveragedMarketError(
            f"yfinance get_earnings_dates failed for {yf_ticker}: {exc}"
        ) from exc

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise LeveragedMarketError(
            f"No earnings calendar for {ticker} ({yf_ticker})"
        )

    now = pd.Timestamp.now(tz=getattr(frame.index, "tz", None))

    # Column names vary slightly across yfinance versions.
    def _col(*candidates: str) -> str | None:
        for c in candidates:
            if c in frame.columns:
                return c
        return None

    eps_est_col = _col("EPS Estimate")
    eps_rep_col = _col("Reported EPS")
    surprise_col = _col("Surprise(%)", "Surprise (%)")

    rows: list[dict[str, Any]] = []
    next_earnings_date: str | None = None
    for idx, record in frame.iterrows():
        try:
            dt = pd.to_datetime(idx)
            date_str = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            dt = None
            date_str = str(idx)
        is_future = bool(dt is not None and dt >= now)
        if is_future:
            # Earliest future date is the next earnings date.
            next_earnings_date = date_str
        rows.append(
            {
                "date": date_str,
                "is_future": is_future,
                "eps_estimate": _safe_float(record.get(eps_est_col)) if eps_est_col else None,
                "reported_eps": _safe_float(record.get(eps_rep_col)) if eps_rep_col else None,
                "surprise_pct": _safe_float(record.get(surprise_col)) if surprise_col else None,
            }
        )

    # Index is most-recent-first; the closest future date is the smallest future one.
    future_dates = [r["date"] for r in rows if r["is_future"]]
    if future_dates:
        next_earnings_date = min(future_dates)

    # Keep the most recent few reported rows (past) for context.
    reported = [r for r in rows if not r["is_future"]][:6]

    payload = {
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": yf_ticker,
        "next_earnings_date": next_earnings_date,
        "reported": reported,
    }

    return _cache_set(key, payload)
