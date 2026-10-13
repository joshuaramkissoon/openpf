"""Smart money: parsers, Congress providers, trend detector and alert dedupe.

All offline — fixtures only (tests/fixtures/smart_money). Real SEC samples:
form4_plan_sale.xml (an Apple 10b5-1 sale) and 13f_prev/13f_cur.xml (trimmed
Berkshire Hathaway information tables, Q1 → Q2 2026).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import entities  # noqa: F401 — registers tables
from app.models.entities import Alert
from app.models.smart_money import Disclosure
from app.services.smart_money import congress, edgar, net, service, trends

FIX = Path(__file__).parent / "fixtures" / "smart_money"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True, expire_on_commit=False)()
    yield session
    session.close()


# ── Form 4 ───────────────────────────────────────────────────────────────

def test_form4_open_market_purchase_by_cfo():
    rows = edgar.parse_form4((FIX / "form4_purchase.xml").read_text(), accession="0001-26-000001",
                             url="https://sec.example/f4", filed=date(2026, 10, 2))
    buy, gift = rows
    assert buy["ticker"] == "EXMP" and buy["side"] == "buy" and buy["kind"] == "open_market"
    assert buy["informative"] is True
    assert buy["actor"] == "Doe Jane" and buy["actor_role"] == "Chief Financial Officer"
    assert buy["meta"]["is_exec"] is True
    assert buy["shares"] == 5000 and buy["size_low"] == pytest.approx(206000)
    assert buy["delay_days"] == 2
    assert gift["kind"] == "gift" and gift["informative"] is False and gift["noise_reason"] == "gift"


def test_form4_real_10b5_1_plan_sale_is_noise():
    rows = edgar.parse_form4((FIX / "form4_plan_sale.xml").read_text(), accession="0001140361-26-039158",
                             filed=date(2026, 10, 8))
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "AAPL" and r["side"] == "sell"
    assert r["kind"] == "plan_sale" and r["informative"] is False
    assert "10b5-1" in r["noise_reason"]


def test_form4_exercise_and_sell_is_noise_but_later_sale_counts():
    rows = edgar.parse_form4((FIX / "form4_exercise_sale.xml").read_text(), accession="x", filed=date(2026, 9, 22))
    kinds = [(r["kind"], r["informative"]) for r in rows]
    assert kinds == [("option_exercise", False), ("exercise_sale", False), ("open_market", True)]
    assert rows[2]["actor_role"] == "Director"


# ── 13F ──────────────────────────────────────────────────────────────────

def test_13f_info_table_aggregates_by_cusip():
    cur = edgar.parse_info_table((FIX / "13f_cur.xml").read_text())
    apple = next(v for v in cur.values() if v["name"] == "APPLE INC")
    assert apple["shares"] > 0 and apple["value"] > 0
    # 12 sub-manager rows collapse into one holding.
    assert sum(1 for v in cur.values() if v["name"] == "APPLE INC") == 1


def test_13f_quarter_diff_finds_new_and_exit():
    prev = edgar.parse_info_table((FIX / "13f_prev.xml").read_text())
    cur = edgar.parse_info_table((FIX / "13f_cur.xml").read_text())
    changes = {c["name"]: c for c in edgar.diff_holdings(prev, cur)}
    assert changes["BANK AMERICA CORP"]["kind"] == "exit"
    assert changes["D R HORTON INC"]["kind"] == "new"
    assert changes["CONSTELLATION BRANDS INC"]["kind"] == "exit"
    # Unchanged share counts never show up as buying/selling.
    assert "COCA COLA CO" not in changes
    rows = edgar.changes_to_rows(list(changes.values()), manager="Berkshire Hathaway", cik=1067983,
                                 period=date(2026, 6, 30), filed=date(2026, 8, 14), url="u",
                                 tickers={"23331A109": "DHI"})
    dhi = next(r for r in rows if r["issuer"] == "D R HORTON INC")
    assert dhi["ticker"] == "DHI" and dhi["side"] == "buy" and dhi["delay_days"] == 45
    bac = next(r for r in rows if r["issuer"] == "BANK AMERICA CORP")
    assert bac["side"] == "sell" and bac["kind"] == "exit"


def test_13f_values_reported_in_thousands_are_normalised():
    xml = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
      <infoTable><nameOfIssuer>ALPHABET INC</nameOfIssuer><cusip>02079K305</cusip><value>120200</value>
        <shrsOrPrnAmt><sshPrnamt>500000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
      <infoTable><nameOfIssuer>ALPHABET INC</nameOfIssuer><cusip>02079K305</cusip><value>999</value><putCall>Call</putCall>
        <shrsOrPrnAmt><sshPrnamt>4000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
    </informationTable>"""
    h = edgar.parse_info_table(xml)
    assert h["02079K305"]["value"] == pytest.approx(120_200_000)  # thousands → dollars; options ignored
    assert h["02079K305"]["shares"] == 500000


def test_tiny_fund_positions_are_noise():
    changes = [{"cusip": "X", "name": "TINY CO", "kind": "new", "change_pct": None, "shares_prev": 0, "shares_cur": 10,
                "value_prev": 0, "value_cur": 1_000}]
    (row,) = edgar.changes_to_rows(changes, manager="F", cik=1, period=date(2026, 6, 30), filed=date(2026, 8, 14), url="u",
                                   tickers={}, totals=(1e9, 1e9))
    assert row["informative"] is False and "tiny" in row["noise_reason"]


def test_sic_sector_mapping():
    assert edgar.sic_to_sector(3674) == "Technology"  # semiconductors
    assert edgar.sic_to_sector(7372) == "Technology"
    assert edgar.sic_to_sector(2834) == "Health Care"
    assert edgar.sic_to_sector(4911) == "Utilities"
    assert edgar.sic_to_sector(6022) == "Financials"
    assert edgar.sic_to_sector(None) is None


# ── Congress providers ───────────────────────────────────────────────────

def test_bargo_rows_and_delay(db, monkeypatch):
    payload = json.loads((FIX / "bargo_trades.json").read_text())
    calls = []

    class Resp:
        headers = {"x-ratelimit-remaining": "29", "x-ratelimit-rows-remaining": "94"}

    def fake_get(url, params=None, headers=None):
        calls.append(url)
        return payload, Resp()

    monkeypatch.setattr(net, "http_get_json", fake_get)
    rows = congress.BargoProvider().fetch(db, tickers=["NVDA"])
    assert len(rows) == len(payload["trades"])
    msft = next(r for r in rows if r["ticker"] == "MSFT")
    assert msft["side"] == "sell" and msft["actor_type"] == "congress"
    assert msft["delay_days"] == (date(2026, 10, 2) - date(2026, 9, 28)).days
    assert msft["size_low"] == 1001 and msft["size_high"] == 15000
    buy = next(r for r in rows if r["ticker"] == "ISRG")
    assert buy["side"] == "buy" and buy["kind"] == "purchase" and buy["informative"]
    # Keyless: one feed request only (budget), and it's cached.
    congress.BargoProvider().fetch(db, tickers=["NVDA"])
    assert len(calls) == 1
    assert net.cache_get(db, "bargo:budget")["remaining"] == "29"


def test_bargo_unreachable_is_graceful(db, monkeypatch):
    def boom(*a, **k):
        raise net.SourceUnavailable("unreachable: timed out")

    monkeypatch.setattr(net, "http_get_json", boom)
    with pytest.raises(net.SourceUnavailable):
        congress.BargoProvider().fetch(db, tickers=[])


def test_capitol_api_rows():
    payload = json.loads((FIX / "capitol_api_trades.json").read_text())
    rows = congress.CapitolApiProvider("http://localhost:3000").rows_from(payload)
    nvda, bill = rows
    assert nvda["ticker"] == "NVDA" and nvda["side"] == "buy" and nvda["informative"]
    assert nvda["delay_days"] == (date(2026, 9, 28) - date(2026, 8, 20)).days
    assert nvda["actor_role"] == "House (D) CA12"
    assert bill["informative"] is False  # no ticker


def test_provider_from_config():
    assert congress.provider_from_config({"congress_provider": "none"}) is None
    assert isinstance(congress.provider_from_config({}), congress.BargoProvider)
    with pytest.raises(net.SourceUnavailable):
        congress.provider_from_config({"congress_provider": "capitol_api"})


# ── Trend detector ───────────────────────────────────────────────────────

TODAY = date(2026, 10, 13)


def _row(source, actor, ticker, side="buy", kind=None, ago=5, size=100_000, actor_type=None, sector="Technology",
         exec_=False, informative=True, delay=2):
    kind = kind or {"form4": "open_market", "congress": "purchase", "13f": "new"}[source]
    t = TODAY - timedelta(days=ago)
    return {
        "source": source, "actor": actor, "actor_type": actor_type or {"form4": "insider", "congress": "congress", "13f": "fund"}[source],
        "actor_role": "CFO" if exec_ else "Director", "ticker": ticker, "sector": sector, "side": side, "kind": kind,
        "size_low": size, "size_label": "", "trade_date": t, "disclosed_date": t + timedelta(days=delay), "delay_days": delay,
        "url": f"https://sec.example/{actor}", "informative": informative, "meta": {"is_exec": exec_},
    }


def test_insider_cluster_buy_scores_and_caveats():
    rows = [
        _row("form4", "A", "XYZ", exec_=True, size=600_000),
        _row("form4", "B", "XYZ", ago=12),
        _row("form4", "C", "XYZ", ago=25),
        _row("form4", "D", "XYZ", ago=45),  # outside 30 days
        _row("form4", "E", "XYZ", side="sell", kind="plan_sale", informative=False),
    ]
    dets = trends.detect(rows, trends.Context(today=TODAY, held={"XYZ"}))
    cl = next(d for d in dets if d["kind"] == "insider_cluster_buy")
    assert cl["actors"] == 3 and cl["ticker"] == "XYZ"
    assert cl["score"] == 40 + 15 + 15 + 5
    assert cl["strength"] == "strong"
    assert cl["touches"] == ["holding"]
    assert any("2 business days" in c for c in cl["caveats"])
    assert trends.should_alert(cl) and trends.alert_severity(cl) == "warning"


def test_single_insider_is_not_a_cluster_and_small_sample_caveat():
    dets = trends.detect([_row("form4", "A", "XYZ"), _row("form4", "B", "XYZ", size=10_000)], trends.Context(today=TODAY))
    cl = next(d for d in dets if d["kind"] == "insider_cluster_buy")
    assert any("small sample" in c for c in cl["caveats"])
    assert not trends.detect([_row("form4", "A", "XYZ")], trends.Context(today=TODAY))


def test_congress_cluster_and_delay_caveat():
    rows = [_row("congress", "Rep A", "OKLO", ago=50, delay=40), _row("congress", "Sen B", "OKLO", ago=30, delay=30)]
    dets = trends.detect(rows, trends.Context(today=TODAY, watchlist={"OKLO"}))
    cg = next(d for d in dets if d["kind"] == "congress_cluster")
    assert cg["actors"] == 2 and cg["median_delay_days"] == 35
    assert "45 days late" in cg["caveats"][0]
    assert cg["touches"] == ["watchlist"]


def test_fund_consensus_and_cross_group():
    rows = [
        _row("13f", "Fund A", "VST", ago=105, delay=44),
        _row("13f", "Fund B", "VST", kind="add", ago=105, delay=44),
        _row("form4", "Dir", "VST", ago=10),
    ]
    dets = {d["kind"]: d for d in trends.detect(rows, trends.Context(today=TODAY))}
    assert dets["fund_consensus_add"]["actors"] == 2
    assert "quarter-end snapshot" in dets["fund_consensus_add"]["caveats"][0]
    assert dets["cross_group_buying"]["actors"] == 3


def test_sector_inflow_and_congress_sector():
    rows = [_row("congress", f"Rep {i}", tk, sector="Utilities", ago=20 + i)
            for i, tk in enumerate(["CEG", "VST", "NEE", "TLN", "NRG", "OKLO"])]
    kinds = {d["kind"] for d in trends.detect(rows, trends.Context(today=TODAY))}
    assert {"sector_inflow", "congress_sector"} <= kinds


def test_sales_alone_never_alert_and_book_touch():
    rows = [_row("form4", n, "PLTR", side="sell") for n in "ABCD"]
    dets = trends.detect(rows, trends.Context(today=TODAY, held={"PLTR"}))
    sell = next(d for d in dets if d["kind"] == "insider_selling")
    assert "weak evidence" in sell["caveats"][0]
    assert not trends.should_alert(sell)
    # Not in the book → insider selling isn't even reported.
    assert not [d for d in trends.detect(rows, trends.Context(today=TODAY)) if d["kind"] == "insider_selling"]


def test_ticker_summary_line():
    rows = [
        _row("form4", "A", "NVDA"), _row("form4", "B", "NVDA", ago=9), _row("form4", "C", "NVDA", ago=20),
        _row("congress", "Rep A", "NVDA", ago=60, delay=38),
    ]
    snaps = [{"name": "Fund A", "holdings": {"NVDA": {"weight": 0.1}}}, {"name": "Fund B", "holdings": {"NVDA": {"weight": 0.2}}},
             {"name": "Fund C", "holdings": {"AAPL": {"weight": 0.2}}}]
    s = trends.ticker_summary("nvda", rows, snaps, TODAY)
    assert s["line"] == "3 insiders bought in 30 days · held by 2 funds you follow · Congress: 1 buy, disclosed 38 days late"


# ── Storage, dedupe and alerts ───────────────────────────────────────────

def test_upsert_dedupes_on_external_id(db):
    rows = edgar.parse_form4((FIX / "form4_purchase.xml").read_text(), accession="acc-1", filed=date(2026, 10, 2))
    assert service.upsert_rows(db, rows) == 2
    assert service.upsert_rows(db, rows) == 0
    assert db.query(Disclosure).count() == 2


def test_alerts_dedupe_and_cap(db, monkeypatch):
    rows = [
        _row("form4", "A", "CCJ", exec_=True, size=400_000), _row("form4", "B", "CCJ", ago=8), _row("form4", "C", "CCJ", ago=15),
        _row("form4", "A2", "ZZZ"), _row("form4", "B2", "ZZZ", ago=8),  # moderate, not in book → no alert
    ]
    for i, r in enumerate(rows):
        r["external_id"] = f"t:{i}"
    service.upsert_rows(db, rows)
    db.commit()
    monkeypatch.setattr(service, "user_context", lambda _db, today=None: trends.Context(today=TODAY, held={"CCJ"}))
    monkeypatch.setattr(service, "load_rows", lambda _db, since_days=200, ticker=None: [service.row_dict(d) for d in db.query(Disclosure)])

    first = service.raise_alerts(db)
    assert first["created"] == 1
    alert = db.query(Alert).one()
    assert alert.category == "smart_money" and alert.ticker == "CCJ" and alert.severity == "warning"
    assert "Caveat" in alert.detail and alert.meta["score"] >= 70

    # Same evidence → no re-fire, even after the user dismisses it.
    alert.status = "dismissed"
    db.commit()
    assert service.raise_alerts(db)["created"] == 0

    # New evidence (a 4th insider) → a fresh alert that supersedes nothing open.
    extra = _row("form4", "D", "CCJ", ago=3)
    extra["external_id"] = "t:new"
    service.upsert_rows(db, [extra])
    db.commit()
    assert service.raise_alerts(db)["created"] == 1


def test_sandbox_fixtures_load_offline(db, monkeypatch):
    from app.services.smart_money import fixtures

    def no_network(*a, **k):
        raise AssertionError("network used in sandbox")

    monkeypatch.setattr(net, "sec_get", no_network)
    monkeypatch.setattr(net, "http_get_json", no_network)
    out = fixtures.load(db)
    assert out["new"] == len(fixtures.rows())
    assert fixtures.load(db)["new"] == 0  # idempotent
    dets = trends.detect(service.load_rows(db), trends.Context(today=date.today(), held={"CCJ", "OKLO"}))
    assert dets[0]["kind"] == "insider_cluster_buy" and dets[0]["ticker"] == "CCJ"
    assert all("Demo" in r.actor for r in db.query(Disclosure))  # never real people
