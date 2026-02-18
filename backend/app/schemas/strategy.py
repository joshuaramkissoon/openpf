from datetime import date

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    symbol: str
    lookback_days: int = Field(default=365, ge=120, le=3650)
    fast_window: int = Field(default=20, ge=5, le=120)
    slow_window: int = Field(default=100, ge=20, le=300)


class EquityPoint(BaseModel):
    date: date
    strategy: float
    benchmark: float


class BacktestResponse(BaseModel):
    symbol: str
    lookback_days: int
    fast_window: int
    slow_window: int
    trades: int
    cagr: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    equity_curve: list[EquityPoint]
