"""Standing permissions: graduated, earned autonomy for Archie.

Archie proposes; a human approves. The one exception is a *standing
permission* the user granted for a narrow category of proposal:

* ``play_tranche`` — buy the next staged tranche of a play the user already
  approved (an active play with at least one human-approved, executed trade);
* ``rebalance`` — trades from the deterministic rebalancer that move the book
  back inside its bands;
* ``deploy_cash`` — put idle cash to work, a buy of a leg of an approved play,
  under the permission's per-trade cap.

Each permission has a per-trade £ cap, a daily £ cap, an optional expiry and an
on/off switch. A proposal that matches an active permission and passes every
execution rail is approved by the system (``approved_by="permission:<id>"``)
and then executed through the SAME ``execution_service.execute_intent`` gate as
a human approval. Nothing here places an order directly.

Safety properties (see ``tests/test_permissions.py`` and
``tests/test_execution_gate.py``):

* Only the HTTP API (the app UI) can create, edit, resume or confirm a
  permission. No MCP tool can — Archie can only *suggest* one
  (:func:`record_suggestion`), which grants nothing.
* The daily cap is reserved with one conditional UPDATE and the intent is
  claimed with another (``status='proposed'`` → ``'approved'``), so concurrent
  sweeps can't overspend a cap or approve the same proposal twice.
* Expired, paused or revoked permissions never apply; a global kill switch
  pauses them all.
* In live mode a permission applies only if the user confirmed it while in live
  mode; editing a permission clears that confirmation.
* Only proposals made after the permission was granted, and recently, are
  eligible — it never sweeps up an old queue.
* Applying permissions runs in the backend process (a small sweeper thread),
  never inside an agent/MCP subprocess.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, desc, or_, select, update
from sqlalchemy.orm import Session

from app.models.entities import (
    ExecutionEvent,
    PermissionEvent,
    PermissionSuggestion,
    Play,
    StandingPermission,
    TradeIntent,
)
from app.services import intent_category
from app.services.config_store import ConfigStore

logger = logging.getLogger(__name__)

KINDS: dict[str, dict[str, str]] = {
    "play_tranche": {
        "label": "Play tranches",
        "description": "Buy the next tranche of a play you already approved.",
        "category": "play_tranche",
    },
    "rebalance": {
        "label": "Rebalance into bands",
        "description": "Trades the rebalancer proposes to bring the book back inside your bands.",
        "category": "rebalance",
    },
    "deploy_cash": {
        "label": "Deploy idle cash",
        "description": "Put idle cash into a leg of a play you already approved, up to the per-trade cap.",
        "category": "deploy_cash",
    },
}

AUTONOMY_KEY = "autonomy"
AUTONOMY_DEFAULT = {"paused": False}
_MAX_CAP = 1_000_000.0
# A permission only acts on proposals at most this old.
_MAX_PROPOSAL_AGE = timedelta(hours=6)
_PREFIX = "permission:"


class PermissionError_(ValueError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def _today() -> str:
    return _now().date().isoformat()


def _event(db: Session, kind: str, permission_id: str | None = None, *, intent_id: str | None = None,
           amount: float | None = None, **detail: Any) -> None:
    db.add(PermissionEvent(kind=kind, permission_id=permission_id, intent_id=intent_id, amount_gbp=amount,
                           detail=detail))


def _broker_mode(db: Session) -> str:
    return str(ConfigStore(db).get_broker().get("broker_mode", "paper"))


# ── kill switch ──────────────────────────────────────────────────────────────

def get_autonomy(db: Session) -> dict[str, Any]:
    return {**AUTONOMY_DEFAULT, **(ConfigStore(db).get(AUTONOMY_KEY, AUTONOMY_DEFAULT) or {})}


def set_kill_switch(db: Session, paused: bool) -> dict[str, Any]:
    cfg = {**get_autonomy(db), "paused": bool(paused), "changed_at": _now().isoformat()}
    ConfigStore(db).set(AUTONOMY_KEY, cfg)
    _event(db, "kill_switch_on" if paused else "kill_switch_off")
    db.commit()
    return cfg


# ── CRUD (HTTP API only — never exposed to MCP) ──────────────────────────────

def _clean_caps(per_trade: Any, daily: Any) -> tuple[float, float]:
    try:
        per = float(per_trade)
        day = float(daily)
    except (TypeError, ValueError) as exc:
        raise PermissionError_("caps must be numbers") from exc
    if not (0 < per <= _MAX_CAP) or not (0 < day <= _MAX_CAP):
        raise PermissionError_("caps must be positive amounts")
    if day < per:
        raise PermissionError_("the daily cap can't be lower than the per-trade cap")
    return round(per, 2), round(day, 2)


def _parse_expiry(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PermissionError_("expiry must be an ISO date") from exc
    if parsed.tzinfo is not None:  # stored as naive UTC like every other timestamp
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _default_label(kind: str, per_trade: float) -> str:
    base = KINDS[kind]["label"]
    return f"{base} up to £{per_trade:,.0f}"


def create_permission(
    db: Session,
    *,
    kind: str,
    per_trade_cap_gbp: Any,
    daily_cap_gbp: Any,
    expires_at: Any = None,
    play_ids: list[str] | None = None,
    label: str | None = None,
    confirm_live: bool = False,
    from_suggestion_id: str | None = None,
) -> StandingPermission:
    if kind not in KINDS:
        raise PermissionError_(f"unknown permission kind {kind!r}")
    per, day = _clean_caps(per_trade_cap_gbp, daily_cap_gbp)
    expiry = _parse_expiry(expires_at)
    if expiry is not None and expiry <= _now():
        raise PermissionError_("expiry is in the past")
    live = _broker_mode(db) == "live"
    perm = StandingPermission(
        kind=kind,
        label=(label or "").strip()[:200] or _default_label(kind, per),
        enabled=True,
        per_trade_cap_gbp=per,
        daily_cap_gbp=day,
        expires_at=expiry,
        play_ids=[str(p) for p in (play_ids or []) if str(p).strip()][:50],
        confirmed_live_at=_now() if (live and confirm_live) else None,
        from_suggestion_id=from_suggestion_id,
    )
    db.add(perm)
    db.flush()
    _event(db, "granted", perm.id, per_trade_cap_gbp=per, daily_cap_gbp=day,
           expires_at=expiry.isoformat() if expiry else None, play_ids=perm.play_ids,
           broker_mode="live" if live else "paper", confirmed_live=bool(perm.confirmed_live_at))
    if from_suggestion_id:
        sug = db.get(PermissionSuggestion, from_suggestion_id)
        if sug is not None and sug.status == "open":
            sug.status = "accepted"
            sug.resolved_at = _now()
    db.commit()
    db.refresh(perm)
    return perm


def update_permission(db: Session, permission_id: str, patch: dict[str, Any], *, confirm_live: bool = False) -> StandingPermission:
    perm = db.get(StandingPermission, permission_id)
    if perm is None:
        raise PermissionError_("permission not found")
    if perm.revoked_at is not None:
        raise PermissionError_("this permission was revoked — grant a new one")
    changed: dict[str, Any] = {}
    if "per_trade_cap_gbp" in patch or "daily_cap_gbp" in patch:
        per, day = _clean_caps(patch.get("per_trade_cap_gbp", perm.per_trade_cap_gbp),
                               patch.get("daily_cap_gbp", perm.daily_cap_gbp))
        if per != perm.per_trade_cap_gbp:
            changed["per_trade_cap_gbp"] = [perm.per_trade_cap_gbp, per]
            perm.per_trade_cap_gbp = per
        if day != perm.daily_cap_gbp:
            changed["daily_cap_gbp"] = [perm.daily_cap_gbp, day]
            perm.daily_cap_gbp = day
    if "expires_at" in patch:
        expiry = _parse_expiry(patch.get("expires_at"))
        if expiry is not None and expiry <= _now():
            raise PermissionError_("expiry is in the past")
        if expiry != perm.expires_at:
            changed["expires_at"] = expiry.isoformat() if expiry else None
            perm.expires_at = expiry
    if "play_ids" in patch:
        ids = [str(p) for p in (patch.get("play_ids") or []) if str(p).strip()][:50]
        if ids != list(perm.play_ids or []):
            changed["play_ids"] = ids
            perm.play_ids = ids
    if "label" in patch and str(patch.get("label") or "").strip():
        perm.label = str(patch["label"]).strip()[:200]
    live = _broker_mode(db) == "live"
    if changed:
        # The terms changed: a live confirmation was for the old terms.
        perm.confirmed_live_at = _now() if (live and confirm_live) else None
        _event(db, "edited", perm.id, changes=changed, confirmed_live=bool(perm.confirmed_live_at))
    if "enabled" in patch and bool(patch["enabled"]) != perm.enabled:
        perm.enabled = bool(patch["enabled"])
        _event(db, "resumed" if perm.enabled else "paused", perm.id)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


def confirm_live(db: Session, permission_id: str) -> StandingPermission:
    perm = db.get(StandingPermission, permission_id)
    if perm is None or perm.revoked_at is not None:
        raise PermissionError_("permission not found")
    if _broker_mode(db) != "live":
        raise PermissionError_("switch to live mode first — a live confirmation can only be given in live mode")
    perm.confirmed_live_at = _now()
    _event(db, "confirmed_live", perm.id)
    db.commit()
    db.refresh(perm)
    return perm


def revoke_permission(db: Session, permission_id: str) -> StandingPermission:
    perm = db.get(StandingPermission, permission_id)
    if perm is None:
        raise PermissionError_("permission not found")
    if perm.revoked_at is None:
        perm.revoked_at = _now()
        perm.enabled = False
        _event(db, "revoked", perm.id)
        db.commit()
        db.refresh(perm)
    return perm


# ── reads ────────────────────────────────────────────────────────────────────

def status_of(perm: StandingPermission, *, broker_mode: str, paused: bool, now: datetime | None = None) -> tuple[bool, str]:
    """``(applies, reason)`` for one permission right now."""
    now = now or _now()
    if perm.revoked_at is not None:
        return False, "revoked"
    if not perm.enabled:
        return False, "paused"
    if perm.expires_at is not None and perm.expires_at <= now:
        return False, "expired"
    if paused:
        return False, "all permissions paused"
    if broker_mode == "live" and perm.confirmed_live_at is None:
        return False, "needs live confirmation"
    return True, "active"


def _spent_today(perm: StandingPermission) -> float:
    return float(perm.spent_today_gbp or 0.0) if perm.spent_day == _today() else 0.0


def serialize(perm: StandingPermission, *, broker_mode: str, paused: bool) -> dict[str, Any]:
    applies, reason = status_of(perm, broker_mode=broker_mode, paused=paused)
    return {
        "id": perm.id,
        "kind": perm.kind,
        "kind_label": KINDS.get(perm.kind, {}).get("label", perm.kind),
        "kind_description": KINDS.get(perm.kind, {}).get("description", ""),
        "label": perm.label,
        "enabled": perm.enabled,
        "per_trade_cap_gbp": perm.per_trade_cap_gbp,
        "daily_cap_gbp": perm.daily_cap_gbp,
        "spent_today_gbp": round(_spent_today(perm), 2),
        "expires_at": perm.expires_at.isoformat() if perm.expires_at else None,
        "play_ids": list(perm.play_ids or []),
        "created_at": perm.created_at.isoformat() if perm.created_at else None,
        "revoked_at": perm.revoked_at.isoformat() if perm.revoked_at else None,
        "confirmed_live_at": perm.confirmed_live_at.isoformat() if perm.confirmed_live_at else None,
        "applies": applies,
        "status": reason,
    }


def list_permissions(db: Session, *, include_revoked: bool = True) -> dict[str, Any]:
    mode = _broker_mode(db)
    paused = bool(get_autonomy(db).get("paused"))
    q = select(StandingPermission).order_by(desc(StandingPermission.created_at))
    if not include_revoked:
        q = q.where(StandingPermission.revoked_at.is_(None))
    rows = db.execute(q).scalars().all()
    return {
        "broker_mode": mode,
        "paused": paused,
        "kinds": [{"kind": k, **v} for k, v in KINDS.items()],
        "permissions": [serialize(p, broker_mode=mode, paused=paused) for p in rows],
    }


def permission_labels(db: Session) -> dict[str, str]:
    return {f"{_PREFIX}{p.id}": p.label for p in db.execute(select(StandingPermission)).scalars().all()}


def history(db: Session, permission_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = select(PermissionEvent).order_by(desc(PermissionEvent.created_at), desc(PermissionEvent.id)).limit(limit)
    if permission_id:
        q = q.where(PermissionEvent.permission_id == permission_id)
    labels = permission_labels(db)
    out = []
    for e in db.execute(q).scalars().all():
        out.append({
            "id": e.id,
            "created_at": e.created_at.isoformat(),
            "permission_id": e.permission_id,
            "permission_label": labels.get(f"{_PREFIX}{e.permission_id}") if e.permission_id else None,
            "kind": e.kind,
            "intent_id": e.intent_id,
            "amount_gbp": e.amount_gbp,
            "detail": e.detail or {},
        })
    return out


# ── matching ─────────────────────────────────────────────────────────────────

def _leg_match(play: Play, intent: TradeIntent) -> bool:
    code = (intent.instrument_code or "").upper()
    sym = (intent.symbol or "").upper()
    for leg in play.legs or []:
        if code and str(leg.get("instrument_code") or "").upper() == code:
            return True
        if sym and str(leg.get("ticker") or "").upper() == sym:
            return True
    return False


def play_is_approved(db: Session, play_id: str) -> bool:
    """An active play with at least one trade a human approved and executed."""
    play = db.get(Play, play_id)
    if play is None or play.status != "active":
        return False
    rows = db.execute(
        select(TradeIntent.meta, TradeIntent.approved_by).where(TradeIntent.status == "executed")
    ).all()
    for meta, approved_by in rows:
        if (meta or {}).get("play_id") == play_id and not str(approved_by or "").startswith(_PREFIX):
            return True
    return False


def _scope_reasons(db: Session, perm: StandingPermission, intent: TradeIntent) -> list[str]:
    """Why ``intent`` is outside ``perm``'s category (empty = inside)."""
    meta = intent.meta or {}
    category = intent_category.of_intent(intent)
    out: list[str] = []
    if perm.kind == "rebalance":
        if meta.get("source") != "rebalance":
            out.append("not a rebalancer trade")
        return out

    if intent.side != "buy":
        out.append("only buys are covered")
    play_id = meta.get("play_id")
    if not play_id:
        return out + ["not linked to a play"]
    if perm.play_ids and play_id not in perm.play_ids:
        out.append("play not in this permission's scope")
    if perm.kind == "play_tranche":
        if category != "play_tranche" or meta.get("tranche_index") is None:
            out.append("not a staged play tranche")
    elif perm.kind == "deploy_cash":
        if category != "deploy_cash":
            out.append("not a deploy-cash proposal")
    play = db.get(Play, play_id)
    if play is None:
        return out + ["play not found"]
    if not _leg_match(play, intent):
        out.append("instrument isn't a leg of the play")
    if not play_is_approved(db, play_id):
        out.append("play not approved by you yet (needs an active play with a trade you approved)")
    return out


def evaluate(db: Session, intent: TradeIntent, *, now: datetime | None = None) -> dict[str, Any]:
    """Which permission (if any) would approve ``intent``, and why not otherwise.
    Read-only."""
    from app.services.execution_service import rail_violations

    now = now or _now()
    mode = _broker_mode(db)
    paused = bool(get_autonomy(db).get("paused"))
    result: dict[str, Any] = {"intent_id": intent.id, "permission_id": None, "reasons": {}}
    if intent.status != "proposed":
        result["blocked"] = f"intent is {intent.status}"
        return result
    if intent.created_at is not None and now - intent.created_at > _MAX_PROPOSAL_AGE:
        result["blocked"] = "proposal too old for a standing permission"
        return result
    from app.services import broker_router

    refused = broker_router.refusal_for(db, intent)
    if refused:
        result["blocked"] = refused
        return result
    perms = db.execute(
        select(StandingPermission).where(StandingPermission.revoked_at.is_(None)).order_by(StandingPermission.created_at)
    ).scalars().all()
    notional = float(intent.estimated_notional or 0.0)
    for perm in perms:
        applies, why = status_of(perm, broker_mode=mode, paused=paused, now=now)
        reasons = [] if applies else [why]
        if intent.created_at is not None and intent.created_at < perm.created_at:
            reasons.append("proposed before this permission was granted")
        reasons += _scope_reasons(db, perm, intent)
        if notional <= 0:
            reasons.append("no notional")
        if notional > perm.per_trade_cap_gbp + 1e-9:
            reasons.append(f"over the £{perm.per_trade_cap_gbp:,.0f} per-trade cap")
        if _spent_today(perm) + notional > perm.daily_cap_gbp + 1e-9:
            reasons.append(f"over the £{perm.daily_cap_gbp:,.0f} daily cap")
        if not reasons:
            violations = rail_violations(
                db,
                symbol=intent.symbol,
                side=intent.side,
                quantity=float(intent.quantity or 0.0),
                estimated_notional=notional,
                exclude_intent_id=intent.id,
                account_kind=(intent.meta or {}).get("account_kind"),
                instrument_code=intent.instrument_code,
            )
            reasons += violations
        result["reasons"][perm.id] = reasons
        if not reasons:
            result["permission_id"] = perm.id
            break
    return result


# ── applying ─────────────────────────────────────────────────────────────────

def _reserve(db: Session, perm_id: str, amount: float) -> bool:
    """Atomically add ``amount`` to today's spend iff it stays under the daily cap."""
    today = _today()
    spent = case((StandingPermission.spent_day == today, StandingPermission.spent_today_gbp), else_=0.0)
    rc = db.execute(
        update(StandingPermission)
        .where(
            StandingPermission.id == perm_id,
            StandingPermission.enabled.is_(True),
            StandingPermission.revoked_at.is_(None),
            spent + amount <= StandingPermission.daily_cap_gbp + 1e-9,
        )
        .values(spent_today_gbp=spent + amount, spent_day=today)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return bool(rc)


def _refund(db: Session, perm_id: str, amount: float) -> None:
    today = _today()
    db.execute(
        update(StandingPermission)
        .where(StandingPermission.id == perm_id, StandingPermission.spent_day == today)
        .values(spent_today_gbp=case(
            (StandingPermission.spent_today_gbp - amount > 0, StandingPermission.spent_today_gbp - amount),
            else_=0.0,
        ))
        .execution_options(synchronize_session=False)
    )
    db.commit()


def try_auto_approve(db: Session, intent_id: str) -> dict[str, Any]:
    """Approve-and-execute ``intent_id`` under a matching standing permission.

    Returns ``{"applied": bool, ...}``. Execution goes through
    ``execution_service.execute_intent`` like any human approval."""
    from app.services.execution_service import ExecutionError, _log_event, execute_intent

    intent = db.get(TradeIntent, intent_id)
    if intent is None:
        return {"applied": False, "reason": "not found"}
    verdict = evaluate(db, intent)
    perm_id = verdict.get("permission_id")
    if not perm_id:
        return {"applied": False, "reason": verdict.get("blocked") or "no matching permission", "detail": verdict}
    amount = float(intent.estimated_notional or 0.0)
    if not _reserve(db, perm_id, amount):
        return {"applied": False, "reason": "daily cap reached"}
    actor = f"{_PREFIX}{perm_id}"
    claimed = db.execute(
        update(TradeIntent)
        .where(TradeIntent.id == intent_id, TradeIntent.status == "proposed")
        .values(status="approved", approved_at=_now(), approved_by=actor, updated_at=_now())
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if not claimed:
        _refund(db, perm_id, amount)
        return {"applied": False, "reason": "already handled"}
    db.refresh(intent)
    perm = db.get(StandingPermission, perm_id)
    label = perm.label if perm else perm_id
    _log_event(db, intent.id, "info", "intent approved", {"actor": actor, "note": f"standing permission: {label}"})
    _event(db, "used", perm_id, intent_id=intent.id, amount=amount, symbol=intent.symbol, side=intent.side)
    db.commit()
    try:
        executed = execute_intent(db, intent.id)
        return {"applied": True, "permission_id": perm_id, "status": executed.status}
    except Exception as exc:  # noqa: BLE001 — record and hand back to the human
        text = str(exc)[:500]
        db.rollback()
        fresh = db.get(TradeIntent, intent_id)
        if fresh is not None and fresh.status == "approved":
            # Not sent (retryable): give it back to the human, unapproved.
            fresh.status = "proposed"
            fresh.approved_at = None
            fresh.approved_by = None
            db.add(fresh)
            db.add(ExecutionEvent(intent_id=fresh.id, level="warn",
                                  message="standing permission couldn't place it — back to you",
                                  payload={"error": text, "actor": actor}))
        _refund(db, perm_id, amount)
        _event(db, "failed", perm_id, intent_id=intent_id, amount=amount, error=text)
        db.commit()
        if not isinstance(exc, ExecutionError):
            logger.warning("permission execution failed for %s: %s", intent_id, exc)
        return {"applied": False, "permission_id": perm_id, "reason": text}


def process_pending(db: Session) -> list[dict[str, Any]]:
    """Apply standing permissions to every eligible open proposal."""
    if get_autonomy(db).get("paused"):
        return []
    has_any = db.execute(
        select(StandingPermission.id).where(StandingPermission.revoked_at.is_(None), StandingPermission.enabled.is_(True)).limit(1)
    ).first()
    if not has_any:
        return []
    since = _now() - _MAX_PROPOSAL_AGE
    ids = db.execute(
        select(TradeIntent.id).where(
            TradeIntent.status == "proposed",
            TradeIntent.created_at >= since,
            # Staged for a later day: not placeable yet, so not a candidate yet.
            or_(TradeIntent.not_before.is_(None), TradeIntent.not_before <= _now().date()),
        )
        .order_by(TradeIntent.created_at)
    ).scalars().all()
    out = []
    for intent_id in ids:
        res = try_auto_approve(db, intent_id)
        if res.get("applied") or res.get("permission_id"):
            out.append({"intent_id": intent_id, **{k: v for k, v in res.items() if k != "detail"}})
    return out


# ── digest ───────────────────────────────────────────────────────────────────

def digest(db: Session, day: date | None = None) -> dict[str, Any]:
    """What Archie did on his own today, for the Home line. Empty when nothing."""
    day = day or _now().date()
    start = datetime.combine(day, datetime.min.time())
    rows = db.execute(
        select(TradeIntent).where(TradeIntent.approved_by.like(f"{_PREFIX}%"), TradeIntent.approved_at >= start,
                                  TradeIntent.approved_at < start + timedelta(days=1))
    ).scalars().all()
    labels = permission_labels(db)
    by_perm: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = (r.approved_by or "")[len(_PREFIX):]
        entry = by_perm.setdefault(pid, {"permission_id": pid, "label": labels.get(r.approved_by or "", pid),
                                         "count": 0, "notional_gbp": 0.0, "symbols": []})
        if r.status == "executed":
            entry["count"] += 1
            entry["notional_gbp"] = round(entry["notional_gbp"] + float(r.estimated_notional or 0.0), 2)
            entry["symbols"].append(r.symbol)
    groups = [g for g in by_perm.values() if g["count"]]
    count = sum(g["count"] for g in groups)
    total = round(sum(g["notional_gbp"] for g in groups), 2)
    kinds = {db.get(StandingPermission, g["permission_id"]).kind for g in groups if db.get(StandingPermission, g["permission_id"])}
    noun = "tranche" if kinds == {"play_tranche"} else "trade"
    line = (f"Archie placed {count} {noun}{'' if count == 1 else 's'} under your permissions today: £{total:,.0f}"
            if count else "")
    return {"date": day.isoformat(), "count": count, "notional_gbp": total, "line": line, "groups": groups}


# ── suggestions (Archie may suggest; only the user grants) ───────────────────

_SUGGEST_MIN_N = 5
_SUGGEST_MIN_HIT = 0.6
_SUGGEST_COOLDOWN = timedelta(days=30)

_SUGGEST_COPY = {
    "play_tranche": ("My play tranches", "place future tranches for approved plays automatically"),
    "rebalance": ("My rebalance trades", "rebalance back into your bands automatically"),
    "deploy_cash": ("My cash deployments", "deploy idle cash into approved plays automatically"),
}


def _round_cap(x: float) -> float:
    return max(50.0, round(x / 50.0) * 50.0)


def _suggestion_message(kind: str, record: dict[str, Any], benchmark: str, cap: float) -> str:
    subject, ask = _SUGGEST_COPY[kind]
    hits, n = record.get("hits") or 0, record.get("n_vs_benchmark") or 0
    return f"{subject} have beaten {benchmark} {hits} of {n} times. Let me {ask}, up to £{cap:,.0f} each?"


def _blocked_by_recent(db: Session, kind: str) -> bool:
    if db.execute(select(StandingPermission.id).where(StandingPermission.kind == kind,
                                                      StandingPermission.revoked_at.is_(None)).limit(1)).first():
        return True
    recent = db.execute(
        select(PermissionSuggestion).where(PermissionSuggestion.kind == kind,
                                           PermissionSuggestion.created_at >= _now() - _SUGGEST_COOLDOWN)
    ).scalars().all()
    return any(s.status in {"open", "dismissed"} for s in recent)


def record_suggestion(db: Session, *, kind: str, per_trade_cap_gbp: float | None = None, note: str = "",
                      source: str = "archie") -> dict[str, Any]:
    """Record a *suggestion* for a permission. Grants nothing. The message is
    built from the real track record, not from the caller's words."""
    from app.services.track_record_service import category_record, get_config

    if kind not in KINDS:
        return {"ok": False, "error": f"kind must be one of {sorted(KINDS)}"}
    if _blocked_by_recent(db, kind):
        return {"ok": False, "error": "already granted, suggested or dismissed recently — don't ask again yet"}
    record = category_record(db, KINDS[kind]["category"])
    if (record.get("n_vs_benchmark") or 0) < _SUGGEST_MIN_N or (record.get("hit_rate") or 0) < _SUGGEST_MIN_HIT \
            or (record.get("avg_excess") or 0) <= 0:
        return {"ok": False, "error": "the track record doesn't support this yet", "record": record}
    risk_cap = float(ConfigStore(db).get_risk().get("max_single_order_notional") or 0.0)
    cap = float(per_trade_cap_gbp or 0.0) or _round_cap(float(record.get("median_notional_gbp") or 200.0))
    if risk_cap:
        cap = min(cap, risk_cap)
    cap = max(1.0, cap)
    benchmark = str(get_config(db).get("benchmark") or "SPY")
    sug = PermissionSuggestion(
        kind=kind, per_trade_cap_gbp=cap, daily_cap_gbp=round(cap * 3, 2),
        message=_suggestion_message(kind, record, benchmark, cap), note=(note or "").strip()[:1000],
        evidence={k: record.get(k) for k in ("n", "n_vs_benchmark", "hits", "hit_rate", "avg_excess", "caveat")},
        source=source,
    )
    db.add(sug)
    db.commit()
    db.refresh(sug)
    return {"ok": True, "suggestion": serialize_suggestion(sug),
            "note": "Suggested only. The user decides in the app; you cannot grant permissions."}


def generate_suggestions(db: Session) -> list[dict[str, Any]]:
    out = []
    for kind in KINDS:
        res = record_suggestion(db, kind=kind, source="system")
        if res.get("ok"):
            out.append(res["suggestion"])
    return out


def serialize_suggestion(s: PermissionSuggestion) -> dict[str, Any]:
    return {
        "id": s.id, "created_at": s.created_at.isoformat(), "kind": s.kind,
        "kind_label": KINDS.get(s.kind, {}).get("label", s.kind),
        "per_trade_cap_gbp": s.per_trade_cap_gbp, "daily_cap_gbp": s.daily_cap_gbp,
        "message": s.message, "note": s.note, "evidence": s.evidence or {}, "status": s.status, "source": s.source,
    }


def list_suggestions(db: Session, status: str = "open") -> list[dict[str, Any]]:
    q = select(PermissionSuggestion).order_by(desc(PermissionSuggestion.created_at))
    if status != "all":
        q = q.where(PermissionSuggestion.status == status)
    return [serialize_suggestion(s) for s in db.execute(q).scalars().all()]


def dismiss_suggestion(db: Session, suggestion_id: str) -> dict[str, Any]:
    s = db.get(PermissionSuggestion, suggestion_id)
    if s is None:
        raise PermissionError_("suggestion not found")
    if s.status == "open":
        s.status = "dismissed"
        s.resolved_at = _now()
        db.commit()
    return serialize_suggestion(s)


# ── sweeper (backend process only) ───────────────────────────────────────────

_SWEEP_SECONDS = 10.0
_stop = threading.Event()
_thread: threading.Thread | None = None


def _sweep_once() -> None:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        done = process_pending(db)
        if done:
            logger.info("standing permissions applied: %s", done)


def start_sweeper(interval: float = _SWEEP_SECONDS) -> None:
    """Apply standing permissions every few seconds. Runs in the backend so
    proposals from any source (chat/MCP, scheduled runs, plays, rebalancer) are
    picked up without the agent ever triggering an order itself."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()

    def _loop() -> None:
        while not _stop.wait(interval):
            try:
                _sweep_once()
            except Exception:  # noqa: BLE001
                logger.warning("permission sweep failed", exc_info=True)

    _thread = threading.Thread(target=_loop, name="permissions-sweeper", daemon=True)
    _thread.start()


def stop_sweeper() -> None:
    _stop.set()
