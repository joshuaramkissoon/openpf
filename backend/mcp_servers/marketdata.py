"""yfinance MCP server for Archie leveraged analysis."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd
from mcp.server.fastmcp import FastMCP

from app.quant import risk as qrisk
from app.quant.indicators import atr, bollinger_bands, ema, macd as macd_fn, rsi, sma
from app.quant.portfolio import correlation_matrix as corr_matrix
from app.services.leveraged_market import (
    LeveragedMarketError,
    _download_history_frame,
    get_price,
    get_price_history,
    get_technicals,
    to_yfinance_ticker,
)

# ── Logging (file-based — stdout is reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "mypf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("marketdata-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "marketdata.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "marketdata",
    instructions=(
        "Market data tools powered by yfinance. "
        "Use for price snapshots, candles, and technical indicators."
    ),
)


def _fmt(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def get_price_snapshot(ticker: str) -> str:
    """Get last price and daily change for a ticker.

    Args:
        ticker: e.g. PLTR, NVDA, SPY, QQQ, 3USL, 3PLT
    """
    logger.info("get_price_snapshot ticker=%s", ticker)
    try:
        data = get_price(ticker)
    except LeveragedMarketError as exc:
        logger.warning("get_price_snapshot failed ticker=%s: %s", ticker, exc)
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker})
    return _fmt({"ok": True, **data})


@mcp.tool()
def get_price_history_rows(ticker: str, period: str = "3mo", interval: str = "1d") -> str:
    """Get OHLCV history for a ticker.

    Args:
        ticker: Symbol or T212 code.
        period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y
        interval: 1m, 5m, 15m, 1h, 1d, 1wk
    """
    logger.info("get_price_history_rows ticker=%s period=%s interval=%s", ticker, period, interval)
    try:
        rows = get_price_history(ticker, period=period, interval=interval)
    except LeveragedMarketError as exc:
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker, "period": period, "interval": interval})

    return _fmt(
        {
            "ok": True,
            "ticker": ticker,
            "yfinance_ticker": to_yfinance_ticker(ticker),
            "period": period,
            "interval": interval,
            "count": len(rows),
            "items": rows,
        }
    )


@mcp.tool()
def get_technical_snapshot(ticker: str, period: str = "6mo") -> str:
    """Get RSI/SMA/MACD/Bollinger/ATR for a ticker.

    Args:
        ticker: Symbol or T212 code.
        period: Lookback period for technical calculations.
    """
    logger.info("get_technical_snapshot ticker=%s period=%s", ticker, period)
    try:
        data = get_technicals(ticker, period=period)
    except LeveragedMarketError as exc:
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker, "period": period})

    return _fmt({"ok": True, **data})


# ── Quant helpers (shared by the analysis tools below) ──────────────────────


def _close_series(ticker: str, period: str) -> pd.Series:
    """Daily close prices for `ticker` over `period`, indexed by date.

    Indexing by date lets pandas align multiple tickers (for correlation)
    even when their histories differ in length.
    """
    rows = get_price_history(ticker, period=period, interval="1d")
    idx = pd.to_datetime([r["date"] for r in rows])
    series = pd.Series([float(r["close"]) for r in rows], index=idx, dtype="float64")
    series.name = ticker.upper().strip()
    return series


def _annualized_return(closes: pd.Series, observations: int) -> float:
    if closes.empty or float(closes.iloc[0]) == 0 or observations <= 0:
        return 0.0
    total = float(closes.iloc[-1]) / float(closes.iloc[0]) - 1.0
    if total <= -1.0:
        return -1.0
    return float((1.0 + total) ** (252.0 / observations) - 1.0)


@mcp.tool()
def get_risk_metrics(ticker: str, period: str = "1y") -> str:
    """Annualized risk/return metrics for a ticker.

    Returns volatility, max drawdown, Sharpe, Sortino, 95% daily VaR, and
    total + annualized return over the lookback window.

    Args:
        ticker: Symbol or T212 code.
        period: 1mo, 3mo, 6mo, 1y, 2y, 5y
    """
    logger.info("get_risk_metrics ticker=%s period=%s", ticker, period)
    try:
        closes = _close_series(ticker, period)
    except LeveragedMarketError as exc:
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker, "period": period})
    if len(closes) < 5:
        return _fmt({"ok": False, "error": "insufficient history", "ticker": ticker, "period": period})

    rets = closes.pct_change().dropna()
    total_return = float(closes.iloc[-1] / closes.iloc[0] - 1.0) if float(closes.iloc[0]) else 0.0
    return _fmt({
        "ok": True,
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": to_yfinance_ticker(ticker),
        "period": period,
        "observations": int(len(rets)),
        "last_close": round(float(closes.iloc[-1]), 4),
        "total_return": round(total_return, 4),
        "annualized_return": round(_annualized_return(closes, len(rets)), 4),
        "annualized_volatility": round(qrisk.annualized_volatility(rets), 4),
        "max_drawdown": round(qrisk.max_drawdown(closes), 4),
        "sharpe_ratio": round(qrisk.sharpe_ratio(rets), 3),
        "sortino_ratio": round(qrisk.sortino_ratio(rets), 3),
        "value_at_risk_95_daily": round(qrisk.value_at_risk(rets, 0.95), 4),
    })


@mcp.tool()
def get_correlation_matrix(tickers: str, period: str = "6mo") -> str:
    """Correlation matrix of daily returns across 2+ tickers.

    Args:
        tickers: Comma-separated symbols, e.g. "PLTR,NVDA,SPY".
        period: Lookback period.
    """
    logger.info("get_correlation_matrix tickers=%s period=%s", tickers, period)
    syms = [t.strip() for t in tickers.split(",") if t.strip()]
    if len(syms) < 2:
        return _fmt({"ok": False, "error": "provide at least 2 tickers", "tickers": syms})

    series: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}
    for sym in syms:
        try:
            series[sym.upper()] = _close_series(sym, period).pct_change().dropna()
        except LeveragedMarketError as exc:
            errors[sym] = str(exc)
    if len(series) < 2:
        return _fmt({"ok": False, "error": "insufficient data for correlation", "errors": errors})

    return _fmt({"ok": True, "period": period, **corr_matrix(series), "errors": errors or None})


@mcp.tool()
def compare_assets(tickers: str, period: str = "6mo") -> str:
    """Side-by-side return/risk comparison across tickers.

    Per asset: total return, annualized volatility, Sharpe, max drawdown,
    ~1M and ~3M momentum, and latest RSI(14).

    Args:
        tickers: Comma-separated symbols.
        period: Lookback period.
    """
    logger.info("compare_assets tickers=%s period=%s", tickers, period)
    syms = [t.strip() for t in tickers.split(",") if t.strip()]
    if not syms:
        return _fmt({"ok": False, "error": "no tickers provided"})

    assets: list[dict] = []
    for sym in syms:
        try:
            closes = _close_series(sym, period)
        except LeveragedMarketError as exc:
            assets.append({"ticker": sym.upper(), "ok": False, "error": str(exc)})
            continue
        if len(closes) < 5:
            assets.append({"ticker": sym.upper(), "ok": False, "error": "insufficient history"})
            continue

        rets = closes.pct_change().dropna()
        last = float(closes.iloc[-1])

        def momentum(n: int) -> float | None:
            return float(last / closes.iloc[-n] - 1.0) if len(closes) > n and float(closes.iloc[-n]) else None

        rsi_series = rsi(closes).dropna()
        latest_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else None
        mom_1m = momentum(21)
        mom_3m = momentum(63)
        assets.append({
            "ticker": sym.upper(),
            "ok": True,
            "last_close": round(last, 4),
            "total_return": round(float(closes.iloc[-1] / closes.iloc[0] - 1.0), 4) if float(closes.iloc[0]) else None,
            "annualized_volatility": round(qrisk.annualized_volatility(rets), 4),
            "sharpe_ratio": round(qrisk.sharpe_ratio(rets), 3),
            "max_drawdown": round(qrisk.max_drawdown(closes), 4),
            "momentum_1m": round(mom_1m, 4) if mom_1m is not None else None,
            "momentum_3m": round(mom_3m, 4) if mom_3m is not None else None,
            "rsi_14": round(latest_rsi, 1) if latest_rsi is not None else None,
        })

    return _fmt({"ok": True, "period": period, "assets": assets})


@mcp.tool()
def get_indicator_series(ticker: str, indicator: str, period: str = "6mo") -> str:
    """Full time series for one technical indicator (not just the latest value).

    Use this to reason about an indicator's trajectory over time.

    Args:
        ticker: Symbol or T212 code.
        indicator: sma20, sma50, sma200, ema20, rsi, macd, atr, or bollinger.
        period: Lookback period.
    """
    ind = indicator.strip().lower()
    logger.info("get_indicator_series ticker=%s indicator=%s period=%s", ticker, ind, period)
    try:
        df = _download_history_frame(ticker, period, "1d")
    except LeveragedMarketError as exc:
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker, "indicator": ind})

    close = df["close"].astype(float).reset_index(drop=True)
    high = df["high"].astype(float).reset_index(drop=True)
    low = df["low"].astype(float).reset_index(drop=True)
    dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").tolist()

    def to_points(values: pd.Series) -> list[dict]:
        return [
            {"date": d, "value": (round(float(v), 4) if pd.notna(v) else None)}
            for d, v in zip(dates, values)
        ]

    single = {
        "sma20": lambda: sma(close, 20),
        "sma50": lambda: sma(close, 50),
        "sma200": lambda: sma(close, 200),
        "ema20": lambda: ema(close, 20),
        "rsi": lambda: rsi(close),
        "atr": lambda: atr(high, low, close),
    }

    if ind in single:
        return _fmt({"ok": True, "ticker": ticker.upper().strip(), "indicator": ind, "period": period,
                     "series": to_points(single[ind]())})
    if ind == "macd":
        macd_line, signal_line, hist = macd_fn(close)
        return _fmt({"ok": True, "ticker": ticker.upper().strip(), "indicator": "macd", "period": period,
                     "macd": to_points(macd_line), "signal": to_points(signal_line), "histogram": to_points(hist)})
    if ind in ("bollinger", "bbands"):
        upper, middle, lower = bollinger_bands(close)
        return _fmt({"ok": True, "ticker": ticker.upper().strip(), "indicator": "bollinger", "period": period,
                     "upper": to_points(upper), "middle": to_points(middle), "lower": to_points(lower)})

    return _fmt({"ok": False, "error": f"unknown indicator {ind!r}",
                 "supported": ["sma20", "sma50", "sma200", "ema20", "rsi", "macd", "atr", "bollinger"]})


if __name__ == "__main__":
    mcp.run()
