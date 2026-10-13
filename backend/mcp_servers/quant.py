"""Quant MCP server: tested portfolio maths as a handful of broad tools.

Archie must not do sizing, risk or allocation arithmetic in his head. These
tools read the live book (GBP), fetch history, convert returns to GBP, use
shrinkage covariance, check the user's risk rails, and return compact JSON
whose ``summary`` lines carry the numbers to quote. Nothing here proposes or
places a trade — feed the output to propose_trade / draft_play.

Backed by ``app.services.quant_service`` (I/O) and ``app.quant`` (pure maths).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from app.core.database import SessionLocal, init_db
from app.quant import QuantDataError
from app.services import quant_service as qs

_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "openpf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("quant-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "quant.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "quant",
    instructions=(
        "Portfolio maths for the user's real book (GBP, risk rails applied). Use these instead of computing "
        "any size, weight, volatility, drawdown, correlation, rebalance or tax-wrapper number yourself. "
        "size_position: how big should a position/theme bet be. analyse_risk: concentration, risk "
        "contribution, drawdown, tail loss, beta, correlation clusters. what_if: effect of trades before "
        "proposing them. rebalance_plan: band rebalancing within a turnover budget. deploy_cash: where idle "
        "cash goes and lump-sum vs staged. placement: ISA vs Invest and CGT. Quote the summary lines; never "
        "places orders — use propose_trade or draft_play after."
    ),
)

# Upfront-loaded even when tool search defers other servers (Claude Code reads _meta).
_META = {"anthropic/alwaysLoad": True}
_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

Lookback = Literal["6mo", "1y", "2y"]
Account = Literal["invest", "stocks_isa"]


def _fmt(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def _run(name: str, fn, **kwargs) -> str:
    logger.info("%s %s", name, {k: v for k, v in kwargs.items() if k != "trades"} | ({"trades": len(kwargs["trades"])} if "trades" in kwargs else {}))
    init_db()
    try:
        with SessionLocal() as db:
            return _fmt(fn(db, **kwargs))
    except QuantDataError as exc:
        return _fmt({"ok": False, "error": str(exc),
                     "hint": "Try lookback='2y'/'6mo', drop the newly listed ticker, or pass the missing input."})
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s failed", name)
        return _fmt({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                     "hint": "If this is a data outage, say so and don't estimate the numbers yourself."})


class TradeIn(BaseModel):
    ticker: str = Field(description="Symbol as held/quoted, e.g. NVDA, CCJ, VUSA.L")
    side: Literal["buy", "sell"] = "buy"
    amount_gbp: float | None = Field(default=None, description="Trade size in GBP")
    fraction_of_holding: float | None = Field(default=None, description="For sells/top-ups of a held name: 0.5 = half")
    account: Account | None = Field(default=None, description="Default: buys → invest, sells → the account holding most")
    isa_only: bool = Field(default=False, description="placement only: leveraged/inverse ETPs (ISA-only on T212)")
    speculative: bool = Field(default=False, description="placement only: likely to be cut at a loss → prefer Invest")


@mcp.tool(annotations=_RO, meta=_META)
def size_position(
    tickers: list[str],
    method: Literal["conviction", "vol_target", "kelly", "budget"] = "conviction",
    weighting: Literal["inverse_vol", "risk_parity", "hrp", "equal"] = "inverse_vol",
    conviction: Literal["low", "medium", "high"] = "medium",
    budget_gbp: float | None = None,
    target_risk: float | None = None,
    expected_return: float | None = None,
    kelly_fraction: float = 0.5,
    max_weight: float | None = None,
    max_loss_pct: float | None = None,
    account: Account = "invest",
    lookback: Lookback = "1y",
) -> str:
    """How big should a position or theme basket be? Returns the £ size, % of book, per-leg split,
    new money after what's already held, basket vol, stress/tail loss, tranches and a rails check.

    method (how big in total):
      conviction — the Plays rule: low/medium/high = 2/4/6% of book, scaled by 25%/basket-vol (0.5–1.25×);
      vol_target — size so the position adds `target_risk` of book volatility (default 0.01 = 1%);
      kelly — fractional Kelly (default half) from `expected_return` or, if absent, an assumed Sharpe by conviction;
      budget — a fixed `budget_gbp`.
    weighting (how the basket is split): inverse_vol (default), risk_parity (equal risk contribution,
    shrinkage covariance), hrp (hierarchical risk parity), equal.
    Caps: `max_weight` (default 10% per play / the single-name rail for one ticker) and `max_loss_pct`
    (largest loss in a 40% fall, as a fraction of book).

    Example: size_position(tickers=["CCJ","NXE","BWXT","LEU"], method="conviction", conviction="high")
    """
    return _run("size_position", qs.size_position_report, tickers=tickers, method=method, weighting=weighting,
                conviction=conviction, budget_gbp=budget_gbp, target_risk=target_risk, expected_return=expected_return,
                kelly_fraction=kelly_fraction, max_weight=max_weight, max_loss_pct=max_loss_pct, account=account,
                lookback=lookback)


@mcp.tool(annotations=_RO, meta=_META)
def analyse_risk(
    scope: Literal["book", "play", "holdings"] = "book",
    view: Literal["overview", "concentration", "contribution", "drawdown", "cvar", "beta", "correlation_clusters"] = "overview",
    play_id: str | None = None,
    tickers: list[str] | None = None,
    lookback: Lookback = "1y",
) -> str:
    """Risk of the whole book, one play (play_id) or some holdings (tickers), in GBP.

    views: concentration (weights, effective number of names, single-name cap and theme breaches),
    contribution (book vol and each holding's share of it — Euler decomposition, Ledoit-Wolf covariance),
    drawdown (today's weights replayed: max/current drawdown, dates), cvar (95% VaR and expected
    shortfall with each holding's share of the tail), beta (to the S&P 500 plus tech/small-cap/rates tilts),
    correlation_clusters (groups that move together, ρ ≥ 0.6). overview = all of them.

    Example: analyse_risk(scope="book", view="overview")
    """
    return _run("analyse_risk", qs.analyse_risk_report, scope=scope, view=view, play_id=play_id, tickers=tickers,
                lookback=lookback)


@mcp.tool(annotations=_RO, meta=_META)
def what_if(trades: list[TradeIn], lookback: Lookback = "1y") -> str:
    """Effect of hypothetical trades on the book before proposing them: weights before → after,
    book volatility and tail loss before → after, effective number of names, cash per account,
    realised gains on sells, and a rails check (single-name cap, order and daily notional, cash).

    Example: what_if(trades=[{"ticker":"NVDA","side":"sell","fraction_of_holding":0.5}])
    """
    return _run("what_if", qs.what_if_report, trades=[t.model_dump() for t in trades], lookback=lookback)


@mcp.tool(annotations=_RO, meta=_META)
def rebalance_plan(
    scope: Literal["book", "play"] = "book",
    target: Literal["caps", "equal", "inverse_vol", "risk_parity", "hrp", "play_legs"] = "caps",
    targets: dict[str, float] | None = None,
    play_id: str | None = None,
    abs_band: float = 0.05,
    rel_band: float = 0.25,
    trade_to: Literal["target", "edge"] = "target",
    turnover_budget: float = 0.10,
    use_cash: bool = False,
    lookback: Lookback = "1y",
) -> str:
    """Band rebalancing with a turnover budget. Only names outside their band trade
    (band = min(abs_band, rel_band × target) — the 5/25 rule by default); largest drifts first until
    `turnover_budget` (fraction of book traded) runs out; buys are funded by sells (plus free cash if use_cash).

    target: caps (trim names above the single-name rail — the least-opinionated de-risk), equal,
    inverse_vol, risk_parity, hrp (re-weight the invested part), play_legs (a play back to its leg weights).
    `targets` = explicit {ticker: weight of book} overrides `target`.

    Example: rebalance_plan(scope="book", target="caps", turnover_budget=0.1)
    """
    return _run("rebalance_plan", qs.rebalance_report, scope=scope, target=target, targets=targets, play_id=play_id,
                abs_band=abs_band, rel_band=rel_band, trade_to=trade_to, turnover_budget=turnover_budget,
                use_cash=use_cash, lookback=lookback)


@mcp.tool(annotations=_RO, meta=_META)
def deploy_cash(
    amount_gbp: float,
    account: Account = "invest",
    tickers: list[str] | None = None,
    weighting: Literal["inverse_vol", "risk_parity", "hrp", "equal"] = "inverse_vol",
    months: int = 6,
    expected_return: float | None = None,
    lookback: Lookback = "1y",
) -> str:
    """Where idle cash should go and whether to invest it at once or stage it.

    Allocates `amount_gbp` across `tickers` (default: existing holdings with room under the
    single-name cap) by `weighting`, respecting the cap; then compares lump sum vs `months` of equal
    monthly tranches (Monte Carlo on the basket's own vol; equities 7%/yr vs 4% cash unless
    `expected_return` given) and recommends one, with a schedule and rails check.

    Example: deploy_cash(amount_gbp=5000, account="stocks_isa")
    """
    return _run("deploy_cash", qs.deploy_cash_report, amount_gbp=amount_gbp, account=account, tickers=tickers,
                weighting=weighting, months=months, expected_return=expected_return, lookback=lookback)


@mcp.tool(annotations=_RO, meta=_META)
def placement(
    trades: list[TradeIn],
    tax_band: Literal["basic", "higher", "additional"] = "higher",
    isa_subscribed_gbp: float | None = None,
    gains_realised_gbp: float = 0.0,
) -> str:
    """ISA vs Invest for buys, and which account to sell from, under UK rules (2026/27: £20k ISA,
    £3k CGT exemption, 18/24% CGT). Buys: the highest expected-tax-drag names get the ISA first
    (growth/volatility and dividends), speculative ones stay in Invest for loss relief, ISA-only ETPs
    go in the ISA. Sells: prefer harvesting Invest losses, then gains within the exemption, else the ISA.
    Also: ISA allowance left (from the synced transaction feed unless `isa_subscribed_gbp` given),
    days to 5 April, and Bed & ISA room. Estimates, not tax advice.

    Example: placement(trades=[{"ticker":"NVDA","side":"buy","amount_gbp":3000}], tax_band="higher")
    """
    return _run("placement", qs.placement_report, trades=[t.model_dump() for t in trades], tax_band=tax_band,
                isa_subscribed_gbp=isa_subscribed_gbp, gains_realised_gbp=gains_realised_gbp)


QUANT_MCP_TOOLS = [f"mcp__quant__{n}" for n in
                   ("size_position", "analyse_risk", "what_if", "rebalance_plan", "deploy_cash", "placement")]


if __name__ == "__main__":
    mcp.run()
