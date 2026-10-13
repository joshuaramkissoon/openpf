"""The sandbox seed funds both accounts at the start of the book, so the warmed
equity curve starts at the funded value: no long flat stretch of idle cash and
no later jump when the second account is funded."""

from datetime import date, datetime
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from app.services import sandbox_broker as sb


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(sb, "_price_on", lambda code, on: 100.0)
    monkeypatch.setattr(sb, "gbpusd", lambda: 1.25)
    return sb._seed_state()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.rstrip("Z"))


def test_accounts_are_funded_together_and_invested_at_once(state):
    starts = []
    for name, acct in state["accounts"].items():
        deposits = sorted(acct["transactions"], key=lambda t: t["dateTime"])
        first_order = min(_dt(o["order"]["createdAt"]) for o in acct["orders"])
        first_dep = _dt(deposits[0]["dateTime"])
        assert first_dep < first_order and (first_order - first_dep).days <= 1, name
        starts.append(first_order)
        # Every first tranche lands within a week of the first buy (no long idle cash).
        firsts: dict[str, datetime] = {}
        for o in acct["orders"]:
            t = o["order"]["ticker"]
            firsts[t] = min(firsts.get(t, _dt(o["order"]["createdAt"])), _dt(o["order"]["createdAt"]))
        assert (max(firsts.values()) - first_order).days <= 7, name
        # Deposits pay for every buy and leave exactly the seed cash.
        spent = sum(float(o["fill"]["walletImpact"]["netValue"]) for o in acct["orders"])
        assert sum(t["amount"] for t in deposits) == pytest.approx(spent + acct["cash"], abs=0.05)
    assert abs((starts[0] - starts[1]).days) <= 7
    # The book starts more than a year back, so the 1Y window opens fully invested.
    assert (date.today() - min(starts).date()).days > 365
