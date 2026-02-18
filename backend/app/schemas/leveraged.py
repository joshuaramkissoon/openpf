from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LeveragedPolicy(BaseModel):
    enabled: bool
    account_kind: Literal["stocks_isa"] = "stocks_isa"
    auto_execute_enabled: bool
    per_position_notional: float = Field(ge=0)
    max_total_exposure: float = Field(ge=0)
    max_open_positions: int = Field(ge=1)
    take_profit_pct: float = Field(ge=0)
    stop_loss_pct: float = Field(ge=0)
    close_time_uk: str
    allow_overnight: bool
    scan_symbols: list[str]
    instrument_priority: list[str]


class LeveragedPolicyPatch(BaseModel):
    enabled: bool | None = None
    auto_execute_enabled: bool | None = None
    per_position_notional: float | None = None
    max_total_exposure: float | None = None
    max_open_positions: int | None = None
    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None
    close_time_uk: str | None = None
    allow_overnight: bool | None = None
    scan_symbols: list[str] | None = None
    instrument_priority: list[str] | None = None


class LeveragedSignalItem(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    symbol: str
    instrument_code: str
    account_kind: str
    direction: str
    entry_side: str
    target_notional: float
    reference_price: float
    stop_loss_pct: float
    take_profit_pct: float
    confidence: float
    expected_edge: float
    rationale: str
    strategy_tag: str
    linked_intent_id: str | None = None
    linked_trade_id: str | None = None
    source_task_id: str | None = None
    meta: dict[str, Any] = {}


class LeveragedTradeItem(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    signal_id: str | None = None
    status: str
    symbol: str
    instrument_code: str
    account_kind: str
    direction: str
    quantity: float
    entry_price: float
    entry_notional: float
    entered_at: datetime
    stop_loss_pct: float
    take_profit_pct: float
    entry_intent_id: str | None = None
    exit_intent_id: str | None = None
    exit_price: float | None = None
    exit_notional: float | None = None
    exited_at: datetime | None = None
    close_reason: str | None = None
    pnl_value: float
    pnl_pct: float
    meta: dict[str, Any] = {}
    current_price: float | None = None
    current_value: float | None = None
    current_pnl_value: float | None = None
    current_pnl_pct: float | None = None


class TaskLogItem(BaseModel):
    id: int
    created_at: datetime
    task_id: str
    status: str
    message: str
    output_path: str | None = None
    payload: dict[str, Any] = {}


class LeveragedSummary(BaseModel):
    open_positions: int
    open_exposure: float
    max_total_exposure: float
    open_unrealized_pnl: float
    closed_realized_pnl: float
    win_rate: float
    wins: int
    losses: int
    closed_trades: int


class LeveragedSnapshotResponse(BaseModel):
    policy: LeveragedPolicy
    summary: LeveragedSummary
    open_trades: list[LeveragedTradeItem]
    closed_trades: list[LeveragedTradeItem]
    signals: list[LeveragedSignalItem]
    recent_task_logs: list[TaskLogItem]


class LeveragedActionResponse(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] = {}


class CloseTradeRequest(BaseModel):
    reason: str = Field(default="manual")
