"""Tests for the macro-events calendar (deterministic with a fixed as_of)."""

from datetime import date
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import macro_calendar as mc


def test_first_friday_is_a_friday():
    for month in range(1, 13):
        d = mc._first_friday(2026, month)
        assert d.weekday() == 4  # Friday
        assert d.day <= 7


def test_upcoming_includes_known_fomc():
    # From 2026-06-10, the 2026-06-17 FOMC must appear within 14 days.
    evs = mc.upcoming_events(as_of=date(2026, 6, 10), within_days=14)
    fomc = [e for e in evs if e["type"] == "FOMC"]
    assert fomc, "expected a FOMC event in window"
    assert fomc[0]["date"] == "2026-06-17"
    assert fomc[0]["days_until"] == 7
    assert fomc[0]["impact"] == "high"
    assert fomc[0]["precision"] == "scheduled"


def test_events_sorted_soonest_first():
    evs = mc.upcoming_events(as_of=date(2026, 6, 1), within_days=30)
    days = [e["days_until"] for e in evs]
    assert days == sorted(days)
    assert all(d >= 0 for d in days)


def test_cpi_is_estimated():
    evs = mc.upcoming_events(as_of=date(2026, 6, 1), within_days=20)
    cpi = [e for e in evs if e["type"] == "CPI"]
    assert cpi
    assert cpi[0]["precision"] == "estimated"


def test_context_line_none_when_quiet():
    # A window with no events (far future beyond 2026 FOMC list, tight window).
    line = mc.macro_context_line(as_of=date(2026, 6, 25), within_days=2)
    # 2026-06-25..27 has no FOMC/NFP/CPI → None.
    assert line is None


def test_context_line_present_near_event():
    line = mc.macro_context_line(as_of=date(2026, 6, 15), within_days=5)
    assert line and "FOMC" in line and "macro" in line.lower()
