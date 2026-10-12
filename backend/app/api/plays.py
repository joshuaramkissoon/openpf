from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.plays import PlayCreate, PlayDraftRequest, PlayUpdate, StageRequest
from app.services import plays_service as ps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plays", tags=["plays"])


def _get_or_404(db: Session, play_id: str):
    play = ps.get_play(db, play_id)
    if not play:
        raise HTTPException(status_code=404, detail="Play not found")
    return play


@router.get("")
def list_plays(status: str = Query(default="all"), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [ps.serialize_play(p) for p in ps.list_plays(db, status=status)]


@router.post("")
def create_play(payload: PlayCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.serialize_play(ps.create_play(db, payload.model_dump()))


# Draft routes come before /{play_id} so "draft" isn't captured as an id.
@router.post("/draft")
def start_draft(payload: PlayDraftRequest) -> dict[str, Any]:
    return ps.start_draft_job(payload.model_dump()).as_dict()


@router.get("/draft/{job_id}")
def draft_status(job_id: str) -> dict[str, Any]:
    job = ps.get_draft_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Draft job not found (it may have expired)")
    return job.as_dict()


@router.post("/review-all")
def review_all(db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.review_active_plays(db)


@router.get("/{play_id}")
def get_play(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.serialize_play(_get_or_404(db, play_id))


@router.patch("/{play_id}")
def update_play(play_id: str, payload: PlayUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        play = ps.update_play(db, play, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ps.serialize_play(play)


@router.delete("/{play_id}")
def delete_play(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    ps.delete_play(db, _get_or_404(db, play_id))
    return {"ok": True}


@router.post("/{play_id}/stage")
def stage_tranche(play_id: str, payload: StageRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.stage_tranche(db, play, payload.tranche_index if payload else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{play_id}/performance")
def performance(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.play_performance(db, play)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{play_id}/review")
def review(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        ps.run_llm_review(db, play)
    except Exception as exc:  # noqa: BLE001
        logger.exception("play review failed")
        raise HTTPException(status_code=502, detail=f"Review failed: {exc}") from exc
    return ps.serialize_play(play)
