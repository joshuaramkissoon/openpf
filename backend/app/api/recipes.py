"""Recipes API — Archie's own harness (skills + triggers + delivery).

Routes (under the API prefix):
  GET    /recipes                          recipes + pending suggestions
  POST   /recipes/draft                    {prompt} → a draft (not saved) or a graceful refusal
  POST   /recipes                          save a recipe (from a draft or written by hand)
  GET    /recipes/suggestions              pending suggestions from unattended runs
  POST   /recipes/suggestions/{id}/accept  · /dismiss
  GET    /recipes/{slug}                   detail (+ runs, versions, suggestions)
  PATCH  /recipes/{slug}                   edit (new version)
  POST   /recipes/{slug}/enabled           {enabled}
  POST   /recipes/{slug}/revise            {instruction} → preview of Archie's change + diff (not saved)
  POST   /recipes/{slug}/run               run now (background)
  GET    /recipes/{slug}/runs
  GET    /recipes/{slug}/versions          · /versions/{n}/diff?against=
  POST   /recipes/{slug}/revert            {version}
  POST   /recipes/{slug}/reset             built-ins: back to the original
  DELETE /recipes/{slug}
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import recipes_author as author
from app.services import recipes_service as svc
from app.services.recipes_validate import RecipeValidationError

router = APIRouter(prefix="/recipes", tags=["recipes"])


class DraftRequest(BaseModel):
    prompt: str = Field(default="", max_length=4000)


class RecipeFields(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=1000)
    body: str | None = Field(default=None, max_length=20000)
    icon: str | None = Field(default=None, max_length=60)
    trigger: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None


class CreateRequest(RecipeFields):
    note: str = Field(default="", max_length=240)
    created_by: Literal["user", "archie"] = "user"
    enabled: bool = True


class PatchRequest(RecipeFields):
    note: str = Field(default="", max_length=240)
    author: Literal["user", "archie"] = "user"


class EnabledRequest(BaseModel):
    enabled: bool


class ReviseRequest(BaseModel):
    instruction: str = Field(default="", max_length=4000)


class RevertRequest(BaseModel):
    version: int = Field(ge=1)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _missing(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="recipe not found")


@router.get("")
def list_recipes(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"recipes": svc.list_recipes(db), "suggestions": svc.list_suggestions(db)}


@router.post("/draft")
async def draft(payload: DraftRequest) -> dict[str, Any]:
    return await author.draft(payload.prompt)


@router.post("")
def create(payload: CreateRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    fields = payload.model_dump(exclude={"note", "created_by", "enabled"}, exclude_none=True)
    try:
        return svc.create(db, fields, created_by=payload.created_by, enabled=payload.enabled,
                          note=payload.note or ("Created by Archie" if payload.created_by == "archie" else "Created"))
    except RecipeValidationError as exc:
        raise _bad(exc) from exc


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return svc.list_suggestions(db)


@router.post("/suggestions/{suggestion_id}/{decision}")
def resolve_suggestion(suggestion_id: str, decision: Literal["accept", "dismiss"], db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return svc.resolve_suggestion(db, suggestion_id, accept=decision == "accept")
    except svc.RecipeNotFound as exc:
        raise HTTPException(status_code=404, detail="suggestion not found or already resolved") from exc
    except RecipeValidationError as exc:
        raise _bad(exc) from exc


@router.get("/{slug}")
def detail(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        recipe = svc.get(db, slug)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc
    return svc.serialize(db, recipe) | {
        "runs": svc.recipe_runs(db, recipe, limit=15),
        "versions": svc.list_versions(db, slug),
        "suggestions": svc.list_suggestions(db, slug=slug),
    }


@router.patch("/{slug}")
def patch(slug: str, payload: PatchRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    fields = payload.model_dump(exclude={"note", "author"}, exclude_none=True)
    try:
        return svc.update(db, slug, fields, author=payload.author, note=payload.note)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc
    except RecipeValidationError as exc:
        raise _bad(exc) from exc


@router.post("/{slug}/enabled")
def set_enabled(slug: str, payload: EnabledRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return svc.set_enabled(db, slug, payload.enabled)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc


@router.post("/{slug}/revise")
async def revise(slug: str, payload: ReviseRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        current = svc.serialize(db, svc.get(db, slug), runs=0)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc
    out = await author.revise(current, payload.instruction)
    if out.get("ok"):
        out["diff"] = svc.diff_fields(current, out["draft"])
        if not any(line["type"] in ("add", "del") for line in out["diff"]):
            return {"ok": False, "kind": "no_change", "reason": "Archie didn't find anything to change for that.",
                    "alternative": "Try being more specific about what should be different."}
    return out


@router.post("/{slug}/run", status_code=202)
def run_now(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return svc.start_run(db, slug)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc


@router.get("/{slug}/runs")
def runs(slug: str, limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        return svc.recipe_runs(db, svc.get(db, slug), limit=limit)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc


@router.get("/{slug}/versions")
def versions(slug: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        return svc.list_versions(db, slug)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc


@router.get("/{slug}/versions/{version}/diff")
def version_diff(slug: str, version: int, against: int | None = Query(default=None, ge=1), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return svc.version_diff(db, slug, version, against)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc


@router.post("/{slug}/revert")
def revert(slug: str, payload: RevertRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return svc.revert(db, slug, payload.version)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc
    except RecipeValidationError as exc:
        raise _bad(exc) from exc


@router.post("/{slug}/reset")
def reset(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return svc.reset_builtin(db, slug)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc
    except RecipeValidationError as exc:
        raise _bad(exc) from exc


@router.delete("/{slug}")
def delete(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        svc.delete(db, slug)
    except svc.RecipeNotFound as exc:
        raise _missing(exc) from exc
    except RecipeValidationError as exc:
        raise _bad(exc) from exc
    return {"ok": True}
