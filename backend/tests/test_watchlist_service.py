"""Tests for the watchlist service: CRUD, active_symbols, seeding/migration,
and the Attention-flag plumbing."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import Alert, AppConfig
from app.services import watchlist_service as wl


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_add_normalizes_symbol_and_defaults(db):
    item = wl.add_item(db, "keys", note="  defensive quality  ", source="archie")
    assert item.symbol == "KEYS"
    assert item.note == "defensive quality"
    assert item.source == "archie"
    assert item.status == "watching"
    assert item.monitor is True


def test_add_is_idempotent_on_active_symbol(db):
    a = wl.add_item(db, "NVDA", note="first")
    b = wl.add_item(db, "nvda", note="updated reason", conviction="high")
    assert a.id == b.id  # same row, updated in place
    assert b.note == "updated reason"
    assert b.conviction == "high"
    assert len(wl.list_items(db)) == 1


def test_invalid_conviction_and_direction_are_dropped(db):
    item = wl.add_item(db, "AAPL", conviction="bananas", target_direction="sideways")
    assert item.conviction is None
    assert item.target_direction is None


def test_update_fields(db):
    item = wl.add_item(db, "MSFT")
    updated = wl.update_item(db, item.id, {
        "note": "trim if it tags 500", "conviction": "medium",
        "target_price": 500.0, "target_direction": "above", "monitor": False,
    })
    assert updated.note == "trim if it tags 500"
    assert updated.conviction == "medium"
    assert updated.target_price == 500.0
    assert updated.target_direction == "above"
    assert updated.monitor is False


def test_update_missing_returns_none(db):
    assert wl.update_item(db, "nope", {"note": "x"}) is None


def test_remove(db):
    item = wl.add_item(db, "TSLA")
    assert wl.remove_item(db, item.id) is True
    assert wl.list_items(db) == []
    assert wl.remove_item(db, item.id) is False


def test_active_symbols_dedupes_and_filters_status(db):
    wl.add_item(db, "NVDA")
    wl.add_item(db, "AAPL")
    acted = wl.add_item(db, "TSLA")
    wl.update_item(db, acted.id, {"status": "acted"})
    syms = wl.active_symbols(db)
    assert set(syms) == {"NVDA", "AAPL"}  # acted item excluded


def test_monitored_items_excludes_muted(db):
    wl.add_item(db, "NVDA")
    muted = wl.add_item(db, "AAPL")
    wl.update_item(db, muted.id, {"monitor": False})
    syms = [i.symbol for i in wl.monitored_items(db)]
    assert syms == ["NVDA"]


def test_seed_from_config_uses_config_symbols(db):
    db.add(AppConfig(key="watchlist", value={"symbols": ["pltr", "amd"]}))
    db.commit()
    created = wl.seed_from_config_if_empty(db)
    assert created == 2
    assert set(wl.active_symbols(db)) == {"PLTR", "AMD"}


def test_seed_from_config_falls_back_to_defaults(db):
    created = wl.seed_from_config_if_empty(db)
    assert created == len(wl._SEED_FALLBACK)
    # idempotent: second call is a no-op
    assert wl.seed_from_config_if_empty(db) == 0


def test_raise_flag_dedupes(db):
    a = wl.raise_flag(db, "KEYS", title="News: beat earnings", detail="…", severity="info")
    assert a is not None
    dup = wl.raise_flag(db, "KEYS", title="News: beat earnings", detail="…", severity="info")
    assert dup is None  # same dedupe key, already open
    assert db.query(Alert).count() == 1


def test_open_flag_counts(db):
    wl.raise_flag(db, "KEYS", title="A", detail="x")
    wl.raise_flag(db, "KEYS", title="B", detail="y")
    wl.raise_flag(db, "NVDA", title="C", detail="z")
    counts = wl.open_flag_counts(db, ["KEYS", "NVDA", "AAPL"])
    assert counts["KEYS"]["open_flags"] == 2
    assert counts["NVDA"]["open_flags"] == 1
    assert "AAPL" not in counts
