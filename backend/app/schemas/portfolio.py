from datetime import datetime

from pydantic import BaseModel


class AccountSummary(BaseModel):
    fetched_at: datetime
    account_kind: str = "all"
    currency: str
    free_cash: float
    invested: float
    pie_cash: float
    total: float
    ppl: float


class PositionItem(BaseModel):
    account_kind: str
    ticker: str
    instrument_code: str
    quantity: float
    average_price: float
    current_price: float
    total_cost: float
    value: float
    ppl: float
    weight: float
    momentum_63d: float | None = None
    rsi_14: float | None = None
    trend_score: float | None = None
    risk_flag: str | None = None


class PortfolioMetrics(BaseModel):
    total_value: float
    free_cash: float
    cash_ratio: float
    concentration_hhi: float
    top_position_weight: float
    estimated_beta: float
    estimated_volatility: float


class PortfolioSnapshotResponse(BaseModel):
    account: AccountSummary
    accounts: list[AccountSummary]
    positions: list[PositionItem]
    metrics: PortfolioMetrics


class RefreshResponse(BaseModel):
    fetched_at: datetime
    source: str
    positions_count: int
