from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ResearchRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2000)
    subject: str = Field(default="", max_length=120)
    hypothesis: str = Field(default="", max_length=4000)
    horizon_days: int = Field(default=30, ge=1, le=365)
    account_kind: str = Field(default="all", max_length=24)
    create_thesis: bool = False


class ResearchRunResponse(BaseModel):
    ok: bool
    markdown: str = ""
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    summary: Optional[str] = None
    suggested_action: Optional[str] = None
    invalidation: Optional[str] = None
    artifact_path: Optional[str] = None
    thesis_id: Optional[str] = None
    error: Optional[str] = None
