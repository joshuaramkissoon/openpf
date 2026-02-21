from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import UsageRecord


def record(
    db: Session,
    *,
    source: str,
    source_id: str,
    model: str,
    total_cost_usd: float | None,
    duration_ms: int | None,
    num_turns: int | None,
) -> UsageRecord:
    row = UsageRecord(
        source=source,
        source_id=source_id,
        model=model,
        total_cost_usd=total_cost_usd,
        duration_ms=duration_ms,
        num_turns=num_turns,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_summary(db: Session) -> dict:
    rows = db.scalars(select(UsageRecord)).all()

    now_utc = datetime.now(timezone.utc)
    week_ago = now_utc - timedelta(days=7)
    month_ago = now_utc - timedelta(days=30)

    def _cost(r: UsageRecord) -> float:
        return r.total_cost_usd or 0.0

    def _ts(r: UsageRecord) -> datetime:
        return r.recorded_at.replace(tzinfo=timezone.utc) if r.recorded_at.tzinfo is None else r.recorded_at

    all_time = sum(_cost(r) for r in rows)
    this_month = sum(_cost(r) for r in rows if _ts(r) >= month_ago)
    this_week = sum(_cost(r) for r in rows if _ts(r) >= week_ago)

    by_source = {
        "chat": sum(_cost(r) for r in rows if r.source == "chat"),
        "scheduled": sum(_cost(r) for r in rows if r.source == "scheduled"),
        "agent_run": sum(_cost(r) for r in rows if r.source == "agent_run"),
    }

    return {
        "all_time_usd": round(all_time, 6),
        "this_month_usd": round(this_month, 6),
        "this_week_usd": round(this_week, 6),
        "by_source": by_source,
        "record_count": len(rows),
    }


def list_records(db: Session, limit: int = 100) -> list[UsageRecord]:
    return list(
        db.scalars(
            select(UsageRecord).order_by(UsageRecord.recorded_at.desc()).limit(limit)
        ).all()
    )
