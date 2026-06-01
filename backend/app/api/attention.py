from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import watch_service

router = APIRouter(prefix="/attention", tags=["attention"])


@router.get("")
def get_attention(db: Session = Depends(get_db)) -> dict:
    """Unified 'what needs my attention' payload: ranked alerts + counts."""
    return watch_service.attention_summary(db)


@router.post("/run")
def run_now(db: Session = Depends(get_db)) -> dict:
    """Run all watches now (on-demand) and return what was raised."""
    try:
        return watch_service.run_watches(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/seen-all")
def seen_all(db: Session = Depends(get_db)) -> dict:
    return {"updated": watch_service.mark_all_seen(db)}


@router.post("/{alert_id}/{action}")
def update_alert(alert_id: str, action: Literal["seen", "dismiss"], db: Session = Depends(get_db)) -> dict:
    status = "dismissed" if action == "dismiss" else "seen"
    result = watch_service.set_alert_status(db, alert_id, status)
    if result is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return result
