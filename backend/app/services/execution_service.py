from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from app.models.entities import AccountSnapshot, ExecutionEvent, PositionSnapshot, TradeIntent
from app.services.agent_skills_bridge import intent_to_skill_action
from app.services.config_store import ConfigStore
from app.services.market_data import MarketDataError, fetch_history
from app.services.t212_client import T212AuthError, T212Error, T212RateLimitError, build_t212_client


logger = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    pass


# An intent left in 'executing' longer than this was interrupted (process
# crash/restart mid-order). Its broker outcome is unknown, so it's marked failed
# with a "check your broker" reason rather than left spinning forever.
_STUCK_EXECUTING_MINUTES = 10
_OUTCOME_UNKNOWN = (
    "outcome unknown — the order may or may not have reached Trading 212. "
    "Check your Trading 212 orders before proposing it again."
)


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


def _latest_account_cash(db: Session, account_kind: str | None = None) -> float | None:
    """Free cash from the latest snapshot of ``account_kind`` (any account when
    None). ``None`` when there's no snapshot to judge by."""
    q = select(AccountSnapshot).order_by(desc(AccountSnapshot.fetched_at))
    if account_kind:
        q = q.where(AccountSnapshot.account_kind == account_kind)
    row = db.execute(q.limit(1)).scalar_one_or_none()
    if not row:
        return None
    return float(row.free_cash or 0.0)


def _latest_held_quantity(db: Session, account_kind: str, symbol: str, instrument_code: str | None) -> float | None:
    """Shares held in ``account_kind`` per the latest position snapshot, or None
    when there's no snapshot for that account (can't judge)."""
    acct_ts = db.execute(
        select(AccountSnapshot.fetched_at)
        .where(AccountSnapshot.account_kind == account_kind)
        .order_by(desc(AccountSnapshot.fetched_at))
        .limit(1)
    ).scalar_one_or_none()
    if acct_ts is None:
        return None
    rows = db.execute(
        select(PositionSnapshot).where(
            PositionSnapshot.account_kind == account_kind, PositionSnapshot.fetched_at == acct_ts
        )
    ).scalars().all()
    code = (instrument_code or "").upper()
    sym = (symbol or "").upper()
    total = 0.0
    for row in rows:
        if (code and (row.instrument_code or "").upper() == code) or (not code and (row.ticker or "").upper() == sym):
            total += float(row.quantity or 0.0)
    return total


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


def recover_stuck_executing(db: Session, older_than_minutes: int = _STUCK_EXECUTING_MINUTES) -> int:
    """Fail intents stuck in 'executing' (the process died mid-order).

    The broker outcome is unknown, so the reason tells the user to check
    Trading 212 rather than retry blindly. Called on startup (with 0 — nothing
    can be mid-flight before the app has served a request) and whenever intents
    are listed."""
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    stuck = (
        db.execute(
            select(TradeIntent).where(TradeIntent.status == "executing", TradeIntent.updated_at <= cutoff)
        )
        .scalars()
        .all()
    )
    for intent in stuck:
        intent.status = "failed"
        intent.failure_reason = f"execution interrupted: {_OUTCOME_UNKNOWN}"
        db.add(ExecutionEvent(intent_id=intent.id, level="error", message="execution interrupted", payload={}))
    if stuck:
        db.commit()
        logger.warning("Marked %d intent(s) stuck in 'executing' as failed", len(stuck))
    return len(stuck)


def list_intents(db: Session, limit: int = 100) -> list[TradeIntent]:
    expire_stale_intents(db)
    recover_stuck_executing(db)
    q = select(TradeIntent).order_by(desc(TradeIntent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def list_events(db: Session, limit: int = 300) -> list[ExecutionEvent]:
    q = select(ExecutionEvent).order_by(desc(ExecutionEvent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def _status_message(intent: TradeIntent, action: str) -> str:
    reason = f" ({intent.failure_reason})" if intent.failure_reason and intent.status in {"expired", "failed"} else ""
    return f"This proposal can't be {action} — it is already {intent.status}{reason}."


def approve_intent(db: Session, intent_id: str, note: str | None = None, actor: str = "human") -> TradeIntent:
    # A proposal past its TTL must not be approvable just because nobody listed
    # the queue (which is what normally expires it).
    expire_stale_intents(db)
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")
    if intent.status not in {"proposed", "approved"}:
        raise ExecutionError(_status_message(intent, "approved"))

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
    if intent.status not in {"proposed", "approved"}:
        # Rejecting an executed/executing intent would rewrite the audit trail.
        raise ExecutionError(_status_message(intent, "rejected"))

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
        price = float(history["close"].iloc[-1])
        return price if math.isfinite(price) and price > 0 else 0.0
    except (MarketDataError, IndexError, KeyError, ValueError, TypeError):
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
    account_kind: str | None = None,
    instrument_code: str | None = None,
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

    if side == "buy":
        cash = _latest_account_cash(db, account_kind)
        if cash is None or estimated_notional > cash:
            out.append("risk-guard: insufficient available cash")

    if side == "sell" and account_kind:
        held = _latest_held_quantity(db, account_kind, symbol, instrument_code)
        if held is not None and quantity > held + 1e-6:
            out.append(f"position-guard: selling {quantity:g} but only {held:g} held in {account_kind}")

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

    if intent.status != "approved" and intent.status != "proposed":
        raise ExecutionError(_status_message(intent, "executed"))
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

    qty = float(intent.quantity or 0.0)
    notional = float(intent.estimated_notional or 0.0)
    if intent.side not in {"buy", "sell"}:
        raise ExecutionError(f"invalid side '{intent.side}': must be 'buy' or 'sell'")
    if not math.isfinite(qty) or qty <= 0:
        raise ExecutionError("invalid quantity: must be a positive number of shares")
    if not math.isfinite(notional) or notional < 0:
        raise ExecutionError("invalid estimated notional on this intent")

    config = ConfigStore(db)
    risk = config.get_risk()
    broker = config.get_broker()

    violations = rail_violations(
        db,
        symbol=intent.symbol,
        side=intent.side,
        quantity=qty,
        estimated_notional=notional,
        exclude_intent_id=intent.id,
        risk=risk,
        account_kind=resolved_account,
        instrument_code=intent.instrument_code,
    )
    if violations:
        raise ExecutionError(violations[0])

    mode = broker.get("broker_mode", "paper")
    if force_live:
        mode = "live"

    # Atomically claim the intent (approved -> executing). Two concurrent
    # executes (double-click, UI + Telegram) both pass the checks above; only
    # the one whose UPDATE matches places the order.
    meta = dict(intent.meta or {})
    meta["account_kind"] = resolved_account
    claimed = db.execute(
        update(TradeIntent)
        .where(TradeIntent.id == intent.id, TradeIntent.status == "approved")
        .values(status="executing", broker_mode=mode, meta=meta, updated_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    db.refresh(intent)
    if not claimed:
        raise ExecutionError(_status_message(intent, "executed"))

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

    except Exception as exc:  # noqa: BLE001 — never leave an intent stuck in 'executing'
        text = str(exc) or exc.__class__.__name__
        # A transport error after the request left us means the order may have
        # been placed — say so instead of inviting a blind retry.
        if isinstance(exc, T212Error) and "request failed" in text:
            text = f"{text} — {_OUTCOME_UNKNOWN}"
        # Definitely-not-placed failures (rate limited, key rejected, exec key
        # missing/disabled) put the intent back to 'approved' so the user can
        # fix the cause and retry; anything else is terminal.
        retryable = isinstance(exc, (ExecutionError, T212RateLimitError, T212AuthError))
        try:
            db.rollback()
            intent = db.get(TradeIntent, intent_id) or intent
            intent.status = "approved" if retryable else "failed"
            intent.failure_reason = text[:2000]
            db.add(intent)
            db.commit()
            db.refresh(intent)
            _log_event(
                db,
                intent.id,
                "warn" if retryable else "error",
                "execution blocked (not sent; retryable)" if retryable else "execution failed",
                {"error": text[:2000]},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not record failure for intent %s", intent_id)
        if isinstance(exc, (ExecutionError, T212Error)):
            raise
        logger.exception("Unexpected error executing intent %s", intent_id)
        raise ExecutionError(f"execution failed: {exc.__class__.__name__}: {exc}") from exc
