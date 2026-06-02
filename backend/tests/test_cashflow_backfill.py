"""Tests for the deep cashflow backfill: it must page to the START of history
(not stop at known pages) and survive a mid-backfill rate-limit by backing off
and retrying — the failure mode that truncated the ISA feed at ~page 6."""

from datetime import datetime
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models.entities import CashflowEvent
from app.services import cashflow_service
from app.services.t212_client import T212RateLimitError


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class _FakeClient:
    """Serves 3 pages newest→oldest; raises a one-shot 429 entering page 2."""

    api_key = "k"
    api_secret = "s"

    def __init__(self):
        self.pages = {
            "p1": {"items": [{"reference": "r1", "type": "DEPOSIT", "amount": 100.0,
                              "currency": "GBP", "dateTime": "2026-01-03T00:00:00Z"}],
                   "nextPagePath": "cursor=p2"},
            "p2": {"items": [{"reference": "r2", "type": "DEPOSIT", "amount": 200.0,
                              "currency": "GBP", "dateTime": "2024-01-02T00:00:00Z"}],
                   "nextPagePath": "cursor=p3"},
            "p3": {"items": [{"reference": "r3", "type": "WITHDRAW", "amount": -50.0,
                              "currency": "GBP", "dateTime": "2021-02-02T00:00:00Z"}],
                   "nextPagePath": None},
        }
        self._raised_429 = False

    def _request(self, method, path, params=None):
        cur = (params or {}).get("cursor", "p1")
        if cur == "p2" and not self._raised_429:
            self._raised_429 = True
            raise T212RateLimitError("429")
        return self.pages[cur], {}


def test_backfill_pages_to_start_and_recovers_from_rate_limit(db, monkeypatch):
    monkeypatch.setattr(cashflow_service, "build_t212_client", lambda *a, **k: _FakeClient())
    monkeypatch.setattr(cashflow_service.time, "sleep", lambda *_: None)  # no real waits

    res = cashflow_service.backfill_cashflows(db, "stocks_isa", page_sleep=0, rate_limit_sleep=0)

    assert res["ok"] is True
    assert res["added"] == 3  # walked all the way to the oldest page despite the 429
    refs = {e.reference for e in db.query(CashflowEvent).all()}
    assert refs == {"r1", "r2", "r3"}


def test_backfill_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(cashflow_service, "build_t212_client", lambda *a, **k: _FakeClient())
    monkeypatch.setattr(cashflow_service.time, "sleep", lambda *_: None)

    cashflow_service.backfill_cashflows(db, "stocks_isa", page_sleep=0, rate_limit_sleep=0)
    res2 = cashflow_service.backfill_cashflows(db, "stocks_isa", page_sleep=0, rate_limit_sleep=0)

    assert res2["added"] == 0  # everything already stored
    assert db.query(CashflowEvent).count() == 3
