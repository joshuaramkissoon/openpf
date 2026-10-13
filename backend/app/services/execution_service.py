from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta

from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.entities import AccountSnapshot, ExecutionEvent, PositionSnapshot, TradeIntent
from app.services import broker_router
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


def utc_today() -> date:
    """The daily caps run on UTC days (midnight UTC resets them)."""
    return datetime.utcnow().date()


def _daily_executed_notional(db: Session) -> float:
    start = datetime.combine(utc_today(), time.min)
    total = db.execute(
        select(func.sum(TradeIntent.estimated_notional)).where(
            TradeIntent.status == "executed",
            TradeIntent.executed_at >= start,
        )
    ).scalar_one_or_none()
    return float(total or 0.0)


# Proposals that still hold a claim on a day's daily notional cap.
_OPEN_STATUSES = ("proposed", "approved", "executing")


def daily_notional_used(
    db: Session,
    day: date | None = None,
    *,
    include_pending: bool = False,
    exclude_intent_id: str | None = None,
) -> float:
    """GBP already counted against ``day``'s daily notional cap.

    Today: what has been placed, plus (``include_pending``) open proposals
    that could still be placed today. A later day: the proposals already
    staged for it (``not_before`` that day) — nothing can be placed yet.
    """
    today = utc_today()
    day = day or today
    total = _daily_executed_notional(db) if day <= today else 0.0
    if day > today or include_pending:
        q = select(func.sum(TradeIntent.estimated_notional)).where(TradeIntent.status.in_(_OPEN_STATUSES))
        if day > today:
            q = q.where(TradeIntent.not_before == day)
        else:
            q = q.where(or_(TradeIntent.not_before.is_(None), TradeIntent.not_before <= today))
        if exclude_intent_id:
            q = q.where(TradeIntent.id != exclude_intent_id)
        total += float(db.execute(q).scalar_one_or_none() or 0.0)
    return total


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
    # A proposal staged for a later day only starts its clock on that day.
    stale = [i for i in stale if not i.not_before or datetime.combine(i.not_before, time.min) < cutoff]
    # Held-back asks (over a limit, kept for the user to stage or drop) go stale too.
    stale += (
        db.execute(select(TradeIntent).where(TradeIntent.status == "held", TradeIntent.created_at < cutoff))
        .scalars()
        .all()
    )
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
    # Nor one the user staged for a later day.
    open_proposals = [i for i in open_proposals if not _is_play_intent(i) and not i.not_before]
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
    # Held-back asks only live on the chat card that raised them.
    q = select(TradeIntent).where(TradeIntent.status != "held").order_by(desc(TradeIntent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def list_events(db: Session, limit: int = 300) -> list[ExecutionEvent]:
    q = select(ExecutionEvent).order_by(desc(ExecutionEvent.created_at)).limit(limit)
    return list(db.execute(q).scalars().all())


def _status_message(intent: TradeIntent, action: str) -> str:
    reason = f" ({intent.failure_reason})" if intent.failure_reason and intent.status in {"expired", "failed"} else ""
    if intent.status == "held":
        return f"This order was held back by a limit, so it can't be {action} — stage it for tomorrow or change your limits."
    return f"This proposal can't be {action} — it is already {intent.status}{reason}."


def _not_yet(intent: TradeIntent) -> str | None:
    """Why a proposal staged for a later day can't be placed yet, else None."""
    if intent.not_before and intent.not_before > utc_today():
        when = "tomorrow" if intent.not_before == utc_today() + timedelta(days=1) else f"{intent.not_before:%a %-d %b}"
        return f"This proposal is staged for {when} (that day's limits) — it can't be placed before then."
    return None


def approve_intent(db: Session, intent_id: str, note: str | None = None, actor: str = "human") -> TradeIntent:
    # A proposal past its TTL must not be approvable just because nobody listed
    # the queue (which is what normally expires it).
    expire_stale_intents(db)
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")
    if intent.status not in {"proposed", "approved"}:
        raise ExecutionError(_status_message(intent, "approved"))
    if reason := _not_yet(intent):
        raise ExecutionError(reason)

    intent.status = "approved"
    intent.approved_at = datetime.utcnow()
    intent.approved_by = actor[:64]
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
    if intent.status not in {"proposed", "approved", "held"}:
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


# The limits the user sets so Archie's proposals stay inside them. On a trade the
# user places by hand (``rails_mode="advisory"``) they are warnings, not refusals.
# Everything else (duplicates, cash, selling more than held, no exchange key) stays hard.
_ADVISORY_RAILS = (
    "risk-guard: order exceeds max single-order notional",
    "risk-guard: order exceeds max daily notional",
    "crypto-guard: crypto would be",
    "crypto-guard: no portfolio snapshot",
)
# A hand-placed order only needs protecting from a double click, not from the
# user deliberately buying the same thing again a minute later.
MANUAL_DUPLICATE_WINDOW_SECONDS = 15
MANUAL_SOURCE = "user"
MANUAL_ACTOR = "user"


def _sandbox() -> bool:
    from app.core.sandbox import is_sandbox

    return is_sandbox()


def _sandbox_top_up(db: Session, intent: TradeIntent, account_kind: str) -> None:
    """Sandbox only: make sure the mock broker/exchange holds enough fake cash for
    this buy, logging "demo cash added" on the intent when it had to."""
    if intent.side != "buy":
        return
    need = float(intent.estimated_notional or 0.0) * 1.02  # fees + price drift since sizing
    try:
        if broker_router.is_crypto(account_kind):
            from app.services import crypto_sandbox

            added = crypto_sandbox.top_up(need)
        else:
            from app.services import sandbox_broker

            added = sandbox_broker.top_up(account_kind, need)
    except Exception:  # noqa: BLE001 — the mock tops up at fill time anyway
        logger.warning("sandbox top-up failed for intent %s", intent.id, exc_info=True)
        return
    if added:
        _log_event(db, intent.id, "info", "demo cash added", {"amount_gbp": added, "account_kind": account_kind})


def is_advisory_rail(violation: str) -> bool:
    return violation.startswith(_ADVISORY_RAILS)


def split_advisory(violations: list[str]) -> tuple[list[str], list[str]]:
    """(hard, advisory) for a trade the user places by hand."""
    return [v for v in violations if not is_advisory_rail(v)], [v for v in violations if is_advisory_rail(v)]


def is_manual_intent(intent: TradeIntent) -> bool:
    """Created by the manual-trade service for the user and approved as the user.
    Only such an intent may execute with advisory limits. Agents can't create
    one: proposals coerce source "user" away and never set ``manual_trade``."""
    meta = intent.meta or {}
    return (
        meta.get("source") == MANUAL_SOURCE
        and meta.get("manual_trade") is True
        and intent.approved_by == MANUAL_ACTOR
    )


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
    not_before: date | None = None,
    daily_used: float | None = None,
    duplicate_window_seconds: int | None = None,
) -> list[str]:
    """Every execution rail an order of this shape would trip right now.

    Shared by ``execute_intent`` (which refuses on any violation) and the agent's
    ``propose_trade`` tool (which reports them up front so the agent sizes within
    the caps). Messages are stable ``<guard>: <reason>`` strings.

    ``not_before``: the day the order is for. A later day's daily cap counts
    what is already staged for that day; ``daily_used`` overrides the figure
    (``propose_trade`` passes one that also counts today's open proposals).
    """
    risk = risk or ConfigStore(db).get_risk()
    out: list[str] = []

    window = int(risk["duplicate_order_window_seconds"]) if duplicate_window_seconds is None else duplicate_window_seconds
    cutoff = datetime.utcnow() - timedelta(seconds=window)
    dup_q = select(TradeIntent.id).where(
        TradeIntent.created_at >= cutoff,
        TradeIntent.symbol == symbol,
        TradeIntent.side == side,
        TradeIntent.quantity == quantity,
        TradeIntent.status.in_(["proposed", "approved", "executed"]),
        # The same order for another day is that day's order, not a repeat.
        TradeIntent.not_before.is_(None) if not_before is None else TradeIntent.not_before == not_before,
    )
    if exclude_intent_id:
        dup_q = dup_q.where(TradeIntent.id != exclude_intent_id)
    if db.execute(dup_q.limit(1)).first() is not None:
        out.append("duplicate-order-guard: similar order already exists in recent window")

    if estimated_notional > float(risk["max_single_order_notional"]):
        out.append("risk-guard: order exceeds max single-order notional")

    if daily_used is None:
        future = not_before is not None and not_before > utc_today()
        daily_used = (
            daily_notional_used(db, not_before, exclude_intent_id=exclude_intent_id)
            if future
            else _daily_executed_notional(db)
        )
    if daily_used + estimated_notional > float(risk["max_daily_notional"]):
        out.append("risk-guard: order exceeds max daily notional")

    # A sandbox's cash is fake and tops itself up at order time (``_sandbox_top_up``).
    if side == "buy" and not _sandbox():
        cash = _latest_account_cash(db, account_kind)
        if cash is None or estimated_notional > cash:
            out.append("risk-guard: insufficient available cash")

    if side == "sell" and account_kind:
        held = _latest_held_quantity(db, account_kind, symbol, instrument_code)
        if held is not None and quantity > held + 1e-6:
            out.append(f"position-guard: selling {quantity:g} but only {held:g} held in {account_kind}")

    if broker_router.is_crypto(account_kind):
        from app.services.crypto_service import crypto_rail_violations

        out.extend(crypto_rail_violations(db, side=side, estimated_notional=estimated_notional, risk=risk,
                                          account_kind=account_kind))

    return out


def execute_intent(
    db: Session,
    intent_id: str,
    *,
    force_live: bool = False,
    account_kind: str | None = None,
    approve: bool = False,
    approved_via: str = "ui",
    rails_mode: str = "enforce",
) -> TradeIntent:
    """Execute an APPROVED intent. This is the single gate for live orders.

    ``approve=True`` is a human-UI convenience: it approves a ``proposed``
    intent and executes it in one call, recording a human approval in the audit
    log. Agent code paths must never pass it — the agent can only propose.

    ``rails_mode="advisory"``: the user's own trade from the stock sheet. The
    notional and crypto-cap limits (which bind Archie's proposals) become
    warnings recorded on the intent; every other check stays hard. Honoured only
    for an intent the manual-trade service created and the user approved
    (``is_manual_intent``); anything else is checked as usual.
    """
    intent = db.get(TradeIntent, intent_id)
    if not intent:
        raise ExecutionError(f"Intent {intent_id} not found")

    # Capability check first: a read-only connection never executes (nor gets
    # approved on the way), whoever asks.
    # The proposed account is checked as well as any override: a suggestion for
    # a read-only account can't be re-routed into a tradable one.
    refused = broker_router.refusal_for(db, intent, account_kind)
    if refused:
        raise ExecutionError(refused)

    if reason := _not_yet(intent):
        raise ExecutionError(reason)

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
    if resolved_account not in broker_router.ACCOUNTS and not broker_router.is_crypto(resolved_account):
        raise ExecutionError(f"invalid account '{account_kind}': must be 'invest', 'stocks_isa' or a crypto account")

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

    advisory = rails_mode == "advisory" and is_manual_intent(intent)
    if rails_mode == "advisory" and not advisory:
        logger.warning("advisory rails refused for intent %s (not a manual trade by the user)", intent.id)
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
        not_before=intent.not_before,
        duplicate_window_seconds=MANUAL_DUPLICATE_WINDOW_SECONDS if advisory else None,
    )
    rail_warnings: list[str] = []
    if advisory:
        violations, rail_warnings = split_advisory(violations)
    if violations:
        raise ExecutionError(violations[0])

    mode = broker.get("broker_mode", "paper")
    if force_live:
        mode = "live"

    from app.core.sandbox import is_sandbox, is_sandbox_url

    if is_sandbox() and mode != "paper" and not broker_router.is_crypto(resolved_account):
        # Defence in depth: a sandbox instance only ever orders against the mock broker.
        probe = build_t212_client(config, account_kind=resolved_account, purpose="execute")  # type: ignore[arg-type]
        if broker.get("t212_base_env") != "sandbox" or not is_sandbox_url(probe.base_url):
            raise ExecutionError("sandbox mode: refusing to execute against a non-sandbox broker")
    if is_sandbox() and mode != "paper":
        _sandbox_top_up(db, intent, resolved_account)

    # Atomically claim the intent (approved -> executing). Two concurrent
    # executes (double-click, UI + Telegram) both pass the checks above; only
    # the one whose UPDATE matches places the order.
    meta = dict(intent.meta or {})
    meta["account_kind"] = resolved_account
    if rail_warnings:
        meta["rail_warnings"] = rail_warnings
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
    if rail_warnings:
        _log_event(db, intent.id, "warn", "placed by the user past their limits (advisory)", {"warnings": rail_warnings})

    try:
        if broker_router.is_crypto(resolved_account):
            return broker_router.execute_crypto(db, intent, mode=mode, log_event=_log_event, error_cls=ExecutionError)
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
                f"no execution key configured for {resolved_account} (add a trade key in Settings → Accounts → Trading 212)"
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
