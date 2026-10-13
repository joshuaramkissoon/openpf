from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    include_watchlist: bool = True


class AgentRunResponse(BaseModel):
    run_id: str
    created_at: datetime
    market_regime: str
    portfolio_score: float
    summary_markdown: str
    intents_created: int
    theses_created: int = 0


class AgentRunItem(BaseModel):
    id: str
    created_at: datetime
    market_regime: str
    portfolio_score: float
    status: str


class TradeIntentItem(BaseModel):
    id: str
    created_at: datetime
    status: str
    symbol: str
    instrument_code: str
    side: Literal["buy", "sell"]
    order_type: str
    quantity: float
    estimated_notional: float
    expected_edge: float
    confidence: float
    risk_score: float
    rationale: str
    broker_mode: str
    account_kind: str | None = None
    # Where the proposal came from ('chat', 'play', 'agent_run', …) and, for play
    # tranches, which play — so the UI can label and link it.
    source: str | None = None
    play_id: str | None = None
    play_title: str | None = None
    # Play tranches: which tranche staged this order, so the UI can group a tranche's legs.
    tranche_index: int | None = None
    tranche_label: str | None = None
    instrument_name: str | None = None
    # "For which play?" — 'play' | 'core' | None (not part of any play). Inferred
    # from the holding's owner when the proposal didn't say.
    owner_kind: str | None = None
    owner_play_id: str | None = None
    owner_label: str | None = None
    owner_inferred: bool = False
    approved_at: datetime | None = None
    # "human", or "permission:<id>" when a standing permission approved it.
    approved_by: str | None = None
    permission_label: str | None = None
    # deploy_cash | rebalance | play_tranche | news_reaction | trim_derisk | other
    category: str | None = None
    executed_at: datetime | None = None
    broker_order_id: str | None = None
    execution_price: float | None = None
    failure_reason: str | None = None
    # Crypto proposals: exchange, market, fee + slippage estimates for the card.
    crypto: dict[str, Any] | None = None
    # Read-only connection (IBKR, SnapTrade, …): never executable — place it in ``place_in``.
    read_only: bool = False
    place_in: str | None = None
    account_label: str | None = None


class IntentDecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class IntentExecuteRequest(BaseModel):
    force_live: bool = False
    # Explicit destination account ('invest' | 'stocks_isa'). When omitted, the
    # account the intent was proposed for is used (then 'invest' as a last resort).
    account_kind: str | None = None
    # Human-UI convenience: approve a 'proposed' intent and execute it in one
    # call (audited as a human approval). Without it the intent must already be
    # 'approved'.
    approve: bool = False


class IntentActionResponse(BaseModel):
    intent_id: str
    status: str
    message: str


class ExecutionEventItem(BaseModel):
    created_at: datetime
    intent_id: str
    level: str
    message: str
    payload: dict = Field(default_factory=dict)
