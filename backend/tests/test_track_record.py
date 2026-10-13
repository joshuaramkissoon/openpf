"""Archie's track record: every proposal tracked forward, whatever happened to it."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import ProposalOutcome, TradeIntent
from app.services import intent_category
from app.services import track_record_service as tr

TODAY = date(2026, 10, 13)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    yield s
    s.close()
    engine.dispose()


def _linear(start: float, daily: float, days: int = 200) -> list[tuple[date, float]]:
    d0 = TODAY - timedelta(days=days)
    return [(d0 + timedelta(days=i), start * (1 + daily) ** i) for i in range(days + 1)]


HISTORIES = {
    "UP": _linear(100.0, 0.002),     # rises
    "DOWN": _linear(100.0, -0.002),  # falls
    "SPY": _linear(400.0, 0.0005),   # gentle rise
}


def history_fn(ticker, days):
    if ticker not in HISTORIES:
        raise ValueError("no data")
    return HISTORIES[ticker]


def _intent(db, symbol, side, status, days_ago, *, reason=None, notional=100.0, **meta):
    i = TradeIntent(
        status=status, symbol=symbol, instrument_code=f"{symbol}_US_EQ", side=side, quantity=1,
        estimated_notional=notional, rationale=meta.pop("rationale", ""),
        created_at=datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time()) + timedelta(hours=12),
        failure_reason=reason, meta={"market_ticker": symbol, **meta},
    )
    if status == "executed":
        i.executed_at = i.created_at + timedelta(minutes=1)
        i.approved_by = "human"
    db.add(i)
    db.commit()
    return i


def test_marks_every_fate_against_the_benchmark(db):
    taken = _intent(db, "UP", "buy", "executed", 40, source="play", tranche_index=0)
    rejected = _intent(db, "UP", "buy", "rejected", 40, source="chat")
    superseded = _intent(db, "DOWN", "sell", "expired", 10, reason="superseded by a newer agent run")
    fresh = _intent(db, "UP", "buy", "proposed", 0)

    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY)
    rows = {r.intent_id: r for r in db.query(ProposalOutcome).all()}

    t = rows[taken.id]
    assert t.fate == "taken" and t.category == "play_tranche"
    assert set(t.marks) == {"1d", "1w", "1m"}  # 3m not reached yet
    assert t.marks["1m"]["ret"] > 0 and t.marks["1m"]["excess"] > 0
    assert rows[rejected.id].fate == "rejected"  # the counterfactual is marked too

    s = rows[superseded.id]
    assert s.fate == "superseded" and s.category == "trim_derisk"
    # A sell that the price fell after is a good call: positive directional return.
    assert s.marks["1w"]["ret"] < 0 and s.marks["1w"]["dir_ret"] > 0 and s.marks["1w"]["excess"] > 0

    assert rows[fresh.id].fate == "open" and rows[fresh.id].marks == {}


def test_reference_price_prefers_the_proposal_price(db):
    i = _intent(db, "UP", "buy", "executed", 20, reference_price=123.0, currency="USD", price_gbp=92.0)
    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY)
    row = db.query(ProposalOutcome).filter_by(intent_id=i.id).one()
    assert row.ref_price == 123.0 and row.ref_price_gbp == 92.0 and row.currency == "USD"


def test_unpriced_instruments_are_kept_but_flagged(db):
    _intent(db, "NOPE", "buy", "executed", 20)
    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY)
    row = db.query(ProposalOutcome).one()
    assert row.state == "unpriced" and row.marks == {}


def test_close_mark_for_a_taken_buy_later_sold(db):
    buy = _intent(db, "UP", "buy", "executed", 50)
    sell = _intent(db, "UP", "sell", "executed", 20)
    sell.execution_price = 130.0
    db.commit()
    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY)
    row = db.query(ProposalOutcome).filter_by(intent_id=buy.id).one()
    assert row.marks["close"]["price"] == 130.0
    assert row.state == "complete"


def test_summary_caveats_and_following_archie_vs_you(db):
    for k in range(3):
        _intent(db, "UP", "buy", "executed", 40 + k)      # taken winners
        _intent(db, "UP", "buy", "rejected", 40 + k)      # passed-on winners
    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY)
    s = tr.summary(db)
    assert s["headline"]["n_vs_benchmark"] == 6
    assert "too early to tell" in s["headline"]["caveat"]
    cmp = s["comparison"]
    assert cmp["following_archie"]["n"] == 6 and cmp["what_you_did"]["n"] == 3 and cmp["passed_on"]["n"] == 3
    assert cmp["following_archie"]["gain_gbp"] > cmp["what_you_did"]["gain_gbp"] > 0
    assert s["items"][0]["marks"]


def test_backfill_is_idempotent(db):
    _intent(db, "UP", "buy", "executed", 120)
    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY, backfill=True)
    first = db.query(ProposalOutcome).one().marks
    tr.mark_outcomes(db, history_fn=history_fn, today=TODAY, backfill=True)
    assert db.query(ProposalOutcome).count() == 1
    assert db.query(ProposalOutcome).one().marks == first
    assert "3m" in first


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({"side": "buy", "source": "rebalance", "rationale": ""}, "rebalance"),
        ({"side": "buy", "source": "play", "rationale": ""}, "play_tranche"),
        ({"side": "buy", "source": "chat", "rationale": "Put idle cash to work in NVDA"}, "deploy_cash"),
        ({"side": "buy", "source": "chat", "rationale": "Earnings beat; guidance raised"}, "news_reaction"),
        ({"side": "sell", "source": "chat", "rationale": "Take some off"}, "trim_derisk"),
        ({"side": "buy", "source": "chat", "rationale": "Nice company"}, "other"),
        ({"side": "buy", "source": "chat", "rationale": "", "meta": {"category": "news"}}, "news_reaction"),
    ],
)
def test_category_inference(kw, expected):
    assert intent_category.infer(**kw) == expected
