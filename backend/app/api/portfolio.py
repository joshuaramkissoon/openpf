from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.portfolio import PortfolioSnapshotResponse, RefreshResponse
from app.services.config_store import ConfigStore
from app.services.portfolio_optimizer import compute_rebalance, propose_rebalance
from app.services.portfolio_service import get_portfolio_snapshot, portfolio_history, refresh_portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.post("/refresh", response_model=RefreshResponse)
def refresh(force: bool = Query(default=False), db: Session = Depends(get_db)) -> RefreshResponse:
    try:
        result = refresh_portfolio(db, force=force)
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


@router.get("/history")
def history(
    account_kind: Literal["all", "invest", "stocks_isa"] = Query(default="all"),
    display_currency: Literal["GBP", "USD"] | None = Query(default=None),
    days: int = Query(default=365, ge=1, le=8000),
    db: Session = Depends(get_db),
) -> dict:
    """Portfolio equity + return curve (value, gain net of contributions, Dietz %).
    Includes reconstructed history before the first recorded snapshot; request a
    large ``days`` (e.g. 8000) for the full account lifetime."""
    return portfolio_history(db, account_kind=account_kind, display_currency=display_currency, days=days)


@router.post("/history/backfill")
def history_backfill(
    account_kind: Literal["all", "invest", "stocks_isa"] = Query(default="all"),
    db: Session = Depends(get_db),
) -> dict:
    """Reconstruct the historical equity curve from full T212 order/dividend
    history (slow + rate-limited — runs synchronously here, intended for an
    occasional manual/scheduled rebuild). Persists to reconstructed_equity_daily."""
    from app.services.equity_backfill import backfill_account, backfill_all

    if account_kind == "all":
        return backfill_all(db)
    return backfill_account(db, account_kind)


@router.post("/cashflows/sync")
def cashflows_sync(
    deep: bool = Query(default=False),
    background: BackgroundTasks = None,
    db: Session = Depends(get_db),
) -> dict:
    """Pull deposits/withdrawals/transfers from T212 (deduped), used by the return
    curve to net contributions out of performance.

    ``deep=true`` runs a FULL historical backfill that pages to the start of the
    feed with rate-limit backoff — needed to recover gaps left when an earlier
    sync was rate-limited mid-history. It can take minutes, so it runs off the
    request on its own DB session."""
    from app.services.cashflow_service import backfill_all_cashflows, sync_all

    if deep:
        def _run() -> None:
            from app.core.database import SessionLocal

            with SessionLocal() as session:
                backfill_all_cashflows(session)

        background.add_task(_run)
        return {"started": True, "mode": "deep-backfill"}

    return sync_all(db)


# ── Autopilot rebalancer ────────────────────────────────────────────────────


@router.get("/rebalance")
def rebalance_preview(
    account_kind: Literal["all", "invest", "stocks_isa"] = Query(default="all"),
    db: Session = Depends(get_db),
) -> dict:
    """Preview the autopilot rebalance plan (no intents created)."""
    try:
        return compute_rebalance(db, account_kind=account_kind)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rebalance/propose")
def rebalance_propose(
    account_kind: Literal["all", "invest", "stocks_isa"] = Query(default="all"),
    db: Session = Depends(get_db),
) -> dict:
    """Compute a rebalance and queue its trades as PROPOSED intents for approval."""
    try:
        return propose_rebalance(db, account_kind=account_kind)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rebalance/policy")
def rebalance_policy(db: Session = Depends(get_db)) -> dict:
    """The rebalance policy (caps, min-trade, turnover budget) Archie operates under."""
    return ConfigStore(db).get_rebalance()


@router.patch("/rebalance/policy")
def patch_rebalance_policy(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict:
    """Tune the rebalance policy (e.g. set a per-name cap). Archie does this when you ask in chat."""
    return ConfigStore(db).set_rebalance(payload or {})
