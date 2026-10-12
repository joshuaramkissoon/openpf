"""Views API — agent-built, sandboxed pages.

Routes (under the API prefix):
  GET    /views                       list views (sidebar + gallery)
  GET    /views/starters              hand-written templates
  POST   /views/starters/{slug}/install
  POST   /views/generate              {prompt, view_id?, kind?} → build job
  GET    /views/jobs/{job_id}         build progress
  POST   /views/data                  {source, params} → whitelisted read-only data
  POST   /views/reorder               {ids}
  GET    /views/{id}                  meta (+ active job)
  GET    /views/{id}/html?version=    the view document
  PATCH  /views/{id}                  title / description / icon / pinned
  POST   /views/{id}/revert           {version}
  DELETE /views/{id}
  GET    /views/{id}/state  ·  PUT /views/{id}/state
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import views_builder as builder
from app.services import views_store as store
from app.services.views_data import DataSourceError, resolve
from app.services.views_validate import parse_meta, validate_html
from app.views_sdk import starter, starters

router = APIRouter(prefix="/views", tags=["views"])


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    view_id: str | None = None
    kind: Literal["edit", "fix"] | None = None


class DataRequest(BaseModel):
    source: str
    params: dict[str, Any] = Field(default_factory=dict)


class MetaPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    icon: str | None = None
    pinned: bool | None = None


class RevertRequest(BaseModel):
    version: int


class ReorderRequest(BaseModel):
    ids: list[str]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="view not found")


@router.get("")
def list_views() -> list[dict[str, Any]]:
    return store.list_views()


@router.get("/starters")
def list_starters() -> list[dict[str, Any]]:
    installed = {v.get("starter") for v in store.list_views()}
    return [{k: s[k] for k in ("slug", "title", "description", "icon")} | {"installed": s["slug"] in installed} for s in starters()]


@router.post("/starters/{slug}/install")
def install_starter(slug: str) -> dict[str, Any]:
    tpl = starter(slug)
    if not tpl:
        raise HTTPException(status_code=404, detail="starter not found")
    problems = validate_html(tpl["html"])
    if problems:  # a shipped template must always pass its own rules
        raise HTTPException(status_code=500, detail=f"starter failed validation: {problems[0]}")
    meta = store.create_view(title=tpl["title"], description=tpl["description"], icon=tpl["icon"], starter=slug)
    return store.save_version(meta["id"], tpl["html"], note=f"Installed starter: {tpl['title']}")


@router.post("/generate")
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    try:
        job = builder.prepare_job(payload.prompt, view_id=payload.view_id, kind=payload.kind)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    except builder.BuildConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    builder.start_job(job)
    return job.public()


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = builder.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.public()


@router.post("/data")
def view_data(payload: DataRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return resolve(db, payload.source, payload.params)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reorder")
def reorder(payload: ReorderRequest) -> list[dict[str, Any]]:
    return store.reorder(payload.ids)


@router.get("/{view_id}")
def get_view(view_id: str) -> dict[str, Any]:
    try:
        meta = store.get_view(view_id)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    job = builder.active_job(view_id)
    return meta | {"active_job": job.public() if job else None}


@router.get("/{view_id}/html")
def get_html(view_id: str, version: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
    try:
        meta = store.get_view(view_id)
        n = version or meta["current_version"]
        html = store.read_version(view_id, n)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    # Re-validate on read: a hand-edited file on disk never reaches the browser unchecked.
    problems = validate_html(html)
    return {"view_id": view_id, "version": n, "html": html if not problems else "", "problems": problems, "meta": parse_meta(html)}


@router.patch("/{view_id}")
def patch_view(view_id: str, payload: MetaPatch) -> dict[str, Any]:
    try:
        return store.update_meta(view_id, **payload.model_dump(exclude_none=True))
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/{view_id}/revert")
def revert(view_id: str, payload: RevertRequest) -> dict[str, Any]:
    try:
        return store.revert(view_id, payload.version)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc


@router.delete("/{view_id}")
def delete(view_id: str) -> dict[str, Any]:
    if builder.active_job(view_id):
        raise HTTPException(status_code=409, detail="this view is being built")
    try:
        store.delete_view(view_id)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    return {"ok": True}


@router.get("/{view_id}/state")
def get_state(view_id: str) -> dict[str, Any]:
    try:
        return store.get_state(view_id)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc


@router.put("/{view_id}/state")
def put_state(view_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return store.set_state(view_id, payload)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
