"""Standard questions for Archie's quant toolkit, with expected tool calls and
numeric sanity checks. Run with ``run.py`` against a sandbox instance.

Each case declares:
* ``expect`` — groups of tool names; at least one tool from every group must be
  called (short names: ``size_position`` matches ``mcp__quant__size_position``).
* ``forbid`` — tools that must not be called (side effects nobody asked for).
* ``checks`` — functions of a :class:`Run` returning ``(ok, detail)``: sanity
  checks on the tool *outputs* (is the maths sane?) and on the reply (did Archie
  quote the tool's numbers rather than inventing his own?).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

NO_SIDE_EFFECTS = ["propose_trade", "stage_play_tranche", "draft_play", "create_view", "create_recipe",
                   "create_scheduled_task", "add_to_watchlist", "assign_owner"]


@dataclass
class ToolCall:
    name: str  # full id, e.g. mcp__quant__size_position
    input: dict[str, Any]
    output: Any  # parsed JSON when possible, else text
    is_error: bool = False

    @property
    def short(self) -> str:
        return self.name.rsplit("__", 1)[-1]


@dataclass
class Run:
    reply: str
    calls: list[ToolCall]
    cost_usd: float | None = None
    duration_s: float | None = None
    error: str | None = None

    def outputs(self, tool: str) -> list[dict[str, Any]]:
        return [c.output for c in self.calls if c.short == tool and isinstance(c.output, dict) and c.output.get("ok")]

    def first(self, tool: str) -> dict[str, Any] | None:
        outs = self.outputs(tool)
        return outs[0] if outs else None


Check = Callable[[Run], tuple[bool, str]]


@dataclass
class Case:
    id: str
    prompt: str
    expect: list[list[str]]
    checks: list[Check] = field(default_factory=list)
    forbid: list[str] = field(default_factory=lambda: list(NO_SIDE_EFFECTS))


# ── check helpers ────────────────────────────────────────────────────────────

_GBP = re.compile(r"£\s?(-?[\d,]+(?:\.\d+)?)\s?(k|K|m|M)?")
_PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s?%")


def gbp_figures(text: str) -> list[float]:
    out = []
    for num, suffix in _GBP.findall(text):
        v = float(num.replace(",", ""))
        v *= {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}.get(suffix or "", 1)
        out.append(v)
    return out


def pct_figures(text: str) -> list[float]:
    return [float(x) / 100 for x in _PCT.findall(text)]


def quotes_gbp(tool: str, getter: Callable[[dict], float | None], rel: float = 0.05, label: str = "") -> Check:
    """The reply contains a £ figure within ``rel`` of the tool's number."""
    def check(run: Run) -> tuple[bool, str]:
        out = run.first(tool)
        if not out:
            return False, f"no ok {tool} output"
        target = getter(out)
        if target is None:
            return False, f"{tool} output lacks {label}"
        figs = gbp_figures(run.reply)
        hit = any(abs(f - target) <= max(abs(target) * rel, 5) for f in figs)
        return hit, f"{label or tool} £{target:,.0f} {'quoted' if hit else 'not quoted'} (reply £: {[round(f) for f in figs[:8]]})"
    return check


def quotes_pct(tool: str, getter: Callable[[dict], float | None], abs_tol: float = 0.006, label: str = "") -> Check:
    """The reply contains a % figure within ``abs_tol`` (fraction) of the tool's number."""
    def check(run: Run) -> tuple[bool, str]:
        out = run.first(tool)
        if not out:
            return False, f"no ok {tool} output"
        target = getter(out)
        if target is None:
            return False, f"{tool} output lacks {label}"
        figs = pct_figures(run.reply)
        hit = any(abs(abs(f) - abs(target)) <= abs_tol for f in figs)
        return hit, f"{label} {target:.1%} {'quoted' if hit else 'not quoted'}"
    return check


def tool_ok(tool: str, pred: Callable[[dict], bool], label: str) -> Check:
    """A sanity predicate on the tool's own output."""
    def check(run: Run) -> tuple[bool, str]:
        out = run.first(tool)
        if not out:
            return False, f"no ok {tool} output"
        try:
            ok = bool(pred(out))
        except Exception as exc:  # noqa: BLE001
            return False, f"{label}: {exc}"
        return ok, label
    return check


def tool_input(tool: str, pred: Callable[[dict], bool], label: str) -> Check:
    def check(run: Run) -> tuple[bool, str]:
        calls = [c for c in run.calls if c.short == tool]
        ok = any(pred(c.input or {}) for c in calls)
        return ok, label
    return check


def reply_matches(pattern: str, label: str) -> Check:
    rx = re.compile(pattern, re.I | re.S)
    return lambda run: (bool(rx.search(run.reply)), label)


def _sum(rows: list[dict], key: str) -> float:
    return float(sum(r.get(key) or 0 for r in rows))


# ── the cases ────────────────────────────────────────────────────────────────

CASES: list[Case] = [
    Case(
        id="deploy_5k",
        prompt="I've got £5k of idle cash in my ISA. Where should it go, and should I put it in all at once or drip it in? "
               "Don't propose anything yet — just the plan.",
        expect=[["deploy_cash"]],
        checks=[
            tool_ok("deploy_cash", lambda o: _sum(o["allocation"], "amount_gbp") <= 5000 * 1.01, "allocation ≤ £5,000"),
            tool_ok("deploy_cash", lambda o: 0 < o["lump_vs_staged"]["prob_lump_wins"] < 1, "P(lump wins) in (0,1)"),
            reply_matches(r"lump|all at once", "answers lump vs staged"),
            quotes_pct("deploy_cash", lambda o: o["lump_vs_staged"]["prob_lump_wins"], abs_tol=0.011,
                       label="P(lump sum ahead)"),
        ],
    ),
    Case(
        id="nuclear_high_conviction",
        prompt="How big should a nuclear energy bet be for me at high conviction? Just the size and why — don't draft a play yet.",
        expect=[["size_position"]],
        checks=[
            tool_input("size_position", lambda i: i.get("conviction") == "high", "passed conviction=high"),
            tool_ok("size_position", lambda o: o["pct_of_book"] <= 0.10 + 1e-9, "≤ 10% play cap"),
            tool_ok("size_position", lambda o: 0.005 <= o["pct_of_book"], "≥ 0.5% of book"),
            quotes_gbp("size_position", lambda o: o["target_gbp"], label="target size"),
        ],
    ),
    Case(
        id="too_concentrated",
        prompt="Am I too concentrated?",
        expect=[["analyse_risk"]],
        checks=[
            tool_ok("analyse_risk", lambda o: "concentration" in o, "concentration view returned"),
            quotes_pct("analyse_risk", lambda o: o["concentration"]["top"][0]["weight_of_book"], label="top weight"),
            reply_matches(r"NVDA", "names the top holding"),
        ],
    ),
    Case(
        id="sell_half_nvda",
        prompt="What if I sell half my NVDA? Don't propose it, just show me the effect.",
        expect=[["what_if"]],
        checks=[
            tool_input("what_if", lambda i: any(str(t.get("ticker", "")).upper() == "NVDA" and t.get("side") == "sell"
                                                 for t in i.get("trades", [])), "sells NVDA"),
            tool_ok("what_if", lambda o: abs(o["weights"][0]["weight_after"] - o["weights"][0]["weight_before"] / 2) < 0.005,
                    "NVDA weight halves"),
            tool_ok("what_if", lambda o: o["book_vol"]["after"] <= o["book_vol"]["before"] + 1e-6, "book vol does not rise"),
            quotes_pct("what_if", lambda o: o["weights"][0]["weight_after"], label="NVDA weight after"),
        ],
    ),
    Case(
        id="risk_drivers",
        prompt="What's actually driving the risk in my portfolio?",
        expect=[["analyse_risk"]],
        checks=[
            tool_ok("analyse_risk", lambda o: abs(sum(r["risk_share"] for r in o["contribution"]["rows"])) <= 1.01,
                    "risk shares ≤ 100%"),
            lambda run: (bool(run.first("analyse_risk")) and run.first("analyse_risk")["contribution"]["rows"][0]["ticker"]
                         in run.reply, "names the biggest risk contributor"),
            quotes_pct("analyse_risk", lambda o: o["contribution"]["book_vol"], abs_tol=0.006, label="book vol"),
        ],
    ),
    Case(
        id="selloff_loss",
        prompt="If we got a sharp selloff like 2022, roughly how much could my book lose? Give me a £ figure.",
        expect=[["analyse_risk"]],
        checks=[
            tool_ok("analyse_risk", lambda o: any(k in o for k in ("cvar", "drawdown", "beta")), "tail/drawdown/beta view"),
            lambda run: (len(gbp_figures(run.reply)) > 0, "gives a £ figure"),
        ],
    ),
    Case(
        id="isa_or_invest",
        prompt="I want to buy £3,000 of MSFT and £2,000 of KO. Which account should each go in, ISA or Invest?",
        expect=[["placement"]],
        checks=[
            tool_ok("placement", lambda o: {i["ticker"] for i in o["items"]} >= {"MSFT", "KO"}, "both buys placed"),
            tool_ok("placement", lambda o: all(i["account"] in ("stocks_isa", "invest", "split") for i in o["items"]), "valid accounts"),
            quotes_gbp("placement", lambda o: o["isa"]["allowance_left_gbp"], label="ISA allowance left"),
        ],
    ),
    Case(
        id="rebalance_low_turnover",
        prompt="Rebalance my book towards something more balanced, but keep turnover under 10% of the book. Plan only.",
        expect=[["rebalance_plan"]],
        checks=[
            tool_input("rebalance_plan", lambda i: float(i.get("turnover_budget", 0.10)) <= 0.10 + 1e-9, "turnover budget ≤ 10%"),
            tool_ok("rebalance_plan", lambda o: (o["turnover_gbp"] or 0) <= (o["turnover_budget_gbp"] or 0) + 1, "turnover within budget"),
        ],
    ),
    Case(
        id="kelly_pltr",
        prompt="What does the Kelly criterion say about how much PLTR I should hold? Should I use it?",
        expect=[["size_position"]],
        checks=[
            tool_input("size_position", lambda i: i.get("method") == "kelly", "method=kelly"),
            tool_ok("size_position", lambda o: o["pct_of_book"] <= 0.25 + 1e-9, "capped at the single-name rail"),
            reply_matches(r"half|fraction|cap", "explains fractional / capped Kelly"),
        ],
    ),
    Case(
        id="same_bet",
        prompt="Which of my holdings are really the same bet?",
        expect=[["analyse_risk"]],
        checks=[
            tool_ok("analyse_risk", lambda o: "correlation_clusters" in o, "clusters view returned"),
            reply_matches(r"correlat|move together|same bet", "talks about co-movement"),
        ],
    ),
    Case(
        id="rates_shock",
        prompt="Rates just jumped 50bp on a hot inflation print. How exposed is my book? No trades.",
        expect=[["analyse_risk"]],
        checks=[
            tool_ok("analyse_risk", lambda o: "beta" in o, "beta/tilts view returned"),
            reply_matches(r"beta|tilt|rates", "uses the factor exposures"),
        ],
    ),
]
