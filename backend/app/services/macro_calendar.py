"""High-impact US macro-events calendar.

The leveraged engine and the alpha loop should know when a market-moving macro
print is imminent — a 3x ETP held into an FOMC decision or a CPI surprise can
gap hard. This module provides a deterministic, testable calendar of the events
that matter most for index/large-cap leveraged exposure:

  * FOMC rate decisions — scheduled dates (2026 Fed calendar).
  * Non-farm payrolls (NFP) — first Friday of each month (algorithmic).
  * CPI — mid-month BLS release (estimated; agent should confirm exact day).

It is NOT a live news feed — Archie has the SDK `WebSearch` tool for that. This
is the dependable "heads-up, a big number is near" layer. Events are tagged
`scheduled` vs `estimated` so nothing is presented as more precise than it is.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

# Official 2026 FOMC decision days (second day of each two-day meeting).
_FOMC_2026: list[date] = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
]


def _first_friday(year: int, month: int) -> date:
    """First Friday of a month — the standard NFP (jobs report) release day."""
    d = date(year, month, 1)
    # weekday(): Mon=0 … Fri=4
    offset = (4 - d.weekday()) % 7
    return d + timedelta(days=offset)


def _nfp_dates(year: int) -> list[date]:
    return [_first_friday(year, m) for m in range(1, 13)]


def _cpi_dates(year: int) -> list[date]:
    """Estimated CPI release days — BLS publishes CPI ~mid-month (often the
    10th–14th). We approximate to the 12th (or the prior business day if it
    falls on a weekend); callers should treat these as ``estimated``.
    """
    out: list[date] = []
    for m in range(1, 13):
        d = date(year, m, 12)
        # Nudge off weekends to the preceding Friday (rough business-day proxy).
        if d.weekday() == 5:  # Saturday
            d -= timedelta(days=1)
        elif d.weekday() == 6:  # Sunday
            d -= timedelta(days=2)
        out.append(d)
    return out


def _all_events(year: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for d in _FOMC_2026 if year == 2026 else []:
        events.append({"date": d, "type": "FOMC", "label": "FOMC rate decision",
                       "impact": "high", "precision": "scheduled"})
    for d in _nfp_dates(year):
        events.append({"date": d, "type": "NFP", "label": "Non-farm payrolls",
                       "impact": "high", "precision": "scheduled"})
    for d in _cpi_dates(year):
        events.append({"date": d, "type": "CPI", "label": "CPI inflation print",
                       "impact": "high", "precision": "estimated"})
    return events


def upcoming_events(as_of: date | None = None, within_days: int = 14) -> list[dict[str, Any]]:
    """Macro events in ``[as_of, as_of + within_days]``, soonest first."""
    today = as_of or date.today()
    horizon = today + timedelta(days=within_days)
    out: list[dict[str, Any]] = []
    for year in {today.year, horizon.year}:
        for ev in _all_events(year):
            if today <= ev["date"] <= horizon:
                out.append({
                    **ev,
                    "date": ev["date"].isoformat(),
                    "days_until": (ev["date"] - today).days,
                })
    out.sort(key=lambda e: e["days_until"])
    return out


def macro_context_line(as_of: date | None = None, within_days: int = 10) -> str | None:
    """One-line heads-up for the goal/regime context, or None if nothing's near."""
    evs = upcoming_events(as_of, within_days)
    if not evs:
        return None
    parts = []
    for e in evs[:3]:
        when = "today" if e["days_until"] == 0 else f"in {e['days_until']}d"
        est = " (est.)" if e["precision"] == "estimated" else ""
        parts.append(f"{e['type']} {when}{est}")
    return "Upcoming high-impact macro: " + ", ".join(parts) + ". Size cautiously into these."
