"""yfinance fundamentals MCP server for Archie valuation analysis."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.services.fundamentals_service import (
    get_earnings_calendar as _get_earnings_calendar,
    get_financial_statements as _get_financial_statements,
    get_fundamentals_snapshot,
    get_valuation_ratios,
)
from app.services.leveraged_market import LeveragedMarketError

# ── Logging (file-based — stdout is reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "mypf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("fundamentals-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "fundamentals.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "fundamentals",
    instructions=(
        "Company fundamentals tools powered by yfinance. "
        "Use for valuation ratios, profitability and growth metrics, "
        "financial statements, and the earnings calendar."
    ),
)


def _fmt(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def get_fundamentals(ticker: str) -> str:
    """Get a company fundamentals snapshot for a ticker.

    Returns company facts (name, sector, industry, currency, market cap,
    enterprise value) plus a curated set of valuation ratios (P/E, fwd P/E,
    P/B, P/S, EV/EBITDA, PEG), profitability margins (gross/operating/profit,
    ROE, ROA), balance-sheet health (debt/equity, cash, debt, free cash flow,
    FCF yield), revenue and growth, dividend yield, and beta.

    Args:
        ticker: e.g. AAPL, NVDA, MSFT, PLTR (or a T212 instrument code).
    """
    logger.info("get_fundamentals ticker=%s", ticker)
    try:
        data = get_fundamentals_snapshot(ticker)
    except LeveragedMarketError as exc:
        logger.warning("get_fundamentals failed ticker=%s: %s", ticker, exc)
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker})
    return _fmt({"ok": True, **data})


@mcp.tool()
def get_valuation(ticker: str) -> str:
    """Get a focused valuation block for a ticker.

    Returns trailing P/E, forward P/E, P/B, P/S, EV/EBITDA, PEG ratio,
    free-cash-flow yield, and earnings yield. Use this to judge whether a
    stock looks cheap or expensive relative to fundamentals.

    Args:
        ticker: e.g. AAPL, NVDA, MSFT, PLTR (or a T212 instrument code).
    """
    logger.info("get_valuation ticker=%s", ticker)
    try:
        data = get_valuation_ratios(ticker)
    except LeveragedMarketError as exc:
        logger.warning("get_valuation failed ticker=%s: %s", ticker, exc)
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker})
    return _fmt({"ok": True, **data})


@mcp.tool()
def get_financial_statements(
    ticker: str,
    statement: str = "income",
    period: str = "annual",
    limit: int = 4,
) -> str:
    """Get the most recent periods of a financial statement for a ticker.

    Returns a list of periods, each with a period_end date and the most
    useful line items for that statement.

    Args:
        ticker: e.g. AAPL, NVDA, MSFT (or a T212 instrument code).
        statement: one of "income", "balance", or "cashflow".
        period: one of "annual" or "quarterly".
        limit: number of most-recent periods to return (default 4, max 8).
    """
    logger.info(
        "get_financial_statements ticker=%s statement=%s period=%s limit=%s",
        ticker, statement, period, limit,
    )
    try:
        data = _get_financial_statements(ticker, statement=statement, period=period, limit=limit)
    except LeveragedMarketError as exc:
        logger.warning("get_financial_statements failed ticker=%s: %s", ticker, exc)
        return _fmt({
            "ok": False, "error": str(exc), "ticker": ticker,
            "statement": statement, "period": period,
        })
    return _fmt({"ok": True, **data})


@mcp.tool()
def get_earnings_calendar(ticker: str) -> str:
    """Get the earnings calendar for a ticker.

    Returns the next upcoming earnings date (if known) and the last few
    reported quarters with EPS estimate vs reported and surprise percentage.

    Args:
        ticker: e.g. AAPL, NVDA, MSFT (or a T212 instrument code).
    """
    logger.info("get_earnings_calendar ticker=%s", ticker)
    try:
        data = _get_earnings_calendar(ticker)
    except LeveragedMarketError as exc:
        logger.warning("get_earnings_calendar failed ticker=%s: %s", ticker, exc)
        return _fmt({"ok": False, "error": str(exc), "ticker": ticker})
    return _fmt({"ok": True, **data})


if __name__ == "__main__":
    mcp.run()
