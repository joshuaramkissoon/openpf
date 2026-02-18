from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class AppConfig(Base):
    __tablename__ = "app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="invest", index=True)
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    free_cash: Mapped[float] = mapped_column(Float, default=0.0)
    invested: Mapped[float] = mapped_column(Float, default=0.0)
    pie_cash: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    ppl: Mapped[float] = mapped_column(Float, default=0.0)


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="invest", index=True)
    ticker: Mapped[str] = mapped_column(String(64), index=True)
    instrument_code: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    average_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    ppl: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="USD")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(24), default="completed")
    summary_markdown: Mapped[str] = mapped_column(Text, default="")
    market_regime: Mapped[str] = mapped_column(String(32), default="neutral")
    portfolio_score: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class TradeIntent(Base):
    __tablename__ = "trade_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)

    status: Mapped[str] = mapped_column(String(24), default="proposed", index=True)
    broker_mode: Mapped[str] = mapped_column(String(16), default="paper")

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_code: Mapped[str] = mapped_column(String(64), index=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16), default="market")

    quantity: Mapped[float] = mapped_column(Float)
    estimated_notional: Mapped[float] = mapped_column(Float, default=0.0)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    expected_edge: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)

    rationale: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    intent_id: Mapped[str] = mapped_column(String(36), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Thesis(Base):
    __tablename__ = "theses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="all", index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    thesis: Mapped[str] = mapped_column(Text, default="")
    catalysts: Mapped[list[str]] = mapped_column(JSON, default=list)
    invalidation: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    title: Mapped[str] = mapped_column(String(240), default="Portfolio Chat")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    role: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True, default=None)


class LeveragedSignal(Base):
    __tablename__ = "leveraged_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    status: Mapped[str] = mapped_column(String(24), default="proposed", index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_code: Mapped[str] = mapped_column(String(64), index=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="stocks_isa", index=True)
    direction: Mapped[str] = mapped_column(String(12), default="long")
    entry_side: Mapped[str] = mapped_column(String(8), default="buy")

    target_notional: Mapped[float] = mapped_column(Float, default=0.0)
    reference_price: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=0.05)
    take_profit_pct: Mapped[float] = mapped_column(Float, default=0.08)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    expected_edge: Mapped[float] = mapped_column(Float, default=0.0)

    rationale: Mapped[str] = mapped_column(Text, default="")
    strategy_tag: Mapped[str] = mapped_column(String(64), default="momentum")
    linked_intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    linked_trade_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class LeveragedTrade(Base):
    __tablename__ = "leveraged_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    signal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_code: Mapped[str] = mapped_column(String(64), index=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="stocks_isa", index=True)
    direction: Mapped[str] = mapped_column(String(12), default="long")

    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    entry_notional: Mapped[float] = mapped_column(Float, default=0.0)
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=0.05)
    take_profit_pct: Mapped[float] = mapped_column(Float, default=0.08)

    entry_intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    exit_intent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pnl_value: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    cron_expr: Mapped[str] = mapped_column(String(80))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    model: Mapped[str] = mapped_column(String(64), default="claude-sonnet-4-20250514")
    prompt: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_status: Mapped[str] = mapped_column(String(24), default="idle")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class ScheduledTaskLog(Base):
    __tablename__ = "scheduled_task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="ok")
    message: Mapped[str] = mapped_column(Text, default="")
    output_path: Mapped[str | None] = mapped_column(String(360), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
