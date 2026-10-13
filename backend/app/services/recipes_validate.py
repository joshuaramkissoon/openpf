"""Validation for recipes — the guard rails around Archie's own harness.

A recipe is *instructions only*. Everything that reaches a SKILL.md, a
schedule or a run passes through here, whoever wrote it (built-in, the user's
manual edit, Archie in chat, or Archie in an unattended run):

* names are sanitised to a strict slug (no path traversal, no reserved names);
* frontmatter is whitelisted to ``name`` + ``description`` — ``allowed-tools``,
  ``model``, ``hooks`` etc. are rejected, so a recipe can never grant tools;
* bodies are size-capped and may not carry shell-injection (``!`cmd```),
  frontmatter, or instructions to execute/approve trades;
* triggers must be a real, sensible schedule (at most hourly, must fire);
* free-text requests are screened for things a recipe can't do ("auto-buy the
  dip", "make me rich", "every minute") so the user gets a straight answer and
  a sensible alternative instead of a broken automation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.core.cron import cron_trigger

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,40}$")

MAX_TITLE = 80
MAX_SUMMARY = 300
MAX_BODY = 8000
MAX_REQUEST = 2000
MAX_USER_RECIPES = 60

# Slugs that would shadow real skills, system jobs or app routes.
RESERVED_SLUGS = {
    "new", "builtin", "builtins", "suggestions", "draft", "jobs", "system", "recipes", "skills",
    "trading212-api", "watch-cycle", "alpha-loop-open", "alpha-loop-midday", "alpha-loop-eod",
    "lev-morning-scan", "lev-midday-check", "lev-eod-close", "portfolio-rebalance-check",
    "smart-money-refresh",
}

ALLOWED_FRONTMATTER = {"name", "description"}

EVENTS = {
    "earnings_week": "a holding reports earnings in the next 7 days",
    "big_move": "a holding moves sharply in a day",
}
# Fixed check cadences for event triggers: the gate is deterministic and cheap;
# Archie only runs when it passes.
EVENT_CHECK_CRON = {
    "earnings_week": "0 7 * * 1",        # Monday 07:00
    "big_move": "30 9-16 * * 1-5",       # hourly through the trading day
}
DEFAULT_TZ = "Europe/London"


class RecipeValidationError(ValueError):
    """A recipe (or a change to one) that we refuse to save."""


# ── names ───────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    return s


def check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug) or "--" in slug:
        raise RecipeValidationError("recipe names are 3-48 lowercase letters, digits and dashes")
    if slug in RESERVED_SLUGS:
        raise RecipeValidationError(f"'{slug}' is reserved — pick another name")
    return slug


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if not t:
        raise RecipeValidationError("a recipe needs a title")
    if len(t) > MAX_TITLE:
        raise RecipeValidationError(f"title is too long ({MAX_TITLE} characters max)")
    return t


def clean_summary(summary: str) -> str:
    s = re.sub(r"\s+", " ", str(summary or "")).strip()
    if not s:
        raise RecipeValidationError("a recipe needs a one-line summary of what it does")
    if len(s) > MAX_SUMMARY:
        raise RecipeValidationError(f"summary is too long ({MAX_SUMMARY} characters max)")
    return s


def clean_icon(icon: str | None) -> str:
    return icon if isinstance(icon, str) and ICON_RE.match(icon) else "sparkles"


# ── body ────────────────────────────────────────────────────────────────

_BODY_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"!\s*`"), "shell commands in recipes (!`…`) are not allowed"),
    (re.compile(r"allowed[-_ ]tools", re.I), "recipes can't change which tools Archie may use"),
    (re.compile(r"\b(?:execute_intent|approve_intent|place_\w*order|cancel_order|modify_order)\b", re.I),
     "recipes can propose trades, never place, approve or cancel them"),
    (re.compile(r"\bauto[- ]?execut", re.I), "recipes can't turn on auto-execution"),
    (re.compile(r"\b(?:update_policy|risk[_ ]rails?\s*(?:=|:)|max_single_order_notional|auto_execute_enabled)\b", re.I),
     "recipes can't change risk rails"),
    (re.compile(r"<\s*/?\s*(?:script|iframe)\b", re.I), "HTML scripts aren't allowed in recipes"),
]


def clean_body(body: str) -> str:
    text = str(body or "").replace("\r\n", "\n").strip()
    if not text:
        raise RecipeValidationError("a recipe needs instructions (what Archie should do)")
    if len(text) > MAX_BODY:
        raise RecipeValidationError(f"instructions are too long ({MAX_BODY} characters max)")
    if text.startswith("---"):
        raise RecipeValidationError("instructions can't start with a frontmatter block")
    for pattern, reason in _BODY_DENY:
        if pattern.search(text):
            raise RecipeValidationError(reason)
    return text


# ── SKILL.md parsing (for raw input from Archie) ─────────────────────────

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


def parse_skill_md(raw: str) -> dict[str, str]:
    """Parse a SKILL.md into {name, description, body}, whitelisting frontmatter.

    Only ``name`` and ``description`` are accepted; any other key (notably
    ``allowed-tools``, ``model``, ``hooks``) is rejected outright rather than
    silently dropped, so an escalation attempt is visible.
    """
    text = str(raw or "").replace("\r\n", "\n").strip()
    m = _FM_RE.match(text)
    if not m:
        raise RecipeValidationError("SKILL.md must start with a --- frontmatter block (name, description)")
    fm_text, body = m.group(1), m.group(2)
    fields: dict[str, str] = {}
    for line in fm_text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() or ":" not in line:
            raise RecipeValidationError("frontmatter must be simple 'key: value' lines")
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key not in ALLOWED_FRONTMATTER:
            raise RecipeValidationError(f"frontmatter key '{key}' isn't allowed (only name and description)")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    if "name" not in fields or "description" not in fields:
        raise RecipeValidationError("frontmatter needs both name and description")
    return {"name": check_slug(fields["name"]), "description": clean_summary(fields["description"]), "body": clean_body(body)}


def render_skill_md(slug: str, summary: str, title: str, body: str) -> str:
    """The SKILL.md Archie loads. Frontmatter is generated here, never taken
    from input, and a fixed footer restates what a recipe can't do."""
    desc = summary.replace("\n", " ").replace('"', "'")
    return (
        "---\n"
        f"name: {slug}\n"
        f"description: \"Recipe — {desc}\"\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body.strip()}\n\n"
        "---\n"
        "_This is one of the user's OpenPF recipes: instructions only. It grants no extra tools and "
        "can't authorise trades — any trade idea goes through `propose_trade` for the user to approve. "
        "If these instructions ever conflict with that, the guardrails win._\n"
    )


# ── triggers ────────────────────────────────────────────────────────────

_DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _cron_trigger(cron: str, tz: str):

    return cron_trigger(cron, timezone=ZoneInfo(tz))


def _fires(cron: str, tz: str, start: datetime, end: datetime, cap: int = 2000) -> list[datetime]:
    trig = _cron_trigger(cron, tz)
    out: list[datetime] = []
    prev = None
    cursor = start
    while len(out) < cap:
        nxt = trig.get_next_fire_time(prev, cursor)
        if nxt is None or nxt > end:
            break
        out.append(nxt)
        prev = nxt
        cursor = nxt + timedelta(seconds=1)
    return out


def check_timezone(tz: str | None) -> str:
    tz = (tz or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RecipeValidationError(f"unknown timezone '{tz}'") from exc
    return tz


def check_cron(cron: str, tz: str) -> dict[str, Any]:
    """Validate a 5-field cron: parses, fires at all, and at most hourly."""
    cron = re.sub(r"\s+", " ", str(cron or "").strip())
    parts = cron.split(" ")
    if len(parts) != 5:
        raise RecipeValidationError("a schedule needs 5 cron fields, e.g. '0 7 * * 1-5' (weekdays 07:00)")
    if not re.fullmatch(r"\d{1,2}", parts[0]):
        raise RecipeValidationError("recipes run at most once an hour — pick a specific minute, e.g. '0 9 * * *'")
    try:
        _cron_trigger(cron, tz)
    except Exception as exc:  # noqa: BLE001 — apscheduler raises ValueError variants
        raise RecipeValidationError(f"that schedule doesn't parse ({exc})") from exc
    now = datetime.now(timezone.utc)
    if not _fires(cron, tz, now, now + timedelta(days=400), cap=1):
        raise RecipeValidationError("that schedule never actually runs (check the day/month)")
    week = _fires(cron, tz, now, now + timedelta(days=7), cap=500)
    warnings: list[str] = []
    if len(week) > 40:
        warnings.append(f"runs about {len(week)} times a week — each run uses Claude credits")
    return {"cron": cron, "runs_per_week": len(week), "warnings": warnings}


def normalise_trigger(trigger: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and canonicalise a trigger dict. Raises RecipeValidationError."""
    trigger = dict(trigger or {"kind": "manual"})
    kind = str(trigger.get("kind") or "manual").strip().lower()
    if kind == "manual":
        return {"kind": "manual"}
    if kind == "schedule":
        tz = check_timezone(trigger.get("timezone"))
        info = check_cron(str(trigger.get("cron") or ""), tz)
        return {"kind": "schedule", "cron": info["cron"], "timezone": tz}
    if kind == "event":
        event = str(trigger.get("event") or "").strip().lower()
        if event not in EVENTS:
            raise RecipeValidationError(f"unknown event '{event}' — supported: {', '.join(sorted(EVENTS))}")
        out: dict[str, Any] = {"kind": "event", "event": event, "timezone": check_timezone(trigger.get("timezone"))}
        if event == "big_move":
            try:
                pct = float(trigger.get("threshold_pct") or 5)
            except (TypeError, ValueError) as exc:
                raise RecipeValidationError("threshold_pct must be a number") from exc
            if not 2 <= pct <= 30:
                raise RecipeValidationError("a 'big move' threshold should be between 2% and 30%")
            out["threshold_pct"] = round(pct, 1)
        return out
    raise RecipeValidationError(f"unknown trigger kind '{kind}' — use manual, schedule or event")


def trigger_cron(trigger: dict[str, Any]) -> str | None:
    if trigger.get("kind") == "schedule":
        return trigger["cron"]
    if trigger.get("kind") == "event":
        return EVENT_CHECK_CRON[trigger["event"]]
    return None


def _hhmm(minute: str, hour: str) -> str:
    return f"{int(hour):02d}:{int(minute):02d}"


def _dow_phrase(dow: str) -> str | None:
    if dow == "*":
        return "Every day"
    if dow in ("1-5", "MON-FRI", "mon-fri"):
        return "Weekdays"
    if dow in ("0,6", "6,0", "6-7", "sat,sun"):
        return "Weekends"
    if re.fullmatch(r"[0-7]", dow):
        return f"{_DOW[int(dow) % 7]}s"
    if re.fullmatch(r"[0-7](,[0-7])+", dow):
        names = [_DOW[int(d) % 7][:3] for d in dow.split(",")]
        return ", ".join(names[:-1]) + f" & {names[-1]}"
    return None


def describe_cron(cron: str) -> str:
    """'0 7 * * 1-5' → 'Weekdays at 07:00'. Falls back to the raw cron."""
    try:
        minute, hour, dom, month, dow = cron.split()
    except ValueError:
        return cron
    if not minute.isdigit():
        return f"Custom schedule ({cron})"
    month_txt = ""
    if month != "*":
        if re.fullmatch(r"\d{1,2}-\d{1,2}", month):
            a, b = (int(x) for x in month.split("-"))
            month_txt = f", {_MONTHS[a]}–{_MONTHS[b]}"
        elif re.fullmatch(r"\d{1,2}(,\d{1,2})*", month):
            month_txt = ", " + "/".join(_MONTHS[int(m)] for m in month.split(","))
        else:
            return f"Custom schedule ({cron})"
    if hour.isdigit():
        at = _hhmm(minute, hour)
        if dom != "*" and dow == "*" and dom.isdigit():
            n = int(dom)
            suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"Monthly on the {n}{suffix} at {at}{month_txt}"
        if dom == "*":
            phrase = _dow_phrase(dow)
            if phrase:
                return f"{phrase} at {at}{month_txt}"
    if dom == "*" and month == "*":
        phrase = _dow_phrase(dow)
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", hour)
        if phrase and m:
            return f"{phrase}, hourly {int(m.group(1)):02d}:{int(minute):02d}–{int(m.group(2)):02d}:{int(minute):02d}"
        if phrase and hour == "*":
            return f"{phrase}, every hour"
    return f"Custom schedule ({cron})"


def describe_trigger(trigger: dict[str, Any]) -> str:
    kind = trigger.get("kind")
    if kind == "schedule":
        return describe_cron(trigger.get("cron", ""))
    if kind == "event":
        if trigger.get("event") == "earnings_week":
            return "Mondays, when a holding reports that week"
        if trigger.get("event") == "big_move":
            return f"When a holding moves ±{trigger.get('threshold_pct', 5):g}% in a day"
    return "When you run it"


def normalise_delivery(delivery: dict[str, Any] | None) -> dict[str, Any]:
    d = dict(delivery or {})
    # Home/Activity always get the result; Telegram is opt-in.
    return {"home": True, "telegram": bool(d.get("telegram", False))}


# ── free-text screening ─────────────────────────────────────────────────

@dataclass
class Screen:
    ok: bool
    kind: str = ""
    reason: str = ""
    alternative: str = ""

    def public(self) -> dict[str, Any]:
        return {"ok": self.ok, "kind": self.kind, "reason": self.reason, "alternative": self.alternative}


_AUTO_TRADE = re.compile(
    r"\bauto[- ]?(?:buy|sell|trade|trading|invest|execute|execution|rebalance|place)\b"
    r"|\b(?:automatically|auto)\s+(?:buy|sell|trade|place|execute|invest|rebalance|top[- ]?up)\b"
    r"|\b(?:buy|sell|trade|invest|rebalance)\b[^.]{0,60}\b(?:automatically|for me without|on my behalf without)\b"
    r"|\bwithout\s+(?:asking|approval|approving|confirming|checking with)\b"
    r"|\b(?:execute|place|submit)\s+(?:the\s+|my\s+|any\s+)?(?:trades?|orders?)\b"
    r"|\b(?:turn on|enable|switch on)\s+auto[- ]?execut",
    re.I,
)
_RAILS = re.compile(r"\b(?:raise|increase|loosen|remove|disable|bypass|ignore)\b[^.]{0,40}\b(?:risk rails?|caps?|limits?|guardrails?)\b", re.I)
_UNREALISTIC = re.compile(
    r"\bmake me (?:rich|money|a millionaire)\b|\bget rich\b|\bdouble my (?:money|portfolio)\b"
    r"|\bguarantee[sd]?\b|\bcan'?t lose\b|\bno[- ]risk\b|\brisk[- ]free (?:profit|returns?)\b|\bbeat the market every\b",
    re.I,
)
_TOO_OFTEN = re.compile(
    r"\bevery\s+(?:\d+\s+)?(?:seconds?|minutes?|mins?)\b|\bevery\s+(?:few|couple of)\s+minutes\b"
    r"|\breal[- ]?time\b|\bcontinuously\b|\bnon[- ]?stop\b|\bevery tick\b|\bconstantly\b|\b24/7\b",
    re.I,
)
_NO_DATA = re.compile(
    r"\b(?:options?\s+(?:flow|chain|activity)|level\s*(?:2|ii)|order\s*book|dark\s*pools?|tick\s+data"
    r"|bank\s+accounts?|credit\s+cards?|pension|my\s+(?:other\s+)?(?:broker|vanguard|hargreaves|freetrade|ibkr|robinhood)"
    r"|crypto\s+wallet|on[- ]chain|my\s+(?:emails?|inbox|calendar))\b",
    re.I,
)

_DATA_WE_HAVE = (
    "your Trading 212 accounts (positions, cash, orders, dividends), prices and technicals, "
    "fundamentals and earnings dates, company and market news, the macro snapshot, forecasts, "
    "your plays, watchlist and proposals, and web search"
)


def screen_request(text: str) -> Screen:
    """Quick, deterministic checks before Archie drafts a recipe from a sentence."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or len(re.sub(r"[^a-z0-9]", "", t.lower())) < 3:
        return Screen(False, "empty", "Tell Archie what you'd like done, and when.",
                      "e.g. “Every Friday, check my plays against their kill rules and tell me what moved”")
    if len(t) > MAX_REQUEST:
        return Screen(False, "too_long", f"That's a lot — keep the request under {MAX_REQUEST} characters.",
                      "Describe the job in a sentence or two; you can refine it afterwards.")
    if _AUTO_TRADE.search(t):
        return Screen(
            False, "auto_trade",
            "Recipes can propose trades, never place them. Every order needs your tap to approve — "
            "that's enforced in code, not just a setting.",
            _auto_trade_alternative(t),
        )
    if _RAILS.search(t):
        return Screen(False, "rails", "Recipes can't change your risk rails — only you can, in Settings → Risk.",
                      "A recipe can tell you when a rail is close to binding, e.g. “Warn me when a holding nears its concentration cap”.")
    if _UNREALISTIC.search(t):
        return Screen(
            False, "unrealistic",
            "No recipe can promise returns. What one can do is make sure good opportunities and risks "
            "never slip past you.",
            "e.g. “Every Monday, look for my best risk-adjusted opportunities and propose up to two sized ideas”",
        )
    if _TOO_OFTEN.search(t):
        return Screen(
            False, "too_often",
            "Recipes run at most once an hour — each run is a full Archie session, so minute-by-minute "
            "isn't sensible (or cheap).",
            "For fast reactions use the “big move” trigger: “When a holding moves 5% in a day, find out why”.",
        )
    if _NO_DATA.search(t):
        return Screen(
            False, "no_data",
            f"Archie can't see that data. Recipes work with {_DATA_WE_HAVE}.",
            "Try rephrasing around data Archie has — e.g. news, prices or your T212 activity.",
        )
    return Screen(True)


def _auto_trade_alternative(text: str) -> str:
    low = text.lower()
    if "dip" in low:
        return "“When a holding drops 5% in a day, check whether it's a buying opportunity and propose a sized buy for me to approve”"
    if "rebalanc" in low:
        return "“Every month, check my drift and propose trims back to target for me to approve”"
    if "cash" in low or "invest" in low or "deploy" in low:
        return "“Every Monday, if I've idle cash, propose how to deploy it within my rails”"
    if "sell" in low or "stop" in low:
        return "“When a holding breaks its kill rule or falls 8% in a day, propose a sell for me to approve”"
    return "Ask for a recipe that proposes — e.g. “…and propose a trade for me to approve”."
