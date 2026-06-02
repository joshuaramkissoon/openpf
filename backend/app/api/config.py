from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import MessageResponse
from app.schemas.config import (
    AppConfigResponse,
    BrokerConfig,
    CredentialConfig,
    CredentialsConfig,
    CredentialsPublicView,
    LeveragedConfig,
    RiskConfig,
)
from app.schemas.telegram import TelegramConfigUpdate, TelegramConfigView
from app.services.config_store import ConfigStore

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=AppConfigResponse)
def get_config(db: Session = Depends(get_db)) -> AppConfigResponse:
    store = ConfigStore(db)
    assembled = store.assembled_public()
    return AppConfigResponse(**assembled)


@router.put("/risk", response_model=RiskConfig)
def update_risk(payload: RiskConfig, db: Session = Depends(get_db)) -> RiskConfig:
    store = ConfigStore(db)
    value = store.set_risk(payload.model_dump())
    return RiskConfig(**value)


@router.put("/broker", response_model=BrokerConfig)
def update_broker(payload: BrokerConfig, db: Session = Depends(get_db)) -> BrokerConfig:
    store = ConfigStore(db)
    value = store.set_broker(payload.model_dump())
    # Apply the scheduler toggle at runtime so it takes effect without a restart
    # or any .env change — start/stop the in-process scheduler to match.
    try:
        from app.services.scheduler import start_scheduler, stop_scheduler

        if value.get("scheduler_enabled"):
            start_scheduler()
        else:
            stop_scheduler()
    except Exception:  # noqa: BLE001 — never fail the config save on scheduler hiccup
        pass
    return BrokerConfig(**value)


@router.put("/leveraged/auto-execute", response_model=LeveragedConfig)
def set_leveraged_auto_execute(enabled: bool, db: Session = Depends(get_db)) -> LeveragedConfig:
    """Toggle whether the leveraged alpha loop AUTO-EXECUTES within rails.

    The single switch that turns the loop from propose-only into live auto-trading
    (also needs broker_mode=live + a trade-enabled key). Kept separate so it's a
    one-field flip from the Settings UI, not a full-policy round-trip.
    """
    store = ConfigStore(db)
    value = store.set_leveraged({"auto_execute_enabled": bool(enabled)})
    return LeveragedConfig(**value)


@router.get("/credentials", response_model=CredentialsPublicView)
def get_credentials(db: Session = Depends(get_db)) -> CredentialsPublicView:
    store = ConfigStore(db)
    return CredentialsPublicView(**store.credentials_public())


@router.put("/credentials", response_model=MessageResponse)
def update_credentials(payload: CredentialsConfig, db: Session = Depends(get_db)) -> MessageResponse:
    store = ConfigStore(db)
    store.set_credentials(payload.model_dump())
    return MessageResponse(message="credentials updated")


@router.put("/credentials/{account_kind}", response_model=MessageResponse)
def update_account_credentials(
    account_kind: Literal["invest", "stocks_isa"],
    payload: CredentialConfig,
    db: Session = Depends(get_db),
) -> MessageResponse:
    store = ConfigStore(db)
    store.set_account_credentials(account_kind, payload.model_dump())
    return MessageResponse(message=f"{account_kind} credentials updated")


@router.put("/leveraged", response_model=LeveragedConfig)
def update_leveraged(payload: LeveragedConfig, db: Session = Depends(get_db)) -> LeveragedConfig:
    store = ConfigStore(db)
    value = store.set_leveraged(payload.model_dump())
    return LeveragedConfig(**value)


@router.put("/telegram", response_model=TelegramConfigView)
def update_telegram(payload: TelegramConfigUpdate, db: Session = Depends(get_db)) -> TelegramConfigView:
    store = ConfigStore(db)
    store.set_telegram(payload.model_dump())
    return TelegramConfigView(**store.telegram_public())
