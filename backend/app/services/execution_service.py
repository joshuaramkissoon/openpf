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


# A 'proposed' intent older than this is auto-archived: a stale proposal no
# longer reflects current prices/conviction, so it shouldn't sit in the queue
# as something actionable.
_INTENT_TTL_HOURS = 24


_PLAY_INTENT_TTL_HOURS = 72


def _is_play_intent(intent: TradeIntent) -> bool:
    return (intent.meta or {}).get("source") == "play"


def expire_stale_intents(db: Session, ttl_hours: int = _INTENT_TTL_HOURS) -> int:
    """Mark un-acted 'proposed' intents older than the TTL as 'expired'.

    Keeps the execution queue to live, still-relevant proposals. Approved
    intents are left alone (acting on them was a deliberate decision).
    """
    cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
    stale = (
        db.execute(
            select(TradeIntent).where(
                TradeIntent.status == "proposed",
                TradeIntent.created_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    # Play tranches are deliberate, staged plans — give them longer to be acted on.
    play_cutoff = datetime.utcnow() - timedelta(hours=_PLAY_INTENT_TTL_HOURS)
    stale = [i for i in stale if not _is_play_intent(i) or i.created_at < play_cutoff]
    if not stale:
        return 0
    for intent in stale:
        intent.status = "expired"
        if not intent.failure_reason:
            intent.failure_reason = f"expired: stale proposal (> {ttl_hours}h old)"
    db.commit()
    return len(stale)


def supersede_open_proposals(db: Session) -> int:
    """Mark every un-acted 'proposed' intent as superseded.

    Called when a new agent run produces fresh proposals so the queue shows only
    the latest run, instead of stacking a duplicate batch every time the agent
    runs. Leaves 'approved'/'executed' intents untouched (those were deliberate)."""
    open_proposals = (
        db.execute(select(TradeIntent).where(TradeIntent.status == "proposed")).scalars().all()
    )
    # A new analyst run only replaces the previous run's batch — never a play's
    # staged tranche, which the user asked for explicitly.
    open_proposals = [i for i in open_proposals if not _is_play_intent(i)]
    for intent in open_proposals:
        intent.status = "expired"
        if not intent.failure_reason:
            intent.failure_reason = "superseded by a newer agent run"
    if open_proposals:
        db.commit()
    return len(open_proposals)


def list_intents(db: Session, limit: int = 100) -> list[TradeIntent]:
    expire_stale_intents(db)
    q = select(TradeIntent).order_by(desc(TradeIntent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def list_events(db: Session, limit: int = 300) -> list[ExecutionEvent]:
    q = select(ExecutionEvent).order_by(desc(ExecutionEvent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def approve_intent(db: Session, intent_id: str, note: str | None = None, actor: str = "human") -> TradeIntent:
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

    _log_event(db, intent.id, "info", "intent approved", {"note": note or "", "actor": actor})
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


def rail_violations(
    db: Session,
    *,
    symbol: str,
    side: str,
    quantity: float,
    estimated_notional: float,
    exclude_intent_id: str | None = None,
    risk: dict | None = None,
) -> list[str]:
    """Every execution rail an order of this shape would trip right now.

    Shared by ``execute_intent`` (which refuses on any violation) and the agent's
    ``propose_trade`` tool (which reports them up front so the agent sizes within
    the caps). Messages are stable ``<guard>: <reason>`` strings.
    """
    risk = risk or ConfigStore(db).get_risk()
    out: list[str] = []

    cutoff = datetime.utcnow() - timedelta(seconds=int(risk["duplicate_order_window_seconds"]))
    dup_q = select(TradeIntent.id).where(
        TradeIntent.created_at >= cutoff,
        TradeIntent.symbol == symbol,
        TradeIntent.side == side,
        TradeIntent.quantity == quantity,
        TradeIntent.status.in_(["proposed", "approved", "executed"]),
    )
    if exclude_intent_id:
        dup_q = dup_q.where(TradeIntent.id != exclude_intent_id)
    if db.execute(dup_q.limit(1)).first() is not None:
        out.append("duplicate-order-guard: similar order already exists in recent window")

    if estimated_notional > float(risk["max_single_order_notional"]):
        out.append("risk-guard: order exceeds max single-order notional")

    if _daily_executed_notional(db) + estimated_notional > float(risk["max_daily_notional"]):
        out.append("risk-guard: order exceeds max daily notional")

    if side == "buy" and estimated_notional > _latest_account_cash(db):
        out.append("risk-guard: insufficient available cash")

    return out


def execute_intent(
    db: Session,
    intent_id: str,
    *,
    force_live: bool = False,
    account_kind: str | None = None,
    approve: bool = False,
    approved_via: str = "ui",
) -> TradeIntent:
    """Execute an APPROVED intent. This is the single gate for live orders.

    ``approve=True`` is a human-UI convenience: it approves a ``proposed``
    intent and executes it in one call, recording a human approval in the audit
    log. Agent code paths must never pass it — the agent can only propose.
    """
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")

    if approve and intent.status == "proposed":
        intent = approve_intent(db, intent_id, note=f"human approval via {approved_via}", actor="human")

    if intent.status != "approved":
        raise ExecutionError(
            f"Intent {intent_id} cannot be executed from status {intent.status} (it must be approved first)"
        )

    # Resolve the destination account explicitly: the caller's choice wins, else
    # the account the intent was proposed for, else invest. No silent invest
    # default once a choice has been made upstream.
    resolved_account = str(account_kind or (intent.meta or {}).get("account_kind") or "invest").strip().lower()
    if resolved_account not in {"invest", "stocks_isa"}:
        raise ExecutionError(f"invalid account '{account_kind}': must be 'invest' or 'stocks_isa'")

    config = ConfigStore(db)
    risk = config.get_risk()
    broker = config.get_broker()

    violations = rail_violations(
        db,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        estimated_notional=intent.estimated_notional,
        exclude_intent_id=intent.id,
        risk=risk,
    )
    if violations:
        raise ExecutionError(violations[0])

    mode = broker.get("broker_mode", "paper")
    if force_live:
        mode = "live"

    intent.broker_mode = mode
    intent.status = "executing"
    # Record the resolved destination account on the intent for the audit trail.
    meta = dict(intent.meta or {})
    meta["account_kind"] = resolved_account
    intent.meta = meta
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

        # Live writes use the dedicated IP-restricted execution key (purpose="execute").
        exec_creds = config.get_account_exec_credentials(resolved_account)  # type: ignore[arg-type]
        if not exec_creds.get("exec_enabled", True):
            raise ExecutionError(f"live execution is disabled for {resolved_account} (enable it in Settings)")
        if not exec_creds.get("t212_api_key") or not exec_creds.get("t212_api_secret"):
            raise ExecutionError(
                f"no execution key configured for {resolved_account} (add it in Settings → Credentials)"
            )
        client = build_t212_client(config, account_kind=resolved_account, purpose="execute")  # type: ignore[arg-type]
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
