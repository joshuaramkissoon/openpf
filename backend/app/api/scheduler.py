from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import MessageResponse
from app.schemas.scheduler import (
    SchedulerDeleteResponse,
    SchedulerTaskCreate,
    SchedulerTaskItem,
    SchedulerTaskLogItem,
    SchedulerTaskPatch,
    SchedulerTaskRunResponse,
)
from app.services.task_scheduler_service import (
    create_task,
    delete_task,
    list_task_logs,
    list_tasks,
    run_due_tasks,
    run_task_now,
    seed_default_tasks,
    start_task_background,
    update_task,
)

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get("/tasks", response_model=list[SchedulerTaskItem])
def tasks(db: Session = Depends(get_db)) -> list[SchedulerTaskItem]:
    rows = list_tasks(db)
    return [SchedulerTaskItem(**row) for row in rows]


@router.post("/tasks", response_model=SchedulerTaskItem)
def add_task(payload: SchedulerTaskCreate, db: Session = Depends(get_db)) -> SchedulerTaskItem:
    try:
        row = create_task(db, payload.model_dump())
        return SchedulerTaskItem(**row)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/tasks/{task_id}", response_model=SchedulerTaskItem)
def patch_task(task_id: str, payload: SchedulerTaskPatch, db: Session = Depends(get_db)) -> SchedulerTaskItem:
    try:
        row = update_task(db, task_id, payload.model_dump(exclude_none=True))
        return SchedulerTaskItem(**row)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/tasks/{task_id}", response_model=SchedulerDeleteResponse)
def remove_task(task_id: str, db: Session = Depends(get_db)) -> SchedulerDeleteResponse:
    deleted = delete_task(db, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="task not found")
    return SchedulerDeleteResponse(id=task_id, deleted=True)


@router.get("/tasks/{task_id}/logs", response_model=list[SchedulerTaskLogItem])
def task_logs(task_id: str, limit: int = Query(default=40, ge=1, le=200), db: Session = Depends(get_db)) -> list[SchedulerTaskLogItem]:
    rows = list_task_logs(db, task_id, limit=limit)
    return [SchedulerTaskLogItem(**row) for row in rows]


@router.post("/tasks/{task_id}/run", response_model=SchedulerTaskRunResponse, status_code=202)
def run_task(task_id: str, db: Session = Depends(get_db)) -> SchedulerTaskRunResponse:
    try:
        row = start_task_background(db, task_id)
        return SchedulerTaskRunResponse(**row)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/run-due", response_model=MessageResponse)
def run_due(db: Session = Depends(get_db)) -> MessageResponse:
    results = run_due_tasks(db)
    return MessageResponse(message=f"ran {len(results)} due tasks")


@router.post("/seed-defaults", response_model=MessageResponse)
def seed_defaults(db: Session = Depends(get_db)) -> MessageResponse:
    created = seed_default_tasks(db)
    return MessageResponse(message=f"seeded {len(created)} tasks")
