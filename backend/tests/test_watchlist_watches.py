"""Tests for the watchlist watches in watch_service — target/big-move/earnings/
news checks that resurface tracked ideas into the Attention feed."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.services.intel_service as intel
import app.services.leveraged_market as lm
import app.services.watch_service as ws
from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import Alert
from app.services import watchlist_service as wl


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _run(fn, db, seen=None):
    out = []
    fn(db, seen if seen is not None else set(), out)
    return out


def test_target_below_breach_alerts(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"price": 140.0})
    item = wl.add_item(db, "KEYS", note="entry post-pullback")
    wl.update_item(db, item.id, {"target_price": 150.0, "target_direction": "below"})
    out = _run(ws._watch_watchlist_target, db)
    assert len(out) == 1
    a = out[0]
    assert a.ticker == "KEYS" and a.category == "watchlist" and a.source == "wl_target"
    assert a.consider == "entry post-pullback"


def test_target_not_breached_silent(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"price": 160.0})
    item = wl.add_item(db, "KEYS")
    wl.update_item(db, item.id, {"target_price": 150.0, "target_direction": "below"})
    assert _run(ws._watch_watchlist_target, db) == []


def test_target_above_breach_alerts(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"price": 185.0, "currency": "USD"})
    item = wl.add_item(db, "NVDA")
    wl.update_item(db, item.id, {"target_price": 180.0, "target_direction": "above"})
    out = _run(ws._watch_watchlist_target, db)
    assert len(out) == 1 and out[0].source == "wl_target"


def test_target_skips_minor_unit_currency_to_avoid_100x_mismatch(db, monkeypatch):
    # A .L name quotes in pence (GBX). A target typed in the major unit would
    # mis-fire 100x — the watch must skip rather than raise a wrong alert.
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"price": 33000.0, "currency": "GBX"})
    item = wl.add_item(db, "VOD.L")
    wl.update_item(db, item.id, {"target_price": 350.0, "target_direction": "above"})
    assert _run(ws._watch_watchlist_target, db) == []


def test_muted_item_is_not_watched(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"price": 140.0})
    item = wl.add_item(db, "KEYS")
    wl.update_item(db, item.id, {"target_price": 150.0, "target_direction": "below", "monitor": False})
    assert _run(ws._watch_watchlist_target, db) == []


def test_big_move_alerts_over_threshold(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"change_pct": 0.061})
    wl.add_item(db, "PLTR")
    out = _run(ws._watch_watchlist_big_move, db)
    assert len(out) == 1 and out[0].severity == "info" and out[0].category == "watchlist"


def test_big_move_bumps_severity(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"change_pct": -0.09})
    wl.add_item(db, "PLTR")
    out = _run(ws._watch_watchlist_big_move, db)
    assert out[0].severity == "warning"


def test_big_move_silent_below_threshold(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"change_pct": 0.02})
    wl.add_item(db, "PLTR")
    assert _run(ws._watch_watchlist_big_move, db) == []


def test_big_move_skips_when_holding_already_alerted(db, monkeypatch):
    monkeypatch.setattr(lm, "get_price", lambda *a, **k: {"change_pct": 0.07})
    wl.add_item(db, "PLTR")
    seen = {f"big_move:PLTR:{ws._today()}"}  # holdings watch already fired
    assert _run(ws._watch_watchlist_big_move, db, seen) == []


def test_earnings_within_window_alerts(db, monkeypatch):
    monkeypatch.setattr(intel, "get_earnings", lambda tk: {"next": {"date": "2026-06-04", "days_away": 2, "hour": "amc"}})
    wl.add_item(db, "MSFT")
    out = _run(ws._watch_watchlist_earnings, db)
    assert len(out) == 1 and out[0].source == "wl_earnings"


def test_earnings_outside_window_silent(db, monkeypatch):
    monkeypatch.setattr(intel, "get_earnings", lambda tk: {"next": {"date": "2026-07-01", "days_away": 30}})
    wl.add_item(db, "MSFT")
    assert _run(ws._watch_watchlist_earnings, db) == []


def test_news_alerts_newest_and_dedupes_by_url(db, monkeypatch):
    monkeypatch.setattr(intel, "get_company_news", lambda tk, since_days=1, limit=5: [
        {"headline": "KEYS beats and raises", "url": "http://x/1", "summary": "strong q", "source": "Reuters"},
        {"headline": "older", "url": "http://x/0", "summary": "", "source": "X"},
    ])
    wl.add_item(db, "KEYS")
    seen = set()
    out = _run(ws._watch_watchlist_news, db, seen)
    assert len(out) == 1 and "beats and raises" in out[0].title
    # second pass with the url already seen → no duplicate
    out2 = _run(ws._watch_watchlist_news, db, seen)
    assert out2 == []


def test_news_silent_when_no_headline(db, monkeypatch):
    monkeypatch.setattr(intel, "get_company_news", lambda tk, since_days=1, limit=5: [])
    wl.add_item(db, "KEYS")
    assert _run(ws._watch_watchlist_news, db) == []


def test_news_does_not_refire_after_dismissal(db, monkeypatch):
    # A dismissed news alert's key is absent from the open `seen` set; the watch
    # must still not re-create it (a story is a point-in-time event).
    monkeypatch.setattr(intel, "get_company_news", lambda tk, since_days=1, limit=5: [
        {"headline": "KEYS news", "url": "http://x/1", "summary": "", "source": "X"},
    ])
    wl.add_item(db, "KEYS")
    db.add(Alert(category="watchlist", severity="info", source="wl_news", status="dismissed",
                 dedupe_key="wl_news:KEYS:http://x/1", title="old", detail="x"))
    db.commit()
    assert _run(ws._watch_watchlist_news, db) == []
