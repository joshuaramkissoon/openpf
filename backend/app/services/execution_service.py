from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.entities import AccountSnapshot, ExecutionEvent, TradeIntent
from app.services.agent_skills_bridge import intent_to_skill_action
from app.services.config_store import ConfigStore
from app.services.market_data import MarketDataError, fetch_history
from app.services.t212_client import T212Error, build_t212_client


class ExecutionError(RuntimeError):
    pass


def _log_event(db: Session, intent_id: str, level: str, message: str, payload: dict | None = None) -> None:
    event = ExecutionEvent(
        intent_id=intent_id,
        level=level,
        message=message,
        payload=payload or {},
    )
    db.add(event)
    db.commit()


def _daily_executed_notional(db: Session) -> float:
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.execute(
        select(func.sum(TradeIntent.estimated_notional)).where(
            TradeIntent.status == "executed",
            TradeIntent.executed_at >= start,
        )
    ).scalar_one_or_none()
    return float(total or 0.0)


def _latest_account_cash(db: Session) -> float:
    row = db.execute(select(AccountSnapshot).order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    if not row:
        return 0.0
    return float(row.free_cash or 0.0)


def _find_duplicate(db: Session, intent: TradeIntent, window_seconds: int) -> bool:
    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
    q = select(TradeIntent).where(
        TradeIntent.created_at >= cutoff,
        TradeIntent.id != intent.id,
        TradeIntent.symbol == intent.symbol,
        TradeIntent.side == intent.side,
        TradeIntent.quantity == intent.quantity,
        TradeIntent.status.in_(["proposed", "approved", "executed"]),
    )
    return db.execute(q).scalar_one_or_none() is not None


def list_intents(db: Session, limit: int = 100) -> list[TradeIntent]:
    q = select(TradeIntent).order_by(desc(TradeIntent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def list_events(db: Session, limit: int = 300) -> list[ExecutionEvent]:
    q = select(ExecutionEvent).order_by(desc(ExecutionEvent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def approve_intent(db: Session, intent_id: str, note: str | None = None) -> TradeIntent:
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")
    if intent.status not in {"proposed", "approved"}:
        raise ExecutionError(f"Intent {intent_id} cannot be approved from status {intent.status}")

    intent.status = "approved"
    intent.approved_at = datetime.utcnow()
    if note:
        metadata = intent.meta or {}
        metadata["approval_note"] = note
        intent.meta = metadata
    db.add(intent)
    db.commit()
    db.refresh(intent)

    _log_event(db, intent.id, "info", "intent approved", {"note": note or ""})
    return intent


def reject_intent(db: Session, intent_id: str, note: str | None = None) -> TradeIntent:
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")

    intent.status = "rejected"
    if note:
        metadata = intent.meta or {}
        metadata["rejection_note"] = note
        intent.meta = metadata
    db.add(intent)
    db.commit()
    db.refresh(intent)

    _log_event(db, intent.id, "warn", "intent rejected", {"note": note or ""})
    return intent


def _paper_fill_price(symbol: str) -> float:
    try:
        history = fetch_history(symbol, lookback_days=90)
        return float(history["close"].iloc[-1])
    except (MarketDataError, IndexError, KeyError):
        return 0.0


def execute_intent(db: Session, intent_id: str, *, force_live: bool = False) -> TradeIntent:
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")

    if intent.status not in {"approved", "proposed"}:
        raise ExecutionError(f"Intent {intent_id} cannot be executed from status {intent.status}")

    config = ConfigStore(db)
    risk = config.get_risk()
    broker = config.get_broker()

    if _find_duplicate(db, intent, int(risk["duplicate_order_window_seconds"])):
        raise ExecutionError("duplicate-order-guard: similar order already exists in recent window")

    if intent.estimated_notional > float(risk["max_single_order_notional"]):
        raise ExecutionError("risk-guard: order exceeds max single-order notional")

    daily_notional = _daily_executed_notional(db)
    if daily_notional + intent.estimated_notional > float(risk["max_daily_notional"]):
        raise ExecutionError("risk-guard: order exceeds max daily notional")

    if intent.side == "buy":
        cash = _latest_account_cash(db)
        if intent.estimated_notional > cash:
            raise ExecutionError("risk-guard: insufficient available cash")

    mode = broker.get("broker_mode", "paper")
    if force_live:
        mode = "live"

    intent.broker_mode = mode
    intent.status = "executing"
    db.add(intent)
    db.commit()
    db.refresh(intent)

    try:
        if mode == "paper":
            fill_price = _paper_fill_price(intent.symbol)
            intent.execution_price = fill_price
            intent.executed_at = datetime.utcnow()
            intent.status = "executed"
            intent.broker_order_id = f"paper-{intent.id[:8]}"
            db.add(intent)
            db.commit()
            db.refresh(intent)
            _log_event(
                db,
                intent.id,
                "info",
                "paper execution completed",
                {
                    "fill_price": fill_price,
                    "estimated_notional": intent.estimated_notional,
                    "skill_action": intent_to_skill_action(intent),
                },
            )
            return intent

        account_kind = str((intent.meta or {}).get("account_kind", "invest")).strip().lower()
        if account_kind not in {"invest", "stocks_isa"}:
            account_kind = "invest"
        client = build_t212_client(config, account_kind=account_kind)  # type: ignore[arg-type]
        signed_qty = intent.quantity if intent.side == "buy" else -abs(intent.quantity)

        if intent.order_type == "limit" and intent.limit_price is not None:
            broker_resp = client.place_limit_order(intent.instrument_code, signed_qty, intent.limit_price)
        elif intent.order_type == "stop" and intent.stop_price is not None:
            broker_resp = client.place_stop_order(intent.instrument_code, signed_qty, intent.stop_price)
        elif intent.order_type == "stop_limit" and intent.stop_price is not None and intent.limit_price is not None:
            broker_resp = client.place_stop_limit_order(intent.instrument_code, signed_qty, intent.stop_price, intent.limit_price)
        else:
            broker_resp = client.place_market_order(intent.instrument_code, signed_qty)

        intent.executed_at = datetime.utcnow()
        intent.status = "executed"
        intent.broker_order_id = str(
            broker_resp.get("id")
            or broker_resp.get("orderId")
            or broker_resp.get("order", {}).get("id")
            or f"live-{intent.id[:8]}"
        )
        if intent.execution_price is None:
            intent.execution_price = float(broker_resp.get("price", 0.0) or 0.0)

        db.add(intent)
        db.commit()
        db.refresh(intent)

        _log_event(
            db,
            intent.id,
            "info",
            "live execution accepted",
            {"broker_response": broker_resp, "skill_action": intent_to_skill_action(intent)},
        )
        return intent

    except (ExecutionError, T212Error) as exc:
        intent.status = "failed"
        intent.failure_reason = str(exc)
        db.add(intent)
        db.commit()
        db.refresh(intent)
        _log_event(db, intent.id, "error", "execution failed", {"error": str(exc)})
        raise
