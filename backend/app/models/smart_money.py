"""Smart-money disclosure tables (SEC Form 4, SEC 13F, US Congress STOCK Act).

Kept in their own module so the shared ``entities.py`` only gains one import.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class Disclosure(Base):
    """One normalised disclosed trade (or quarter-on-quarter 13F change).

    ``external_id`` is the dedupe key (unique per source): Form 4 =
    accession + row index, 13F = manager CIK + period + CUSIP, Congress =
    provider id or a hash of member/ticker/date/type/amount.
    """

    __tablename__ = "disclosures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    source: Mapped[str] = mapped_column(String(16), index=True)  # form4 | 13f | congress
    external_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    actor: Mapped[str] = mapped_column(String(200), default="", index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="")  # insider | fund | congress
    actor_role: Mapped[str] = mapped_column(String(160), default="")  # "CFO", "Director", "House CA-17", fund name…
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    instrument_code: Mapped[str | None] = mapped_column(String(48), nullable=True)  # T212 code when found
    issuer: Mapped[str] = mapped_column(String(200), default="")
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    side: Mapped[str] = mapped_column(String(8), default="")  # buy | sell
    # form4: open_market | plan_sale | exercise_sale | option_exercise | gift | award | tax | other
    # 13f: new | add | trim | exit ; congress: purchase | sale | partial_sale | exchange
    kind: Mapped[str] = mapped_column(String(24), default="")
    size_low: Mapped[float | None] = mapped_column(Float, nullable=True)  # USD
    size_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_label: Mapped[str] = mapped_column(String(64), default="")
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    disclosed_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(String(400), default="")
    # False for noise kept for completeness: option exercises, 10b5-1 plan
    # sales, gifts, awards, tax withholding, exchanges.
    informative: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    noise_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="")  # sec | bargo | capitol_api | fixture
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class DisclosureCache(Base):
    """Tiny key/value cache for upstream responses and lookups (CIK map,
    CUSIP→ticker, SIC sector, 13F holdings, rate-limit budgets)."""

    __tablename__ = "disclosure_cache"

    key: Mapped[str] = mapped_column(String(240), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    payload: Mapped[str] = mapped_column(Text, default="")
