"""Triage view requests before anything is built.

Cheap, deterministic checks that catch input a view can't serve — empty or
nonsense text, trading requests, data OpenPF doesn't have, attempts to make a
view reach the network — and turn it into a clear explanation plus the closest
presets, instead of a long build that fails or a page that invents data.
The builder model makes the subtler calls (it may still decline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.views_presets import closest_presets

MAX_PROMPT_CHARS = 4000

EXAMPLES = [
    "My holdings as a treemap coloured by today's move",
    "Which sectors am I most exposed to?",
    "Dividends by month this year",
    "How my ISA compares to my Invest account",
    "Biggest winners and losers since I bought",
]


@dataclass
class Decline:
    code: str
    message: str
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    examples: list[str] = field(default_factory=lambda: list(EXAMPLES))

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "suggestions": [{"slug": s["slug"], "title": s["title"], "why": s.get("why")} for s in self.suggestions],
            "examples": self.examples,
        }


_TRADE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:buy|sell|short|trade|purchase|place\s+(?:an?\s+)?order|execute|rebalance\s+(?:my|the)\s+portfolio\s+now|invest\s+(?:£|\$)?\d)"
    r"|\b(?:buy|sell|short)\s+(?:\d+\s+|£\d+\s+(?:of\s+|worth\s+of\s+)?)?(?:shares?\s+(?:of|in)\s+)?(?-i:[A-Z]{2,5}(?:\.L)?)\b(?!\s+(?:signal|history|price|orders?))"
    r"|\bmake\s+me\s+(?:rich|money|a\s+millionaire)\b|\bget\s+rich\b|\bguarantee[d]?\s+(?:returns?|profit)",
    re.I,
)
_UNAVAILABLE = [
    (re.compile(r"\b(?:crypto|bitcoin|btc|ethereum|eth|solana|dogecoin|nft|coinbase\s+wallet|binance|altcoins?)\b", re.I), "crypto holdings — OpenPF only sees your Trading 212 Invest and Stocks ISA accounts"),
    (re.compile(r"\b(?:bank\s+accounts?|current\s+account|savings\s+account|credit\s+cards?|monzo|revolut|barclays|hsbc|natwest|mortgage|pension|sipp|premium\s+bonds?|spending|budget(?:ing)?|salary)\b", re.I), "bank, pension or spending data — OpenPF only sees your Trading 212 Invest and Stocks ISA accounts"),
    (re.compile(r"\b(?:order\s*book|level\s*(?:2|ii)|market\s+depth|bid[\s/-]*ask\s+(?:depth|ladder)|tick\s+data|real[\s-]*time\s+(?:ticks|stream|quotes\s+every)|options?\s+chain|implied\s+vol(?:atility)?\s+surface|greeks)\b", re.I), "live market microstructure (order books, ticks, options chains) — views get daily prices and delayed quotes"),
    (re.compile(r"\b(?:other\s+people'?s|friends'?|someone\s+else'?s|hedge\s+fund|congress(?:ional)?|insider)\s+(?:portfolios?|holdings?|trades?)\b", re.I), "other people's portfolios — views only read your own accounts"),
]
_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.]{0,40}\b(?:rules?|instructions?|guardrails?|sandbox|restrictions?|previous|system\s+prompt)\b"
    r"|\b(?:fetch|curl|wget|post|send|upload|exfiltrate|beacon|ping)\b[^.]{0,60}(?:https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|xyz|ru|cn|co|dev|app)\b)"
    r"|https?://|\bwww\.[a-z0-9-]+\."
    r"|\b(?:api\s*keys?|secrets?|tokens?|passwords?|\.env|credentials?)\b"
    r"|<\s*script\b|javascript:|\beval\s*\(|\bwindow\.(?:open|location)|document\.cookie",
    re.I,
)
_VOWEL_RE = re.compile(r"[aeiouy]", re.I)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'&-]*")
# Words that make a short prompt meaningful even without much else.
_DOMAIN = set(
    "portfolio holdings holding stocks stock shares isa invest account accounts dividend dividends income yield cash risk sector sectors country "
    "currency usd gbp fx today daily weekly monthly performance returns return gains gain losses loss winners losers allocation exposure "
    "concentration treemap chart history value p&l pnl movers top biggest largest smallest plays play theme themes watchlist orders trades "
    "proposals price prices etf etfs fund funds tech nvda aapl msft tax allowance drift target targets weight weights compare benchmark".split()
)


def _looks_like_nonsense(text: str) -> bool:
    words = _WORD_RE.findall(text)
    if not words:
        return True
    lowered = [w.lower() for w in words]
    if any(w in _DOMAIN for w in lowered):
        return False
    letters = "".join(words)
    if len(words) == 1 and len(letters) <= 6:
        return True  # "asdf", "test", "hmm"
    vowel_ratio = len(_VOWEL_RE.findall(letters)) / max(1, len(letters))
    if vowel_ratio < 0.18:
        return True  # keyboard mash: "qwrtpsdfg"
    if len(set(lowered)) == 1 and len(lowered) > 1:
        return True  # "hello hello hello"
    return len(words) <= 2 and not any(len(w) > 3 for w in words)


def triage(prompt: str) -> Decline | None:
    """Return a Decline for requests that can't (or shouldn't) become a view."""
    text = (prompt or "").strip()
    if not text:
        return Decline("empty", "Describe what you'd like to see — for example “my holdings by sector” — or pick a preset.", closest_presets("", 3))
    if len(text) > MAX_PROMPT_CHARS:
        return Decline("too_long", f"That request is {len(text):,} characters; keep it under {MAX_PROMPT_CHARS:,} — one or two sentences works best.", closest_presets(text[:400], 3))
    if _INJECTION_RE.search(text):
        return Decline(
            "injection",
            "Views can't reach the internet, open links or read keys — they only read your own portfolio data inside a sandbox. Describe the chart you want instead.",
            closest_presets(text, 3),
        )
    if _TRADE_RE.search(text):
        return Decline(
            "trade",
            "Views can show your portfolio but never trade. To act on an idea, ask Archie in chat — it can propose a trade for you to approve.",
            closest_presets(text, 3),
        )
    for pattern, what in _UNAVAILABLE:
        if pattern.search(text):
            return Decline("unavailable", f"OpenPF doesn't have {what}.", closest_presets(text, 3))
    if _looks_like_nonsense(text):
        return Decline("nonsense", "I couldn't tell what to build from that. Try describing a chart in a sentence, or start from a preset.", closest_presets(text, 3))
    return None
