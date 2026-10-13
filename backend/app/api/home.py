"""Home layout (customisable widgets).

  GET    /home/layout          the saved layout, or the default
  PUT    /home/layout          {widgets: [...]} → validated, saved
  POST   /home/layout/reset    back to the default
  POST   /home/widgets         {kind, ref?|spec?, size?, block?, title?} → append one ("Add to Home")
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import home_layout

router = APIRouter(prefix="/home", tags=["home"])


@router.get("/layout")
def get_layout(db: Session = Depends(get_db)) -> dict[str, Any]:
    return home_layout.migrate_inline_specs(db)


@router.put("/layout")
def put_layout(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return home_layout.save_layout(db, payload)
    except home_layout.LayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/layout/reset")
def reset_layout(db: Session = Depends(get_db)) -> dict[str, Any]:
    return home_layout.reset_layout(db)


@router.post("/widgets")
def add_widget(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        layout, widget = home_layout.add_widget(db, payload)
    except home_layout.LayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"layout": layout, "widget": widget}
