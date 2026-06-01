"""Market-intelligence MCP server — news (Finnhub) + macro (FRED).

Gives Archie continuous-information tools so it can watch the world for Josh:
company/market news, a macro snapshot, and earnings timing. Keys are read from
the env or ConfigStore by app.services.intel_service (keyless-degradable).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.services import intel_service as intel

# ── Logging (file-based — stdout is reserved for MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "mypf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("intel-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "intel.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "intel",
    instructions=(
        "Market-intelligence tools: recent company news (get_company_news), general "
        "market/world news (get_market_news), a macro snapshot of key rates/FX/VIX "
        "(get_macro_snapshot), and earnings timing + surprise history (get_earnings). "
        "Use these to surface what's happening with the user's holdings and the wider "
        "market. CURATE — read many items and report only what's material; never dump "
        "raw headline lists."
    ),
)


def _fmt(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def get_company_news(ticker: str, since_days: int = 3, limit: int = 20) -> str:
    """Recent news for a single ticker (newest first).

    Args:
        ticker: stock symbol, e.g. "NVDA" or "PLTR".
        since_days: look back this many days (default 3).
        limit: max items to return (default 20).
    """
    try:
        items = intel.get_company_news(ticker, since_days=since_days, limit=limit)
        return _fmt({"ok": True, "ticker": ticker.upper(), "count": len(items), "items": items})
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_company_news error: %s", exc)
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def get_market_news(limit: int = 20) -> str:
    """General market / world financial news (newest first).

    Args:
        limit: max items to return (default 20).
    """
    try:
        items = intel.get_market_news(limit=limit)
        return _fmt({"ok": True, "count": len(items), "items": items})
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_market_news error: %s", exc)
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def get_macro_snapshot() -> str:
    """Latest values + recent change for key macro series: US 10Y/2Y yields,
    the 10Y-2Y spread, VIX, the Fed funds rate, and USD/GBP (FRED)."""
    try:
        return _fmt({"ok": True, **intel.get_macro_snapshot()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_macro_snapshot error: %s", exc)
        return _fmt({"ok": False, "error": str(exc)})


@mcp.tool()
def get_earnings(ticker: str) -> str:
    """Next earnings date (forward 90 days) + recent surprise history for a ticker.

    Args:
        ticker: stock symbol, e.g. "NVDA".
    """
    try:
        return _fmt({"ok": True, **intel.get_earnings(ticker)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_earnings error: %s", exc)
        return _fmt({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    mcp.run()
