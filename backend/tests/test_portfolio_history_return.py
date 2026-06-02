"""Accuracy tests for the return curve in ``portfolio_history``.

The 'Return' curve must report *true* gain net of contributions:

  * the "All" / full-history endpoint = current equity − net lifetime
    contributions (exact; independent of the reconstruction's level estimate);
  * internal Invest↔ISA transfers are NOT external capital for the combined
    view and must not move the combined gain (their cross-currency legs don't
    cancel at market FX, so they can't simply be summed);
  * a single-account view DOES treat a transfer as an external in/out;
  * a sub-window reports the return earned *over the window* (starts at 0).

All fixtures use GBP so FX conversion is the identity (rate(GBP,GBP)=1), making
the arithmetic exact and the test hermetic.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models.entities import (
    AccountSnapshot,
    CashflowEvent,
    FxRateDaily,
    ReconstructedEquityDaily,
)
from app.services.portfolio_service import portfolio_history


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        # One recent FX anchor short-circuits ensure_history() so the test never
        # touches the network; rate(GBP,GBP) is 1.0 regardless of its value.
        s.add(FxRateDaily(date=date(2020, 1, 1), usd_per_gbp=1.25, fetched_at=datetime.utcnow()))
        s.commit()
        yield s


def _recon(db, kind, day, total):
    db.add(ReconstructedEquityDaily(
        account_kind=kind, date=day, total=total, invested=total, cash=0.0, currency="GBP"))


def _recorded(db, kind, day, total):
    db.add(AccountSnapshot(
        fetched_at=datetime.combine(day, datetime.min.time()),
        account_kind=kind, currency="GBP", total=total, invested=total, free_cash=0.0))


def _flow(db, kind, day, type_, amount, ref):
    db.add(CashflowEvent(
        account_kind=kind, reference=ref, type=type_, amount=amount, currency="GBP",
        occurred_at=datetime.combine(day, datetime.min.time())))


ALL = 10000  # days → full history


def test_full_history_gain_is_reconstruction_level_independent(db):
    # Single account, no transfers. The reconstructed START level is deliberately
    # WRONG (1500 vs 1000 actually deposited by then) — the deep-past level is an
    # estimate. The lifetime gain must still equal RECORDED equity − KNOWN
    # contributions (8000 − 3000 = 5000), i.e. independent of that estimate.
    _recon(db, "invest", date(2021, 1, 1), 1500.0)
    _recorded(db, "invest", date(2026, 2, 16), 5000.0)
    _recorded(db, "invest", date(2026, 6, 2), 8000.0)
    _flow(db, "invest", date(2021, 1, 1), "DEPOSIT", 1000.0, "d1")
    _flow(db, "invest", date(2023, 1, 1), "DEPOSIT", 2000.0, "d2")
    db.commit()

    h = portfolio_history(db, account_kind="invest", display_currency="GBP", days=ALL)
    pts = h["points"]
    assert pts[-1]["gain"] == pytest.approx(5000.0)  # 8000 − 3000, exact
    assert h["end_value"] == pytest.approx(8000.0)
    assert h["net_contributed"] == pytest.approx(3000.0)


def test_internal_transfer_does_not_change_combined_gain(db):
    # Two accounts; an internal transfer whose legs DON'T net to zero (simulating
    # the cross-currency FX-spread residual: -1000 out, +950 in).
    _recon(db, "invest", date(2021, 1, 1), 1000.0)
    _recon(db, "stocks_isa", date(2021, 1, 1), 500.0)
    _recorded(db, "invest", date(2026, 2, 16), 5000.0)
    _recorded(db, "invest", date(2026, 6, 2), 8000.0)
    _recorded(db, "stocks_isa", date(2026, 2, 16), 3000.0)
    _recorded(db, "stocks_isa", date(2026, 6, 2), 4000.0)
    _flow(db, "invest", date(2021, 1, 1), "DEPOSIT", 1000.0, "i1")
    _flow(db, "stocks_isa", date(2021, 1, 1), "DEPOSIT", 500.0, "s1")
    _flow(db, "invest", date(2023, 1, 1), "DEPOSIT", 2000.0, "i2")
    _flow(db, "invest", date(2024, 1, 1), "TRANSFER", -1000.0, "t-out")
    _flow(db, "stocks_isa", date(2024, 1, 1), "TRANSFER", 950.0, "t-in")
    db.commit()

    h = portfolio_history(db, account_kind="all", display_currency="GBP", days=ALL)
    # External deposits only = 3500; combined equity = 12000 → gain 8500.
    # If transfers were (mis)counted, the −50 residual would inflate gain to 8550.
    assert h["points"][-1]["gain"] == pytest.approx(8500.0)
    assert h["net_contributed"] == pytest.approx(3500.0)


def test_single_account_counts_transfer_as_contribution(db):
    # For the Invest account alone, money transferred out IS an external outflow.
    _recon(db, "invest", date(2021, 1, 1), 1000.0)
    _recorded(db, "invest", date(2026, 2, 16), 5000.0)
    _recorded(db, "invest", date(2026, 6, 2), 8000.0)
    _flow(db, "invest", date(2021, 1, 1), "DEPOSIT", 1000.0, "i1")
    _flow(db, "invest", date(2023, 1, 1), "DEPOSIT", 2000.0, "i2")
    _flow(db, "invest", date(2024, 1, 1), "TRANSFER", -1000.0, "t-out")
    db.commit()

    h = portfolio_history(db, account_kind="invest", display_currency="GBP", days=ALL)
    # net contributed = 1000 + 2000 − 1000 = 2000; equity 8000 → gain 6000
    assert h["points"][-1]["gain"] == pytest.approx(6000.0)


def test_sub_window_reports_windowed_return(db):
    # A short window starts at the first recorded point, not inception, and the
    # gain is the change earned *over the window* (starts at 0).
    _recon(db, "invest", date(2021, 1, 1), 1000.0)
    _recorded(db, "invest", date(2026, 2, 16), 5000.0)
    _recorded(db, "invest", date(2026, 6, 2), 8000.0)
    _flow(db, "invest", date(2021, 1, 1), "DEPOSIT", 1000.0, "d1")
    _flow(db, "invest", date(2023, 1, 1), "DEPOSIT", 2000.0, "d2")
    db.commit()

    # Window covering only the recorded span (no deposits inside it).
    h = portfolio_history(db, account_kind="invest", display_currency="GBP", days=200)
    pts = h["points"]
    assert pts[0]["gain"] == pytest.approx(0.0)
    assert pts[-1]["gain"] == pytest.approx(3000.0)  # 5000 → 8000, no in-window flows
