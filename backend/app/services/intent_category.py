"""What kind of call a proposal is — used by the track record and by standing
permissions.

``propose_trade`` stores an explicit ``meta["category"]`` going forward; older
intents (and ones created outside ``propose_trade``) are inferred from their
source, play link and rationale.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORIES: dict[str, str] = {
    "deploy_cash": "Deploy cash",
    "rebalance": "Rebalance",
    "play_tranche": "Play tranche",
    "news_reaction": "News reaction",
    "trim_derisk": "Trim / de-risk",
    "other": "Other",
}

_NEWS = re.compile(r"\b(news|headline|earnings|guidance|announce|downgrade|upgrade|reaction|sell-?off|big move|catalyst)\b", re.I)
_CASH = re.compile(r"\b(idle cash|free cash|deploy|dry powder|uninvested|cash drag|put .* cash to work)\b", re.I)
_TRIM = re.compile(r"\b(trim|de-?risk|reduce|take profit|lock in|concentration|overweight|stop.?loss)\b", re.I)


def normalize(value: Any) -> str | None:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"tranche": "play_tranche", "play": "play_tranche", "news": "news_reaction",
               "trim": "trim_derisk", "derisk": "trim_derisk", "de_risk": "trim_derisk",
               "cash": "deploy_cash", "deploy": "deploy_cash", "rebalancing": "rebalance"}
    v = aliases.get(v, v)
    return v if v in CATEGORIES else None


def infer(*, side: str, source: str | None, rationale: str | None, meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    explicit = normalize(meta.get("category"))
    if explicit:
        return explicit
    src = str(source or meta.get("source") or "").lower()
    text = rationale or ""
    if src == "rebalance" or text.startswith("[rebalance]"):
        return "rebalance"
    if src == "play" or meta.get("tranche_index") is not None:
        return "play_tranche"
    if "deploy" in src or "cash" in src:
        return "deploy_cash"
    if any(k in src for k in ("news", "big-move", "big_move", "alert", "earnings")):
        return "news_reaction"
    if side == "sell":
        return "trim_derisk"
    if _NEWS.search(text):
        return "news_reaction"
    if _CASH.search(text):
        return "deploy_cash"
    if _TRIM.search(text):
        return "trim_derisk"
    return "other"


def of_intent(intent: Any) -> str:
    meta = intent.meta if isinstance(getattr(intent, "meta", None), dict) else {}
    return infer(side=str(intent.side or ""), source=meta.get("source"), rationale=intent.rationale, meta=meta)
