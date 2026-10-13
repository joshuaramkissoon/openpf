"""Settings → Connections: every broker, exchange and aggregator account.

Keys go in, never out: responses carry only status, accounts and a masked
hint. Adding a connection verifies the credentials with the provider first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import connections_service, providers
from app.services.config_store import ConfigStore

router = APIRouter(prefix="/connections", tags=["connections"])

_VALUE_LEN = 4096  # Coinbase CDP secrets are PEM private keys


class ConnectRequest(BaseModel):
    provider: str = Field(max_length=32)
    mode: str = Field(default="read", pattern="^(read|trade)$")
    fields: dict[str, str] = Field(default_factory=dict)

    def clean_fields(self) -> dict[str, str]:
        if len(self.fields) > 12 or any(len(k) > 64 or len(v) > _VALUE_LEN for k, v in self.fields.items()):
            raise HTTPException(status_code=422, detail="fields too long")
        return self.fields


class ReconnectRequest(BaseModel):
    mode: str = Field(default="read", pattern="^(read|trade)$")
    fields: dict[str, str] = Field(default_factory=dict)


class EnabledRequest(BaseModel):
    enabled: bool


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/providers")
def list_providers() -> dict[str, Any]:
    return providers.public_catalog()


@router.get("")
def list_connections(db: Session = Depends(get_db)) -> dict[str, Any]:
    return connections_service.list_connections(db)


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Labels and capabilities per account_kind (holding chips, proposal tiles)."""
    return {"accounts": connections_service.account_directory(ConfigStore(db))}


def _sync_now(db: Session, conn_id: str | None) -> None:
    from app.services.portfolio_service import refresh_portfolio

    connections_service.request_sync(db, conn_id)
    try:
        refresh_portfolio(db, force=True)
    except Exception:  # noqa: BLE001 — the connection is saved; the next refresh picks it up
        pass


@router.post("")
def add_connection(payload: ConnectRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        out = connections_service.add_connection(db, payload.provider, payload.clean_fields(), mode=payload.mode)
    except connections_service.ConnectionError_ as exc:
        raise _refused(exc) from exc
    _sync_now(db, out["id"])
    return {**out, **connections_service.list_connections(db)}


@router.put("/{conn_id}")
def reconnect(conn_id: str, payload: ReconnectRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """New keys for an existing connection (also how a read-only crypto
    connection is upgraded to trade: reconnect with a trade key)."""
    current = next((c for c in connections_service.list_connections(db)["connections"] if c["id"] == conn_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail="connection not found")
    try:
        out = connections_service.add_connection(db, current["provider"], payload.fields, mode=payload.mode, conn_id=conn_id)
    except connections_service.ConnectionError_ as exc:
        raise _refused(exc) from exc
    _sync_now(db, conn_id)
    return {**out, **connections_service.list_connections(db)}


@router.post("/{conn_id}/sync")
def sync(conn_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _sync_now(db, conn_id)
    return connections_service.list_connections(db)


@router.post("/{conn_id}/link")
def snaptrade_link(conn_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return {"link_url": connections_service.snaptrade_link(db, conn_id)}
    except connections_service.ConnectionError_ as exc:
        raise _refused(exc) from exc


@router.put("/{conn_id}/enabled")
def set_enabled(conn_id: str, payload: EnabledRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        connections_service.set_enabled(db, conn_id, payload.enabled)
    except connections_service.ConnectionError_ as exc:
        raise _refused(exc) from exc
    return connections_service.list_connections(db)


@router.delete("/{conn_id}")
def remove(conn_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        connections_service.remove_connection(db, conn_id)
    except connections_service.ConnectionError_ as exc:
        raise _refused(exc) from exc
    _sync_now(db, None)
    return connections_service.list_connections(db)
