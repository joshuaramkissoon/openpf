"""Built-in recipes that ship with OpenPF.

Each lives in ``<slug>/SKILL.md`` (a real Claude Agent SDK skill, validated by
the same rules as user recipes). This file adds what a SKILL.md doesn't carry:
the display title/icon, default trigger, delivery and whether it starts on.

``legacy_task`` names the pre-recipes scheduled task this built-in replaces;
on first sync the existing row (with its run history and the user's
schedule/enabled choice) is adopted instead of creating a duplicate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BUILTIN_DIR = Path(__file__).resolve().parent

BUILTINS: list[dict[str, Any]] = [
    {
        "slug": "morning-brief",
        "title": "Morning brief",
        "icon": "sunrise",
        "trigger": {"kind": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London"},
        "enabled": True,
        "legacy_task": "morning_brief",
        "category": "Daily",
    },
    {
        "slug": "watchlist-review",
        "title": "Watchlist review",
        "icon": "binoculars",
        "trigger": {"kind": "schedule", "cron": "15 7 * * 1-5", "timezone": "Europe/London"},
        "enabled": True,
        "legacy_task": "watchlist_review",
        "category": "Daily",
    },
    {
        "slug": "weekly-review",
        "title": "Weekly review",
        "icon": "calendar-check",
        "trigger": {"kind": "schedule", "cron": "0 10 * * 0", "timezone": "Europe/London"},
        "enabled": True,
        "legacy_task": "weekly_review",
        "category": "Weekly",
    },
    {
        "slug": "play-check-in",
        "title": "Play check-in",
        "icon": "target",
        "trigger": {"kind": "schedule", "cron": "0 17 * * 5", "timezone": "Europe/London"},
        "enabled": False,
        "category": "Weekly",
    },
    {
        "slug": "deploy-idle-cash",
        "title": "Deploy idle cash",
        "icon": "coins",
        "trigger": {"kind": "schedule", "cron": "0 8 * * 1", "timezone": "Europe/London"},
        "enabled": False,
        "category": "Weekly",
    },
    {
        "slug": "earnings-prep",
        "title": "Earnings week prep",
        "icon": "calendar-clock",
        "trigger": {"kind": "event", "event": "earnings_week", "timezone": "Europe/London"},
        "enabled": False,
        "category": "When something happens",
    },
    {
        "slug": "big-move-explainer",
        "title": "Big move explainer",
        "icon": "activity",
        "trigger": {"kind": "event", "event": "big_move", "threshold_pct": 6, "timezone": "Europe/London"},
        "enabled": False,
        "category": "When something happens",
    },
    {
        "slug": "macro-on-my-book",
        "title": "Macro on my book",
        "icon": "globe",
        "trigger": {"kind": "schedule", "cron": "30 12 * * 1-5", "timezone": "Europe/London"},
        "enabled": False,
        "category": "Daily",
    },
    {
        "slug": "isa-allowance",
        "title": "ISA allowance top-up",
        "icon": "piggy-bank",
        "trigger": {"kind": "schedule", "cron": "0 9 * * 1", "timezone": "Europe/London"},
        "enabled": False,
        "category": "Weekly",
    },
]


def builtin_skill_md(slug: str) -> str:
    return (BUILTIN_DIR / slug / "SKILL.md").read_text(encoding="utf-8")


def builtin(slug: str) -> dict[str, Any] | None:
    return next((b for b in BUILTINS if b["slug"] == slug), None)
