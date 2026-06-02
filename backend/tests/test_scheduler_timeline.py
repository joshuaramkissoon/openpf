"""Tests for the Scheduled Jobs 'Today' timeline.

Covers the two new pieces of logic behind ``GET /scheduler/today``:

* ``fires_in_window`` — expand a cron expression into the fire times that fall
  within a (start, end] window, in the cron's own timezone.
* ``build_today_timeline`` — aggregate today's past runs (grouped per task) and
  today's remaining upcoming fires (collapsed per task) for the timeline view.

Plus a thin wiring/error test for the endpoint itself.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import ScheduledTaskLog
from app.services.task_scheduler_service import (
    build_today_timeline,
    create_task,
    fires_in_window,
)

LONDON = ZoneInfo("Europe/London")
# 2026-06-02 is a Tuesday (a weekday → cron `* * * 1-5` fires) and June is BST (UTC+1).


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _log(db: Session, task_id: str, when_utc: datetime, status: str, output_path: str | None) -> None:
    """Insert a task log at an explicit (naive UTC) created_at."""
    db.add(
        ScheduledTaskLog(
            task_id=task_id,
            status=status,
            message="run",
            output_path=output_path,
            payload={},
            created_at=when_utc,
        )
    )
    db.commit()


# ── fires_in_window ──────────────────────────────────────────────────────────


def test_fires_in_window_expands_hourly_after_start():
    start = datetime(2026, 6, 2, 9, 30, tzinfo=LONDON)  # 09:30 BST
    end = datetime(2026, 6, 2, 23, 59, 59, tzinfo=LONDON)
    fires = fires_in_window("0 8-21 * * 1-5", "Europe/London", start, end)
    hours = [f.astimezone(LONDON).hour for f in fires]
    # start-exclusive: 09:30 → next is 10:00, through the last 21:00 fire.
    assert hours == [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]


def test_fires_in_window_empty_when_none_left_today():
    start = datetime(2026, 6, 2, 22, 0, tzinfo=LONDON)
    end = datetime(2026, 6, 2, 23, 59, 59, tzinfo=LONDON)
    assert fires_in_window("0 8-21 * * 1-5", "Europe/London", start, end) == []


# ── build_today_timeline ─────────────────────────────────────────────────────


def test_build_today_timeline_groups_past_and_collapses_upcoming(db):
    now = datetime(2026, 6, 2, 14, 0, 0)  # naive UTC == 15:00 BST

    watch = create_task(db, {
        "name": "watch", "cron_expr": "0 8-21 * * 1-5", "timezone": "Europe/London",
        "prompt": "x", "enabled": True, "meta": {"task_kind": "watch_cycle"},
    })
    brief = create_task(db, {
        "name": "brief", "cron_expr": "0 7 * * 1-5", "timezone": "Europe/London",
        "prompt": "x", "enabled": True, "meta": {"task_kind": "claude"},
    })
    create_task(db, {
        "name": "paused", "cron_expr": "0 16 * * 1-5", "timezone": "Europe/London",
        "prompt": "x", "enabled": False, "meta": {"task_kind": "claude"},
    })

    # watch ran 3× today (07:00/08:00/09:00 UTC) — one with no output — plus one yesterday.
    _log(db, watch["id"], datetime(2026, 6, 2, 7, 0, 5), "ok", "/a/a.md")
    _log(db, watch["id"], datetime(2026, 6, 2, 8, 0, 5), "ok", "/a/b.md")
    _log(db, watch["id"], datetime(2026, 6, 2, 9, 0, 5), "error", None)
    _log(db, watch["id"], datetime(2026, 6, 1, 9, 0, 5), "ok", "/a/old.md")  # yesterday
    _log(db, brief["id"], datetime(2026, 6, 2, 6, 0, 5), "ok", "/a/c.md")

    result = build_today_timeline(db, "Europe/London", now_utc=now)

    assert result["date"] == "2026-06-02"
    assert result["timezone"] == "Europe/London"

    past = {g["name"]: g for g in result["past"]}
    # Yesterday's run is excluded; watch is grouped into one entry with 3 runs.
    assert past["watch"]["run_count"] == 3
    assert past["watch"]["status_summary"] == {"ok": 2, "error": 1, "running": 0}
    assert past["brief"]["run_count"] == 1
    # Runs ordered newest-first; the 09:00 run had no output.
    assert [r["has_output"] for r in past["watch"]["runs"]] == [False, True, True]

    up = {u["name"]: u for u in result["upcoming"]}
    # paused is disabled → excluded; brief already ran its only fire → excluded.
    assert "paused" not in up
    assert "brief" not in up
    # watch: now is 15:00 BST → remaining fires 16:00..21:00 (next 16:00, 5 more after it).
    assert up["watch"]["next_fire_at"].astimezone(LONDON).hour == 16
    assert up["watch"]["remaining_today"] == len(up["watch"]["fires"]) - 1
    assert up["watch"]["remaining_today"] == 5


# ── endpoint wiring / errors ─────────────────────────────────────────────────


def _client_with_memory_db() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def _override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def test_today_endpoint_returns_timeline_shape():
    client = _client_with_memory_db()
    try:
        resp = client.get("/api/scheduler/today")
        assert resp.status_code == 200
        body = resp.json()
        assert set(["date", "timezone", "now", "past", "upcoming"]).issubset(body)
        assert isinstance(body["past"], list)
        assert isinstance(body["upcoming"], list)
    finally:
        app.dependency_overrides.clear()


def test_today_endpoint_rejects_unknown_timezone():
    client = _client_with_memory_db()
    try:
        resp = client.get("/api/scheduler/today", params={"tz": "Bogus/Zone"})
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()
