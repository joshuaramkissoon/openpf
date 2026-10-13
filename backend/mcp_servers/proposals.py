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
        "rails and queues it for the user to approve. For two or more orders at once "
        "('buy NVDA, PLTR and MU'), call propose_trades ONCE with every order: the cards "
        "appear together and the daily limit is counted across the batch. When an ask is over the per-order or "
        "daily limit it proposes the part that fits and holds back the rest on a card the "
        "user can stage for tomorrow or take to their limits — never loosen limits, and "
        "don't re-propose the held-back part yourself. Write every rationale to the user "
        "('you', 'your book'), never 'the user'. "
        "get_track_record shows how your past proposals actually did — cite it honestly, "
        "with its sample size. suggest_permission can ASK the user for a standing permission "
        "when a category's record is good; it grants nothing."
    ),
)


# 'play' and 'rebalance' tags are set only by the app's own play staging and
# rebalancer (standing permissions key off them); 'user' only by the user's own
# trades from the stock sheet (which may place past the limits). Never by an agent.
_RESERVED_SOURCES = {"play", "rebalance", "user"}


def _source(source: str) -> str:
    s = (source or "").strip()
    return "agent" if not s or s.lower() in _RESERVED_SOURCES else s


def _day(for_day: str):
    from datetime import timedelta

    from app.services.execution_service import utc_today

    return utc_today() + timedelta(days=1) if (for_day or "").strip().lower() == "tomorrow" else None


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
    category: str = "",
    for_day: str = "today",
    hold_back_over_limit: bool = True,
) -> str:
    """Queue a trade proposal for the user to approve. Does NOT execute anything.

    Args:
        side: 'buy' or 'sell'.
        account_kind: 'invest', 'stocks_isa' or 'crypto' — required, never assumed. 'crypto' routes
            to the connected exchange (symbol 'BTC', 'ETH', …; market orders sized by notional_gbp;
            the result carries fee + slippage estimates and the crypto cap is enforced).
            A connected account's key also works ('crypto:binance', 'ibkr:u1234567', 'snap:…' —
            see the portfolio's accounts). Read-only accounts get a suggestion marked
            "place it in <broker>": OpenPF never executes those; the user does, in that broker.
        rationale: Why, plus what would invalidate the idea. Shown on the card the user approves:
            write it TO the user ("you", "your ISA"), never "the user's"; one or two short sentences.
        symbol: Ticker (e.g. 'AAPL'); or pass instrument_code instead.
        instrument_code: Exact Trading 212 code (e.g. 'AAPL_US_EQ'), preferred when known.
        quantity: Shares (fractional ok). Pass this OR notional_gbp, not both.
        notional_gbp: GBP amount to size the order from the latest price.
        order_type: market | limit | stop | stop_limit.
        limit_price: Required for limit / stop_limit (instrument's quote currency).
        stop_price: Required for stop / stop_limit (instrument's quote currency).
        play_id: Which play this proposal is for — a Play id, or "core" for long-term / no-thesis
            holdings. Always say: proposals with neither are flagged "not part of any play".
        source: Free-form origin tag (e.g. 'chat', 'scheduled:<task>').
        category: What kind of call this is, for your track record: deploy_cash | rebalance |
            play_tranche | news_reaction | trim_derisk | other. Inferred when omitted.
        for_day: 'today' (default) or 'tomorrow' — stage it for tomorrow's limit window (only
            when the user asks for that; it can't be placed before tomorrow and counts against
            tomorrow's daily limit).
        hold_back_over_limit: True (default): if the order is over the per-order or daily limit
            (today's open proposals count toward the daily one), propose the part that fits and
            hold back the rest — the result's ``held_back`` and ``blocked`` say what and which
            limit, and the chat shows the held-back part as a card. False: just report the
            violations so you can resize yourself.
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
            source=_source(source),
            category=category or None,
            not_before=_day(for_day),
            hold_back=bool(hold_back_over_limit),
        )
        return _fmt(result)
    except Exception as exc:  # noqa: BLE001 — surface as a tool error payload
        logger.exception("propose_trade failed")
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def propose_trades(orders: list[dict[str, Any]], source: str = "chat", hold_back_over_limit: bool = True) -> str:
    """Queue SEVERAL trade proposals in one call: use this whenever the user asks for two or more
    orders at once ("buy 500 NVDA, 1000 PLTR and 400 BTC"). Does NOT execute anything. The orders are
    checked together (each counts the earlier ones against the daily limit) and every result comes
    back at once, so the user sees all the cards together instead of one by one.

    Args:
        orders: One object per order, with the same fields as propose_trade: side, account_kind,
            rationale, and symbol or instrument_code, and quantity or notional_gbp; optionally
            order_type, limit_price, stop_price, play_id, category, for_day ('today' | 'tomorrow').
            Rationale goes on the card: address the user ("you"), never "the user". Max 12.
        source: Free-form origin tag (e.g. 'chat', 'scheduled:<task>').
        hold_back_over_limit: As in propose_trade, per order: the part over a limit is held back on
            its own card; say which limit cut it in one line, and don't re-propose it.
    """
    logger.info("propose_trades n=%s", len(orders) if isinstance(orders, list) else "?")
    init_db()
    db = SessionLocal()
    try:
        days = [_day(str((o or {}).get("for_day") or "")) if isinstance(o, dict) else None for o in (orders or [])]
        result = proposal_service.propose_batch(
            db, orders, source=_source(source), hold_back=bool(hold_back_over_limit), not_before_by_order=days,
        )
        return _fmt(result)
    except Exception as exc:  # noqa: BLE001 — surface as a tool error payload
        logger.exception("propose_trades failed")
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


@mcp.tool()
def get_track_record() -> str:
    """How your past proposals actually did, tracked forward from each proposal's reference
    price at 1d / 1w / 1m / 3m (and at close) against the benchmark — including the ones the
    user rejected or let expire ("if you'd taken it"). Per-category hit rates, "following Archie
    vs what you did", and the most recent items. Always quote numbers with their sample size."""
    init_db()
    db = SessionLocal()
    try:
        from app.services.track_record_service import for_agent

        return _fmt({"ok": True, **for_agent(db)})
    finally:
        db.close()


@mcp.tool()
def suggest_permission(kind: str, per_trade_cap_gbp: float = 0.0, note: str = "") -> str:
    """ASK the user for a standing permission in one category. This only records a suggestion
    card for the user to accept or dismiss in the app — it grants nothing, and you cannot grant,
    widen or enable permissions yourself. Refused unless the category's track record supports it.

    Args:
        kind: play_tranche | rebalance | deploy_cash.
        per_trade_cap_gbp: The per-trade cap you'd suggest (defaults from the record; never above
            the user's single-order rail).
        note: One or two sentences in your own words on why.
    """
    init_db()
    db = SessionLocal()
    try:
        from app.services.permissions_service import record_suggestion

        return _fmt(record_suggestion(db, kind=kind, per_trade_cap_gbp=per_trade_cap_gbp or None, note=note,
                                      source="archie"))
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
