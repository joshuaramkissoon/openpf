from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.plays import (
    AcceptSuggestionRequest,
    AssignOwnerRequest,
    PlayCreate,
    PlayDraftRequest,
    PlayUpdate,
    PromoteBenchRequest,
    RingItemRequest,
    StageRequest,
    SuggestRequest,
)
from app.services import book_service as bs
from app.services import plays_service as ps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plays", tags=["plays"])

PORTFOLIO_UNAVAILABLE = "Your portfolio isn't available right now — check the broker connection and try again."


def _get_or_404(db: Session, play_id: str):
    play = ps.get_play(db, play_id)
    if not play:
        raise HTTPException(status_code=404, detail="This play doesn't exist any more.")
    return play


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_plays(status: str = Query(default="all"), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [ps.serialize_play(p) for p in ps.list_plays(db, status=status)]


@router.post("")
def create_play(payload: PlayCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return ps.serialize_play(ps.create_play(db, payload.model_dump()))
    except ValueError as exc:
        raise _bad(exc) from exc


# Fixed routes come before /{play_id} so they aren't captured as an id.

# ── The book by play ─────────────────────────────────────────────────────────

@router.get("/book")
def book(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every holding with its owner, grouped by play + Core + Unsorted, with each
    play's three rings (owned / related / bench), needs-you cues and theme risk."""
    try:
        return bs.compute_book(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("book unavailable: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=PORTFOLIO_UNAVAILABLE) from exc


@router.post("/owners")
def assign_owner(payload: AssignOwnerRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Move a holding to a play, Core or Unsorted. Never trades."""
    try:
        rows = bs.assign_owner(db, payload.ticker, payload.owner, account_kind=payload.account_kind, source="user")
    except bs.OwnershipError as exc:
        raise _bad(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=PORTFOLIO_UNAVAILABLE) from exc
    return {"ok": True, "moved": rows}


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return bs.get_suggestions(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=PORTFOLIO_UNAVAILABLE) from exc


@router.post("/suggestions")
def generate_suggestions(payload: SuggestRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """A fast model groups Unsorted holdings (a few seconds); the theme rules are
    the fallback if it fails or runs past ``MODEL_WAIT_S``. Nothing is applied."""
    try:
        return bs.generate_suggestions(db, use_model=payload.use_model if payload else True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggestions failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=PORTFOLIO_UNAVAILABLE) from exc


@router.post("/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: str, payload: AcceptSuggestionRequest | None = None,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    p = payload or AcceptSuggestionRequest()
    try:
        return bs.accept_suggestion(db, suggestion_id, title=p.title, tickers=p.tickers, kind=p.kind)
    except ValueError as exc:
        raise _bad(exc) from exc


@router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(suggestion_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        bs.dismiss_suggestion(db, suggestion_id)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"ok": True}


# ── Drafting ─────────────────────────────────────────────────────────────────

@router.post("/draft")
def start_draft(payload: PlayDraftRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return ps.start_draft_job(db, payload.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc


@router.get("/draft/{job_id}")
def draft_status(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = ps.get_draft_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="That draft has gone — start a new one.")
    return job


@router.post("/draft/{job_id}/retry")
def retry_draft(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return ps.retry_draft_job(db, job_id)
    except ValueError as exc:
        raise _bad(exc) from exc


@router.post("/review-all")
def review_all(db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.review_active_plays(db)


# ── One play ─────────────────────────────────────────────────────────────────

@router.get("/{play_id}")
def get_play(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.serialize_play(_get_or_404(db, play_id))


@router.patch("/{play_id}")
def update_play(play_id: str, payload: PlayUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        play = ps.update_play(db, play, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise _bad(exc) from exc
    return ps.serialize_play(play)


@router.delete("/{play_id}")
def delete_play(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    moved = ps.delete_play(db, _get_or_404(db, play_id))
    return {"ok": True, "moved_to_unsorted": moved}


@router.get("/{play_id}/rings")
def rings(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return bs.play_rings(db, play)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=PORTFOLIO_UNAVAILABLE) from exc


@router.post("/{play_id}/related")
def add_related(play_id: str, payload: RingItemRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.serialize_play(bs.add_related(db, play, payload.ticker, payload.why))
    except ValueError as exc:
        raise _bad(exc) from exc


@router.delete("/{play_id}/related/{ticker}")
def remove_related(play_id: str, ticker: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.serialize_play(bs.remove_related(db, _get_or_404(db, play_id), ticker))


@router.post("/{play_id}/bench")
def add_bench(play_id: str, payload: RingItemRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.serialize_play(bs.add_bench(db, play, payload.ticker, payload.why, payload.name))
    except ValueError as exc:
        raise _bad(exc) from exc


@router.delete("/{play_id}/bench/{ticker}")
def remove_bench(play_id: str, ticker: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ps.serialize_play(bs.remove_bench(db, _get_or_404(db, play_id), ticker))


@router.post("/{play_id}/bench/{ticker}/promote")
def promote_bench(play_id: str, ticker: str, payload: PromoteBenchRequest | None = None,
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.serialize_play(bs.promote_bench(db, play, ticker, (payload or PromoteBenchRequest()).weight))
    except ValueError as exc:
        raise _bad(exc) from exc


@router.post("/{play_id}/stage")
def stage_tranche(play_id: str, payload: StageRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.stage_tranche(db, play, payload.tranche_index if payload else None)
    except ValueError as exc:
        raise _bad(exc) from exc


@router.get("/{play_id}/performance")
def performance(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        return ps.play_performance(db, play)
    except Exception as exc:  # noqa: BLE001
        logger.warning("performance unavailable for %s: %s", play_id, exc)
        raise HTTPException(status_code=503, detail="Live performance is unavailable right now.") from exc


@router.post("/{play_id}/review")
def review(play_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    play = _get_or_404(db, play_id)
    try:
        ps.run_llm_review(db, play)
    except Exception as exc:  # noqa: BLE001
        logger.exception("play review failed")
        message, _ = ps._friendly_draft_error(exc)
        raise HTTPException(status_code=502, detail=message.replace("drafting this play", "reviewing this play")
                            .replace("draft plays", "review plays")) from exc
    return ps.serialize_play(play)
