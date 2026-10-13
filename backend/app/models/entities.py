from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
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


class FxRateDaily(Base):
    """Backfilled daily FX so historical portfolio points convert at the rate that
    held on their own date, not today's spot. Stored as USD per 1 GBP (FRED DEXUSUK);
    all GBP↔USD conversions derive from this single series."""

    __tablename__ = "fx_rate_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    usd_per_gbp: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="fred")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CashflowEvent(Base):
    """External account cashflows (deposits/withdrawals/transfers/fees) from the
    T212 transactions feed. Used to net contributions out of the return curve.
    Deduped by (account_kind, reference) so ingestion is idempotent."""

    __tablename__ = "cashflow_events"
    __table_args__ = (UniqueConstraint("account_kind", "reference", name="uq_cashflow_account_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="invest", index=True)
    reference: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(24), index=True)  # DEPOSIT/WITHDRAW/TRANSFER/FEE/...
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # signed, in `currency`
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReconstructedEquityDaily(Base):
    """Backfilled daily portfolio value, reconstructed from T212 order/dividend/
    transaction history valued at historical prices + FX. Fills the equity curve
    for the period *before* the app began recording live AccountSnapshots. Each
    point is an estimate (exact at the right edge, where it's anchored to live
    holdings); recorded snapshots take over from the first real snapshot onward.
    Deduped by (account_kind, date) so a re-run overwrites cleanly."""

    __tablename__ = "reconstructed_equity_daily"
    __table_args__ = (UniqueConstraint("account_kind", "date", name="uq_recon_equity_account_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_kind: Mapped[str] = mapped_column(String(24), default="invest", index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)     # holdings + cash, in `currency`
    invested: Mapped[float] = mapped_column(Float, default=0.0)  # holdings (market) value
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="GBP")
    built_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)   # chat | scheduled | agent_run
    source_id: Mapped[str] = mapped_column(String(240), index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)


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


class Alert(Base):
    """A proactive 'needs your attention' item raised by the watch service.

    Deduped via ``dedupe_key`` so the same condition doesn't re-fire every cycle.
    """

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # thesis_invalidation | concentration | earnings | big_move | news | rebalance
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(12), default="info")  # info | warning | critical
    title: Mapped[str] = mapped_column(String(240), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    consider: Mapped[str | None] = mapped_column(Text, nullable=True)  # optional suggested action
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)  # new | seen | dismissed
    dedupe_key: Mapped[str] = mapped_column(String(160), index=True)
    source: Mapped[str] = mapped_column(String(48), default="watch")
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class WatchlistItem(Base):
    """A tracked idea on the watchlist — the layer between a passing mention and a
    full Thesis. Holds *why* a symbol is being watched (``note``, which doubles as
    the natural-language watch condition) plus the knobs that let the watch service
    resurface it (``target_price``/``target_direction``, ``monitor``). CRUD'd by the user
    (UI) or Archie (watchlist MCP). ``active_symbols`` (status='watching') is what the
    agent run + leveraged scan consume — this table replaced the old config symbol list.
    """

    __tablename__ = "watchlist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | archie | agent_run
    conviction: Mapped[str | None] = mapped_column(String(12), nullable=True)  # low | medium | high
    status: Mapped[str] = mapped_column(String(16), default="watching", index=True)  # watching | acted | archived

    # Optional alert level. target_direction is 'above' (sell/trim watch) or 'below' (entry watch).
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_direction: Mapped[str | None] = mapped_column(String(8), nullable=True)  # above | below
    monitor: Mapped[bool] = mapped_column(Boolean, default=True)  # false = keep on board but don't ping

    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    model: Mapped[str] = mapped_column(String(64), default="claude-sonnet-4-6")
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


class Play(Base):
    """A theme bet: a plain-English thesis turned into a sized, staged, monitored
    basket. Orders are never placed from here — staging a tranche creates
    ``proposed`` TradeIntents that still need human approval."""

    __tablename__ = "plays"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    title: Mapped[str] = mapped_column(String(240), default="")
    thesis: Mapped[str] = mapped_column(Text, default="")  # markdown
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft | active | paused | closed
    conviction: Mapped[str] = mapped_column(String(12), default="medium")  # low | medium | high
    horizon_months: Mapped[int] = mapped_column(Integer, default=12)
    account_kind: Mapped[str] = mapped_column(String(24), default="stocks_isa")  # invest | stocks_isa
    budget_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    budget_pct_of_book: Mapped[float] = mapped_column(Float, default=0.0)

    # [{ticker, instrument_code, name, role, target_weight, rationale, currency?}]
    legs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # {rationale, tranches: [{label, pct_of_budget, trigger}], max_loss_gbp, ...}
    sizing: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # [{text, rule?: {type, ticker?, value}}]
    kill_criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    what_would_change_my_mind: Mapped[str] = mapped_column(Text, default="")
    # {last_reviewed_at, verdict, notes, history: [...]}
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Ring 2 — holdings owned elsewhere (another play or Core) that matter to this
    # theme: [{ticker, why, added_at}]. Never counted in the play's size.
    related: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Ring 3 — not-held candidates for later tranches: [{ticker, name, why, instrument_verified, added_at}]
    bench: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    source_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class Recipe(Base):
    """A recipe: a skill Archie runs (instructions in a SKILL.md) + a trigger +
    delivery. Instructions only — a recipe can never grant tools, touch risk
    rails or execute anything; runs use the scheduled-task MCP context, so the
    most a recipe can do is create proposals. Versions live in RecipeVersion."""

    __tablename__ = "recipes"

    slug: Mapped[str] = mapped_column(String(48), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    source: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user
    created_by: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user | archie
    title: Mapped[str] = mapped_column(String(80), default="")
    summary: Mapped[str] = mapped_column(String(300), default="")
    icon: Mapped[str] = mapped_column(String(40), default="sparkles")
    body: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # {"kind": "manual"} | {"kind": "schedule", "cron", "timezone"} |
    # {"kind": "event", "event": "earnings_week"|"big_move", "threshold_pct"?}
    trigger: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    delivery: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"home": true, "telegram": bool}
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class RecipeVersion(Base):
    """Immutable snapshot of a recipe after each change (for diff + revert)."""

    __tablename__ = "recipe_versions"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_recipe_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    slug: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[int] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(16), default="user")  # builtin | user | archie
    note: Mapped[str] = mapped_column(String(240), default="")
    title: Mapped[str] = mapped_column(String(80), default="")
    summary: Mapped[str] = mapped_column(String(300), default="")
    icon: Mapped[str] = mapped_column(String(40), default="sparkles")
    body: Mapped[str] = mapped_column(Text, default="")
    trigger: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    delivery: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RecipeSuggestion(Base):
    """A change Archie proposed from an unattended (scheduled) run. Never applied
    until the user approves it — news text can't rewrite the harness."""

    __tablename__ = "recipe_suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    slug: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)  # None → new recipe
    action: Mapped[str] = mapped_column(String(16), default="update")  # create | update | enable | disable
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(600), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | accepted | dismissed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HoldingOwner(Base):
    """Explicit owner of one holding (per account): a play, Core or Unsorted.

    Holdings without a row get a derived owner (the active play whose basket
    holds them, else Unsorted) — see ``plays_service.compute_book``."""

    __tablename__ = "holding_owners"
    __table_args__ = (UniqueConstraint("account_kind", "ticker", name="uq_holding_owner"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    account_kind: Mapped[str] = mapped_column(String(24), index=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)  # display ticker, upper-case
    owner_kind: Mapped[str] = mapped_column(String(12), default="unsorted")  # play | core | unsorted
    play_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="user")  # user | archie | suggestion | system


class PlayDraftJob(Base):
    """A play draft running (or run) in the background. Persisted so a restart
    mid-draft surfaces as a failed job with Retry instead of a stuck spinner."""

    __tablename__ = "play_draft_jobs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)  # running | done | error
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    play_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # friendly, shown to the user
    # unclear_idea | no_instruments | interrupted | model | failed
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(16), nullable=True)
