"""Readable names for Yahoo-style symbols, and the Trading 212 listing check."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from app.services import instrument_names as names
from app.services import plays_service as ps
from app.services import portfolio_service

META = {
    "BAL_EQ": {"name": "BAE Systems plc", "short_name": "BA", "ticker": "BAl_EQ", "currency": "GBX", "type": "STOCK"},
    "RHMD_EQ": {"name": "Rheinmetall AG", "short_name": "RHM", "ticker": "RHMd_EQ", "currency": "EUR", "type": "STOCK"},
    "HOP_EQ": {"name": "Thales S.A.", "short_name": "HO", "ticker": "HOp_EQ", "currency": "EUR", "type": "STOCK"},
    "NVDA_US_EQ": {"name": "NVIDIA Corporation", "short_name": "NVDA", "ticker": "NVDA_US_EQ", "currency": "USD", "type": "STOCK"},
    "ITA_US_EQ": {"name": "iShares US Aerospace & Defense ETF", "short_name": "ITA", "ticker": "ITA_US_EQ", "currency": "USD", "type": "ETF"},
}


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


def test_listing_check_covers_continental_venues(meta):
    items = {i["symbol"]: i for i in names.describe(None, ["BA.L", "RHM.DE", "HO.PA", "SAAB-B.ST", "ITA"],
                                                    account_kind="stocks_isa", profile=False)}
    assert items["BA.L"]["name"] == "BAE Systems" and items["BA.L"]["tradable"] is True
    assert items["RHM.DE"]["instrument_code"] == "RHMD_EQ"
    assert items["HO.PA"]["tradable"] is True
    assert items["SAAB-B.ST"]["tradable"] is False  # list available, not on it
    assert items["ITA"]["accounts"] == ["invest"] and items["ITA"]["in_account"] is False  # US ETF: not in an ISA


def test_unknown_when_the_list_cannot_be_read(monkeypatch):
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: {})
    monkeypatch.setattr(names, "_index_cache", None)
    assert names.describe(None, ["RHM.DE"], profile=False)[0]["tradable"] is None


def test_draft_legs_resolve_to_the_right_listing(meta):
    assert ps.resolve_instrument(None, "RHM.DE") == ("RHMD_EQ", True)
    assert ps.resolve_instrument(None, "BA.L") == ("BAL_EQ", True)
    code, verified = ps.resolve_instrument(None, "SAAB-B.ST")
    assert verified is False
