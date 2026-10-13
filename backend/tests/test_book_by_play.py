"""The book by play: ownership (one owner per holding, sums to 100%), the three
rings, needs-you cues, suggestions from Unsorted holdings, persisted draft jobs,
and graceful handling of garbage / impossible input. No network."""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import HoldingOwner, Play, PlayDraftJob, TradeIntent
from app.services import book_service as bs
from app.services import plays_service as ps

RISK = {"max_single_order_notional": 500.0, "max_daily_notional": 1500.0, "max_position_weight": 0.2,
        "duplicate_order_window_seconds": 60}


def _pos(ticker, value, account="stocks_isa", ppl=0.0, price=100.0, name=None):
    return {"ticker": ticker, "raw_ticker": ticker, "instrument_code": f"{ticker}_US_EQ", "yfinance_ticker": ticker,
            "name": name or ticker, "account_kind": account, "value": value, "ppl": ppl, "weight": value / 10_000,
            "price": price}


BOOK = {
    "total_gbp": 10_000.0,
    "free_cash_gbp": {"stocks_isa": 1000.0, "invest": 0.0},  # 9000 held + 1000 cash
    "positions": [
        _pos("NVDA", 2000, ppl=400), _pos("AVGO", 1000, ppl=100), _pos("TSM", 800, ppl=-50),
        _pos("MSFT", 1500, ppl=200), _pos("CCJ", 700, ppl=70), _pos("SPY", 2000, account="invest", ppl=150),
        _pos("COIN", 300, account="invest", ppl=-60), _pos("PLTR", 700, account="invest", ppl=300),
    ],
}


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No market data / instrument list: every ticker resolves unverified-but-ok."""
    monkeypatch.setattr(ps, "basket_volatility", lambda legs, *a, **k: None)
    monkeypatch.setattr(ps, "instruments_known", lambda db: False)
    monkeypatch.setattr(ps, "resolve_instrument", lambda db, t: (f"{t.split('.')[0]}_US_EQ", True))


def _play(db, title="AI power", legs=("NVDA", "AVGO", "VRT"), status="active", **kw) -> Play:
    n = len(legs)
    play = Play(title=title, status=status, conviction="medium", account_kind=kw.get("account", "stocks_isa"),
                budget_gbp=kw.get("budget", 1000.0),
                legs=[{"ticker": t, "instrument_code": f"{t}_US_EQ", "role": "core", "target_weight": 1 / n,
                       "rationale": f"{t} powers the theme"} for t in legs],
                sizing=kw.get("sizing", {"tranches": ps.build_tranches(1000, RISK), "target_pct_of_book": 0.06}),
                kill_criteria=kw.get("kill", []), review={}, meta={}, related=[], bench=[],
                activated_at=kw.get("activated_at", datetime.utcnow() - timedelta(days=30)) if status != "draft" else None,
                created_at=kw.get("created_at", datetime.utcnow() - timedelta(days=40)))
    db.add(play)
    db.commit()
    return play


def _group(book, play_or_kind):
    key = play_or_kind if isinstance(play_or_kind, str) else play_or_kind.id
    return next(g for g in book["groups"] if g["id"] == key)


# ── Ownership ───────────────────────────────────────────────────────────────

def test_every_holding_has_one_owner_and_book_sums_to_100(db):
    ai = _play(db)
    book = bs.compute_book(db, book=BOOK)
    owners = {h["ticker"]: h["owner"]["kind"] for h in book["holdings"]}
    assert len(owners) == len(BOOK["positions"])
    assert _group(book, ai)["value_gbp"] == pytest.approx(3000)  # NVDA + AVGO derived from the basket
    assert {h["ticker"] for h in _group(book, ai)["owned"]} == {"NVDA", "AVGO"}
    assert all(h["owner"]["source"] == "basket" for h in _group(book, ai)["owned"])
    total = sum(g["value_gbp"] for g in book["groups"]) + book["cash_gbp"]
    assert total == pytest.approx(BOOK["total_gbp"])  # groups + cash = the whole book
    weights = sum(g["weight"] for g in book["groups"])
    assert weights == pytest.approx(sum(p["value"] for p in BOOK["positions"]) / BOOK["total_gbp"])


def test_oldest_active_play_owns_shared_legs_and_others_see_it_as_related(db):
    ai = _play(db, created_at=datetime.utcnow() - timedelta(days=90), activated_at=datetime.utcnow() - timedelta(days=80))
    chips = _play(db, title="Chips", legs=("NVDA", "TSM"), activated_at=datetime.utcnow() - timedelta(days=5))
    book = bs.compute_book(db, book=BOOK)
    assert {h["ticker"] for h in _group(book, chips)["owned"]} == {"TSM"}
    rel = _group(book, chips)["related"]
    assert [r["ticker"] for r in rel] == ["NVDA"] and rel[0]["owner"]["play_id"] == ai.id
    assert rel[0]["why"] == "NVDA powers the theme"
    g = _group(book, chips)
    assert g["effective_value_gbp"] == pytest.approx(800 + 2000)
    nvda = next(h for h in book["holdings"] if h["ticker"] == "NVDA")
    assert nvda["related_to"][0]["play_id"] == chips.id


def test_assign_owner_moves_holding_and_is_reflected(db):
    ai = _play(db)
    bs.assign_owner(db, "MSFT", ai.id, book=BOOK)
    bs.assign_owner(db, "SPY", "core", book=BOOK)
    bs.assign_owner(db, "NVDA", "core", book=BOOK)
    book = bs.compute_book(db, book=BOOK)
    assert {h["ticker"] for h in _group(book, ai)["owned"]} == {"AVGO", "MSFT"}
    assert {h["ticker"] for h in _group(book, "core")["owned"]} == {"SPY", "NVDA"}
    # NVDA is still in AI's basket, owned by Core → related, not counted in size.
    assert [r["ticker"] for r in _group(book, ai)["related"]] == ["NVDA"]
    assert _group(book, ai)["value_gbp"] == pytest.approx(2500)


def test_assign_owner_rejects_bad_targets(db):
    closed = _play(db, title="Old", status="closed")
    with pytest.raises(bs.OwnershipError, match="don't hold"):
        bs.assign_owner(db, "ZZZZ", "core", book=BOOK)
    with pytest.raises(bs.OwnershipError, match="doesn't exist"):
        bs.assign_owner(db, "NVDA", "no-such-play", book=BOOK)
    with pytest.raises(bs.OwnershipError, match="closed"):
        bs.assign_owner(db, "NVDA", closed.id, book=BOOK)
    with pytest.raises(bs.OwnershipError):
        bs.assign_owner(db, "  ", "core", book=BOOK)


def test_deleting_a_play_sends_its_holdings_to_unsorted(db, monkeypatch):
    other = _play(db, title="Chips", legs=("NVDA", "TSM"), activated_at=datetime.utcnow() - timedelta(days=1))
    ai = _play(db, activated_at=datetime.utcnow() - timedelta(days=60))
    bs.assign_owner(db, "MSFT", ai.id, book=BOOK)
    monkeypatch.setattr(ps, "book_context", lambda db: BOOK)
    moved = ps.delete_play(db, ai)
    assert moved == 3  # NVDA, AVGO (derived) + MSFT (explicit), each counted once
    book = bs.compute_book(db, book=BOOK)
    unsorted = {h["ticker"] for h in _group(book, "unsorted")["owned"]}
    assert {"NVDA", "AVGO", "MSFT"} <= unsorted  # NVDA does NOT silently jump to "Chips"
    assert {h["ticker"] for h in _group(book, other)["owned"]} == {"TSM"}


def test_closing_a_play_releases_explicit_ownership(db):
    ai = _play(db)
    bs.assign_owner(db, "MSFT", ai.id, book=BOOK)
    ai.status = "closed"
    db.commit()
    book = bs.compute_book(db, book=BOOK)
    msft = next(h for h in book["holdings"] if h["ticker"] == "MSFT")
    assert msft["owner"]["kind"] == "unsorted"


def test_sizing_ignores_holdings_owned_by_core_or_other_plays(db):
    bs_play = _play(db, title="Chips", legs=("NVDA", "TSM"), status="draft")
    bs.assign_owner(db, "NVDA", "core", book=BOOK)
    ps.resize_play(db, bs_play, risk=RISK, book=BOOK)
    held = {a["ticker"]: a["held_gbp"] for a in bs_play.sizing["allocations"]}
    assert held["NVDA"] == 0  # Core owns it — not double counted
    assert held["TSM"] == pytest.approx(800)  # Unsorted, so the draft would adopt it


# ── Rings ───────────────────────────────────────────────────────────────────

def test_related_and_bench_rings(db):
    ai = _play(db)
    bs.assign_owner(db, "MSFT", "core", book=BOOK)
    bs.add_related(db, ai, "MSFT", "Azure is the biggest buyer of AI power", book=BOOK)
    bs.add_bench(db, ai, "GEV", "Turbines for datacentres", "GE Vernova", book=BOOK)
    book = bs.compute_book(db, book=BOOK)
    g = _group(book, ai)
    assert [r["ticker"] for r in g["related"]] == ["MSFT"] and g["related"][0]["source"] == "user"
    assert g["effective_weight"] == pytest.approx((3000 + 1500) / 10_000)
    bench = {b["ticker"]: b for b in g["bench"]}
    assert bench["VRT"]["in_plan"] is True and bench["GEV"]["in_plan"] is False
    # Friendly rejections
    with pytest.raises(bs.OwnershipError, match="don't hold"):
        bs.add_related(db, ai, "ZZZZ", "", book=BOOK)
    with pytest.raises(bs.OwnershipError, match="already hold"):
        bs.add_bench(db, ai, "MSFT", "", book=BOOK)
    with pytest.raises(bs.OwnershipError, match="ticker"):
        bs.add_bench(db, ai, "not a ticker!", "", book=BOOK)
    # Hiding an auto-related basket leg owned elsewhere
    bs.assign_owner(db, "AVGO", "core", book=BOOK)
    bs.remove_related(db, ai, "AVGO")
    g = _group(bs.compute_book(db, book=BOOK), ai)
    assert "AVGO" not in {r["ticker"] for r in g["related"]}


def test_bench_rejects_tickers_not_on_t212(db, monkeypatch):
    ai = _play(db)
    monkeypatch.setattr(ps, "instruments_known", lambda db: True)
    monkeypatch.setattr(ps, "resolve_instrument", lambda db, t: ("X", False))
    with pytest.raises(bs.OwnershipError, match="isn't available on Trading 212"):
        bs.add_bench(db, ai, "FAKE", "", book=BOOK)


def test_promote_bench_adds_a_leg(db, monkeypatch):
    ai = _play(db)
    monkeypatch.setattr(ps, "book_context", lambda db: BOOK)
    monkeypatch.setattr("app.services.config_store.ConfigStore.get_risk", lambda self: RISK)
    bs.add_bench(db, ai, "GEV", "Turbines", book=BOOK)
    ai = bs.promote_bench(db, ai, "GEV", 0.2)
    assert "GEV" in [l["ticker"] for l in ai.legs]
    assert sum(l["target_weight"] for l in ai.legs) == pytest.approx(1.0, abs=1e-3)
    assert not ai.bench


# ── Needs-you + theme risk ──────────────────────────────────────────────────

def test_needs_you_cues(db):
    kill = [{"text": "Play too big", "rule": {"type": "weight_above", "ticker": None, "value": 0.25}}]
    staged_all = {"tranches": ps.build_tranches(1000, RISK), "target_pct_of_book": 0.06}
    staged_all["staged"] = [{"index": i} for i in range(len(staged_all["tranches"]))]
    ai = _play(db, kill=kill, sizing=staged_all)
    g = _group(bs.compute_book(db, book=BOOK), ai)
    kinds = [n["kind"] for n in g["needs"]]
    # 30% of book vs a 25% cap → tripped; 30% vs 6% target → over size.
    assert kinds[0] == "kill_tripped" and "drift_over" in kinds
    # A play whose holdings were all sold
    sold = {**BOOK, "positions": [p for p in BOOK["positions"] if p["ticker"] not in {"NVDA", "AVGO"}]}
    g = _group(bs.compute_book(db, book=sold), ai)
    assert g["value_gbp"] == 0 and any(n["kind"] == "empty" for n in g["needs"])


def test_tranche_due_and_awaiting(db):
    sizing = {"tranches": ps.build_tranches(1000, RISK), "target_pct_of_book": 0.06,
              "staged": [{"index": 0}]}
    ai = _play(db, sizing=sizing, activated_at=datetime.utcnow() - timedelta(weeks=20))
    g = _group(bs.compute_book(db, book=BOOK), ai)
    assert any(n["kind"] == "tranche_due" for n in g["needs"])
    db.add(TradeIntent(symbol="VRT", instrument_code="VRT_US_EQ", side="buy", quantity=1, status="proposed",
                       meta={"play_id": ai.id, "source": "play"}))
    db.commit()
    g = _group(bs.compute_book(db, book=BOOK), ai)
    assert any(n["kind"] == "awaiting" for n in g["needs"])


def test_theme_concentration(db):
    ai = _play(db)
    bs.assign_owner(db, "TSM", ai.id, book=BOOK)
    risk = bs.compute_book(db, book=BOOK)["theme_risk"]
    assert risk[0]["play_id"] == ai.id and risk[0]["tone"] == "warn"
    assert "38% of your book is one bet: AI power" in risk[0]["text"]


# ── Suggestions ─────────────────────────────────────────────────────────────

class _Store:
    data: dict = {}

    def __init__(self, db):
        pass

    def get(self, key, default):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        return value


@pytest.fixture()
def store(monkeypatch):
    _Store.data = {}
    monkeypatch.setattr(bs, "_store", lambda db: _Store(db))
    return _Store


def test_rules_suggestions_group_unsorted_and_never_auto_apply(db, store):
    out = bs.generate_suggestions(db, use_model=False, book=BOOK)
    assert out["source"] == "rules"
    by_title = {i["title"]: i for i in out["items"]}
    assert set(by_title["AI infrastructure"]["tickers"]) == {"NVDA", "AVGO", "TSM"}
    assert by_title["AI infrastructure"]["weight"] == pytest.approx(0.38)
    assert set(by_title["Core"]["tickers"]) == {"MSFT", "SPY"}
    # Nothing moved yet
    assert not db.execute(select(HoldingOwner)).scalars().all()


def test_accept_and_dismiss_suggestions(db, store):
    out = bs.generate_suggestions(db, use_model=False, book=BOOK)
    ai = next(i for i in out["items"] if i["title"] == "AI infrastructure")
    core = next(i for i in out["items"] if i["kind"] == "core")
    res = bs.accept_suggestion(db, ai["id"], title="AI picks & shovels", tickers=["NVDA", "AVGO"], book=BOOK)
    play = db.get(Play, res["play_id"])
    assert play.status == "active" and play.title == "AI picks & shovels" and play.meta.get("origin") == "adopted"
    assert play.sizing.get("tranches") == []  # built from what you own: nothing to stage
    bs.accept_suggestion(db, core["id"], book=BOOK)
    book = bs.compute_book(db, book=BOOK)
    assert {h["ticker"] for h in _group(book, play)["owned"]} == {"NVDA", "AVGO"}
    # Adopting what you hold must not instantly read as "too big" or "kill rule tripped".
    assert _group(book, play)["target_pct"] == pytest.approx(0.30)
    assert not [n for n in _group(book, play)["needs"] if n["kind"] in {"kill_tripped", "kill_near", "drift_over"}]
    assert {h["ticker"] for h in _group(book, "core")["owned"]} == {"MSFT", "SPY"}
    left = bs.get_suggestions(db, book=BOOK)["items"]
    assert all(i["id"] not in {ai["id"], core["id"]} for i in left)
    for i in left:
        bs.dismiss_suggestion(db, i["id"])
    assert bs.get_suggestions(db, book=BOOK)["items"] == []
    with pytest.raises(bs.OwnershipError, match="no longer available"):
        bs.accept_suggestion(db, ai["id"], book=BOOK)
    # Staging an adopted play with nothing planned is a friendly no
    with pytest.raises(ps.PlayInputError, match="No new money"):
        ps.stage_tranche(db, play, risk=RISK, free_cash_gbp=1000)


def _kill_states(db, play, book):
    perf = ps.play_performance(db, play, book=book, history_fn=lambda t, n: [], price_fn=lambda t: None)
    return {k["rule"]["type"]: k["state"] for k in perf["kill_status"] if k.get("rule")}


def test_adopted_play_does_not_trip_its_own_concentration_cap(db, store):
    """Regression: a play accepted from 'Sort with Archie' showed Broken at once —
    'grows past 58% of the book' Triggered. The cap came from the Unsorted pool
    (45%) but the move also pulled the same ticker out of Core in the other
    account, so the play really started at 70%."""
    book = {
        "total_gbp": 10_000.0,
        "free_cash_gbp": {"stocks_isa": 500.0, "invest": 0.0},
        "positions": [_pos("NVDA", 3000), _pos("AVGO", 1500), _pos("NVDA", 2500, account="invest"),
                      _pos("MSFT", 1000), _pos("SPY", 1500, account="invest")],
    }
    bs.assign_owner(db, "NVDA", "core", account_kind="invest", book=book)
    out = bs.generate_suggestions(db, use_model=False, book=book)
    ai = next(i for i in out["items"] if i["title"] == "AI infrastructure")
    assert ai["weight"] == pytest.approx(0.45)
    play = db.get(Play, bs.accept_suggestion(db, ai["id"], book=book)["play_id"])
    full = bs.compute_book(db, book=book)
    # Only the Unsorted holdings move: Core keeps the Invest NVDA.
    assert {h["key"] for h in _group(full, play)["owned"]} == {"stocks_isa:NVDA", "stocks_isa:AVGO"}
    assert {h["key"] for h in _group(full, "core")["owned"]} >= {"invest:NVDA"}
    assert _group(full, play)["weight"] == pytest.approx(0.45)
    cap = next(c for c in play.kill_criteria if c["rule"]["type"] == "weight_above")
    assert cap["rule"]["value"] > 0.45 / 0.9  # not even "near"
    assert _kill_states(db, play, book)["weight_above"] == "intact"
    assert not [n for n in _group(full, play)["needs"] if n["kind"] in {"kill_tripped", "kill_near"}]

    # Joining more holdings to the adopted play later lifts its cap with it.
    book["positions"].append(_pos("TSM", 1500))
    sug = bs.save_agent_suggestions(db, [{"kind": "existing", "play_id": play.id, "tickers": ["TSM"]}], book=book)
    bs.accept_suggestion(db, sug["items"][0]["id"], book=book)
    db.refresh(play)
    assert _group(bs.compute_book(db, book=book), play)["weight"] == pytest.approx(0.60)
    assert _kill_states(db, play, book)["weight_above"] == "intact"
    assert play.sizing["target_pct_of_book"] == pytest.approx(0.60)


def test_adopted_cap_only_lifts_the_automatic_rule(db):
    play = _play(db, legs=("NVDA",), sizing={"adopted": True, "target_pct_of_book": 0.2, "tranches": []},
                 kill=[{"text": "This bet grows past 25% of the book", "rule": {"type": "weight_above", "ticker": None, "value": 0.25}},
                       {"text": "NVDA over half the play", "rule": {"type": "weight_above", "ticker": "NVDA", "value": 0.5}},
                       {"text": "Hard cap I set", "rule": {"type": "weight_above", "ticker": None, "value": 0.3}}])
    bs.assign_owner(db, "NVDA", play.id, book=BOOK)
    bs.fit_adopted_cap(db, play, {**BOOK, "total_gbp": 5000.0})  # NVDA 2000 → 40%
    rules = [c["rule"]["value"] for c in play.kill_criteria]
    assert rules == [0.52, 0.5, 0.3]  # the user's own rules are left alone
    assert play.kill_criteria[0]["text"] == "This bet grows past 52% of the book"


def test_model_suggestions_are_validated_and_fall_back(db, store, monkeypatch):
    monkeypatch.setattr(bs, "_model_suggestions", lambda *a: [
        {"kind": "play", "title": "AI", "tickers": ["NVDA", "NVDA", "FAKE", "tsm"], "why": "chips"},
        {"kind": "core", "title": "Core", "tickers": ["NVDA", "SPY"], "why": "index"},  # NVDA already used
        {"kind": "play", "title": "Empty", "tickers": ["NOPE"], "why": ""},
    ])
    out = bs.generate_suggestions(db, use_model=True, book=BOOK)
    assert out["source"] == "archie"
    assert [i["tickers"] for i in out["items"]] == [["NVDA", "TSM"], ["SPY"]]

    def boom(*a):
        raise RuntimeError("no claude login")
    monkeypatch.setattr(bs, "_model_suggestions", boom)
    assert bs.generate_suggestions(db, use_model=True, book=BOOK)["source"] == "rules"


def test_background_regroup_returns_rules_at_once_then_swaps_in_refined(db, store, monkeypatch):
    import copy
    import time as _t

    started = []
    monkeypatch.setattr(bs, "refine_available", lambda: True)
    monkeypatch.setattr(bs, "_refine_in_background", lambda gen, unsorted: started.append((gen, unsorted)))
    t0 = _t.monotonic()
    out = bs.generate_suggestions(db, use_model=True, book=BOOK, background=True)
    assert _t.monotonic() - t0 < 1.0
    assert out["source"] == "rules" and out["refining"] is True and out["items"]
    gen, unsorted = started[0]
    monkeypatch.setattr(bs, "_model_suggestions", lambda *a: [{"kind": "play", "title": "Chips", "tickers": ["NVDA", "TSM"], "why": "x"}])
    assert bs.apply_refinement(db, gen, unsorted) is True
    after = bs.get_suggestions(db, book=BOOK)
    assert after["source"] == "archie" and after["refining"] is False
    assert [i["title"] for i in after["items"]] == ["Chips"]

    # The user acted on a rules card meanwhile → the refinement doesn't clobber it.
    out = bs.generate_suggestions(db, use_model=True, book=BOOK, background=True)
    gen2 = started[-1][0]
    bs.dismiss_suggestion(db, out["items"][0]["id"])
    before = copy.deepcopy(store.data)
    assert bs.apply_refinement(db, gen2, unsorted) is False
    now = bs.get_suggestions(db, book=BOOK)
    assert now["source"] == "rules" and now["refining"] is False
    assert [i["id"] for i in now["items"]] == [i["id"] for i in bs._decorate(before[bs.SUGGESTIONS_KEY]["items"], unsorted)]


def test_background_regroup_without_a_fast_model_just_shows_rules(db, store, monkeypatch):
    monkeypatch.setattr(bs, "refine_available", lambda: False)
    monkeypatch.setattr(bs, "_refine_in_background", lambda *a: pytest.fail("should not refine"))
    out = bs.generate_suggestions(db, use_model=True, book=BOOK, background=True)
    assert out["source"] == "rules" and out["refining"] is False


def test_proposals_answer_for_which_play(db):
    ai = _play(db)
    bs.assign_owner(db, "SPY", "core", book=BOOK)
    mk = lambda sym, **meta: TradeIntent(symbol=sym, instrument_code=f"{sym}_US_EQ", side="buy", quantity=1, meta=meta)  # noqa: E731
    idx = bs.ownership_index(db)
    assert bs.intent_owner(idx, mk("VRT", play_id=ai.id))["title"] == "AI power"
    assert bs.intent_owner(idx, mk("SPY"))["kind"] == "core"
    inferred = bs.intent_owner(idx, mk("NVDA"))
    assert inferred["play_id"] == ai.id and inferred["inferred"] is True
    assert bs.intent_owner(idx, mk("KO")) is None  # not part of any play
    assert bs.intent_owner(idx, mk("KO", owner="core"))["kind"] == "core"
    assert bs.intent_owner(idx, mk("KO", play_id="deleted-play", play_title="Gone")) is None


# ── Garbage input ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("idea", ["", "  ", "as", "asdf", "qwerty", "!!!???", "12345 678", "make me rich",
                                  "I want to get rich quick", "best stocks", "zzzz"])
def test_nonsense_ideas_are_rejected_kindly(idea):
    msg = ps.idea_problem(idea)
    assert msg and "Traceback" not in msg and len(msg) < 220


@pytest.mark.parametrize("idea", ["Nuclear renaissance", "AI datacentres need far more power",
                                  "GLP-1 drugs keep expanding", "Europe rearms for a decade"])
def test_real_ideas_pass(idea):
    assert ps.idea_problem(idea) is None


@pytest.mark.parametrize("data,match", [
    ({"title": ""}, "short name"),
    ({"title": "x", "conviction": "extreme"}, "low, medium or high"),
    ({"title": "x", "horizon_months": 0}, "between 1 and 120"),
    ({"title": "x", "horizon_months": "soon"}, "whole number"),
    ({"title": "x", "budget_gbp": -5}, "positive"),
    ({"title": "x", "budget_gbp": float("nan")}, "positive"),
    ({"title": "x", "account_kind": "sipp"}, "Invest or Stocks ISA"),
    ({"title": "x", "legs": [{"ticker": "NVDA", "target_weight": 0.5}, {"ticker": "nvda", "target_weight": 0.5}]}, "twice"),
    ({"title": "x", "legs": [{"ticker": "$$$", "target_weight": 1}]}, "doesn't look like a ticker"),
    ({"title": "x", "legs": [{"ticker": "A", "target_weight": 0}, {"ticker": "B", "target_weight": 0}]}, "above zero"),
])
def test_out_of_range_play_input(data, match):
    with pytest.raises(ps.PlayInputError, match=match):
        ps.validate_play_input(data, creating=True)


def test_staging_with_no_cash_is_refused_kindly(db):
    ai = _play(db)
    with pytest.raises(ps.PlayInputError, match="no free cash"):
        ps.stage_tranche(db, ai, risk=RISK, free_cash_gbp=0.0)
    paused = _play(db, title="P", status="paused")
    with pytest.raises(ps.PlayInputError, match="Resume"):
        ps.stage_tranche(db, paused, risk=RISK, free_cash_gbp=100)
    with pytest.raises(ps.PlayInputError, match="no tranche 9"):
        ps.stage_tranche(db, ai, 8, risk=RISK, free_cash_gbp=100)


def test_draft_drops_unlisted_legs_or_fails_kindly(db, monkeypatch):
    from app.schemas.plays import PlayDraftOut

    monkeypatch.setattr(ps, "instruments_known", lambda db: True)
    listed = {"VST", "CEG"}
    monkeypatch.setattr(ps, "resolve_instrument", lambda db, t: (f"{t}_US_EQ", t in listed))
    monkeypatch.setattr("app.services.config_store.ConfigStore.get_risk", lambda self: RISK)
    kills = [{"text": "a", "rule": {"type": "drawdown_pct", "value": 30}},
             {"text": "b", "rule": {"type": "price_below", "ticker": "VST", "value": 50}}]
    leg = lambda t: {"ticker": t, "name": t, "role": "core", "weight": 0.25, "rationale": "r"}  # noqa: E731
    out = PlayDraftOut.model_validate({"title": "Power", "thesis": "t", "what_would_change_my_mind": "w",
                                       "legs": [leg("VST"), leg("CEG"), leg("FAKE1"), leg("FAKE2")], "kill_criteria": kills})
    play = ps._draft_to_play(db, {"idea": "power"}, out, BOOK)
    assert [l["ticker"] for l in play.legs] == ["VST", "CEG"] and play.meta["dropped_legs"] == ["FAKE1", "FAKE2"]
    out2 = PlayDraftOut.model_validate({"title": "Moon", "thesis": "t", "what_would_change_my_mind": "w",
                                        "legs": [leg("VST"), leg("FAKE1"), leg("FAKE2")], "kill_criteria": kills})
    with pytest.raises(ps.DraftFailure) as e:
        ps._draft_to_play(db, {"idea": "moon"}, out2, BOOK)
    assert e.value.kind == "no_instruments" and "Trading 212" in e.value.message


def test_draft_schema_allows_unclear_without_a_basket():
    from app.schemas.plays import PlayDraftOut

    out = PlayDraftOut.model_validate({"unclear": "That's a goal, not a theme."})
    assert out.unclear and not out.legs
    with pytest.raises(Exception):
        PlayDraftOut.model_validate({"title": "x"})


# ── Persisted draft jobs ────────────────────────────────────────────────────

@pytest.fixture()
def jobs(engine, monkeypatch):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(ps, "_session_factory", factory)
    return factory


def test_draft_job_persists_and_restart_marks_it_failed_with_retry(db, jobs):
    job = ps.start_draft_job(db, {"idea": "Nuclear renaissance", "account_kind": "stocks_isa"}, run=False)
    assert job["status"] == "running"
    handle = ps.DraftJob(job["job_id"], job["request"])
    handle.add("Researching the theme")
    assert ps.fail_interrupted_jobs(db) == 1  # what the startup sweep does
    db.expire_all()
    got = ps.get_draft_job(db, job["job_id"])
    assert got["status"] == "error" and got["error_kind"] == "interrupted" and got["retryable"]
    assert [p["text"] for p in got["progress"]][:2] == ["Reading your portfolio", "Researching the theme"]
    retry = ps.retry_draft_job(db, job["job_id"], run=False)
    assert retry["status"] == "running" and retry["job_id"] != job["job_id"]
    assert db.get(PlayDraftJob, retry["job_id"]).retry_of == job["job_id"]


def test_stale_running_job_is_reported_as_interrupted(db, jobs):
    job = ps.start_draft_job(db, {"idea": "AI needs power"}, run=False)
    row = db.get(PlayDraftJob, job["job_id"])
    row.updated_at = datetime.utcnow() - timedelta(hours=1)
    db.commit()
    assert ps.get_draft_job(db, job["job_id"])["error_kind"] == "interrupted"


def test_draft_job_failures_are_friendly(db, jobs, monkeypatch):
    monkeypatch.setattr(ps, "book_context", lambda db: BOOK)

    async def unclear(*a, **k):
        return {"unclear": "That reads like a goal — what do you believe will happen?"}
    monkeypatch.setattr(ps, "_run_structured", unclear)
    monkeypatch.setattr(ps, "_mcp_servers", lambda: {})
    job = ps.start_draft_job(db, {"idea": "something about the future of things"}, run=False)
    ps.run_draft_job(ps.DraftJob(job["job_id"], job["request"]))
    db.expire_all()
    got = ps.get_draft_job(db, job["job_id"])
    assert got["status"] == "error" and got["error_kind"] == "unclear_idea" and not got["retryable"]
    assert "goal" in got["error"]

    class CLINotFoundError(Exception):
        pass

    async def no_cli(*a, **k):
        raise CLINotFoundError("Claude Code not found")
    monkeypatch.setattr(ps, "_run_structured", no_cli)
    job = ps.start_draft_job(db, {"idea": "Nuclear renaissance"}, run=False)
    ps.run_draft_job(ps.DraftJob(job["job_id"], job["request"]))
    db.expire_all()
    got = ps.get_draft_job(db, job["job_id"])
    assert got["error_kind"] == "model" and "claude login" in got["error"]
    assert "Traceback" not in got["error"]


def test_start_draft_rejects_garbage_before_spending_a_model_call(db, jobs):
    with pytest.raises(ps.PlayInputError):
        ps.start_draft_job(db, {"idea": "asdf"}, run=False)
    with pytest.raises(ps.PlayInputError, match="Conviction"):
        ps.start_draft_job(db, {"idea": "Nuclear renaissance", "conviction": "yolo"}, run=False)
    assert not db.execute(select(PlayDraftJob)).scalars().all()


# ── API: friendly messages end-to-end ───────────────────────────────────────

@pytest.fixture()
def client(engine, monkeypatch):
    from app.core.database import get_db
    from app.main import app

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(ps, "_session_factory", factory)

    def _db():
        with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_returns_one_friendly_sentence_for_bad_input(client):
    r = client.post("/api/plays/draft", json={"idea": "make me rich"})
    assert r.status_code == 400 and "goal" in r.json()["detail"]
    r = client.post("/api/plays/draft", json={"idea": "Nuclear", "conviction": "max"})
    assert r.status_code == 422 and r.json()["detail"] == "Conviction must be low, medium or high."
    r = client.post("/api/plays/draft", json={"idea": "Nuclear", "budget_gbp": -1})
    assert r.json()["detail"] == "Budget must be a positive amount in £."
    r = client.get("/api/plays/draft/nope")
    assert r.status_code == 404 and "start a new one" in r.json()["detail"]
    r = client.get("/api/plays/not-a-play")
    assert r.status_code == 404 and r.json()["detail"] == "This play doesn't exist any more."
    r = client.post("/api/plays", json={"title": "  "})
    assert r.status_code == 400 and "short name" in r.json()["detail"]


def test_api_book_and_owner_move(client, monkeypatch):
    monkeypatch.setattr(ps, "book_context", lambda db: BOOK)
    r = client.get("/api/plays/book")
    assert r.status_code == 200 and r.json()["unsorted_count"] == len(BOOK["positions"])
    r = client.post("/api/plays/owners", json={"ticker": "SPY", "owner": "core"})
    assert r.status_code == 200
    core = next(g for g in client.get("/api/plays/book").json()["groups"] if g["kind"] == "core")
    assert [h["ticker"] for h in core["owned"]] == ["SPY"]
    r = client.post("/api/plays/owners", json={"ticker": "NOPE", "owner": "core"})
    assert r.status_code == 400 and "don't hold" in r.json()["detail"]


def test_api_book_without_a_portfolio_is_a_clear_503(client, monkeypatch):
    def down(db):
        raise RuntimeError("broker offline")
    monkeypatch.setattr(ps, "book_context", down)
    r = client.get("/api/plays/book")
    assert r.status_code == 503 and "portfolio isn't available" in r.json()["detail"]


def test_draft_progress_events_carry_phase_source_and_tickers():
    ev = ps._tool_event("mcp__marketdata__get_price_snapshot", {"ticker": "rbot.l"})
    assert ev == {"text": "Checking price for rbot.l", "phase": "prices", "source": "market", "tickers": ["RBOT.L"]}
    ev = ps._tool_event("mcp__marketdata__get_correlation_matrix", {"tickers": ["NVDA", "AVGO"]})
    assert (ev["phase"], ev["source"], ev["tickers"]) == ("compare", "quant", ["NVDA", "AVGO"])
    ev = ps._tool_event("WebSearch", {"query": "uranium supply 2026"})
    assert (ev["phase"], ev["source"]) == ("research", "web") and "tickers" not in ev
    assert ps._phase_for("Book: £10,000 across 8 holdings")["phase"] == "portfolio"
    assert ps._phase_for("Sizing against your book and risk rails")["phase"] == "sizing"
    assert ps._phase_for("Draft ready")["phase"] == "done"
