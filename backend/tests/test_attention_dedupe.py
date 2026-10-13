"""Attention hygiene: newer alerts supersede older ones of the same ticker+kind,
and presented severity is capped (age decay + a hard ceiling on criticals)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.services.watch_service as ws
from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import Alert


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _alert(db, *, ticker="ASML", category="earnings", source="earnings", severity="info", age_h=0.0, key=None):
    a = Alert(
        category=category, severity=severity, ticker=ticker, source=source,
        dedupe_key=key or f"{category}:{ticker}:{age_h}", title=f"{ticker} {category}", detail="",
        created_at=NOW - timedelta(hours=age_h),
    )
    db.add(a)
    db.commit()
    return a


def test_newer_alert_supersedes_older_same_ticker_and_kind(db):
    for h in (30, 20, 10, 1):  # four near-identical ASML earnings cards
        _alert(db, age_h=h)
    _alert(db, category="big_move", source="big_move", severity="warning")  # different kind: kept
    _alert(db, ticker="NVDA")  # different ticker: kept

    summary = ws.attention_summary(db)
    open_alerts = summary["alerts"]
    asml_earnings = [a for a in open_alerts if a["ticker"] == "ASML" and a["category"] == "earnings"]
    assert len(asml_earnings) == 1
    assert summary["counts"]["alerts_open"] == 3
    statuses = sorted(a.status for a in db.query(Alert).filter(Alert.ticker == "ASML", Alert.category == "earnings"))
    assert statuses == ["new", "superseded", "superseded", "superseded"]


def test_superseded_key_does_not_refire(db, monkeypatch):
    old = _alert(db, key="earnings:ASML:2026-10-15", age_h=5)
    _alert(db, key="earnings:ASML:2026-10-16", age_h=1)
    ws.supersede_duplicates(db)
    db.commit()
    db.refresh(old)
    assert old.status == "superseded"
    assert "earnings:ASML:2026-10-15" in ws._open_dedupe_keys(db)


def test_run_watches_supersedes_on_arrival(db, monkeypatch):
    _alert(db, category="big_move", source="big_move", key="big_move:ASML:yesterday", age_h=24)

    def fake_watch(_db, seen, out):
        out.append(Alert(category="big_move", source="big_move", severity="info", ticker="ASML",
                         dedupe_key="big_move:ASML:today", title="ASML +6%", detail=""))

    monkeypatch.setattr(ws, "_WATCHES", [("fake", fake_watch)])
    ws.run_watches(db)
    open_rows = db.query(Alert).filter(Alert.status.in_(["new", "seen"])).all()
    assert [a.dedupe_key for a in open_rows] == ["big_move:ASML:today"]


def test_critical_cap_and_age_decay(db):
    for i in range(6):
        _alert(db, ticker=f"T{i}", category="thesis_invalidation", source="thesis", severity="critical", age_h=i)
    stale = _alert(db, ticker="OLD", category="thesis_invalidation", source="thesis", severity="critical", age_h=24 * 5)
    stale_warn = _alert(db, ticker="W", category="concentration", source="concentration", severity="warning", age_h=24 * 10)

    alerts = {a["ticker"]: a for a in ws.list_alerts(db)}
    criticals = [t for t, a in alerts.items() if a["severity"] == "critical"]
    assert sorted(criticals) == ["T0", "T1", "T2"]  # newest MAX_CRITICAL only
    assert alerts["T5"]["severity"] == "warning"
    assert alerts[stale.ticker]["severity"] == "warning"  # >3 days old critical decays
    assert alerts[stale_warn.ticker]["severity"] == "info"  # >7 days old warning decays
    # Stored severity is untouched — the cap is presentation only.
    db.refresh(stale)
    assert stale.severity == "critical"
