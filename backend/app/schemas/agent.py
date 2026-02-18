from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    include_watchlist: bool = True
    execute_auto: bool = False


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
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    broker_order_id: str | None = None
    execution_price: float | None = None
    failure_reason: str | None = None


class IntentDecisionRequest(BaseModel):
    note: str | None = None


class IntentExecuteRequest(BaseModel):
    force_live: bool = False


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
