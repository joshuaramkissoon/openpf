"""Plays: sizing math, tranche staging (quantities + rail pre-check) and
kill-rule evaluation / monitoring. No network: prices and history are injected."""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import Alert, Play, TradeIntent
from app.schemas.plays import PlayDraftOut
from app.services import plays_service as ps

RISK = {"max_single_order_notional": 500.0, "max_daily_notional": 1500.0, "max_position_weight": 0.2,
        "duplicate_order_window_seconds": 60}


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _play(db, **kw) -> Play:
    budget = kw.get("budget", 3000.0)
    play = Play(
        title=kw.get("title", "Nuclear renaissance"), status=kw.get("status", "active"), conviction="medium",
        account_kind="stocks_isa", budget_gbp=kw.get("budget", 3000.0),
        legs=kw.get("legs", [
            {"ticker": "CCJ", "instrument_code": "CCJ_US_EQ", "role": "core", "target_weight": 0.5},
            {"ticker": "NUCG.L", "instrument_code": "NUCGl_EQ", "role": "core", "target_weight": 0.3},
            {"ticker": "SMR", "instrument_code": "SMR_US_EQ", "role": "satellite", "target_weight": 0.2},
        ]),
        sizing={"tranches": ps.build_tranches(budget, RISK, max_leg_weight=0.5)},
        kill_criteria=kw.get("kill", []), review={}, meta={},
        activated_at=kw.get("activated_at", datetime.utcnow() - timedelta(days=30)),
    )
    db.add(play)
    db.commit()
    return play


# ── Sizing ──────────────────────────────────────────────────────────────────

def test_sizing_scales_with_conviction_and_vol():
    base = dict(book_gbp=80_000, basket_vol=0.25, overlap_gbp=0, free_cash_gbp=50_000, risk=RISK)
    low = ps.compute_sizing(conviction="low", **base)
    high = ps.compute_sizing(conviction="high", **base)
    assert low["budget_gbp"] == pytest.approx(1600)
    assert high["budget_gbp"] == pytest.approx(4800)
    # A basket twice as volatile as reference is halved (floor 0.5).
    vol = ps.compute_sizing(conviction="high", **{**base, "basket_vol": 0.5})
    assert vol["vol_factor"] == 0.5
    assert vol["budget_gbp"] == pytest.approx(2400)
    assert "volatility" in vol["rationale"]


def test_sizing_nets_out_existing_overlap_and_caps_at_cash():
    s = ps.compute_sizing(book_gbp=80_000, conviction="medium", basket_vol=None, overlap_gbp=1000,
                          free_cash_gbp=50_000, risk=RISK)
    assert s["budget_gbp"] == pytest.approx(3200 - 1000)
    capped = ps.compute_sizing(book_gbp=80_000, conviction="medium", basket_vol=None, overlap_gbp=0,
                               free_cash_gbp=900, risk=RISK)
    assert capped["budget_gbp"] == 900


def test_sizing_never_exceeds_play_ceiling_but_user_override_is_flagged():
    s = ps.compute_sizing(book_gbp=80_000, conviction="high", basket_vol=0.05, overlap_gbp=0,
                          free_cash_gbp=50_000, risk=RISK)
    assert s["target_pct_of_book"] <= ps.MAX_PLAY_PCT
    o = ps.compute_sizing(book_gbp=80_000, conviction="low", basket_vol=0.25, overlap_gbp=0,
                          free_cash_gbp=50_000, risk=RISK, budget_override_gbp=12_000)
    assert o["budget_gbp"] == 12_000
    assert any("ceiling" in w for w in o["warnings"])


def test_overlap_only_counts_up_to_each_legs_slice():
    legs = [{"ticker": "CCJ", "target_weight": 0.25}, {"ticker": "LEU", "target_weight": 0.75}]
    s = ps.compute_sizing(book_gbp=80_000, conviction="medium", basket_vol=None, free_cash_gbp=50_000,
                          risk=RISK, legs=legs, held_by_leg={"CCJ": 2600})
    # target 3200: CCJ slice 800 fully covered by the 2600 held; LEU needs 2400.
    assert s["overlap_gbp"] == pytest.approx(800)
    assert s["budget_gbp"] == pytest.approx(2400)
    alloc = {a["ticker"]: a["pct_of_budget"] for a in s["allocations"]}
    assert alloc == {"CCJ": 0.0, "LEU": 1.0}


def test_tranches_fit_daily_rail():
    small = ps.build_tranches(3000, RISK)
    assert [t["pct_of_budget"] for t in small] == [0.4, 0.3, 0.3]
    big = ps.build_tranches(7500, RISK)  # 7500 / 1500 → 5 tranches
    assert len(big) == 5
    assert sum(t["pct_of_budget"] for t in big) == pytest.approx(1.0)
    assert max(t["pct_of_budget"] for t in big) * 7500 <= 1500 + 1e-6
    # A 50% leg at 40% of a £3000 budget is a £600 order > £500 single-order rail → 4 tranches.
    legs = ps.build_tranches(3000, RISK, max_leg_weight=0.5)
    assert len(legs) == 4
    assert max(t["pct_of_budget"] for t in legs) * 3000 * 0.5 <= 500


def test_basket_volatility_from_history():
    def hist(t, _n):
        # alternating ±1% daily → ~16%/yr
        p, out = 100.0, []
        for i in range(120):
            p *= 1.01 if i % 2 else 0.99
            out.append((date(2026, 1, 1) + timedelta(days=i), p))
        return out
    vol = ps.basket_volatility([{"ticker": "A", "target_weight": 0.5}, {"ticker": "B", "target_weight": 0.5}], hist)
    assert vol == pytest.approx(0.01 * (252 ** 0.5), rel=0.05)


# ── Staging ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def priced(monkeypatch):
    """Stub instrument metadata + prices for propose_trade (no network)."""
    from app.services import proposal_service

    book = {
        "CCJ_US_EQ": ("CCJ", 50.0, "USD"),
        "NUCGl_EQ": ("NUCG", 25.0, "GBP"),
        "SMR_US_EQ": ("SMR", 20.0, "USD"),
    }
    fx = {"USD": 0.8, "GBP": 1.0}

    def resolve(db, symbol, instrument_code):
        if instrument_code not in book:
            return None
        t, _, ccy = book[instrument_code]
        return {"instrument_code": instrument_code, "ticker": t, "currency": ccy, "name": t, "short_name": t,
                "verified": True}

    monkeypatch.setattr(proposal_service, "_resolve_instrument", resolve)
    monkeypatch.setattr(proposal_service, "_reference_price",
                        lambda inst: (book[inst["instrument_code"]][1], book[inst["instrument_code"]][2]))
    monkeypatch.setattr(proposal_service, "_to_gbp", lambda amount, ccy: amount * fx[ccy])
    return book


def test_stage_creates_proposed_intents_with_quantities(db, priced):
    play = _play(db)
    res = ps.stage_tranche(db, play, risk=RISK, free_cash_gbp=10_000)
    assert res["tranche_index"] == 0
    assert res["notional_gbp"] == pytest.approx(750, abs=0.05)  # 4 tranches of 25% (rails) of 3000
    rows = db.execute(select(TradeIntent)).scalars().all()
    assert len(rows) == 3
    assert {r.status for r in rows} == {"proposed"}
    by = {r.symbol: r for r in rows}
    # CCJ: £375 at $50 × 0.8 = £40/share → 9.375 shares
    assert by["CCJ"].quantity == pytest.approx(9.375)
    # NUCG: £225 at £25 → 9 shares
    assert by["NUCG"].quantity == pytest.approx(9.0)
    m = by["CCJ"].meta
    assert m["play_id"] == play.id and m["source"] == "play" and m["account_kind"] == "stocks_isa"
    assert m["tranche_index"] == 0
    assert res["rail_breaches"] == []
    # Next call stages the following tranche, never the same one twice.
    assert ps.stage_tranche(db, play, risk=RISK, free_cash_gbp=10_000)["tranche_index"] == 1
    with pytest.raises(ValueError):
        ps.stage_tranche(db, play, 0, risk=RISK)


def test_stage_reports_rail_breaches_and_skips_unknown(db, priced):
    play = _play(db, budget=10_000.0, legs=[
        {"ticker": "CCJ", "instrument_code": "CCJ_US_EQ", "role": "core", "target_weight": 0.5},
        {"ticker": "SMR", "instrument_code": "SMR_US_EQ", "role": "satellite", "target_weight": 0.1},
        {"ticker": "XYZ", "instrument_code": "XYZ_US_EQ", "role": "satellite", "target_weight": 0.4},
    ])
    play.sizing = {"tranches": [{"label": "Starter", "pct_of_budget": 0.5, "trigger": "now"}]}
    db.commit()
    res = ps.stage_tranche(db, play, risk=RISK, free_cash_gbp=100)
    # CCJ £2,500 > £500 single-order rail → reported, not proposed.
    assert [b["ticker"] for b in res["rail_breaches"]] == ["CCJ"]
    assert [s["ticker"] for s in res["skipped"]] == ["XYZ"]
    assert [i["ticker"] for i in res["intents"]] == ["SMR"]
    assert res["warnings"]  # not enough cash
    assert db.execute(select(TradeIntent)).scalars().all()[0].symbol == "SMR"


def test_stage_refuses_closed_play(db):
    play = _play(db, status="closed")
    with pytest.raises(ValueError):
        ps.stage_tranche(db, play, risk=RISK)


# ── Kill rules ──────────────────────────────────────────────────────────────

def test_evaluate_rules():
    ctx = {"prices": {"CCJ": 41.0}, "drawdown_pct": 20.0, "weights": {"__play__": 0.11, "CCJ": 0.02},
           "today": date(2026, 10, 12)}
    assert ps.evaluate_rule({"type": "price_below", "ticker": "CCJ", "value": 42}, ctx)["state"] == "tripped"
    assert ps.evaluate_rule({"type": "price_below", "ticker": "CCJ", "value": 40}, ctx)["state"] == "near"
    assert ps.evaluate_rule({"type": "price_below", "ticker": "CCJ", "value": 30}, ctx)["state"] == "intact"
    assert ps.evaluate_rule({"type": "drawdown_pct", "value": 25}, ctx)["state"] == "near"
    assert ps.evaluate_rule({"type": "drawdown_pct", "value": 15}, ctx)["state"] == "tripped"
    assert ps.evaluate_rule({"type": "drawdown_pct", "value": 0.15}, ctx)["state"] == "tripped"
    assert ps.evaluate_rule({"type": "weight_above", "ticker": None, "value": 0.10}, ctx)["state"] == "tripped"
    assert ps.evaluate_rule({"type": "weight_above", "ticker": "CCJ", "value": 5}, ctx)["state"] == "intact"
    assert ps.evaluate_rule({"type": "date_after", "value": "2026-10-01"}, ctx)["state"] == "tripped"
    assert ps.evaluate_rule({"type": "date_after", "value": "2026-10-20"}, ctx)["state"] == "near"
    assert ps.evaluate_rule(None, ctx)["state"] == "manual"
    assert ps.verdict_from_states(["intact", "near"]) == "watch"
    assert ps.verdict_from_states(["intact", "tripped"]) == "broken"
    assert ps.verdict_from_states(["intact"]) == "on_track"


def test_review_active_plays_raises_deduped_alert(db):
    kill = [
        {"text": "Cameco breaks below $45", "rule": {"type": "price_below", "ticker": "CCJ", "value": 45}},
        {"text": "Basket down 30%", "rule": {"type": "drawdown_pct", "value": 30}},
    ]
    play = _play(db, kill=kill)
    book = {"total_gbp": 80_000, "free_cash_gbp": {"stocks_isa": 5000}, "positions": [
        {"ticker": "CCJ", "raw_ticker": "CCJ", "instrument_code": "CCJ_US_EQ", "yfinance_ticker": "CCJ",
         "account_kind": "stocks_isa", "value": 600.0, "ppl": -40.0, "weight": 0.0075}]}

    def hist(t, n):
        return [(date.today() - timedelta(days=5 - i), 100.0 - i) for i in range(6)]

    kwargs = dict(book=book, history_fn=hist, price_fn=lambda t: 40.0)
    res = ps.review_active_plays(db, **kwargs)
    assert res["reviewed"] == 1
    alerts = db.execute(select(Alert).where(Alert.category == "play")).scalars().all()
    assert any("kill rule tripped" in a.title and a.meta["play_id"] == play.id for a in alerts)
    db.refresh(play)
    assert play.review["verdict"] == "broken"
    # Second run does not duplicate the open alert.
    n_before = len(alerts)
    ps.review_active_plays(db, **kwargs)
    assert len(db.execute(select(Alert).where(Alert.category == "play")).scalars().all()) == n_before


# ── Draft schema ────────────────────────────────────────────────────────────

def test_draft_schema_requires_machine_checkable_rules():
    base = {
        "title": "AI power", "thesis": "x", "what_would_change_my_mind": "y",
        "legs": [{"ticker": "VST", "name": "Vistra", "role": "core", "weight": 0.5, "rationale": "r"},
                 {"ticker": "CEG", "name": "Constellation", "role": "core", "weight": 0.5, "rationale": "r"}],
        "kill_criteria": [{"text": "a"}, {"text": "b", "rule": {"type": "drawdown_pct", "value": 30}}],
    }
    with pytest.raises(Exception):
        PlayDraftOut.model_validate(base)
    base["kill_criteria"][0]["rule"] = {"type": "price_below", "ticker": "VST", "value": 100}
    assert PlayDraftOut.model_validate(base).legs[0].ticker == "VST"


def test_drift_only_flagged_once_basket_is_built(db):
    play = _play(db, budget=1000.0)
    hist = lambda t, n: [(date.today() - timedelta(days=1), 10.0), (date.today(), 10.0)]  # noqa: E731
    pos = lambda v: {"total_gbp": 80_000, "free_cash_gbp": {}, "positions": [  # noqa: E731
        {"ticker": "CCJ", "raw_ticker": "CCJ", "instrument_code": "CCJ_US_EQ", "yfinance_ticker": "CCJ",
         "account_kind": "stocks_isa", "value": v, "ppl": 0.0, "weight": v / 80_000}]}
    # Mid-entry: only CCJ held (100% of the play vs 50% target) — not drift yet.
    perf = ps.play_performance(db, play, book=pos(300.0), history_fn=hist, price_fn=lambda t: 10.0)
    assert perf["basket_built"] is False
    ps.review_active_plays(db, book=pos(300.0), history_fn=hist, price_fn=lambda t: 10.0)
    assert not db.execute(select(Alert).where(Alert.dedupe_key.like("%drift"))).scalars().all()
    # Fully staged + built but all in one leg → drift alert.
    play.sizing = {**play.sizing, "staged": [{"index": i} for i in range(len(play.sizing["tranches"]))]}
    db.commit()
    ps.review_active_plays(db, book=pos(1000.0), history_fn=hist, price_fn=lambda t: 10.0)
    assert db.execute(select(Alert).where(Alert.dedupe_key.like("%drift"))).scalars().all()
