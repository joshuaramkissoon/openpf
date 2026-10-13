"""Smart money API: disclosures as signals (insiders, funds, Congress).

``GET /api/instruments/{ticker}/smart-money`` lives here (not in
``instruments.py``) so the stock sheet can consume it without touching that
router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.smart_money import service

router = APIRouter(tags=["smart-money"])


@router.get("/instruments/{ticker}/smart-money")
def instrument_smart_money(ticker: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Compact summary for one name: insider buyers/sellers (30d, plan sales
    excluded), followed funds holding it, Congress trades (90d) with their
    disclosure delay, any detections, and recent evidence rows. Kicks a
    background Form 4 fetch if the name hasn't been looked at in 24h
    (``refreshing: true`` → poll again in a few seconds)."""
    t = ticker.strip().upper()
    if not t or len(t) > 16:
        raise HTTPException(status_code=422, detail="bad ticker")
    refreshing = service.maybe_refresh_ticker(db, t)
    out = service.ticker_summary(db, t)
    out["refreshing"] = refreshing
    return out


@router.get("/smart-money")
def smart_money(
    ticker: str | None = None,
    sector: str | None = None,
    actor: str | None = None,
    days: int = Query(90, ge=1, le=400),
    include_noise: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.query(db, ticker=ticker, sector=sector, actor=actor, days=days, include_noise=include_noise)


@router.get("/smart-money/trends")
def smart_money_trends(window: int = Query(90, ge=7, le=365), db: Session = Depends(get_db)) -> dict[str, Any]:
    return service.trends_report(db, window=window)


@router.get("/smart-money/status")
def smart_money_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return service.status(db)


@router.post("/smart-money/refresh")
async def smart_money_refresh(db: Session = Depends(get_db)) -> dict[str, Any]:
    return await run_in_threadpool(service.refresh, db)


@router.get("/smart-money/config")
def smart_money_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    return service.public_config(db)


@router.put("/smart-money/config")
def smart_money_config_update(patch: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    return service.update_config(db, patch)
