from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UsageRecordItem(BaseModel):
    id: int
    recorded_at: datetime
    source: str
    source_id: str
    model: str
    total_cost_usd: float | None
    duration_ms: int | None
    num_turns: int | None

    model_config = {"from_attributes": True}


class CostBySource(BaseModel):
    chat: float
    scheduled: float
    agent_run: float


class CostSummary(BaseModel):
    all_time_usd: float
    this_month_usd: float
    this_week_usd: float
    by_source: CostBySource
    record_count: int
