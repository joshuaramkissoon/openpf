from datetime import datetime

from pydantic import BaseModel


class AccountSummary(BaseModel):
    fetched_at: datetime
    account_kind: str = "all"
    currency: str
    free_cash: float
    invested: float | None = None
    pie_cash: float | None = None
    total: float | None = None
    ppl: float | None = None


class PositionItem(BaseModel):
    account_kind: str
    ticker: str
    # Real current market ticker (T212 shortName) for display; falls back to `ticker`.
    # e.g. Nebius is held as YNDX_US_EQ but trades as NBIS.
    display_ticker: str | None = None
    instrument_code: str
    name: str | None = None
    # yfinance symbol resolved from T212 venue metadata (e.g. NUCGl_EQ → NUCG.L)
    # so charts/market-data work for London/Xetra/Euronext listings, not just US.
    yfinance_ticker: str | None = None
    # Venue quote currency (USD/GBX/GBP/EUR…); GBX means prices are in pence.
    instrument_currency: str | None = None
    quantity: float
    average_price: float
    current_price: float | None = None
    total_cost: float
    value: float | None = None
    ppl: float | None = None
    weight: float | None = None
    momentum_63d: float | None = None
    rsi_14: float | None = None
    trend_score: float | None = None
    volatility_30d: float | None = None
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
