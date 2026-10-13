"""Digest mode: built-in "gather then write" recipes gather data in code and make
ONE fast_llm call; the run's cost is recorded with the fast model. Agent-mode
scheduled runs are capped and leave the old Opus default. No network."""

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import Recipe, ScheduledTask
from app.services import fast_llm, model_routing, report_digest
from app.services import recipes_service as svc
from app.services import task_scheduler_service as ts


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "recipes_data_dir", str(tmp_path / "recipes"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_routing_db(monkeypatch):
    monkeypatch.setattr(model_routing, "_stored", lambda: {})


def _fake_llm(monkeypatch, data, seen):
    def fake(job, **kw):
        seen.update(job=job, **kw)
        out = kw["validate"](dict(data)) if kw.get("validate") else data
        return fast_llm.LLMResult(data=out, provider="together", model="m", duration_ms=1800,
                                  usage=fast_llm.Usage(6000, 900), cost_usd=0.0029)

    monkeypatch.setattr(fast_llm, "complete_json_sync", fake)


REPLY = {
    "report": "## Key numbers\n- Book: £82,705\n- Today: -£816 (-1.0%)\n\n## What stands out\n- NVDA fell 2%.",
    "summary": {"headline": "NVDA led a -1% day", "points": [], "action": None, "signal": "low"},
}


def test_builtins_default_to_digest_and_can_opt_out():
    assert report_digest.mode_for(SimpleNamespace(slug="morning-brief", meta={})) == "digest"
    assert report_digest.mode_for(SimpleNamespace(slug="morning-brief", meta={"execution_mode": "agent"})) == "agent"
    assert report_digest.mode_for(SimpleNamespace(slug="big-move-explainer", meta={})) == "agent"
    assert report_digest.mode_for(SimpleNamespace(slug="my-own-recipe", meta={})) == "agent"


def test_run_digest_makes_one_call_in_the_report_frame(db, monkeypatch):
    seen: dict = {}
    monkeypatch.setitem(report_digest.GATHERERS, "morning-brief", lambda db, ctx: {"book": {"total": 82705.1}})
    _fake_llm(monkeypatch, REPLY, seen)
    recipe = SimpleNamespace(slug="morning-brief", title="Morning brief", body="## Steps\n1. `get_positions`", meta={})
    output, meta, cost = report_digest.run_digest(db, recipe)
    assert seen["job"] == "report_digest" and seen["record_usage"] is False
    assert "## Key numbers" in seen["system"] and "cannot call tools" in seen["system"]
    assert '"total":82705.1' in seen["user"]
    assert output.startswith("## Key numbers") and "```summary" in output
    assert meta["summary"]["headline"] == "NVDA led a -1% day" and meta["mode"] == "digest"
    assert cost == {"total_cost_usd": 0.0029, "duration_ms": cost["duration_ms"], "num_turns": 1, "model": "together:m"}


def test_builtin_recipe_runs_as_digest_through_the_scheduler(db, monkeypatch, tmp_path):
    svc.ensure_builtins(db)
    recipe = db.get(Recipe, "weekly-review")
    task = db.get(ScheduledTask, recipe.task_id)
    seen: dict = {}
    monkeypatch.setitem(report_digest.GATHERERS, "weekly-review", lambda db, ctx: {"week": {"return_pct": 0.012}})
    _fake_llm(monkeypatch, REPLY, seen)
    monkeypatch.setattr(ts, "_run_claude_prompt", lambda *a, **k: pytest.fail("digest recipes must not start an agent"))
    monkeypatch.setattr(ts, "_cron_log_path", lambda name, content, **kw: str(tmp_path / "out.md"))
    recorded = []
    monkeypatch.setattr(ts.costs_service, "record", lambda _db, **kw: recorded.append(kw))
    out = ts.run_task_now(db, task.id)
    assert out["status"] == "ok" and out["payload"]["mode"] == "digest"
    assert out["payload"]["digest"]["tokens"] == {"in": 6000, "out": 900}
    assert recorded[0]["model"] == "together:m" and recorded[0]["total_cost_usd"] == 0.0029
    assert recorded[0]["source"] == "scheduled"


def test_digest_without_any_fast_model_falls_back_to_agent(db, monkeypatch, tmp_path):
    svc.ensure_builtins(db)
    recipe = db.get(Recipe, "morning-brief")
    task = db.get(ScheduledTask, recipe.task_id)
    monkeypatch.setitem(report_digest.GATHERERS, "morning-brief", lambda db, ctx: {})

    def none(job, **kw):
        raise fast_llm.NoProvider("none")

    monkeypatch.setattr(fast_llm, "complete_json_sync", none)
    monkeypatch.setattr(ts, "_run_claude_prompt", lambda t, goal_context="": ("Quiet morning.", {}, {}))
    monkeypatch.setattr(ts, "_cron_log_path", lambda name, content, **kw: str(tmp_path / "o.md"))
    status, payload, _, _ = svc.run_recipe_task(db, task)
    assert status == "ok" and payload["mode"] == "agent"


def test_watchlist_digest_raises_flags_only_for_watched_symbols(db, monkeypatch):
    from app.models.entities import Alert
    from app.services import watchlist_service as wl

    wl.add_item(db, "NVDA", note="Buy below 150")
    monkeypatch.setitem(report_digest.GATHERERS, "watchlist-review", lambda db, ctx: {"watchlist": []})
    reply = {**REPLY, "flags": [
        {"symbol": "NVDA", "title": "Setup triggered", "detail": "Closed at 148", "severity": "warning"},
        {"symbol": "FAKE", "title": "Not watched", "detail": "x", "severity": "info"},
    ]}
    _fake_llm(monkeypatch, reply, {})
    recipe = SimpleNamespace(slug="watchlist-review", title="Watchlist review", body="", meta={})
    _, meta, _ = report_digest.run_digest(db, recipe)
    assert meta["flags_raised"] == 1
    assert [a.ticker for a in db.query(Alert).all()] == ["NVDA"]


def test_scheduled_agent_runs_leave_the_old_opus_default():
    assert ts.scheduled_model("claude-opus-4-8") == "claude-sonnet-5-5"
    assert ts.scheduled_model(None) == "claude-sonnet-5-5"
    assert ts.scheduled_model("claude-opus-5-5") == "claude-opus-5-5"  # a deliberate choice is kept


def test_book_section_uses_the_shared_day_move(db, monkeypatch):
    from app.services import day_move, portfolio_service

    snap = {"account": {"currency": "GBP", "total": 2000.0, "free_cash": 0.0},
            "accounts": [], "positions": [
                {"ticker": "NVDA", "display_ticker": "NVDA", "yfinance_ticker": "NVDA", "value": 1000.0, "total_cost": 800.0, "ppl": 200.0, "account_kind": "invest"},
                {"ticker": "NVDA", "display_ticker": "NVDA", "yfinance_ticker": "NVDA", "value": 1000.0, "total_cost": 900.0, "ppl": 100.0, "account_kind": "stocks_isa"},
            ]}
    monkeypatch.setattr(portfolio_service, "get_portfolio_snapshot", lambda db, **kw: snap)
    monkeypatch.setattr(day_move, "session_move", lambda sym: {"pct": 0.01, "session": "2026-10-12"})
    book = report_digest._book(db)
    assert book["holdings"][0]["value"] == 2000.0 and book["holdings"][0]["accounts"] == ["invest", "stocks_isa"]
    assert book["today"]["day_change"] == pytest.approx(2000 - 2000 / 1.01, abs=0.01)
