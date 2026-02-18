from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.telegram import TelegramConfigView


class RiskConfig(BaseModel):
    max_single_order_notional: float = Field(ge=0)
    max_daily_notional: float = Field(ge=0)
    max_position_weight: float = Field(ge=0, le=1)
    duplicate_order_window_seconds: int = Field(ge=15)


class BrokerConfig(BaseModel):
    broker_mode: Literal["paper", "live"]
    autopilot_enabled: bool
    t212_base_env: Literal["live", "demo"]


class CredentialConfig(BaseModel):
    t212_api_key: str
    t212_api_secret: str
    enabled: bool = True


class AccountCredentialView(BaseModel):
    account_kind: Literal["invest", "stocks_isa"]
    enabled: bool
    configured: bool


class CredentialsConfig(BaseModel):
    invest: CredentialConfig
    stocks_isa: CredentialConfig


class CredentialsPublicView(BaseModel):
    invest: AccountCredentialView
    stocks_isa: AccountCredentialView


class WatchlistConfig(BaseModel):
    symbols: list[str]


class LeveragedConfig(BaseModel):
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


class AppConfigResponse(BaseModel):
    risk: RiskConfig
    broker: BrokerConfig
    watchlist: list[str]
    telegram: TelegramConfigView
    credentials: CredentialsPublicView
    leveraged: LeveragedConfig
