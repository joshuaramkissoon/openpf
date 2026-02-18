from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.portfolio import PortfolioSnapshotResponse, RefreshResponse
from app.services.portfolio_service import get_portfolio_snapshot, refresh_portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.post("/refresh", response_model=RefreshResponse)
def refresh(db: Session = Depends(get_db)) -> RefreshResponse:
    try:
        result = refresh_portfolio(db)
        return RefreshResponse(**result)
    except Exception:
        # Keep UI usable under upstream rate-limit spikes by falling back to latest snapshot.
        snapshot = get_portfolio_snapshot(db, account_kind="all")
        account = snapshot.get("account", {})
        fetched_at = account.get("fetched_at") or datetime.utcnow()
        return RefreshResponse(
            fetched_at=fetched_at,
            positions_count=len(snapshot.get("positions", [])),
            source="refresh-error-cache",
        )


@router.get("/snapshot", response_model=PortfolioSnapshotResponse)
def snapshot(
    account_kind: Literal["all", "invest", "stocks_isa"] = Query(default="all"),
    display_currency: Literal["GBP", "USD"] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PortfolioSnapshotResponse:
    result = get_portfolio_snapshot(db, account_kind=account_kind, display_currency=display_currency)
    return PortfolioSnapshotResponse(**result)
