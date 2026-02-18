from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Thesis
from app.schemas.thesis import ThesisCreate, ThesisItem, ThesisStatusUpdate

router = APIRouter(prefix="/theses", tags=["theses"])


@router.get("", response_model=list[ThesisItem])
def list_theses(db: Session = Depends(get_db), limit: int = 100) -> list[ThesisItem]:
    rows = list(db.execute(select(Thesis).order_by(desc(Thesis.created_at)).limit(max(1, min(limit, 500)))).scalars().all())
    return [
        ThesisItem(
            id=r.id,
            created_at=r.created_at,
            updated_at=r.updated_at,
            source_run_id=r.source_run_id,
            symbol=r.symbol,
            account_kind=r.account_kind,
            title=r.title,
            thesis=r.thesis,
            catalysts=r.catalysts or [],
            invalidation=r.invalidation,
            confidence=r.confidence,
            status=r.status,
            meta=r.meta or {},
        )
        for r in rows
    ]


@router.post("", response_model=ThesisItem)
def create_thesis(payload: ThesisCreate, db: Session = Depends(get_db)) -> ThesisItem:
    row = Thesis(
        symbol=payload.symbol.upper(),
        account_kind=payload.account_kind,
        title=payload.title,
        thesis=payload.thesis,
        catalysts=payload.catalysts,
        invalidation=payload.invalidation,
        confidence=max(0.0, min(1.0, payload.confidence)),
        status=payload.status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return ThesisItem(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        source_run_id=row.source_run_id,
        symbol=row.symbol,
        account_kind=row.account_kind,
        title=row.title,
        thesis=row.thesis,
        catalysts=row.catalysts or [],
        invalidation=row.invalidation,
        confidence=row.confidence,
        status=row.status,
        meta=row.meta or {},
    )


@router.delete("/{thesis_id}")
def archive_thesis(thesis_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(Thesis, thesis_id)
    if not row:
        raise HTTPException(status_code=404, detail="thesis not found")

    row.status = "archived"
    db.add(row)
    db.commit()
    return {"ok": True, "id": thesis_id, "status": row.status}


@router.patch("/{thesis_id}/status", response_model=ThesisItem)
def update_thesis_status(thesis_id: str, payload: ThesisStatusUpdate, db: Session = Depends(get_db)) -> ThesisItem:
    row = db.get(Thesis, thesis_id)
    if not row:
        raise HTTPException(status_code=404, detail="thesis not found")

    row.status = payload.status
    db.add(row)
    db.commit()
    db.refresh(row)
    return ThesisItem(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        source_run_id=row.source_run_id,
        symbol=row.symbol,
        account_kind=row.account_kind,
        title=row.title,
        thesis=row.thesis,
        catalysts=row.catalysts or [],
        invalidation=row.invalidation,
        confidence=row.confidence,
        status=row.status,
        meta=row.meta or {},
    )
