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
    WatchlistConfig,
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
    return BrokerConfig(**value)


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


@router.put("/watchlist", response_model=WatchlistConfig)
def update_watchlist(payload: WatchlistConfig, db: Session = Depends(get_db)) -> WatchlistConfig:
    store = ConfigStore(db)
    value = store.set_watchlist(payload.model_dump())
    return WatchlistConfig(**value)


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
