from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SchedulerTaskItem(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    name: str
    cron_expr: str
    timezone: str
    model: str
    prompt: str
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str
    run_count: int
    failure_count: int
    meta: dict[str, Any] = {}


class SchedulerTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    cron_expr: str = Field(min_length=5, max_length=80)
    timezone: str = Field(default="Europe/London", max_length=64)
    model: str = Field(default="claude-sonnet-4-6", max_length=64)
    prompt: str = Field(default="", max_length=12000)
    enabled: bool = True
    meta: dict[str, Any] = {}


class SchedulerTaskPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    cron_expr: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    prompt: str | None = Field(default=None, max_length=12000)
    enabled: bool | None = None
    meta: dict[str, Any] | None = None


class SchedulerTaskRunResponse(BaseModel):
    task: SchedulerTaskItem
    status: str
    payload: dict[str, Any] = {}
    output_path: str | None = None


class SchedulerTaskLogItem(BaseModel):
    id: int
    task_id: str
    created_at: datetime
    status: str
    message: str
    output_path: str | None = None
    payload: dict[str, Any] = {}


class SchedulerDeleteResponse(BaseModel):
    id: str
    deleted: bool


class TimelineRun(BaseModel):
    log_id: int
    ran_at: datetime
    status: str
    message: str
    has_output: bool
    output_path: str | None = None


class TimelinePastGroup(BaseModel):
    task_id: str
    name: str
    task_kind: str
    run_count: int
    first_ran_at: datetime
    last_ran_at: datetime
    status_summary: dict[str, int] = {}
    runs: list[TimelineRun] = []


class TimelineUpcoming(BaseModel):
    task_id: str
    name: str
    task_kind: str
    cron_expr: str
    next_fire_at: datetime
    remaining_today: int
    fires: list[datetime] = []


class SchedulerTodayResponse(BaseModel):
    date: str
    timezone: str
    now: datetime
    past: list[TimelinePastGroup] = []
    upcoming: list[TimelineUpcoming] = []
