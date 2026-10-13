"""Crypto exchange: connection (trade-only keys), overview, risk, starter play.

Keys go in, never out: responses carry only a masked hint. Connecting verifies
the key with the exchange and refuses one that can withdraw or transfer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import crypto_service
from app.services.config_store import ConfigStore
from app.services.crypto_exchanges import public_catalog

router = APIRouter(prefix="/crypto", tags=["crypto"])

_KEY_LEN = 4096  # Coinbase CDP secrets are PEM private keys


class ConnectRequest(BaseModel):
    exchange: str = Field(max_length=32)
    api_key: str = Field(max_length=_KEY_LEN)
    api_secret: str = Field(max_length=_KEY_LEN)


class EnabledRequest(BaseModel):
    enabled: bool


class StarterPlayRequest(BaseModel):
    conviction: str = Field(default="low", pattern="^(low|medium|high)$")
    include_eth: bool = True
    weeks: int = Field(default=10, ge=4, le=16)
    budget_gbp: float | None = Field(default=None, ge=0, le=10_000_000)


@router.get("/exchanges")
def exchanges() -> list[dict[str, Any]]:
    return public_catalog()


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return crypto_service.public(ConfigStore(db))


@router.put("/connection")
def connect(payload: ConnectRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return crypto_service.connect(ConfigStore(db), payload.exchange, payload.api_key, payload.api_secret)
    except crypto_service.CryptoConnectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/connection")
def disconnect(db: Session = Depends(get_db)) -> dict[str, Any]:
    return crypto_service.disconnect(ConfigStore(db))


@router.put("/enabled")
def set_enabled(payload: EnabledRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return crypto_service.set_enabled(ConfigStore(db), payload.enabled)


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return crypto_service.overview(db)


@router.get("/risk")
def risk(force: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return crypto_service.crypto_risk(db, force=force)
    except Exception as exc:  # noqa: BLE001 — risk is best-effort market data
        raise HTTPException(status_code=503, detail=f"Crypto risk is unavailable right now: {exc}") from exc


@router.post("/starter-play")
def starter_play(payload: StarterPlayRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create the built-in Starter crypto play as a draft (no orders)."""
    from app.services import crypto_play, plays_service

    try:
        play = crypto_play.create_starter_play(db, conviction=payload.conviction, include_eth=payload.include_eth,
                                               weeks=payload.weeks, budget_gbp=payload.budget_gbp)
    except plays_service.PlayInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return plays_service.serialize_play(play)
