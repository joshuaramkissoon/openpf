"""The quant MCP surface: registry wiring, tool schemas, and the service layer on a
fixed fake book with synthetic prices (no network, in-memory DB)."""

from datetime import date
from pathlib import Path
import asyncio
import sys

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401
from app.services import claude_sdk_config, mcp_registry
from app.services import plays_service as ps
from app.services import quant_service as qs

QUANT_TOOLS = {"size_position", "analyse_risk", "what_if", "rebalance_plan", "deploy_cash", "placement"}


@pytest.fixture()
def no_db_creds(monkeypatch):
    monkeypatch.setattr(claude_sdk_config, "resolve_t212_env", lambda: {"T212_BASE_ENV": "demo"})


# ── registry / tool navigation ──────────────────────────────────────────────

@pytest.mark.parametrize("context", sorted(mcp_registry.ALL_CONTEXTS))
def test_quant_registered_for_chat_research_scheduled_only(context, no_db_creds):
    servers, allowed = mcp_registry.build_mcp_servers(context)
    has = "quant" in servers
    assert has == (context in {mcp_registry.CHAT, mcp_registry.RESEARCH, mcp_registry.SCHEDULED})
    if has:
        assert servers["quant"]["alwaysLoad"] is True
        assert {t.removeprefix("mcp__quant__") for t in allowed if t.startswith("mcp__quant__")} == QUANT_TOOLS


def test_tool_search_env_and_always_load(monkeypatch, no_db_creds):
    monkeypatch.setattr(mcp_registry.get_settings(), "claude_tool_search", "true")
    assert mcp_registry.sdk_env()["ENABLE_TOOL_SEARCH"] == "true"
    servers, allowed = mcp_registry.build_mcp_servers(mcp_registry.CHAT)
    assert "ToolSearch" in allowed
    loaded = {n for n, cfg in servers.items() if cfg.get("alwaysLoad")}
    assert {"quant", "trading212", "marketdata", "proposals", "plays"} <= loaded
    assert not loaded & {"scheduler", "views", "recipes", "fundamentals", "intel", "forecast", "watchlist"}
    monkeypatch.setattr(mcp_registry.get_settings(), "claude_tool_search", "false")
    assert "ToolSearch" not in mcp_registry.build_mcp_servers(mcp_registry.CHAT)[1]


def test_scheduler_maintenance_tools_hidden_from_chat_and_scheduled(no_db_creds):
    for ctx in (mcp_registry.CHAT, mcp_registry.SCHEDULED):
        servers, allowed = mcp_registry.build_mcp_servers(ctx)
        assert "mcp__scheduler__run_due_scheduled_tasks" not in allowed
        assert "run_due_scheduled_tasks" in servers["scheduler"]["env"]["OPENPF_MCP_HIDE_TOOLS"]
    _, agent_allowed = mcp_registry.build_mcp_servers(mcp_registry.AGENT)
    assert "mcp__scheduler__run_due_scheduled_tasks" in agent_allowed


def test_quant_subagent_gets_quant_tools():
    agents = claude_sdk_config.build_subagents()
    assert "mcp__quant__size_position" in agents["quant"].tools


def test_quant_server_tools_match_registry_and_are_read_only_typed():
    sys.path.append(str(mcp_registry.MCP_SERVER_DIR))
    import quant as server  # mcp_servers/quant.py

    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == QUANT_TOOLS == set(mcp_registry.server_spec("quant").tools)
    by = {t.name: t for t in tools}
    for t in tools:
        assert t.annotations and t.annotations.readOnlyHint is True
        assert (t.meta or {}).get("anthropic/alwaysLoad") is True
        assert len(t.description or "") < 2048  # Claude Code truncates longer descriptions
    props = by["size_position"].inputSchema["properties"]
    assert props["method"]["enum"] == ["conviction", "vol_target", "kelly", "budget"]
    assert props["weighting"]["enum"] == ["inverse_vol", "risk_parity", "hrp", "equal"]
    assert "correlation_clusters" in by["analyse_risk"].inputSchema["properties"]["view"]["enum"]


# ── service layer on a fake book ────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.close()
    engine.dispose()


VOLS = {"AAA": 0.20, "BBB": 0.40, "CCC": 0.30, "DDD": 0.50, "SPY": 0.16, "QQQ": 0.20, "IWM": 0.22, "TLT": 0.12}


@pytest.fixture()
def fake_prices(monkeypatch):
    """Deterministic GBM closes in GBP (currency GBP → no FX leg), ~300 business days."""
    idx = pd.bdate_range(end=pd.Timestamp("2026-10-09"), periods=300)
    rng = np.random.default_rng(42)
    market = rng.normal(0, 0.01, len(idx))
    cache = {}
    for i, (t, v) in enumerate(VOLS.items()):
        daily = v / np.sqrt(252)
        beta = 0.6 + 0.2 * i
        idio = np.sqrt(max(daily**2 - (beta * 0.01) ** 2, 1e-8))
        r = beta * market + rng.normal(0, idio, len(idx))
        cache[t] = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)

    monkeypatch.setattr(qs, "_closes", lambda sym, days: cache[sym.upper()])
    monkeypatch.setattr(qs, "currency_for", lambda t, book=None: (t.upper(), "GBP"))
    return cache


def _book() -> qs.Book:
    def h(t, v, acct="invest", ppl=0.0):
        return qs.Holding(ticker=t, name=t, value_gbp=v, weight=v / 100_000, yf_ticker=t, currency="GBP",
                          by_account={acct: v}, ppl_by_account={acct: ppl})
    return qs.Book(total_gbp=100_000, cash_gbp={"invest": 10_000, "stocks_isa": 5_000},
                   holdings={"AAA": h("AAA", 40_000, "stocks_isa", 8_000), "BBB": h("BBB", 30_000, ppl=6_000),
                             "CCC": h("CCC", 15_000, ppl=-1_000)})


def test_size_position_conviction_matches_plays_rule(db, fake_prices):
    out = qs.size_position_report(db, ["DDD"], conviction="high", book=_book())
    vol = out["basket_vol"]
    expected = ps.CONVICTION_PCT["high"] * min(max(ps.REFERENCE_VOL / vol, 0.5), 1.25)
    assert out["pct_of_book"] == pytest.approx(expected, abs=1e-4)
    assert out["target_gbp"] == pytest.approx(expected * 100_000, abs=10)
    assert out["summary"][0].startswith("Size: £")
    assert any(c["rail"].startswith("single order") for c in out["limits"])


def test_size_position_counts_existing_holdings_and_caps(db, fake_prices):
    out = qs.size_position_report(db, ["AAA", "DDD"], method="kelly", conviction="high", book=_book())
    assert out["pct_of_book"] == pytest.approx(0.10)  # Kelly is always > the 10% play cap here
    aaa = next(l for l in out["legs"] if l["ticker"] == "AAA")
    assert aaa["held_gbp"] == 40_000 and aaa["new_gbp"] == 0.0
    assert out["new_money_gbp"] == pytest.approx(next(l for l in out["legs"] if l["ticker"] == "DDD")["new_gbp"])


def test_what_if_sell_half(db, fake_prices):
    out = qs.what_if_report(db, [{"ticker": "AAA", "side": "sell", "fraction_of_holding": 0.5}], book=_book())
    row = out["weights"][0]
    assert row["weight_before"] == pytest.approx(0.40) and row["weight_after"] == pytest.approx(0.20)
    assert out["cash_after_gbp"]["stocks_isa"] == pytest.approx(25_000)
    assert out["book_vol"]["after"] < out["book_vol"]["before"]
    assert out["realised"][0]["taxable"] is False  # sold from the ISA (where most is held)


def test_rebalance_caps_trims_the_breach(db, fake_prices, monkeypatch):
    monkeypatch.setattr(qs, "rails", lambda db: {"max_position_weight": 0.25, "max_single_order_notional": 500.0,
                                                 "max_daily_notional": 1500.0, "max_play_pct": 0.10, "theme_warn_pct": 0.25})
    out = qs.rebalance_report(db, target="caps", turnover_budget=0.5, book=_book())
    assert [(t["ticker"], t["side"], t["amount_gbp"]) for t in out["trades"]] == [("AAA", "sell", 15_000)]


def test_deploy_cash_respects_free_cash_and_recommends(db, fake_prices):
    out = qs.deploy_cash_report(db, 5_000, account="invest", tickers=["CCC", "DDD"], book=_book())
    assert sum(a["amount_gbp"] for a in out["allocation"]) == pytest.approx(5_000)
    assert out["recommendation"] in ("lump", "staged")
    assert 0 < out["lump_vs_staged"]["prob_lump_wins"] < 1


def test_placement_with_known_allowance(db, fake_prices, monkeypatch):
    import app.services.fundamentals_service as fs

    monkeypatch.setattr(fs, "get_fundamentals_snapshot", lambda t: {"dividend_yield": 0.0})
    out = qs.placement_report(db, [{"ticker": "DDD", "side": "buy", "amount_gbp": 4_000},
                                   {"ticker": "BBB", "side": "sell", "amount_gbp": 5_000},
                                   {"ticker": "CCC", "side": "sell", "amount_gbp": 3_000}],
                              isa_subscribed_gbp=18_000, book=_book(), today=date(2027, 3, 1))
    assert out["isa"]["allowance_left_gbp"] == 2_000 and out["isa"]["days_to_year_end"] == 35
    items = {i["ticker"]: i for i in out["items"]}
    assert items["DDD"]["account"] == "stocks_isa"  # £5k ISA cash covers it
    # BBB: £30k with £6k gain → £5k sale realises £1k, inside the £3k exemption
    assert items["BBB"]["account"] == "invest" and items["BBB"]["realised_gain_gbp"] == pytest.approx(1_000)
    assert items["BBB"]["cgt_gbp"] == 0
    assert items["CCC"]["account"] == "invest" and "loss" in items["CCC"]["reason"]


def test_analyse_risk_overview_on_holdings(db, fake_prices):
    out = qs.analyse_risk_report(db, scope="holdings", tickers=["AAA", "BBB", "CCC"], view="overview", book=_book())
    rc = out["contribution"]
    assert sum(r["risk_share"] for r in rc["rows"]) == pytest.approx(1.0, abs=1e-3)
    assert out["concentration"]["names"] == 3
    assert out["cvar"]["es_95_daily"] < out["cvar"]["var_95_daily"] <= 0
    assert out["drawdown"]["max_drawdown"] <= 0
    assert "beta_spy" in out["beta"]
    assert len(out["summary"]) >= 5
