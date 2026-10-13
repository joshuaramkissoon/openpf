"""Readable names for Yahoo-style symbols, the Trading 212 listing check across
venues (by symbol + quote currency, then company name), and names in an idea."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from app.services import instrument_names as names
from app.services import plays_service as ps
from app.services import portfolio_service


def _m(code, name, short, ccy, typ="STOCK", isin=""):
    return {"name": name, "short_name": short, "ticker": code, "currency": ccy, "type": typ, "isin": isin}


# Shaped like T212's /equity/metadata/instruments as _instrument_meta_map keeps it:
# keyed by the UPPER code, the original-case code (with its venue letter) kept in "ticker".
_ROWS = [
    _m("BAl_EQ", "BAE Systems plc", "BA", "GBX", isin="GB0002634946"),
    _m("RRl_EQ", "Rolls-Royce Holdings plc", "RR", "GBX"),
    _m("ARMRl_EQ", "Global X Defence Tech UCITS ETF", "ARMR", "USD", "ETF"),
    _m("RHMd_EQ", "Rheinmetall AG", "RHM", "EUR", isin="DE0007030033"),
    _m("HOp_EQ", "Thales S.A.", "HO", "EUR", isin="FR0000121329"),
    _m("LDOm_EQ", "Leonardo S.p.a.", "LDO", "EUR"),
    _m("SAABBs_EQ", "Saab AB (publ)", "SAAB B", "SEK"),
    _m("SMSNl_EQ", "Samsung Electronics Co., Ltd.", "SMSN", "USD"),
    _m("BESIa_EQ", "BE Semiconductor Industries N.V.", "BESI", "EUR"),
    _m("NVDA_US_EQ", "NVIDIA Corporation", "NVDA", "USD"),
    _m("SNDK1_US_EQ", "Sandisk Corporation", "SNDK", "USD"),
    _m("RHM_US_EQ", "Some US RHM Inc", "RHM", "USD"),
    _m("ITA_US_EQ", "iShares US Aerospace & Defense ETF", "ITA", "USD", "ETF"),
    _m("3NVDl_EQ", "Leverage Shares 3x NVIDIA ETP", "3NVD", "USD", "ETF"),
]
META = {r["ticker"].upper(): r for r in _ROWS}


@pytest.fixture()
def meta(monkeypatch):
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: META)
    monkeypatch.setattr(names, "_index_cache", None)


def test_split_and_clean():
    assert names.split_symbol("BA.L") == ("BA", ".L")
    assert names.split_symbol("BRK.B") == ("BRK.B", "")  # share class, not a venue
    assert names.clean_ticker("SAAB-B.ST") == "SAAB B"
    assert names.exchange_of("LDO.MI") == {"code": "IT", "venue": "Milan"}
    assert names.exchange_of("NVDA") == {"code": "US", "venue": "US"}
    assert names.tidy_name("BAE Systems plc") == "BAE Systems"
    assert names.tidy_name("Saab AB (publ)") == "Saab"


def test_t212_symbol_reads_the_original_case_code():
    t = portfolio_service.t212_symbol
    assert t("BAl_EQ") == "BA"  # upper-casing first would give "BAL"
    assert t("RHMd_EQ") == "RHM" and t("3ASMl1_EQ") == "3ASM" and t("AAPL_US_EQ") == "AAPL"


@pytest.mark.parametrize("symbol,code", [
    ("BA.L", "BAL_EQ"),          # London, pence
    ("RR.L", "RRL_EQ"),
    ("ARMR.L", "ARMRL_EQ"),      # London ETF quoted in USD
    ("RHM.DE", "RHMD_EQ"),       # Xetra, EUR — not the US line with the same symbol
    ("HO.PA", "HOP_EQ"),         # Paris
    ("LDO.MI", "LDOM_EQ"),       # Milan
    ("SAAB-B.ST", "SAABBS_EQ"),  # Stockholm, share class written with a dash
    ("BESI.AS", "BESIA_EQ"),     # Amsterdam
    ("SMSN.L", "SMSNL_EQ"),      # Samsung's London GDR
    ("NVDA", "NVDA_US_EQ"),
    ("SNDK", "SNDK1_US_EQ"),     # frozen code, real symbol in shortName
])
def test_resolves_listings_on_every_venue(meta, symbol, code):
    hit = names.find_listing(None, symbol)
    assert hit and hit["instrument_code"] == code
    assert ps.resolve_instrument(None, symbol) == (code, True)


def test_wrong_venue_or_unlisted_markets_do_not_match(meta):
    assert names.find_listing(None, "BA.PA") is None       # BAE isn't a EUR listing
    assert names.find_listing(None, "000660.KS") is None   # Korea: T212 has no such venue
    assert names.find_listing(None, "NVDA.L") is None


def test_falls_back_to_the_company_name(meta):
    # A symbol the model got wrong still resolves when the name matches on that venue.
    hit = names.find_listing(None, "RHMX.DE", "Rheinmetall")
    assert hit and hit["instrument_code"] == "RHMD_EQ" and hit["matched_by"] == "name"
    assert ps.resolve_instrument(None, "SAAB.ST", "Saab AB") == ("SAABBS_EQ", True)
    assert names.find_listing(None, "XXX.DE", "Nobody GmbH") is None


def test_search_rows_use_the_real_symbol(meta):
    rows = portfolio_service.search_instruments("BA", None, limit=5)
    assert any(r["ticker"] == "BA" and r["instrument_code"] == "BAL_EQ" for r in rows)
    typeahead = names.search(None, "Rheinmetall")
    assert typeahead[0]["symbol"] == "RHM.DE" and typeahead[0]["name"] == "Rheinmetall"


def test_listing_check_and_accounts(meta):
    items = {i["symbol"]: i for i in names.describe(None, ["BA.L", "RHM.DE", "SAAB-B.ST", "000660.KS", "ITA"],
                                                    account_kind="stocks_isa", profile=False)}
    assert items["BA.L"]["name"] == "BAE Systems" and items["BA.L"]["tradable"] is True
    assert items["SAAB-B.ST"]["tradable"] is True
    assert items["000660.KS"]["tradable"] is False
    assert items["ITA"]["accounts"] == ["invest"] and items["ITA"]["in_account"] is False  # US ETF: not in an ISA


def test_unknown_when_the_list_cannot_be_read(monkeypatch):
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: {})
    monkeypatch.setattr(names, "_index_cache", None)
    assert names.describe(None, ["RHM.DE"], profile=False)[0]["tradable"] is None


def test_detects_names_written_in_the_idea(meta):
    found = names.detect_in_text(None, "High bandwidth memory chips, no SNDK surprises me. Maybe Rheinmetall too? AI and HBM.")
    syms = {f["symbol"] for f in found}
    assert "SNDK" in syms and "RHM.DE" in syms
    assert not any(f["query"] in {"AI", "HBM"} for f in found)


def test_resolve_names_for_includes(meta):
    rows = names.resolve_names(None, ["SanDisk", "SMSN.L", "SK Hynix"])
    assert rows[0]["symbol"] == "SNDK" and rows[0]["tradable"]
    assert rows[1]["symbol"] == "SMSN.L"
    assert rows[2]["tradable"] is False
