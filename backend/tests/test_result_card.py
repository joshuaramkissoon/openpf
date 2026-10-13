"""Result cards: the richer summary block (stats / holdings / gaps / viz), exact
digest stats computed in code, machine JSON kept out of the rendered report,
run headlines, and the earnings-week gate agreeing with the report. No network."""

from pathlib import Path
from types import SimpleNamespace
import json
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import ScheduledTask
from app.services import artifact_service as arts
from app.services import fast_llm, model_routing, recipes_events, report_digest
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


# ── parsing ─────────────────────────────────────────────────────────────

def test_old_summaries_stay_valid_and_unchanged():
    old = {"headline": "Quiet day", "points": ["a"], "action": None, "signal": "low"}
    assert arts.normalise_summary(old) == old
    summary, body = arts.extract_summary("## Key numbers\n- A: 1\n\n```summary\n" + json.dumps(old) + "\n```\n")
    assert summary == old and "```summary" not in body


def test_stats_are_coerced_capped_and_bad_ones_dropped():
    s = arts.normalise_summary({
        "headline": "ASML and TSM report this week", "signal": "high",
        "stats": [
            {"label": "Exposure", "value": "£11,320", "kind": "money", "sub": "13.6% of book", "bar": {"value": 0.136, "max": 1}},
            {"label": "Week", "value": 0.024, "kind": "signedPct", "tone": "positive", "spark": [1, "2", None, 3]},
            {"label": "Next report", "value": "2026-10-14", "kind": "date", "tickers": ["asml"],
             "timeline": [{"date": "2026-10-14", "label": "ASML"}, {"date": "soon", "label": "x"}]},
            {"label": "Bad date", "value": "Wednesday", "kind": "date"},
            {"label": "", "value": 3, "kind": "count"},
            {"label": "Weird", "value": "lots", "kind": "money"},
            {"label": "Fifth", "value": 5, "kind": "count"},
        ],
    })
    stats = s["stats"]
    assert len(stats) == 4
    assert stats[0] == {"label": "Exposure", "value": 11320.0, "kind": "money", "sub": "13.6% of book", "bar": {"value": 0.136, "max": 1.0}}
    assert stats[1]["spark"] == [1.0, 2.0, 3.0] and stats[1]["tone"] == "positive"
    assert stats[2]["tickers"] == ["ASML"] and stats[2]["timeline"] == [{"date": "2026-10-14", "label": "ASML"}]
    assert stats[3] == {"label": "Weird", "value": "lots", "kind": "text"}  # a non-number money value degrades to text


def test_holdings_gaps_and_viz():
    s = arts.normalise_summary({
        "headline": "h", "signal": "low",
        "holdings": [{"ticker": "asml", "verdict": "Hold through", "value": "6200", "weight": 0.074, "watch": "orders", "date": "2026-10-14"},
                     {"verdict": "trim"}],
        "gaps": ["timing", "surprise history"],
        "viz": {"version": 1, "title": "Nope", "blocks": [{"type": "nonsense"}]},
    })
    assert s["holdings"] == [{"ticker": "ASML", "date": "2026-10-14", "verdict": "hold", "value": 6200.0, "weight": 0.074, "watch": ["orders"]}]
    assert s["gaps"] == ["timing", "surprise history"]
    assert "viz" not in s  # an invalid spec is dropped, never fatal


def test_machine_json_blocks_are_data_not_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "artifacts_dir", lambda: tmp_path)
    out = ("## Key numbers\n- A: 1\n\n## ASML\n- Verdict: Hold through\n\n"
           "```json\n{\"reporting\": [{\"ticker\": \"ASML\"}]}\n```\n\n"
           "```summary\n{\"headline\": \"h\", \"points\": [], \"action\": null, \"signal\": \"low\"}\n```\n")
    path = ts._cron_log_path("recipe_x", out, task_kind="recipe")
    text = Path(path).read_text()
    assert "```json" not in text and '"reporting"' in text.split("---")[1]  # stored in frontmatter
    monkeypatch.setattr(arts, "artifacts_dir", lambda: tmp_path)
    detail = arts.get_artifact(str(Path(path).relative_to(tmp_path)))
    assert detail["metadata"]["data"] == {"reporting": [{"ticker": "ASML"}]}
    assert "```json" not in detail["content"] and "## ASML" in detail["content"]


def test_old_artifact_with_json_in_body_is_cleaned_on_read(tmp_path, monkeypatch):
    monkeypatch.setattr(arts, "artifacts_dir", lambda: tmp_path)
    (tmp_path / "scheduled" / "t").mkdir(parents=True)
    (tmp_path / "scheduled" / "t" / "a.md").write_text(
        "---\ntype: scheduled\ntitle: T\n---\n\n## Key numbers\n- A: 1\n\n```json\n{\"policy_updates\": []}\n```\n")
    detail = arts.get_artifact("scheduled/t/a.md")
    assert "policy_updates" not in detail["content"] and detail["metadata"]["data"] == {"policy_updates": []}


def test_derived_summary_never_uses_a_section_label():
    assert arts.derive_summary("## Key numbers\n- A: 1\n\n## NVDA beat\n- x") ["headline"] == "NVDA beat"


# ── digest stats ────────────────────────────────────────────────────────

EARNINGS_DATA = {
    "reporting_within_7d": [
        {"ticker": "ASML", "name": "ASML Holding", "value": 6200.0, "weight": 0.0745, "ppl_pct": 0.21, "cost": 5124.0, "ppl": 1076.0,
         "earnings": {"date": "2026-10-14", "days_away": 1, "hour": "bmo"}, "valuation": {"forward_pe": 31.2},
         "surprise_history": {"unavailable": "surprise history ASML unavailable (HTTPError)"}},
        {"ticker": "TSM", "name": "Taiwan Semi", "value": 5120.0, "weight": 0.0615, "ppl_pct": -0.05, "cost": 5389.0, "ppl": -269.0,
         "earnings": {"date": "2026-10-15", "days_away": 2}, "valuation": {"unavailable": "valuation TSM unavailable (KeyError)"}},
    ],
    "checked": ["ASML", "TSM", "NVDA"], "book_total": 83261.0, "currency": "GBP",
}


def test_earnings_card_is_exact():
    card = report_digest.build_card("earnings-prep", EARNINGS_DATA)
    by = {s["label"]: s for s in card["stats"]}
    assert by["Reporting this week"] == {"label": "Reporting this week", "value": 2, "kind": "count", "tickers": ["ASML", "TSM"]}
    assert by["Exposure"]["value"] == 11320.0 and by["Exposure"]["sub"] == "13.6% of your book"
    assert by["Exposure"]["bar"] == {"value": 0.136, "max": 1}
    assert by["Next report"]["value"] == "2026-10-14" and by["Next report"]["sub"] == "before open"
    assert [m["label"] for m in by["Next report"]["timeline"]] == ["ASML", "TSM"]
    assert by["Gain on cost"]["value"] == round(807 / 10513, 4) and by["Gain on cost"]["tone"] == "positive"
    asml, tsm = card["holdings"]
    assert asml["forward_pe"] == 31.2 and asml["timing"] == "before open" and tsm["forward_pe"] is None
    assert card["gaps"] == ["surprise history", "valuation"]


def test_every_builtin_card_survives_missing_data():
    for slug in report_digest.CARDS:
        card = report_digest.build_card(slug, {})
        assert isinstance(card.get("stats", []), list)


def test_isa_and_weekly_cards():
    isa = report_digest.build_card("isa-allowance", {
        "allowance_gbp": 20000, "remaining_estimate_gbp": 7500.0,
        "tax_year": {"start": "2026-04-06", "deadline": "2027-04-05", "days_left": 174}})
    by = {s["label"]: s for s in isa["stats"]}
    assert by["Allowance used"]["value"] == 12500.0 and by["Allowance used"]["bar"] == {"value": 12500.0, "max": 20000}
    assert by["Deadline"]["value"] == "2027-04-05"
    wk = report_digest.build_card("weekly-review", {
        "week": {"return_pct": 0.024, "end_value": 83000.0, "daily_values": [81000, 81500, 83000]},
        "benchmarks_week_change_pct": {"SPY": 0.01}, "proposals_last_7d": []})
    by = {s["label"]: s for s in wk["stats"]}
    assert by["Week"]["spark"] == [81000, 81500, 83000] and by["vs S&P 500"]["value"] == 0.014
    assert by["Proposals"]["value"] == 0


def test_digest_numbers_come_from_code_not_the_model(db, monkeypatch):
    monkeypatch.setitem(report_digest.GATHERERS, "earnings-prep", lambda db, ctx: EARNINGS_DATA)
    seen = {}

    def fake(job, **kw):
        seen.update(kw)
        data = {
            "report": "## Key numbers\n- Reporting: 2\n\n## ASML\n- Timing: Wed, per the trigger context.\n\n"
                      "## Notes\n- Unavailable in this data: surprise history.",
            "summary": {"headline": "ASML and TSM report this week", "points": [], "action": None, "signal": "high",
                        "stats": [{"label": "Made up", "value": 1, "kind": "count"}],
                        "holdings": [{"ticker": "ASML", "verdict": "hold", "watch": ["2027 orders", "China mix"]},
                                     {"ticker": "TSM", "verdict": "trim", "watch": ["AI capex"]}]},
        }
        return fast_llm.LLMResult(data=data, provider="together", model="m", duration_ms=1, usage=fast_llm.Usage(1, 1), cost_usd=0.0)

    monkeypatch.setattr(fast_llm, "complete_json_sync", fake)
    recipe = SimpleNamespace(slug="earnings-prep", title="Earnings week prep", body="## Steps\n1. x", meta={})
    output, meta, _ = report_digest.run_digest(db, recipe, trigger_context="Holdings reporting…")
    payload = json.loads(seen["user"])
    assert payload["card_stats"][0]["label"] == "Reporting this week" and "trigger_context" not in payload
    s = meta["summary"]
    assert [x["label"] for x in s["stats"]] == ["Reporting this week", "Exposure", "Next report", "Gain on cost"]
    assert s["holdings"][0]["verdict"] == "hold" and s["holdings"][0]["value"] == 6200.0
    assert s["holdings"][1]["verdict"] == "trim" and s["gaps"] == ["surprise history", "valuation"]
    assert "trigger context" not in output and "## Notes" not in output and "Timing: Wed." in output


# ── run headlines & the gate ────────────────────────────────────────────

def test_run_headline_is_the_summary_not_a_section_label():
    out = "## Key numbers\n- A: 1\n\n```summary\n{\"headline\": \"ASML and TSM report this week\", \"signal\": \"high\"}\n```"
    assert svc._run_headline(out, None) == "ASML and TSM report this week"
    assert svc._run_headline("## Key numbers\n- Exposure: £11k", None) == "Exposure: £11k"


def test_earnings_gate_asks_with_the_market_symbol(db, monkeypatch):
    monkeypatch.setattr(recipes_events, "_holdings", lambda db: [
        {"ticker": "ASML", "yfinance_ticker": "ASML", "value": 6200.0},
        {"ticker": "TSM", "yfinance_ticker": "TSM", "value": 5120.0},
        {"ticker": "RR", "display_ticker": "RR.", "yfinance_ticker": "RR.L", "value": 900.0},
    ])
    asked = []

    def nxt(sym):
        asked.append(sym)
        return {"ASML": {"date": "2026-10-14", "days_away": 1, "hour": "bmo"}, "TSM": {"date": "2026-10-15", "days_away": 2}}.get(sym)

    monkeypatch.setattr(report_digest, "next_earnings", nxt)
    gate = recipes_events.check_event(db, {"kind": "event", "event": "earnings_week"}, {})
    assert asked == ["ASML", "TSM", "RR.L"]
    assert gate["fire"] and "ASML reports 2026-10-14 (before open)" in gate["context"] and "TSM" in gate["context"]


def test_forced_run_with_nothing_found_has_no_false_why(db, monkeypatch, tmp_path):
    monkeypatch.setattr(recipes_events, "_holdings", lambda db: [{"ticker": "NVDA", "value": 1.0}])
    monkeypatch.setattr(recipes_events, "_next_earnings", lambda sym: None)
    r = svc.create(db, {"title": "Prep", "summary": "s", "icon": "calendar", "body": "## Steps\n1. Do it.\n",
                        "trigger": {"kind": "event", "event": "earnings_week"}, "delivery": {"telegram": False}})
    task = db.get(ScheduledTask, db.get(entities.Recipe, r["slug"]).task_id)
    task.meta = {**task.meta, "force_next_run": True}
    db.commit()
    monkeypatch.setattr(ts, "_run_claude_prompt", lambda t, goal_context="": ("## Key numbers\n- A: 1", {}, {}))
    monkeypatch.setattr(ts, "_cron_log_path", lambda name, content, **kw: str(tmp_path / "o.md"))
    status, payload, _, _ = svc.run_recipe_task(db, task)
    assert status == "ok" and payload["manual"] is True and payload["trigger_context"] == ""
    assert payload["summary"] == "A: 1"
