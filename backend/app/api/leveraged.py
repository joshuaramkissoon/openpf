from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.leveraged import (
    AdoptPositionRequest,
    CloseTradeRequest,
    ClosePositionRequest,
    HeldPositionsResponse,
    LeveragedActionResponse,
    LeveragedPolicy,
    LeveragedPolicyPatch,
    LeveragedSnapshotResponse,
)
from app.services.leveraged_service import (
    LeveragedError,
    adopt_position,
    close_position,
    close_trade,
    execute_signal,
    get_policy,
    leveraged_snapshot,
    list_held_leveraged_positions,
    refresh_instrument_cache_now,
    run_leveraged_cycle,
    scan_signals,
    serialize_trade,
    update_policy,
)
from app.services.leveraged_universe import build_universe
from app.services.macro_calendar import upcoming_events
from app.services.regime_service import compute_regime
from app.services.signal_attribution import compute_attribution

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


@router.get("/positions", response_model=HeldPositionsResponse)
def held_positions(db: Session = Depends(get_db)) -> HeldPositionsResponse:
    """Live leveraged ETPs held in T212 (engine-tracked and not), with P&L."""
    return HeldPositionsResponse(positions=list_held_leveraged_positions(db))


@router.post("/positions/close", response_model=LeveragedActionResponse)
def close_held_position(payload: ClosePositionRequest, db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        trade = close_position(db, payload.instrument_code, quantity=payload.quantity, reason=payload.reason)
        return LeveragedActionResponse(ok=True, message="position closed", data={"trade": serialize_trade(trade)})
    except LeveragedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/positions/adopt", response_model=LeveragedActionResponse)
def adopt_held_position(payload: AdoptPositionRequest, db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        trade = adopt_position(
            db,
            payload.instrument_code,
            stop_loss_pct=payload.stop_loss_pct,
            take_profit_pct=payload.take_profit_pct,
        )
        return LeveragedActionResponse(ok=True, message="position adopted", data={"trade": serialize_trade(trade)})
    except LeveragedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cache/instruments", response_model=LeveragedActionResponse)
def refresh_instruments(db: Session = Depends(get_db)) -> LeveragedActionResponse:
    try:
        result = refresh_instrument_cache_now(db)
        return LeveragedActionResponse(ok=True, message="instrument cache refreshed", data=result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/regime")
def get_regime() -> dict:
    """Current market regime (SPY/QQQ vs SMA50/200 + VIX) with long/inverse bias."""
    return compute_regime().to_dict()


@router.get("/universe")
def get_universe(top_n: int = 8, db: Session = Depends(get_db)) -> dict:
    """Regime-gated, market-driven leveraged watchlist derived from live T212 metadata."""
    try:
        return build_universe(db, top_n=max(1, min(int(top_n), 25)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/attribution")
def get_attribution(lookback_days: int = 120, db: Session = Depends(get_db)) -> dict:
    """Predicted-vs-realized edge from closed trades joined to their signals."""
    return compute_attribution(db, lookback_days=max(7, min(int(lookback_days), 730)))


@router.get("/macro")
def get_macro(within_days: int = 14) -> dict:
    """Upcoming high-impact US macro events (FOMC/CPI/NFP)."""
    days = max(1, min(int(within_days), 90))
    return {"events": upcoming_events(within_days=days), "within_days": days}
