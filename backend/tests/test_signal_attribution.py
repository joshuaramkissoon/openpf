"""Tests for the signal-attribution loop (predicted vs realized edge)."""

from datetime import datetime
from pathlib import Path
import sys
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models.entities import LeveragedSignal, LeveragedTrade
from app.services.signal_attribution import compute_attribution, data_driven_edge


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _add(db, *, direction, expected_edge, confidence, pnl_pct, regime="risk_on"):
    sid = str(uuid.uuid4())
    sig = LeveragedSignal(
        id=sid, status="executed", symbol="3NVD", instrument_code="3NVD_EQ",
        account_kind="stocks_isa", direction=direction, entry_side="buy",
        target_notional=200.0, reference_price=100.0, confidence=confidence,
        expected_edge=expected_edge, rationale="t", strategy_tag="leveraged-momentum",
        meta={"tech": {"regime": regime}},
    )
    db.add(sig)
    db.add(LeveragedTrade(
        id=str(uuid.uuid4()), status="closed", symbol="3NVD", instrument_code="3NVD_EQ",
        account_kind="stocks_isa", direction=direction, quantity=2.0, entry_price=100.0,
        entry_notional=200.0, entered_at=datetime(2026, 5, 1), exited_at=datetime(2026, 5, 2),
        pnl_pct=pnl_pct, pnl_value=pnl_pct * 200.0, close_reason="take_profit", signal_id=sid,
    ))
    db.commit()


def test_empty_history_is_uncalibrated_and_edge_unchanged(db):
    attr = compute_attribution(db)
    assert attr["overall"]["n"] == 0
    assert attr["overall"]["calibrated"] is False
    # No history → base edge returned untouched.
    assert data_driven_edge(attr, "long", 0.012) == 0.012


def test_attribution_aggregates_predicted_vs_realized(db):
    # 4 long winners, 2 long losers; predicted 1% edge, realized averages higher.
    for _ in range(4):
        _add(db, direction="long", expected_edge=0.01, confidence=0.7, pnl_pct=0.06)
    for _ in range(2):
        _add(db, direction="long", expected_edge=0.01, confidence=0.7, pnl_pct=-0.04)
    attr = compute_attribution(db)
    o = attr["overall"]
    assert o["n"] == 6
    assert o["calibrated"] is True
    assert o["win_rate"] == round(4 / 6, 3)
    assert o["avg_predicted_edge"] == 0.01
    # realized avg = (4*0.06 - 2*0.04)/6 = (0.24-0.08)/6 = 0.0267
    assert abs(o["avg_realized_pnl_pct"] - 0.02667) < 1e-3
    assert attr["by_direction"]["long"]["n"] == 6
    assert attr["by_regime"]["risk_on"]["n"] == 6


def test_data_driven_edge_blends_when_enough_history(db):
    # 6 long trades each realizing 2% → blended edge moves above the 0.6% base.
    for _ in range(6):
        _add(db, direction="long", expected_edge=0.006, confidence=0.6, pnl_pct=0.02)
    attr = compute_attribution(db)
    blended = data_driven_edge(attr, "long", 0.006)
    # 0.5*0.006 + 0.5*0.02 = 0.013, clamped to ceil 0.03 → 0.013
    assert abs(blended - 0.013) < 1e-6
    # Inverse direction has no history → unchanged.
    assert data_driven_edge(attr, "short", 0.006) == 0.006


def test_edge_blend_respects_floor_ceiling(db):
    for _ in range(6):
        _add(db, direction="short", expected_edge=0.01, confidence=0.6, pnl_pct=0.5)  # absurd win
    attr = compute_attribution(db)
    blended = data_driven_edge(attr, "inverse", 0.01)
    assert blended <= 0.03  # clamped to ceiling
