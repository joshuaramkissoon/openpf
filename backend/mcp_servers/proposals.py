"""Trade-proposal MCP server for Archie.

The agent's ONLY trade-related write: ``propose_trade`` records a ``proposed``
TradeIntent for a human to approve in the app. Nothing here can place, modify
or cancel a broker order — execution happens only through the app's
approval-gated ``execution_service.execute_intent``. Backed by the app's
database (DATABASE_URL in the env).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.services import proposal_service

# ── Logging (file-based — stdout is reserved for the MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("proposals-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "proposals.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "proposals",
    instructions=(
        "Trade proposals. You cannot place or cancel orders. To suggest a trade, call "
        "propose_trade: it validates the instrument, sizes the order, checks the risk "
        "rails and queues it for the user to approve. If it returns violations, resize "
        "within the caps and try again rather than asking the user to loosen them."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def propose_trade(
    side: str,
    account_kind: str,
    rationale: str,
    symbol: str = "",
    instrument_code: str = "",
    quantity: float = 0.0,
    notional_gbp: float = 0.0,
    order_type: str = "market",
    limit_price: float = 0.0,
    stop_price: float = 0.0,
    play_id: str = "",
    source: str = "chat",
) -> str:
    """Queue a trade proposal for the user to approve. Does NOT execute anything.

    Args:
        side: 'buy' or 'sell'.
        account_kind: 'invest' or 'stocks_isa' — required, never assumed.
        rationale: Why, plus what would invalidate the idea.
        symbol: Ticker (e.g. 'AAPL'); or pass instrument_code instead.
        instrument_code: Exact Trading 212 code (e.g. 'AAPL_US_EQ'), preferred when known.
        quantity: Shares (fractional ok). Pass this OR notional_gbp, not both.
        notional_gbp: GBP amount to size the order from the latest price.
        order_type: market | limit | stop | stop_limit.
        limit_price: Required for limit / stop_limit (instrument's quote currency).
        stop_price: Required for stop / stop_limit (instrument's quote currency).
        play_id: Optional id of the Play this proposal belongs to.
        source: Free-form origin tag (e.g. 'chat', 'scheduled:<task>').
    """
    logger.info("propose_trade side=%s symbol=%s code=%s account=%s", side, symbol, instrument_code, account_kind)
    init_db()
    db = SessionLocal()
    try:
        result = proposal_service.propose_trade(
            db,
            side=side,
            account_kind=account_kind,
            rationale=rationale,
            symbol=symbol or None,
            instrument_code=instrument_code or None,
            quantity=quantity or None,
            notional_gbp=notional_gbp or None,
            order_type=order_type,
            limit_price=limit_price or None,
            stop_price=stop_price or None,
            play_id=play_id or None,
            source=source,
        )
        return _fmt(result)
    except Exception as exc:  # noqa: BLE001 — surface as a tool error payload
        logger.exception("propose_trade failed")
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def list_proposals(status: str = "all", limit: int = 20) -> str:
    """Recent trade intents and their status (proposed, approved, executed, rejected, expired, failed).

    Args:
        status: Filter by status, or 'all' (default).
        limit: Max rows (default 20, max 100).
    """
    init_db()
    db = SessionLocal()
    try:
        return _fmt({"ok": True, "intents": proposal_service.list_proposals(db, limit=limit, status=status)})
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
