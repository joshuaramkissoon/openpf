"""Views API — agent-built, sandboxed pages.

Routes (under the API prefix):
  GET    /views                       list views (sidebar + gallery)
  GET    /views/presets               hand-made spec presets, ranked for this portfolio (+ why)
  POST   /views/presets/{slug}/install  instant (no model)
  POST   /views/specs                 {spec, title?} → save a viz spec as a view ("Pin as view")
  POST   /views/generate              {prompt, view_id?, kind?} → build job (422 + suggestions if declined)
  POST   /views/{id}/retry            re-run the last failed build
  POST   /views/{id}/good             {version} → remember the last version that rendered cleanly
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
import json

from app.services.views_data import DataSourceError, resolve
from app.services.views_presets import rank_presets
from app.services.views_validate import parse_meta, validate_html
from app.services.viz_spec import validate_spec
from app.views_sdk import preset_specs

router = APIRouter(prefix="/views", tags=["views"])


class GenerateRequest(BaseModel):
    prompt: str = Field(default="", max_length=20000)  # length is triaged with a friendly message
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


class SpecRequest(BaseModel):
    spec: dict[str, Any]
    title: str | None = Field(default=None, max_length=60)


class GoodRequest(BaseModel):
    version: int


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="view not found")


@router.get("")
def list_views() -> list[dict[str, Any]]:
    return store.list_views()


@router.get("/presets")
def list_presets(currency: str = Query(default="GBP"), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    installed = {v.get("starter") for v in store.list_views()}
    return rank_presets(db, currency="USD" if currency.upper() == "USD" else "GBP", installed=installed)


@router.post("/presets/{slug}/install")
def install_preset(slug: str) -> dict[str, Any]:
    raw = preset_specs().get(slug)
    if raw is None:
        raise HTTPException(status_code=404, detail="preset not found")
    spec, problems = validate_spec(raw)
    if problems or spec is None:  # a shipped preset must always pass its own schema
        raise HTTPException(status_code=500, detail=f"preset failed validation: {problems[0] if problems else 'invalid'}")
    meta = store.create_view(title=spec["title"], description=spec.get("description", ""), icon=spec.get("icon") or store.DEFAULT_ICON, starter=slug, slug=slug)
    return store.save_version(meta["id"], json.dumps(spec, indent=1), note=f"Preset: {spec['title']}", kind="spec")


@router.post("/specs")
def pin_spec(payload: SpecRequest) -> dict[str, Any]:
    spec, problems = validate_spec(payload.spec)
    if problems or spec is None:
        raise HTTPException(status_code=400, detail="This chart can't be saved: " + "; ".join(problems[:3]))
    if payload.title:
        spec["title"] = payload.title.strip()[:60] or spec["title"]
    meta = store.create_view(title=spec["title"], description=spec.get("description", ""), icon=spec.get("icon") or store.DEFAULT_ICON, slug=store.prompt_slug(spec["title"]))
    return store.save_version(meta["id"], json.dumps(spec, indent=1), note="Pinned from chat", kind="spec")


def _declined(exc: builder.ViewDeclined) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.decline.public())


@router.post("/generate")
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    try:
        job = builder.prepare_job(payload.prompt, view_id=payload.view_id, kind=payload.kind)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    except builder.BuildConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except builder.ViewDeclined as exc:
        raise _declined(exc) from exc
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
    build = meta.get("build") or None
    if not job and build and build.get("status") in ("queued", "running") and build.get("job_id") and not builder.get_job(build["job_id"]):
        # A build this process doesn't know about (it died with a restart, or never
        # started): report it failed rather than leave the UI spinning. Queued
        # requests without a job id are still waiting for the worker.
        store.set_build(view_id, "error", error="The build was interrupted. Retry to build it again.", prompt=build.get("prompt"))
        meta = store.get_view(view_id)
    return meta | {"active_job": job.public() if job else None}


@router.post("/{view_id}/retry")
async def retry(view_id: str) -> dict[str, Any]:
    try:
        meta = store.get_view(view_id)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    build = meta.get("build") or {}
    prompt = build.get("prompt") or next((h["prompt"] for h in reversed(meta.get("history") or []) if h.get("prompt")), "")
    if not prompt:
        raise HTTPException(status_code=400, detail="nothing to retry")
    try:
        job = builder.prepare_job(prompt, view_id=view_id)
    except builder.BuildConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except builder.ViewDeclined as exc:
        raise _declined(exc) from exc
    builder.start_job(job)
    return job.public()


@router.post("/{view_id}/good")
def mark_good(view_id: str, payload: GoodRequest) -> dict[str, Any]:
    try:
        return store.mark_good(view_id, payload.version)
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/{view_id}/html")
def get_html(view_id: str, version: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
    """The view document: a viz spec (kind "spec") or legacy HTML (kind "code")."""
    try:
        meta = store.get_view(view_id)
        n, kind, content = store.read_document(view_id, version or meta["current_version"])
    except store.ViewNotFound as exc:
        raise _not_found(exc) from exc
    # Re-validate on read: a hand-edited file on disk never reaches the browser unchecked.
    if kind == "spec":
        spec, problems = validate_spec(content)
        return {"view_id": view_id, "version": n, "kind": "spec", "html": "", "spec": spec, "problems": problems, "meta": {}}
    problems = validate_html(content)
    return {"view_id": view_id, "version": n, "kind": "code", "html": content if not problems else "", "spec": None, "problems": problems, "meta": parse_meta(content)}


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
