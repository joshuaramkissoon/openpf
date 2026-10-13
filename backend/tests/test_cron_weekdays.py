from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.cron import cron_trigger, standard_dow
from app.services.task_scheduler_service import fires_in_window

TZ = ZoneInfo("Europe/London")
TUE = datetime(2026, 10, 13, 12, 0, tzinfo=TZ)  # a Tuesday


def _next(expr: str) -> str:
    return cron_trigger(expr, timezone=TZ).get_next_fire_time(None, TUE).strftime("%a")


@pytest.mark.parametrize(
    "expr,day",
    [
        ("0 10 * * 0", "Sun"),
        ("0 10 * * 7", "Sun"),
        ("0 8 * * 1", "Mon"),
        ("30 8 * * 3", "Wed"),
        ("0 9 * * 6", "Sat"),
        ("0 17 * * 5", "Fri"),
        ("0 7 * * mon", "Mon"),
    ],
)
def test_numeric_weekdays_follow_standard_crontab(expr, day):
    assert _next(expr) == day


def test_weekday_range_is_monday_to_friday():
    start = datetime(2026, 10, 11, 0, 0, tzinfo=TZ)  # Sunday
    end = datetime(2026, 10, 18, 0, 0, tzinfo=TZ)
    days = [d.strftime("%a") for d in fires_in_window("0 7 * * 1-5", "Europe/London", start, end)]
    assert days == ["Mon", "Tue", "Wed", "Thu", "Fri"]


def test_dow_field_translation():
    assert standard_dow("1-5") == "mon-fri"
    assert standard_dow("0,6") == "sun,sat"
    assert standard_dow("*") == "*"
    assert standard_dow("*/2") == "*/2"
    assert standard_dow("mon-fri") == "mon-fri"
    assert standard_dow("5-7") == "fri-sat,sun"
