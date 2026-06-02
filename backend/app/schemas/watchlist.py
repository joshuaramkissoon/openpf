from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    symbol: str
    note: str = ""
    conviction: Literal["low", "medium", "high"] | None = None
    target_price: float | None = None
    target_direction: Literal["above", "below"] | None = None


class WatchlistUpdate(BaseModel):
    note: str | None = None
    name: str | None = None
    conviction: Literal["low", "medium", "high"] | None = None
    status: Literal["watching", "acted", "archived"] | None = None
    target_price: float | None = None
    target_direction: Literal["above", "below"] | None = None
    monitor: bool | None = None


class WatchlistItemView(BaseModel):
    id: str
    created_at: str | None = None
    updated_at: str | None = None
    symbol: str
    name: str = ""
    note: str = ""
    source: str
    conviction: str | None = None
    status: str
    target_price: float | None = None
    target_direction: str | None = None
    monitor: bool = True
    last_reviewed_at: str | None = None
    # Live enrichment (request-time; null on failure — never blocks the row).
    price: float | None = None
    change_pct: float | None = None
    currency: str | None = None
    # Board "stays alive" signals.
    open_flags: int = 0
    latest_flag: str | None = None
    latest_severity: str | None = None
