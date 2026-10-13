from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.telegram import TelegramConfigView

# Hard ceilings on user-set rails: well above any real personal account, low
# enough that a typo (an extra few zeros) or Infinity can't switch a rail off.
_MAX_NOTIONAL = 10_000_000.0
_KEY_LEN = 512


class RiskConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    max_single_order_notional: float = Field(ge=0, le=_MAX_NOTIONAL)
    max_daily_notional: float = Field(ge=0, le=_MAX_NOTIONAL)
    max_position_weight: float = Field(ge=0, le=1)
    duplicate_order_window_seconds: int = Field(ge=15, le=86_400)


class BrokerConfig(BaseModel):
    broker_mode: Literal["paper", "live"]
    autopilot_enabled: bool
    t212_base_env: Literal["live", "demo", "sandbox"]
    scheduler_enabled: bool = False


class CredentialConfig(BaseModel):
    # All optional: a partial update (e.g. only the execution key) leaves the
    # other fields untouched — ConfigStore treats blank/None as "keep existing".
    t212_api_key: str = Field(default="", max_length=_KEY_LEN)
    t212_api_secret: str = Field(default="", max_length=_KEY_LEN)
    enabled: bool = True
    # Dedicated IP-restricted execution (write) key.
    exec_api_key: str = Field(default="", max_length=_KEY_LEN)
    exec_api_secret: str = Field(default="", max_length=_KEY_LEN)
    exec_enabled: bool | None = None


class AccountCredentialView(BaseModel):
    account_kind: Literal["invest", "stocks_isa"]
    enabled: bool
    configured: bool
    exec_enabled: bool = True
    exec_configured: bool = False


class CredentialsConfig(BaseModel):
    invest: CredentialConfig
    stocks_isa: CredentialConfig


class CredentialsPublicView(BaseModel):
    invest: AccountCredentialView
    stocks_isa: AccountCredentialView


class LeveragedConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    enabled: bool
    account_kind: Literal["stocks_isa"] = "stocks_isa"
    auto_execute_enabled: bool
    per_position_notional: float = Field(ge=0, le=_MAX_NOTIONAL)
    max_total_exposure: float = Field(ge=0, le=_MAX_NOTIONAL)
    max_open_positions: int = Field(ge=1, le=100)
    take_profit_pct: float = Field(ge=0, le=10)
    stop_loss_pct: float = Field(ge=0, le=1)
    close_time_uk: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    allow_overnight: bool
    scan_symbols: list[str] = Field(max_length=200)
    instrument_priority: list[str] = Field(max_length=200)


class AppConfigResponse(BaseModel):
    risk: RiskConfig
    broker: BrokerConfig
    telegram: TelegramConfigView
    credentials: CredentialsPublicView
    leveraged: LeveragedConfig
