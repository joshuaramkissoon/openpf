"""Archie's track record and the standing permissions that gate his autonomy.

* ``ProposalOutcome`` — one row per TradeIntent, tracked forward from the
  proposal's reference price whatever happened to it (taken, rejected, expired,
  superseded), marked at 1d / 1w / 1m / 3m (and at close) against a benchmark.
* ``StandingPermission`` — a narrow, capped, user-granted standing approval for
  one category of proposal. Only the user creates or loosens one (HTTP API from
  the app UI); no MCP tool can. Every permission-approved trade still goes
  through ``execution_service.execute_intent``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


def _uuid() -> str:
    return str(uuid.uuid4())


class ProposalOutcome(Base):
    __tablename__ = "proposal_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    proposed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    category: Mapped[str] = mapped_column(String(24), default="other", index=True)
    # taken | rejected | expired | superseded | failed | open
    fate: Mapped[str] = mapped_column(String(16), default="open", index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    instrument_code: Mapped[str] = mapped_column(String(64), default="")
    market_ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str] = mapped_column(String(8))
    notional_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Reference price at proposal time, in the instrument's currency (+ GBP).
    ref_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ref_price_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark: Mapped[str] = mapped_column(String(16), default="SPY")
    bench_ref_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # {"1d": {"date", "price", "ret", "bench_ret", "excess"}, "1w": ..., "close": ...}
    marks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # unpriced | tracking | complete
    state: Mapped[str] = mapped_column(String(12), default="tracking", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class StandingPermission(Base):
    __tablename__ = "standing_permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # play_tranche | rebalance | deploy_cash
    kind: Mapped[str] = mapped_column(String(24), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    per_trade_cap_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    daily_cap_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optional scope: only these play ids (empty = any approved play).
    play_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set only when the user confirmed the permission while in live mode. Live
    # orders never use a permission without it; any edit clears it.
    confirmed_live_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Daily-cap ledger, reserved atomically before each approval.
    spent_day: Mapped[str] = mapped_column(String(10), default="")
    spent_today_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    from_suggestion_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PermissionEvent(Base):
    __tablename__ = "permission_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    permission_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # granted | edited | paused | resumed | revoked | confirmed_live | used |
    # failed | kill_switch_on | kill_switch_off
    kind: Mapped[str] = mapped_column(String(24), index=True)
    intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    amount_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PermissionSuggestion(Base):
    """Archie (or the daily track-record job) asking for a standing permission.
    A suggestion grants nothing: the user turns it into a permission in the UI."""

    __tablename__ = "permission_suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    per_trade_cap_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    daily_cap_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")  # Archie's own words, if any
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # open | dismissed | accepted
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    source: Mapped[str] = mapped_column(String(16), default="system")  # system | archie
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
