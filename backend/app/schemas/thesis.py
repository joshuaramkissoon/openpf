from datetime import datetime

from pydantic import BaseModel, Field
from typing import Literal


class ThesisItem(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    source_run_id: str | None = None
    symbol: str
    account_kind: str
    title: str
    thesis: str
    catalysts: list[str] = Field(default_factory=list)
    invalidation: str
    confidence: float
    status: str
    meta: dict = Field(default_factory=dict)


class ThesisCreate(BaseModel):
    symbol: str
    account_kind: str = "all"
    title: str
    thesis: str
    catalysts: list[str] = Field(default_factory=list)
    invalidation: str = ""
    confidence: float = 0.5
    status: str = "active"


class ThesisStatusUpdate(BaseModel):
    status: Literal["active", "archived"]
