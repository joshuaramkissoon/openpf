from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ArtifactItem(BaseModel):
    path: str
    title: str
    type: str
    created_at: str
    task_name: str | None = None
    tags: list[str] = Field(default_factory=list)
    size_bytes: int
    # {"headline", "points", "action", "signal": none|low|high} — or a derived
    # fallback ({"headline", "derived": true, "signal": null}) for older reports.
    summary: dict[str, Any] | None = None


class ArtifactDetail(BaseModel):
    path: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
