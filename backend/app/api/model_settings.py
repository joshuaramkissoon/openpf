"""Settings → Models (which model runs which job) and Settings → Data providers
(Together / Finnhub / FRED keys). Keys are write-only: reads say whether a key
is configured and where it comes from, never the key itself."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import model_routing
from app.services.config_store import ConfigStore

router = APIRouter(prefix="/config", tags=["config"])

# field → (env var, Settings attribute, label)
PROVIDER_KEYS = {
    "together_api_key": ("TOGETHER_API_KEY", "together_api_key", "Together AI"),
    "finnhub_api_key": ("FINNHUB_API_KEY", "finnhub_api_key", "Finnhub"),
    "fred_api_key": ("FRED_API_KEY", "fred_api_key", "FRED"),
}


class JobRoute(BaseModel):
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=120)


class ModelsUpdate(BaseModel):
    jobs: dict[str, JobRoute] = Field(default_factory=dict)
    together_price: list[float] | None = Field(default=None, description="[input, output] USD per million tokens; empty resets")


class DataProvidersUpdate(BaseModel):
    together_api_key: str | None = Field(default=None, max_length=400)
    finnhub_api_key: str | None = Field(default=None, max_length=400)
    fred_api_key: str | None = Field(default=None, max_length=400)
    clear: list[str] = Field(default_factory=list)


@router.get("/models")
def get_models() -> dict[str, Any]:
    return model_routing.describe()


@router.put("/models")
def update_models(payload: ModelsUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    body: dict[str, Any] = {"jobs": {k: v.model_dump(exclude_unset=True) for k, v in payload.jobs.items()}}
    if "together_price" in payload.model_fields_set:
        tp = payload.together_price
        if tp is not None and len(tp) != 2:
            raise HTTPException(status_code=422, detail="together_price needs [input, output]")
        body["together_price"] = tp
    try:
        return model_routing.update(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _env_value(env: str, attr: str) -> str:
    v = os.environ.get(env, "").strip()
    if v:
        return v
    from app.core.config import get_settings

    return str(getattr(get_settings(), attr, "") or "").strip()


def data_providers_public(db: Session) -> dict[str, Any]:
    stored = ConfigStore(db).get("data_providers", {}) or {}
    out: dict[str, Any] = {}
    for field, (env, attr, label) in PROVIDER_KEYS.items():
        in_env = bool(_env_value(env, attr))
        in_settings = bool(str(stored.get(field) or "").strip())
        out[field] = {
            "label": label,
            "configured": in_env or in_settings,
            # env wins over the Settings value (see intel_service._key)
            "source": "env" if in_env else ("settings" if in_settings else None),
        }
    return out


@router.get("/data-providers")
def get_data_providers(db: Session = Depends(get_db)) -> dict[str, Any]:
    return data_providers_public(db)


@router.put("/data-providers")
def update_data_providers(payload: DataProvidersUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Blank fields keep the stored key; ``clear`` removes one."""
    store = ConfigStore(db)
    cur = dict(store.get("data_providers", {}) or {})
    for field in PROVIDER_KEYS:
        v = getattr(payload, field)
        if v is not None and v.strip():
            cur[field] = v.strip()
    for field in payload.clear:
        if field in PROVIDER_KEYS:
            cur.pop(field, None)
    store.set("data_providers", cur)
    return data_providers_public(db)
