from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.leveraged import (
    CloseTradeRequest,
    LeveragedActionResponse,
    LeveragedPolicy,
    LeveragedPolicyPatch,
    LeveragedSnapshotResponse,
)
from app.services.leveraged_service import (
    LeveragedError,
    close_trade,
    execute_signal,
    get_policy,
    leveraged_snapshot,
    refresh_instrument_cache_now,
    run_leveraged_cycle,
    scan_signals,
    serialize_trade,
    update_policy,
)

router = APIRouter(prefix="/leveraged", tags=["leveraged"])


@router.get("/snapshot", response_model=LeveragedSnapshotResponse)
def get_snapshot(db: Session = Depends(get_db)) -> LeveragedSnapshotResponse:
    payload = leveraged_snapshot(db)
    return LeveragedSnapshotResponse(**payload)


@router.get("/policy", response_model=LeveragedPolicy)
def policy(db: Session = Depends(get_db)) -> LeveragedPolicy:
    return LeveragedPolicy(**get_policy(db))


@router.patch("/policy", response_model=LeveragedPolicy)
def patch_policy(payload: LeveragedPolicyPatch, db: Session = Depends(get_db)) -> LeveragedPolicy:
    updated = update_policy(db, payload.model_dump(exclude_none=True), actor="user")
    return LeveragedPolicy(**updated)


@router.post("/scan", response_model=LeveragedActionResponse)
def scan(db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        result = scan_signals(db)
        return LeveragedActionResponse(ok=True, message="leveraged scan completed", data=result)
    except LeveragedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cycle", response_model=LeveragedActionResponse)
def cycle(db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        result = run_leveraged_cycle(db)
        return LeveragedActionResponse(ok=True, message="leveraged cycle completed", data=result)
    except LeveragedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/signals/{signal_id}/execute", response_model=LeveragedActionResponse)
def execute(signal_id: str, db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        trade = execute_signal(db, signal_id, source="manual")
        return LeveragedActionResponse(ok=True, message="signal executed", data={"trade": serialize_trade(trade)})
    except LeveragedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/trades/{trade_id}/close", response_model=LeveragedActionResponse)
def close(trade_id: str, payload: CloseTradeRequest, db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        trade = close_trade(db, trade_id, reason=payload.reason)
        return LeveragedActionResponse(ok=True, message="trade closed", data={"trade": serialize_trade(trade)})
    except LeveragedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cache/instruments", response_model=LeveragedActionResponse)
def refresh_instruments(db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        result = refresh_instrument_cache_now(db)
        return LeveragedActionResponse(ok=True, message="instrument cache refreshed", data=result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
