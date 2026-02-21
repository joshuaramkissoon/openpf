from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.costs import CostBySource, CostSummary, UsageRecordItem
from app.services import costs_service

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("/summary", response_model=CostSummary)
def get_summary(db: Session = Depends(get_db)):
    data = costs_service.get_summary(db)
    data["by_source"] = CostBySource(**data["by_source"])
    return CostSummary(**data)


@router.get("/records", response_model=list[UsageRecordItem])
def get_records(limit: int = 100, db: Session = Depends(get_db)):
    rows = costs_service.list_records(db, limit=limit)
    return [UsageRecordItem.model_validate(r) for r in rows]
