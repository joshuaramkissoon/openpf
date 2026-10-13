"""Track record + standing permissions.

The permission endpoints are the ONLY way to create, edit, resume or confirm a
standing permission — they are called by the app UI. Archie can't reach them
(the SDK hooks block the agent from the app's own HTTP API) and no MCP tool
wraps them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import permissions_service as ps
from app.services import track_record_service as tr

router = APIRouter(tags=["autonomy"])

_MAX = 1_000_000.0


# ── track record ─────────────────────────────────────────────────────────────

@router.get("/track-record")
def track_record(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)) -> dict[str, Any]:
    return tr.summary(db, limit=limit)


@router.post("/track-record/refresh")
def track_record_refresh(backfill: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = tr.mark_outcomes(db, backfill=backfill)
    return {"counts": counts, **tr.summary(db)}


class TrackRecordConfig(BaseModel):
    benchmark: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.^=\-]+$")


@router.put("/track-record/config")
def track_record_config(payload: TrackRecordConfig, db: Session = Depends(get_db)) -> dict[str, Any]:
    return tr.set_config(db, payload.model_dump())


# ── permissions ──────────────────────────────────────────────────────────────

class PermissionCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    kind: Literal["play_tranche", "rebalance", "deploy_cash"]
    per_trade_cap_gbp: float = Field(gt=0, le=_MAX)
    daily_cap_gbp: float = Field(gt=0, le=_MAX)
    expires_at: datetime | None = None
    play_ids: list[str] = Field(default_factory=list, max_length=50)
    label: str | None = Field(default=None, max_length=200)
    # Required for the permission to apply in live mode; only honoured when the
    # broker is in live mode at the time.
    confirm_live: bool = False
    from_suggestion_id: str | None = None


class PermissionPatch(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    per_trade_cap_gbp: float | None = Field(default=None, gt=0, le=_MAX)
    daily_cap_gbp: float | None = Field(default=None, gt=0, le=_MAX)
    expires_at: datetime | None = None
    clear_expiry: bool = False
    play_ids: list[str] | None = Field(default=None, max_length=50)
    label: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    confirm_live: bool = False


class KillSwitch(BaseModel):
    paused: bool


def _err(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _one(db: Session, perm) -> dict[str, Any]:  # noqa: ANN001
    listing = ps.list_permissions(db)
    return next(p for p in listing["permissions"] if p["id"] == perm.id)


@router.get("/permissions")
def permissions(db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.list_permissions(db)


@router.post("/permissions")
def create(payload: PermissionCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        perm = ps.create_permission(db, **payload.model_dump())
    except ps.PermissionError_ as exc:
        raise _err(exc) from exc
    return _one(db, perm)


@router.patch("/permissions/{permission_id}")
def edit(permission_id: str, payload: PermissionPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True, exclude={"confirm_live", "clear_expiry"})
    patch = {k: v for k, v in patch.items() if v is not None or k == "expires_at"}
    if payload.clear_expiry:
        patch["expires_at"] = None
    elif "expires_at" in patch and patch["expires_at"] is None:
        patch.pop("expires_at")
    try:
        perm = ps.update_permission(db, permission_id, patch, confirm_live=payload.confirm_live)
    except ps.PermissionError_ as exc:
        raise _err(exc) from exc
    return _one(db, perm)


@router.post("/permissions/{permission_id}/confirm-live")
def confirm_live(permission_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        perm = ps.confirm_live(db, permission_id)
    except ps.PermissionError_ as exc:
        raise _err(exc) from exc
    return _one(db, perm)


@router.post("/permissions/{permission_id}/revoke")
def revoke(permission_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        perm = ps.revoke_permission(db, permission_id)
    except ps.PermissionError_ as exc:
        raise _err(exc) from exc
    return _one(db, perm)


@router.get("/permissions/history")
def all_history(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return ps.history(db, limit=limit)


@router.get("/permissions/digest")
def digest(db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.digest(db)


@router.put("/permissions/kill-switch")
def kill_switch(payload: KillSwitch, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.set_kill_switch(db, payload.paused)


@router.get("/permissions/suggestions")
def suggestions(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return ps.list_suggestions(db)


@router.post("/permissions/suggestions/{suggestion_id}/dismiss")
def dismiss(suggestion_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return ps.dismiss_suggestion(db, suggestion_id)
    except ps.PermissionError_ as exc:
        raise _err(exc) from exc


@router.get("/permissions/{permission_id}/history")
def one_history(permission_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return ps.history(db, permission_id)
