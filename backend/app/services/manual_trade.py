"""A trade the user places by hand from the stock sheet.

Not a proposal: the user is the decision-maker, so there is nothing to wait
for. It still goes through the single gate: a TradeIntent with source "user"
is recorded, approved *as the user* (``approved_by="user"``) and handed to
``execution_service.execute_intent``, which re-checks every rail. Agents can't
reach this: there is no MCP tool for it, and the agent's Bash/WebFetch hooks
block the app's own API (see ``claude_sdk_config``).

``preview`` sizes and rail-checks the order for the review step without
recording anything.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services import execution_service, proposal_service
from app.services.config_store import ConfigStore

ACCOUNTS = ("invest", "stocks_isa")
# Trading 212 charges no commission on shares; non-GBP instruments pay a 0.15% FX fee each way.
FX_FEE_RATE = 0.0015
SOURCE = "user"
ACTOR = "user"


class ManualTradeError(ValueError):
    """Refused before anything was recorded or sent."""

    def __init__(self, message: str, *, violations: list[str] | None = None, code: str = "refused") -> None:
        super().__init__(message)
        self.violations = violations or []
        self.code = code


def broker_mode(db: Session) -> str:
    """'live', 'paper', 'demo' or 'sandbox' — what the user's confirm step depends on."""
    broker = ConfigStore(db).get_broker()
    env = str(broker.get("t212_base_env") or "")
    if env == "sandbox":
        return "sandbox"
    if str(broker.get("broker_mode") or "paper") == "paper":
        return "paper"
    return "demo" if env == "demo" else "live"


def _fees(sizing: dict[str, Any]) -> dict[str, Any]:
    ccy = str(sizing.get("currency") or "").upper()
    fx = ccy not in ("GBP", "GBX", "")
    notional = float(sizing.get("estimated_notional_gbp") or 0.0)
    return {"commission_gbp": 0.0, "fx_fee_rate": FX_FEE_RATE if fx else 0.0, "fx_fee_gbp": round(notional * FX_FEE_RATE, 2) if fx else 0.0}


def _label(ident: dict[str, Any]) -> str:
    return str(ident.get("display_ticker") or ident.get("ticker") or ident.get("sym") or "")


def _check(side: str, account_kind: str, notional_gbp: float) -> tuple[str, str]:
    side = (side or "").strip().lower()
    account_kind = (account_kind or "").strip().lower()
    if side not in ("buy", "sell"):
        raise ManualTradeError("side must be 'buy' or 'sell'")
    if account_kind not in ACCOUNTS:
        raise ManualTradeError("account must be 'invest' or 'stocks_isa'")
    if not notional_gbp or notional_gbp <= 0:
        raise ManualTradeError("amount must be more than zero")
    return side, account_kind


def preview(db: Session, ident: dict[str, Any], *, side: str, account_kind: str, notional_gbp: float) -> dict[str, Any]:
    """Size + rails for the review step. Records nothing."""
    side, account_kind = _check(side, account_kind, notional_gbp)
    res = proposal_service.propose_trade(
        db, side=side, account_kind=account_kind, rationale="manual trade (preview)",
        symbol=None if ident.get("instrument_code") else _label(ident), instrument_code=ident.get("instrument_code"),
        notional_gbp=notional_gbp, source=SOURCE, dry_run=True,
    )
    out: dict[str, Any] = {"ok": bool(res.get("ok")), "broker_mode": broker_mode(db), "caps": res.get("caps")}
    if res.get("sizing"):
        out["sizing"] = res["sizing"]
        out["fees"] = _fees(res["sizing"])
    if res.get("error"):
        out["error"] = res["error"]
    # Not enough cash is only a warning on a proposal (cash may land before it's
    # approved); a trade placed now would be refused, so it blocks here.
    warnings = res.get("warnings") or []
    out["violations"] = (res.get("violations") or []) + [w for w in warnings if "insufficient available cash" in w]
    out["warnings"] = [w for w in warnings if "insufficient available cash" not in w]
    out["ok"] = out["ok"] and not out["violations"]
    return out


def place(
    db: Session,
    ident: dict[str, Any],
    *,
    side: str,
    account_kind: str,
    notional_gbp: float,
    live_confirmed: bool = False,
    note: str = "",
) -> dict[str, Any]:
    """Record → approve as the user → execute. Raises ManualTradeError when refused
    before anything was recorded; ExecutionError/T212Error from the gate otherwise
    (the intent is then closed so it never lingers in Decisions)."""
    side, account_kind = _check(side, account_kind, notional_gbp)
    mode = broker_mode(db)
    if mode == "live" and not live_confirmed:
        raise ManualTradeError("confirm the live order first", code="needs_live_confirmation")

    rationale = note.strip() or f"Placed by hand from the {_label(ident)} sheet."
    res = proposal_service.propose_trade(
        db, side=side, account_kind=account_kind, rationale=rationale,
        symbol=None if ident.get("instrument_code") else _label(ident), instrument_code=ident.get("instrument_code"),
        notional_gbp=notional_gbp, source=SOURCE,
    )
    if not res.get("ok"):
        raise ManualTradeError(res.get("error") or "the order breaks a rail", violations=res.get("violations") or [])

    intent_id = res["intent"]["id"]
    execution_service.approve_intent(db, intent_id, note="placed by hand from the stock sheet", actor=ACTOR)
    try:
        intent = execution_service.execute_intent(db, intent_id, account_kind=account_kind, approved_via="stock sheet")
    except Exception:
        # A refused manual order must not wait in Decisions as an approved proposal.
        try:
            execution_service.reject_intent(db, intent_id, note="manual trade not placed")
        except execution_service.ExecutionError:
            pass  # already terminal (failed)
        raise
    return {
        "ok": True,
        "intent": proposal_service.serialize_intent(intent),
        "fill_price": intent.execution_price,
        "broker_mode": intent.broker_mode,
        "fees": _fees({"currency": (intent.meta or {}).get("currency"), "estimated_notional_gbp": intent.estimated_notional}),
    }
