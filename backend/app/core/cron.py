"""Standard-crontab triggers.

APScheduler 3's ``CronTrigger.from_crontab`` passes the day-of-week field
straight through, where 0 means *Monday*. Standard crontab (and every cron in
this app, its labels and Archie's prompts) uses 0/7 = Sunday, so numeric
weekdays fired a day late. ``cron_trigger`` rewrites numeric weekdays to names
before building the trigger; named weekdays (``mon-fri``) pass through.
"""

from __future__ import annotations

import re
from datetime import tzinfo

from apscheduler.triggers.cron import CronTrigger

_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _day(token: str) -> str:
    return _NAMES[int(token)] if token.isdigit() and 0 <= int(token) <= 7 else token


def _dow_part(part: str) -> str:
    base, _, step = part.partition("/")
    if base in ("*", "?"):
        return part
    if "-" in base:
        lo, hi = base.split("-", 1)
        # "5-7"/"x-0": a range that wraps to Sunday is split so names stay ordered.
        if lo.isdigit() and hi.isdigit() and int(hi) in (0, 7) and int(lo) > 0:
            out = f"{_day(lo)}-sat,sun" if int(lo) <= 6 else "sun"
            return out if not step else part  # stepped wrap-around ranges are left as written
        base = f"{_day(lo)}-{_day(hi)}"
    else:
        base = _day(base)
    return f"{base}/{step}" if step else base


def standard_dow(field: str) -> str:
    """Translate a crontab day-of-week field to APScheduler's named form."""
    return ",".join(_dow_part(p) for p in field.split(","))


def cron_trigger(expr: str, timezone: tzinfo | str | None = None) -> CronTrigger:
    """``CronTrigger.from_crontab`` with standard (0 = Sunday) weekday numbers."""
    fields = re.split(r"\s+", (expr or "").strip())
    if len(fields) == 5:
        fields[4] = standard_dow(fields[4])
        expr = " ".join(fields)
    return CronTrigger.from_crontab(expr, timezone=timezone)
