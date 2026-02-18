"""yfinance MCP server for Archie leveraged analysis."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.services.leveraged_market import (
    LeveragedMarketError,
    get_price,
    get_price_history,
    get_technicals,
    to_yfinance_ticker,
)

# ── Logging (file-based — stdout is reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR", "/app/logs"))
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


if __name__ == "__main__":
    mcp.run()
