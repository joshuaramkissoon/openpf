"""Company logos for the UI. The server fetches each one once from a public
logo service and caches it (``logo_service``); the browser only talks to us."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import logo_service

router = APIRouter(prefix="/logos", tags=["logos"])


class LogoSettings(BaseModel):
    enabled: bool


@router.get("/settings", response_model=LogoSettings)
def get_settings_(db: Session = Depends(get_db)) -> LogoSettings:
    return LogoSettings(enabled=bool(logo_service.get_logo_settings(db).get("enabled", True)))


@router.put("/settings", response_model=LogoSettings)
def put_settings(payload: LogoSettings, db: Session = Depends(get_db)) -> LogoSettings:
    return LogoSettings(enabled=bool(logo_service.set_logo_settings(db, payload.enabled).get("enabled")))


@router.get("/{key}")
def get_logo(key: str, db: Session = Depends(get_db)) -> Response:
    if not logo_service.valid_key(key):
        raise HTTPException(status_code=400, detail="bad logo key")
    got = logo_service.get_logo(db, key)
    if not got:
        # Short browser cache: the UI keeps its own 7-day negative cache.
        return Response(status_code=404, headers={"Cache-Control": "private, max-age=3600"})
    data, content_type = got
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=604800",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": "inline",
        },
    )
