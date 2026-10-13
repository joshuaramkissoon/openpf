"""The quant toolkit's I/O layer: the book, GBP returns, risk rails → ``app.quant``.

Each public ``*_report`` function backs one tool of the ``quant`` MCP server and
returns compact JSON-able dicts that always lead with ``summary`` — plain-English
lines carrying the key numbers — so Archie quotes numbers instead of computing
them. All money is GBP; returns are converted to GBP (a US stock's risk for a
UK investor includes the dollar). Nothing here creates proposals or orders.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.quant import QuantDataError
from app.quant import clustering, covariance, deploy, exposure, rebalance, returns as qret, risk, sizing, uk_tax

logger = logging.getLogger(__name__)

Lookback = Literal["6mo", "1y", "2y"]
LOOKBACK_DAYS = {"6mo": 190, "1y": 380, "2y": 750}
CASH_RATE = 0.04  # planning rate on uninvested GBP cash (≈ UK base rate)
STRESS_DRAWDOWN = 0.40  # same planning shock the plays sizing uses
EQUITY_RETURN = 0.07  # planning arithmetic return for diversified equities

ACCOUNT_LABEL = {"invest": "Invest", "stocks_isa": "ISA"}


# ── Formatting ───────────────────────────────────────────────────────────────

def gbp(v: float | None) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    sign = "-" if v < 0 else ""
    return f"{sign}£{abs(v):,.0f}"


def pct(v: float | None, dp: int = 1) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    return f"{v * 100:.{dp}f}%"


def r4(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), 4)


def r2(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), 2)


# ── The book ─────────────────────────────────────────────────────────────────

@dataclass
class Holding:
    ticker: str
    name: str | None
    value_gbp: float
    weight: float
    yf_ticker: str
    currency: str
    by_account: dict[str, float] = field(default_factory=dict)  # value per account
    ppl_by_account: dict[str, float] = field(default_factory=dict)  # unrealised P&L per account
    price_gbp: float = 0.0


@dataclass
class Book:
    total_gbp: float
    cash_gbp: dict[str, float]
    holdings: dict[str, Holding]

    @property
    def cash_total(self) -> float:
        return float(sum(self.cash_gbp.values()))

    def weights(self) -> dict[str, float]:
        return {t: h.weight for t, h in self.holdings.items()}


def _norm(t: str | None) -> str:
    return str(t or "").upper().strip()


def load_book(db: Session) -> Book:
    """Whole-book exposure by ticker (PLTR in Invest and ISA is one position)."""
    from app.services.portfolio_service import get_portfolio_snapshot

    snap = get_portfolio_snapshot(db, account_kind="all", display_currency="GBP")
    total = float(snap.get("account", {}).get("total") or snap.get("metrics", {}).get("total_value") or 0.0)
    cash = {a["account_kind"]: float(a.get("free_cash") or 0.0) for a in snap.get("accounts", [])}
    holdings: dict[str, Holding] = {}
    for p in snap.get("positions", []):
        t = _norm(p.get("display_ticker") or p.get("ticker"))
        if not t:
            continue
        value = float(p.get("value") or 0.0)
        acct = str(p.get("account_kind") or "invest")
        h = holdings.get(t)
        if h is None:
            h = holdings[t] = Holding(
                ticker=t, name=p.get("name"), value_gbp=0.0, weight=0.0,
                yf_ticker=str(p.get("yfinance_ticker") or t), currency=_norm(p.get("instrument_currency")) or "USD",
                price_gbp=float(p.get("current_price") or 0.0),
            )
        h.value_gbp += value
        h.by_account[acct] = h.by_account.get(acct, 0.0) + value
        h.ppl_by_account[acct] = h.ppl_by_account.get(acct, 0.0) + float(p.get("ppl") or 0.0)
    if total <= 0:
        total = sum(h.value_gbp for h in holdings.values()) + sum(cash.values())
    for h in holdings.values():
        h.weight = h.value_gbp / total if total else 0.0
    return Book(total, cash, dict(sorted(holdings.items(), key=lambda kv: -kv[1].value_gbp)))


def rails(db: Session) -> dict[str, float]:
    """The user's risk rails plus the plays / theme limits, as fractions or GBP."""
    from app.services.book_service import THEME_CONCENTRATION_WARN
    from app.services.config_store import ConfigStore
    from app.services.plays_service import MAX_PLAY_PCT

    r = ConfigStore(db).get_risk() or {}
    return {
        "max_position_weight": float(r.get("max_position_weight") or 0.25),
        "max_single_order_notional": float(r.get("max_single_order_notional") or 0.0),
        "max_daily_notional": float(r.get("max_daily_notional") or 0.0),
        "max_play_pct": float(MAX_PLAY_PCT),
        "theme_warn_pct": float(THEME_CONCENTRATION_WARN),
    }


# ── Prices → GBP returns ─────────────────────────────────────────────────────

_FX_SYMBOL = {"USD": ("GBPUSD=X", True), "EUR": ("EURGBP=X", False)}  # (symbol, quoted as foreign per GBP)


def _closes(symbol: str, days: int) -> pd.Series:
    from app.services.market_data import fetch_history

    frame = fetch_history(symbol, lookback_days=days)
    idx = pd.DatetimeIndex(pd.to_datetime(frame["date"])).normalize()  # market_data returns plain dates
    s = pd.Series(frame["close"].astype(float).to_numpy(), index=idx)
    return s[~s.index.duplicated(keep="last")].dropna()


def _fx_returns(currency: str, days: int, cache: dict[str, pd.Series | None]) -> pd.Series | None:
    """Daily return of one unit of ``currency`` measured in GBP (None if same / unavailable)."""
    cur = currency.upper()
    if cur in ("GBP", "GBX", "GBP_PENCE"):
        return None
    if cur in cache:
        return cache[cur]
    out: pd.Series | None = None
    spec = _FX_SYMBOL.get(cur)
    if spec:
        try:
            px = _closes(spec[0], days)
            gbp_per_unit = 1.0 / px if spec[1] else px
            out = gbp_per_unit.pct_change().dropna()
        except Exception as exc:  # noqa: BLE001
            logger.info("fx history %s unavailable: %s", cur, exc)
    cache[cur] = out
    return out


def currency_for(ticker: str, book: Book | None = None) -> tuple[str, str]:
    """(yfinance symbol, listing currency) for a held or not-held ticker."""
    from app.services.plays_service import listing_currency

    h = (book.holdings.get(_norm(ticker)) if book else None)
    if h:
        return h.yf_ticker, h.currency
    return _norm(ticker), listing_currency(_norm(ticker))


@dataclass
class ReturnSet:
    frame: pd.DataFrame  # aligned GBP daily returns, columns = tickers
    missing: dict[str, str]  # ticker → why it was dropped
    fx_note: str | None


def gbp_returns(tickers: Iterable[str], lookback: Lookback, book: Book | None = None,
                *, min_obs: int | None = None) -> ReturnSet:
    """Aligned daily GBP returns for ``tickers``; names without enough history are
    dropped (and listed in ``missing``) rather than shortening everyone's window."""
    days = LOOKBACK_DAYS.get(lookback, 380)
    min_obs = min_obs or (60 if lookback == "6mo" else 120)
    names = list(dict.fromkeys(_norm(t) for t in tickers if _norm(t)))
    fx_cache: dict[str, pd.Series | None] = {}
    missing: dict[str, str] = {}
    series: dict[str, pd.Series] = {}

    def fetch(t: str) -> tuple[str, pd.Series | None, str | None]:
        sym, _ = currency_for(t, book)
        try:
            return t, _closes(sym, days), None
        except Exception as exc:  # noqa: BLE001
            return t, None, str(exc)[:120]

    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(fetch, names))
    fx_failed: set[str] = set()
    for t, closes, err in fetched:
        if closes is None:
            missing[t] = f"no price history ({err})"
            continue
        r = qret.simple_returns(closes)
        _, cur = currency_for(t, book)
        fx = _fx_returns(cur, days, fx_cache)
        if fx is not None:
            r = qret.convert_returns(r, fx)
        elif cur not in ("GBP", "GBX", "GBP_PENCE"):
            fx_failed.add(cur)
        if len(r) < min_obs:
            missing[t] = f"only {len(r)} days of history (need {min_obs})"
            continue
        series[t] = r
    if not series:
        raise QuantDataError("no usable price history for " + ", ".join(names) + (f" — {missing}" if missing else ""))
    frame = pd.concat(series, axis=1, join="inner").dropna()
    if len(frame) < min_obs:
        # one young listing can collapse the window — drop the shortest until it fits
        for t in sorted(series, key=lambda k: len(series[k])):
            if len(frame) >= min_obs or len(series) <= 1:
                break
            missing[t] = f"history too short to align ({len(series[t])} days)"
            series.pop(t)
            frame = pd.concat(series, axis=1, join="inner").dropna()
    qret.align_returns({c: frame[c] for c in frame.columns}, min_obs=min_obs)
    fx_note = None
    if fx_failed:
        fx_note = f"FX history unavailable for {', '.join(sorted(fx_failed))} — those returns are in local currency"
    return ReturnSet(frame, missing, fx_note)


# ── Limits ───────────────────────────────────────────────────────────────────

def limits_check(
    rail: dict[str, float],
    *,
    weights_after: dict[str, float],
    orders: list[tuple[str, float]] | None = None,
    play_weight_after: float | None = None,
    cash_needed: dict[str, float] | None = None,
    cash_available: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Each rail with its limit, the post-trade value and ok/breach."""
    out: list[dict[str, Any]] = []
    cap = rail["max_position_weight"]
    for t, w in sorted(weights_after.items(), key=lambda kv: -kv[1])[:3]:
        out.append({"rail": f"single-name cap ({t})", "limit": pct(cap, 0), "value": pct(w), "ok": w <= cap + 1e-9})
    for t, n in orders or []:
        single = rail["max_single_order_notional"]
        if single:
            out.append({"rail": f"single order ({t})", "limit": gbp(single), "value": gbp(abs(n)),
                        "ok": abs(n) <= single + 1e-6, "fix": None if abs(n) <= single else f"split into {math.ceil(abs(n) / single)} orders"})
    if orders and rail["max_daily_notional"]:
        day = sum(abs(n) for _, n in orders)
        daily = rail["max_daily_notional"]
        out.append({"rail": "daily notional", "limit": gbp(daily), "value": gbp(day), "ok": day <= daily + 1e-6,
                    "fix": None if day <= daily else f"stage over {math.ceil(day / daily)} days"})
    if play_weight_after is not None:
        out.append({"rail": "per-play ceiling", "limit": pct(rail["max_play_pct"], 0), "value": pct(play_weight_after),
                    "ok": play_weight_after <= rail["max_play_pct"] + 1e-9})
    for acct, need in (cash_needed or {}).items():
        have = (cash_available or {}).get(acct, 0.0)
        if need > 0:
            out.append({"rail": f"free cash ({ACCOUNT_LABEL.get(acct, acct)})", "limit": gbp(have), "value": gbp(need),
                        "ok": need <= have + 1e-6})
    return out


def _breaches(checks: list[dict[str, Any]]) -> list[str]:
    return [f"{c['rail']}: {c['value']} vs limit {c['limit']}" + (f" — {c['fix']}" if c.get("fix") else "")
            for c in checks if not c["ok"]]


# ── size_position ────────────────────────────────────────────────────────────

SizeMethod = Literal["conviction", "vol_target", "kelly", "budget"]
Weighting = Literal["inverse_vol", "risk_parity", "hrp", "equal"]
Conviction = Literal["low", "medium", "high"]


def _split(method: Weighting, rs: ReturnSet, cov: covariance.CovEstimate) -> dict[str, float]:
    cols = list(rs.frame.columns)
    if len(cols) == 1:
        return {cols[0]: 1.0}
    if method == "equal":
        return {c: 1.0 / len(cols) for c in cols}
    if method == "risk_parity":
        return sizing.risk_parity_weights(cov.matrix)
    if method == "hrp":
        return clustering.hrp_weights(cov.matrix)
    return sizing.inverse_vol_weights(cov.vols().to_dict())


def size_position_report(
    db: Session,
    tickers: list[str],
    *,
    method: SizeMethod = "conviction",
    weighting: Weighting = "inverse_vol",
    conviction: Conviction = "medium",
    budget_gbp: float | None = None,
    target_risk: float | None = None,
    expected_return: float | None = None,
    kelly_fraction: float = 0.5,
    max_weight: float | None = None,
    max_loss_pct: float | None = None,
    account: Literal["invest", "stocks_isa"] = "invest",
    lookback: Lookback = "1y",
    book: Book | None = None,
) -> dict[str, Any]:
    from app.services.plays_service import CONVICTION_PCT, REFERENCE_VOL, VOL_FACTOR_BOUNDS, build_tranches

    book = book or load_book(db)
    rail = rails(db)
    rs = gbp_returns(tickers, lookback, book)
    cov = covariance.covariance(rs.frame)
    cov_ann = cov.annualised()
    split = _split(weighting, rs, cov)
    names = list(split)
    w_vec = np.array([split[n] for n in names])
    basket_vol = risk.portfolio_volatility(w_vec, cov_ann.loc[names, names].to_numpy())
    vols = cov.vols()
    cap = max_weight if max_weight is not None else (rail["max_play_pct"] if len(names) > 1 else rail["max_position_weight"])
    steps: list[str] = []
    assumptions: list[str] = []

    if method == "budget":
        if not budget_gbp:
            raise QuantDataError("method=budget needs budget_gbp")
        total_w = budget_gbp / book.total_gbp
        steps.append(f"Fixed budget {gbp(budget_gbp)} = {pct(total_w)} of the {gbp(book.total_gbp)} book.")
    elif method == "vol_target":
        tr = target_risk if target_risk is not None else 0.01
        total_w = sizing.vol_target_weight(basket_vol, tr)
        steps.append(f"Vol target: {pct(tr)} of book volatility ÷ {pct(basket_vol)} basket vol = {pct(total_w)} of book.")
    elif method == "kelly":
        if expected_return is not None:
            excess = expected_return - CASH_RATE
            assumptions.append(f"expected return {pct(expected_return)} vs {pct(CASH_RATE)} cash (your input)")
        else:
            sr = sizing.CONVICTION_SHARPE[conviction]
            excess = sr * basket_vol
            assumptions.append(f"{conviction} conviction → assumed Sharpe {sr:.2f} (excess return {pct(excess)}/yr) — a planning assumption, not a forecast")
        k = sizing.kelly_weight(excess, basket_vol, fraction=kelly_fraction, cap=None)
        total_w = k.weight
        steps.append(f"Kelly: full Kelly {pct(k.full_kelly, 0)} × {kelly_fraction:g} = {pct(k.fractional)} of book.")
    else:  # conviction — the plays rule, so a chat answer matches what draft_play would size
        base = CONVICTION_PCT[conviction]
        total_w, factor = sizing.vol_scaled_weight(base, basket_vol, REFERENCE_VOL, VOL_FACTOR_BOUNDS)
        steps.append(f"{conviction.capitalize()} conviction → {pct(base, 0)} of book, × {factor:.2f} for {pct(basket_vol, 0)} vol "
                     f"(calibrated to {pct(REFERENCE_VOL, 0)}) = {pct(total_w)}.")

    binding: list[str] = []
    if total_w > cap:
        binding.append(f"cap {pct(cap, 0)}")
        total_w = cap
    if max_loss_pct is not None:
        lw = sizing.max_weight_for_loss(max_loss_pct, STRESS_DRAWDOWN)
        if total_w > lw:
            binding.append(f"max loss {pct(max_loss_pct)} at a {pct(STRESS_DRAWDOWN, 0)} fall")
            total_w = lw
    if binding:
        steps.append(f"Capped by {', '.join(binding)} → {pct(total_w)}.")
    target_gbp = total_w * book.total_gbp

    held = {n: (book.holdings[n].value_gbp if n in book.holdings else 0.0) for n in names}
    legs = []
    for n in names:
        tgt = target_gbp * split[n]
        legs.append({"ticker": n, "weight_in_basket": r4(split[n]), "vol": r4(float(vols[n])),
                     "target_gbp": r2(tgt), "held_gbp": r2(held[n]), "new_gbp": r2(max(tgt - held[n], 0.0))})
    rc = risk.risk_contributions(w_vec, cov_ann.loc[names, names].to_numpy())
    for leg, p in zip(legs, rc.pct):
        leg["risk_share"] = r4(float(p))
    new_money = sum(l["new_gbp"] for l in legs)
    if sum(held.values()) > 0:
        steps.append(f"You already hold {gbp(sum(held.values()))} of these, so new money is {gbp(new_money)}.")
    free = book.cash_gbp.get(account, 0.0)
    basket_ret = qret.portfolio_returns(rs.frame, split)
    es = risk.expected_shortfall(basket_ret, 0.95)
    dd = risk.drawdown_stats(qret.equity_curve(basket_ret))
    stress = target_gbp * STRESS_DRAWDOWN

    after = {t: h.weight for t, h in book.holdings.items()}
    for l in legs:
        after[l["ticker"]] = after.get(l["ticker"], 0.0) + l["new_gbp"] / book.total_gbp
    orders = [(l["ticker"], l["new_gbp"]) for l in legs if l["new_gbp"] > 0]
    checks = limits_check(rail, weights_after=after, orders=orders,
                          play_weight_after=total_w if len(names) > 1 else None,
                          cash_needed={account: new_money}, cash_available=book.cash_gbp)
    tranches = build_tranches(new_money, {"max_daily_notional": rail["max_daily_notional"],
                                          "max_single_order_notional": rail["max_single_order_notional"]},
                              max_leg_weight=max(split.values()))
    label = names[0] if len(names) == 1 else f"{len(names)}-name basket"
    summary = [
        f"Size: {gbp(target_gbp)} ({pct(total_w)} of book) for the {label}; new money {gbp(new_money)}.",
        f"Basket vol {pct(basket_vol, 0)}/yr (GBP, {cov.n_obs} days, Ledoit-Wolf shrinkage {cov.shrinkage:.2f})." if cov.shrinkage is not None
        else f"Vol {pct(basket_vol, 0)}/yr (GBP, {cov.n_obs} days).",
        f"Downside: a {pct(STRESS_DRAWDOWN, 0)} fall costs {gbp(stress)} ({pct(stress / book.total_gbp)} of book); "
        f"worst-5% day averages {pct(es)}; max drawdown over the window {pct(dd.max_drawdown, 0)}.",
    ]
    if new_money > free:
        summary.append(f"Only {gbp(free)} free cash in {ACCOUNT_LABEL[account]} — fund it or stage it.")
    br = _breaches(checks)
    summary.append("Limits: all within rails." if not br else "Limits: " + "; ".join(br))
    if len(tranches) > 1:
        summary.append(f"Stage in {len(tranches)} tranches ({', '.join(f'{t['pct_of_budget']:.0%}' for t in tranches)}).")
    return {
        "ok": True,
        "summary": summary,
        "method": method, "weighting": weighting,
        "target_gbp": r2(target_gbp), "pct_of_book": r4(total_w), "new_money_gbp": r2(new_money),
        "basket_vol": r4(basket_vol), "es_95_daily": r4(es), "max_drawdown": r4(dd.max_drawdown),
        "stress_loss_gbp": r2(stress), "legs": legs, "tranches": tranches,
        "limits": checks, "steps": steps, "assumptions": assumptions,
        "data": {"days": cov.n_obs, "lookback": lookback, "shrinkage": r4(cov.shrinkage) if cov.shrinkage is not None else None,
                 "dropped": rs.missing or None, "fx": rs.fx_note},
    }


# ── analyse_risk ─────────────────────────────────────────────────────────────

RiskView = Literal["overview", "concentration", "contribution", "drawdown", "cvar", "beta", "correlation_clusters"]
Scope = Literal["book", "play", "holdings"]

FACTORS = {"market": "SPY", "tech": "QQQ", "small": "IWM", "rates": "TLT"}


def _scope_weights(db: Session, book: Book, scope: Scope, play_id: str | None, tickers: list[str] | None) -> tuple[dict[str, float], str]:
    """Book weights for the scoped holdings (fractions of the whole book)."""
    if scope == "holdings":
        if not tickers:
            raise QuantDataError("scope=holdings needs tickers")
        w = {t: book.holdings[t].weight for t in map(_norm, tickers) if t in book.holdings}
        missing = [t for t in map(_norm, tickers) if t not in book.holdings]
        if not w:
            raise QuantDataError(f"none of {', '.join(missing)} are held — use size_position or what_if for new names")
        return w, "selected holdings"
    if scope == "play":
        from app.services import book_service, plays_service

        play = plays_service.get_play(db, play_id or "")
        if not play:
            raise QuantDataError(f"no play with id {play_id!r} — list_plays shows the ids")
        ctx = plays_service.book_context(db)
        owned = book_service.owned_positions(db, play, ctx["positions"])
        w: dict[str, float] = {}
        for p in owned:
            t = _norm(p.get("ticker"))
            w[t] = w.get(t, 0.0) + (float(p.get("value") or 0.0) / book.total_gbp if book.total_gbp else 0.0)
        if not w:  # not built yet: use the basket at its planned size
            size = float((play.sizing or {}).get("target_pct_of_book") or 0.05)
            legs = play.legs or []
            ws = sum(float(l.get("target_weight") or 0.0) for l in legs) or 1.0
            w = {_norm(l["ticker"]): size * float(l.get("target_weight") or 0.0) / ws for l in legs}
            return w, f"play '{play.title}' (planned basket — nothing owned yet)"
        return w, f"play '{play.title}'"
    return book.weights(), "whole book"


def analyse_risk_report(
    db: Session,
    *,
    scope: Scope = "book",
    view: RiskView = "overview",
    play_id: str | None = None,
    tickers: list[str] | None = None,
    lookback: Lookback = "1y",
    book: Book | None = None,
) -> dict[str, Any]:
    book = book or load_book(db)
    rail = rails(db)
    weights, label = _scope_weights(db, book, scope, play_id, tickers)
    scope_total = sum(weights.values())
    out: dict[str, Any] = {"ok": True, "scope": label, "view": view, "summary": []}
    summary: list[str] = out["summary"]
    want = (lambda v: view == v or view == "overview")

    if want("concentration"):
        c = exposure.concentration(weights.values())
        top = sorted(weights.items(), key=lambda kv: -kv[1])[:5]
        over = [f"{t} {pct(w)}" for t, w in top if w > rail["max_position_weight"]]
        out["concentration"] = {"top": [{"ticker": t, "weight_of_book": r4(w)} for t, w in top],
                                "hhi": r4(c.hhi), "effective_n": round(c.effective_n, 1), "names": c.n,
                                "top1_share": r4(c.top1), "top5_share": r4(c.top5),
                                "cash_weight": r4(book.cash_total / book.total_gbp) if scope == "book" and book.total_gbp else None}
        summary.append(f"{c.n} names behave like {c.effective_n:.1f} equal-sized ones; top holding {top[0][0]} is "
                       f"{pct(top[0][1])} of book, top 5 are {pct(sum(w for _, w in top))}.")
        if over:
            summary.append(f"Above the {pct(rail['max_position_weight'], 0)} single-name cap: {', '.join(over)}.")
        if scope == "book":
            try:
                from app.services.book_service import compute_book

                themes = compute_book(db).get("theme_risk") or []
                out["concentration"]["themes"] = [{"title": t["title"], "weight": r4(t["weight"]),
                                                   "effective_weight": r4(t.get("effective_weight")), "warn": t["tone"] == "warn"}
                                                  for t in themes[:5]]
                warn = [t for t in themes if t["tone"] == "warn"]
                if warn:
                    summary.append("Theme concentration: " + "; ".join(t["text"] for t in warn[:3]) + ".")
            except Exception as exc:  # noqa: BLE001
                logger.info("theme concentration unavailable: %s", exc)

    needs_returns = any(want(v) for v in ("contribution", "drawdown", "cvar", "beta", "correlation_clusters"))
    if needs_returns:
        rs = gbp_returns(list(weights), lookback, book)
        cols = list(rs.frame.columns)
        w = {c: weights[c] for c in cols}
        covered = sum(w.values())
        out["data"] = {"days": len(rs.frame), "lookback": lookback, "covered_weight": r4(covered),
                       "dropped": rs.missing or None, "fx": rs.fx_note}
        if rs.missing:
            summary.append(f"Excluded for missing history: {', '.join(rs.missing)} ({pct(scope_total - covered)} of book).")
        port = qret.portfolio_returns(rs.frame, w)

        if want("contribution"):
            cov = covariance.covariance(rs.frame)
            ann = cov.annualised()
            wv = np.array([w[c] for c in cols])
            rc = risk.risk_contributions(wv, ann.to_numpy())
            rows = sorted(({"ticker": c, "weight_of_book": r4(w[c]), "vol": r4(float(cov.vols()[c])),
                            "risk_share": r4(float(p)), "risk_to_weight": r2(float(p) / (w[c] / covered)) if w[c] else None}
                           for c, p in zip(cols, rc.pct)), key=lambda r: -r["risk_share"])
            dr = exposure.diversification_ratio(wv, ann.to_numpy())
            out["contribution"] = {"book_vol": r4(rc.total_vol), "vol_of_invested": r4(rc.total_vol / covered) if covered else None,
                                   "diversification_ratio": r2(dr), "rows": rows[:12], "shrinkage": r4(cov.shrinkage)}
            top3 = rows[:3]
            summary.append(f"Book volatility {pct(rc.total_vol)}/yr in GBP (invested part {pct(rc.total_vol / covered)}). "
                           + ", ".join(f"{r['ticker']} drives {pct(r['risk_share'], 0)} of risk on {pct(r['weight_of_book'], 0)} of book" for r in top3) + ".")

        if want("cvar"):
            es = risk.expected_shortfall(port, 0.95)
            var = risk.value_at_risk(port, 0.95)
            tails = risk.tail_contributions(rs.frame, w, 0.95)
            rows = sorted(({"ticker": t, "es_contribution": r4(v), "share": r4(v / es) if es else None} for t, v in tails.items()),
                          key=lambda r: r["es_contribution"])
            out["cvar"] = {"var_95_daily": r4(var), "es_95_daily": r4(es), "es_95_daily_gbp": r2(es * book.total_gbp),
                           "rows": rows[:8]}
            summary.append(f"On the worst 5% of days the {'book' if scope == 'book' else 'selection'} lost {pct(-es)} on average "
                           f"(≈{gbp(-es * book.total_gbp)} a day at today's size); {rows[0]['ticker']} was {pct(rows[0]['share'], 0)} of that.")

        if want("drawdown"):
            dd = risk.drawdown_stats(qret.equity_curve(port / covered if covered else port))
            out["drawdown"] = {"max_drawdown": r4(dd.max_drawdown), "peak": str(dd.peak_date)[:10], "trough": str(dd.trough_date)[:10],
                               "recovered": str(dd.recovery_date)[:10] if dd.recovery_date is not None else None,
                               "longest_underwater_days": dd.max_duration_days, "current_drawdown": r4(dd.current_drawdown),
                               "calmar": r2(dd.calmar), "note": "today's weights replayed over the window (invested part)"}
            summary.append(f"Today's holdings replayed over {lookback}: max drawdown {pct(dd.max_drawdown)} "
                           f"({str(dd.peak_date)[:10]} → {str(dd.trough_date)[:10]}), now {pct(dd.current_drawdown)} from peak.")

        if want("beta"):
            try:
                fx = gbp_returns(list(FACTORS.values()), lookback, None)
                f = fx.frame
                factors = pd.DataFrame({"market": f["SPY"], "tech_tilt": f["QQQ"] - f["SPY"],
                                        "small_tilt": f["IWM"] - f["SPY"], "rates": f["TLT"]}).dropna()
                single = exposure.ols_exposure(port, f["SPY"].rename("SPY"))
                multi = exposure.ols_exposure(port, factors)
                out["beta"] = {"beta_spy": r2(single.betas["SPY"]), "r_squared": r2(single.r_squared),
                               "factors": {k: r2(v) for k, v in multi.betas.items()}, "factor_r_squared": r2(multi.r_squared),
                               "idiosyncratic_vol": r4(multi.residual_vol), "note": "betas are for the book (cash = 0), GBP returns"}
                summary.append(f"Beta to the S&P 500 (GBP) {single.betas['SPY']:.2f} (R² {single.r_squared:.2f}); "
                               f"tech tilt {multi.betas['tech_tilt']:+.2f}, small-cap tilt {multi.betas['small_tilt']:+.2f}, rates {multi.betas['rates']:+.2f}.")
            except QuantDataError as exc:
                out["beta"] = {"error": str(exc)}

        if want("correlation_clusters"):
            corr = covariance.covariance(rs.frame).correlation()
            groups = clustering.correlation_clusters(corr, min_corr=0.6)
            rows = []
            for g in groups:
                gw = sum(w[t] for t in g)
                if len(g) == 1:
                    continue
                sub = corr.loc[g, g].to_numpy()
                avg = (sub.sum() - len(g)) / (len(g) * (len(g) - 1))
                rows.append({"tickers": g, "weight_of_book": r4(gw), "avg_corr": r2(avg)})
            rows.sort(key=lambda r: -r["weight_of_book"])
            out["correlation_clusters"] = {"clusters": rows, "threshold": 0.6,
                                           "independent_bets": len(groups)}
            if rows:
                summary.append("Moves together (ρ≥0.6): " + "; ".join(
                    f"{', '.join(r['tickers'])} = {pct(r['weight_of_book'], 0)} of book (avg ρ {r['avg_corr']})" for r in rows[:3]) + ".")
            else:
                summary.append("No group of holdings has average correlation ≥ 0.6.")
    return out


# ── what_if ──────────────────────────────────────────────────────────────────

def _apply_trades(book: Book, trades: list[dict[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, float]]:
    """Post-trade GBP values per ticker, normalised trades, and cash change per account."""
    values = {t: h.value_gbp for t, h in book.holdings.items()}
    cash_delta: dict[str, float] = {}
    norm: list[dict[str, Any]] = []
    for tr in trades:
        t = _norm(tr.get("ticker"))
        side = str(tr.get("side") or "buy").lower()
        h = book.holdings.get(t)
        amt = tr.get("amount_gbp")
        frac = tr.get("fraction_of_holding")
        if amt is None and frac is not None:
            if not h:
                raise QuantDataError(f"{t} isn't held, so fraction_of_holding has nothing to apply to")
            amt = h.value_gbp * float(frac)
        if amt is None or float(amt) <= 0:
            raise QuantDataError(f"trade on {t} needs a positive amount_gbp or fraction_of_holding")
        amt = float(amt)
        acct = tr.get("account")
        if side == "sell":
            if not h:
                raise QuantDataError(f"can't sell {t}: not held")
            amt = min(amt, h.value_gbp)
            acct = acct or max(h.by_account, key=h.by_account.get)
            values[t] = values.get(t, 0.0) - amt
            cash_delta[acct] = cash_delta.get(acct, 0.0) + amt
        else:
            acct = acct or "invest"
            values[t] = values.get(t, 0.0) + amt
            cash_delta[acct] = cash_delta.get(acct, 0.0) - amt
        norm.append({"ticker": t, "side": side, "amount_gbp": round(amt, 2), "account": acct})
    return values, norm, cash_delta


def what_if_report(db: Session, trades: list[dict[str, Any]], *, lookback: Lookback = "1y",
                   book: Book | None = None) -> dict[str, Any]:
    book = book or load_book(db)
    rail = rails(db)
    values, norm, cash_delta = _apply_trades(book, trades)
    total = book.total_gbp  # trades move money between cash and holdings; the book total is unchanged
    before = {t: h.weight for t, h in book.holdings.items()}
    after = {t: v / total for t, v in values.items() if v > 0.5}
    touched = [n["ticker"] for n in norm]
    rs = gbp_returns(set(before) | set(after), lookback, book)
    cols = list(rs.frame.columns)
    cov = covariance.covariance(rs.frame)
    ann = cov.annualised().to_numpy()
    wb = np.array([before.get(c, 0.0) for c in cols])
    wa = np.array([after.get(c, 0.0) for c in cols])
    vb, va = risk.portfolio_volatility(wb, ann), risk.portfolio_volatility(wa, ann)
    esb = risk.expected_shortfall(qret.portfolio_returns(rs.frame, dict(zip(cols, wb))))
    esa = risk.expected_shortfall(qret.portfolio_returns(rs.frame, dict(zip(cols, wa))))
    cb, ca = exposure.concentration(before.values()), exposure.concentration(after.values() or [1.0])
    cash_after = {a: book.cash_gbp.get(a, 0.0) + cash_delta.get(a, 0.0) for a in set(book.cash_gbp) | set(cash_delta)}
    needed = {a: -d for a, d in cash_delta.items() if d < 0}
    checks = limits_check(rail, weights_after=after,
                          orders=[(n["ticker"], n["amount_gbp"]) for n in norm],
                          cash_needed=needed, cash_available={a: book.cash_gbp.get(a, 0.0) + max(cash_delta.get(a, 0.0), 0.0)
                                                              for a in needed})
    rows = [{"ticker": t, "weight_before": r4(before.get(t, 0.0)), "weight_after": r4(after.get(t, 0.0))} for t in dict.fromkeys(touched)]
    gains = []
    for n in norm:
        h = book.holdings.get(n["ticker"])
        if n["side"] == "sell" and h:
            acct = n["account"]
            val = h.by_account.get(acct, 0.0)
            g = (h.ppl_by_account.get(acct, 0.0) / val * n["amount_gbp"]) if val else 0.0
            gains.append({"ticker": n["ticker"], "account": acct, "realised_gain_gbp": r2(g),
                          "taxable": acct == "invest"})
    summary = [
        "Trades: " + "; ".join(f"{n['side']} {gbp(n['amount_gbp'])} {n['ticker']} ({ACCOUNT_LABEL.get(n['account'], n['account'])})" for n in norm) + ".",
        "Weights: " + "; ".join(f"{r['ticker']} {pct(r['weight_before'])} → {pct(r['weight_after'])}" for r in rows) + ".",
        f"Book vol {pct(vb)} → {pct(va)}/yr; worst-5%-day loss {pct(esb)} → {pct(esa)}; "
        f"effective names {cb.effective_n:.1f} → {ca.effective_n:.1f}.",
        "Cash after: " + ", ".join(f"{ACCOUNT_LABEL.get(a, a)} {gbp(v)}" for a, v in sorted(cash_after.items())) + ".",
    ]
    taxable = [g for g in gains if g["taxable"] and g["realised_gain_gbp"]]
    if taxable:
        summary.append("Realises " + ", ".join(f"{gbp(g['realised_gain_gbp'])} {'gain' if g['realised_gain_gbp'] > 0 else 'loss'} on {g['ticker']}" for g in taxable)
                       + " in Invest (CGT — see placement).")
    br = _breaches(checks)
    summary.append("Limits: all within rails." if not br else "Limits: " + "; ".join(br))
    return {
        "ok": True, "summary": summary, "trades": norm, "weights": rows,
        "book_vol": {"before": r4(vb), "after": r4(va)}, "es_95_daily": {"before": r4(esb), "after": r4(esa)},
        "effective_n": {"before": round(cb.effective_n, 1), "after": round(ca.effective_n, 1)},
        "top_after": [{"ticker": t, "weight": r4(w)} for t, w in sorted(after.items(), key=lambda kv: -kv[1])[:5]],
        "cash_after_gbp": {a: r2(v) for a, v in cash_after.items()}, "realised": gains, "limits": checks,
        "data": {"days": cov.n_obs, "dropped": rs.missing or None, "fx": rs.fx_note},
    }


# ── rebalance_plan ───────────────────────────────────────────────────────────

TargetMethod = Literal["caps", "equal", "inverse_vol", "risk_parity", "hrp", "play_legs"]


def rebalance_report(
    db: Session,
    *,
    scope: Literal["book", "play"] = "book",
    play_id: str | None = None,
    target: TargetMethod = "caps",
    targets: dict[str, float] | None = None,
    abs_band: float = 0.05,
    rel_band: float = 0.25,
    trade_to: rebalance.TradeTo = "target",
    turnover_budget: float = 0.10,
    use_cash: bool = False,
    lookback: Lookback = "1y",
    book: Book | None = None,
) -> dict[str, Any]:
    book = book or load_book(db)
    rail = rails(db)
    current, label = _scope_weights(db, book, scope, play_id, None)
    invested = sum(current.values())
    notes: list[str] = []
    if targets:
        tgt = {_norm(k): float(v) for k, v in targets.items()}
        method_note = "your target weights"
    elif target == "caps":
        cap = rail["max_position_weight"]
        tgt = {t: min(w, cap) for t, w in current.items()}
        method_note = f"trim anything above the {pct(cap, 0)} single-name cap"
    elif target == "play_legs":
        from app.services import plays_service

        play = plays_service.get_play(db, play_id or "")
        if not play:
            raise QuantDataError("target=play_legs needs a valid play_id")
        ws = sum(float(l.get("target_weight") or 0.0) for l in play.legs or []) or 1.0
        size = invested if invested > 0 else float((play.sizing or {}).get("target_pct_of_book") or 0.0)
        tgt = {_norm(l["ticker"]): size * float(l.get("target_weight") or 0.0) / ws for l in play.legs or []}
        method_note = f"the play's leg weights at its current size ({pct(size)})"
    else:
        rs = gbp_returns(list(current), lookback, book)
        cov = covariance.covariance(rs.frame)
        split = _split({"equal": "equal", "inverse_vol": "inverse_vol", "risk_parity": "risk_parity", "hrp": "hrp"}[target], rs, cov)
        tgt = {t: invested * w for t, w in split.items()}
        for t in rs.missing:
            tgt[t] = current.get(t, 0.0)  # no data → leave alone
        method_note = f"{target.replace('_', ' ')} weights over the {pct(invested, 0)} invested"
        if rs.missing:
            notes.append(f"Left as-is (no history): {', '.join(rs.missing)}.")
    cash = book.cash_total if use_cash else 0.0
    plan = rebalance.rebalance_plan(current, tgt, book.total_gbp, abs_band=abs_band, rel_band=rel_band, trade_to=trade_to,
                                    turnover_budget=turnover_budget, cash_available_gbp=cash)
    trades = [{"ticker": t.ticker, "side": "sell" if t.notional_gbp < 0 else "buy", "amount_gbp": r2(abs(t.notional_gbp)),
               "weight": r4(t.current_weight), "target": r4(t.target_weight), "band": r4(t.band),
               "account": (max(book.holdings[t.ticker].by_account, key=book.holdings[t.ticker].by_account.get)
                           if t.ticker in book.holdings else "invest"),
               "note": t.note or None} for t in plan.trades]
    checks = limits_check(rail, weights_after={k: v for k, v in plan.after_weights.items() if v > 0},
                          orders=[(t["ticker"], t["amount_gbp"]) for t in trades])
    summary = [f"Target: {method_note}; bands {pct(abs_band, 0)} / {pct(rel_band, 0)} of target ({trade_to})."]
    if not trades:
        summary.append("Nothing outside its band — no trades needed.")
    else:
        summary.append("Trades: " + "; ".join(f"{t['side']} {gbp(t['amount_gbp'])} {t['ticker']}" for t in trades) + ".")
        summary.append(f"Turnover {gbp(plan.turnover_gbp)} ({pct(plan.turnover_gbp / book.total_gbp)} of book) vs budget "
                       f"{gbp(plan.turnover_budget_gbp)}" + (" — budget binding, rest deferred." if plan.budget_binding else "."))
    if plan.deferred:
        summary.append(f"Out of band but deferred: {', '.join(plan.deferred)}.")
    if plan.cash_binding:
        summary.append("Buys were scaled down to sale proceeds" + (" plus free cash." if use_cash else " (use_cash=false)."))
    sells_invest = [t for t in trades if t["side"] == "sell" and t["account"] == "invest"]
    if sells_invest:
        summary.append("Sells in Invest realise gains — run placement for the CGT view.")
    br = _breaches(checks)
    summary.append("Limits after: all within rails." if not br else "Limits after: " + "; ".join(br))
    return {"ok": True, "scope": label, "summary": summary + notes, "trades": trades,
            "turnover_gbp": r2(plan.turnover_gbp), "turnover_budget_gbp": r2(plan.turnover_budget_gbp),
            "in_band": plan.in_band, "deferred": plan.deferred, "limits": checks}


# ── deploy_cash ──────────────────────────────────────────────────────────────

def deploy_cash_report(
    db: Session,
    amount_gbp: float,
    *,
    account: Literal["invest", "stocks_isa"] = "invest",
    tickers: list[str] | None = None,
    weighting: Weighting = "inverse_vol",
    months: int = 6,
    expected_return: float | None = None,
    lookback: Lookback = "1y",
    book: Book | None = None,
) -> dict[str, Any]:
    book = book or load_book(db)
    rail = rails(db)
    free = book.cash_gbp.get(account, 0.0)
    notes: list[str] = []
    if amount_gbp <= 0:
        raise QuantDataError("amount_gbp must be positive")
    cap = rail["max_position_weight"]
    if tickers:
        candidates = [_norm(t) for t in tickers]
        source = "your list"
    else:
        # Default: top up existing holdings that have room under the cap, skipping
        # the names already driving concentration (above half the cap).
        candidates = [t for t, h in book.holdings.items() if h.weight < cap * 0.5][:10]
        source = "existing holdings with room under the single-name cap"
    rs = gbp_returns(candidates, lookback, book)
    cov = covariance.covariance(rs.frame)
    split = _split(weighting, rs, cov)
    names = list(split)
    allocation = []
    for n in names:
        amt = amount_gbp * split[n]
        room = max(cap * book.total_gbp - (book.holdings[n].value_gbp if n in book.holdings else 0.0), 0.0)
        if amt > room:
            notes.append(f"{n} capped at its room under the {pct(cap, 0)} cap ({gbp(room)}).")
            amt = room
        allocation.append({"ticker": n, "amount_gbp": r2(amt), "weight_in_deploy": r4(split[n]), "vol": r4(float(cov.vols()[n]))})
    allocated = sum(a["amount_gbp"] for a in allocation)
    basket_vol = risk.portfolio_volatility(np.array([split[n] for n in names]), cov.annualised().loc[names, names].to_numpy())
    er = expected_return if expected_return is not None else EQUITY_RETURN
    cmp = deploy.compare_lump_vs_staged(amount_gbp, max(months, 1), expected_return=er, vol=basket_vol, cash_rate=CASH_RATE)
    size_vs_book = amount_gbp / book.total_gbp if book.total_gbp else 0.0
    recommend = "staged" if (size_vs_book >= 0.10 or basket_vol >= 0.35) else "lump"
    why = (f"the amount is {pct(size_vs_book, 0)} of your book" if size_vs_book >= 0.10 else
           f"the basket is volatile ({pct(basket_vol, 0)})" if basket_vol >= 0.35 else
           f"it's {pct(size_vs_book, 0)} of the book into a {pct(basket_vol, 0)}-vol basket — expected-value favours investing now")
    schedule = deploy.staged_schedule(allocated, max(min(months, 6), 1), spacing_weeks=max(round(months * 4.3 / max(min(months, 6), 1)), 1))
    orders = [(a["ticker"], a["amount_gbp"] if recommend == "lump" else a["amount_gbp"] / len(schedule)) for a in allocation]
    after = {t: h.weight for t, h in book.holdings.items()}
    for a in allocation:
        after[a["ticker"]] = after.get(a["ticker"], 0.0) + a["amount_gbp"] / book.total_gbp
    checks = limits_check(rail, weights_after=after, orders=orders,
                          cash_needed={account: amount_gbp}, cash_available=book.cash_gbp)
    summary = [
        f"Deploy {gbp(amount_gbp)} from {ACCOUNT_LABEL[account]} ({gbp(free)} free) across {len(allocation)} names ({source}, {weighting.replace('_', ' ')}): "
        + ", ".join(f"{a['ticker']} {gbp(a['amount_gbp'])}" for a in allocation[:8]) + ".",
        f"Lump sum vs {months}-month staging (assumes {pct(er, 0)}/yr equities vs {pct(CASH_RATE, 0)} cash, {pct(basket_vol, 0)} vol): "
        f"lump sum ends ahead {pct(cmp.prob_lump_wins, 0)} of the time, +{gbp(cmp.expected_lump_gbp - cmp.expected_staged_gbp)} on average; "
        f"a 20% fall right after investing costs {gbp(cmp.lump_worst_drop_gbp)} lump vs {gbp(cmp.staged_worst_drop_gbp)} staged.",
        f"Suggest: {recommend} — {why}.",
    ]
    if amount_gbp > free:
        summary.append(f"Only {gbp(free)} is free in {ACCOUNT_LABEL[account]}.")
    br = _breaches(checks)
    summary.append("Limits: all within rails." if not br else "Limits: " + "; ".join(br))
    return {"ok": True, "summary": summary + notes, "allocation": allocation, "recommendation": recommend,
            "schedule": schedule if recommend == "staged" else None,
            "lump_vs_staged": {"months": months, "prob_lump_wins": r4(cmp.prob_lump_wins),
                               "expected_lump_gbp": r2(cmp.expected_lump_gbp), "expected_staged_gbp": r2(cmp.expected_staged_gbp),
                               "p05_lump_gbp": r2(cmp.lump_p05_gbp), "p05_staged_gbp": r2(cmp.staged_p05_gbp),
                               "evidence": "Vanguard (2012, 2023): lump sum beat 12-month DCA in ~2/3 of historical periods"},
            "basket_vol": r4(basket_vol), "limits": checks,
            "data": {"days": cov.n_obs, "dropped": rs.missing or None, "fx": rs.fx_note}}


# ── placement ────────────────────────────────────────────────────────────────

def isa_subscriptions_this_year(db: Session, today: date | None = None) -> tuple[float | None, str]:
    """Net ISA subscriptions this tax year from the stored transaction feed.

    Trading 212's Stocks ISA is flexible: withdrawals can be replaced in the same
    tax year without using allowance, so withdrawals net off. Returns (None, why)
    when no cashflow history has been synced.
    """
    from app.models.entities import CashflowEvent
    from app.services.fx import get_fx_rate
    from sqlalchemy import select

    start, _ = uk_tax.tax_year(today or date.today())
    rows = db.execute(select(CashflowEvent).where(CashflowEvent.account_kind == "stocks_isa")).scalars().all()
    if not rows:
        return None, "no ISA cashflow history synced"
    total = 0.0
    for r in rows:
        if r.occurred_at.date() < start or r.type not in ("DEPOSIT", "WITHDRAW", "TRANSFER"):
            continue
        total += float(r.amount or 0.0) * get_fx_rate(r.currency or "GBP", "GBP")
    return max(total, 0.0), "net deposits + transfers into the ISA since 6 April (flexible ISA)"


def placement_report(
    db: Session,
    trades: list[dict[str, Any]],
    *,
    tax_band: uk_tax.Band = "higher",
    isa_subscribed_gbp: float | None = None,
    gains_realised_gbp: float = 0.0,
    lookback: Lookback = "1y",
    book: Book | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    from app.services.fundamentals_service import get_fundamentals_snapshot

    book = book or load_book(db)
    today = today or date.today()
    notes: list[str] = []
    if isa_subscribed_gbp is None:
        sub, how = isa_subscriptions_this_year(db, today)
        if sub is None:
            notes.append(f"ISA allowance used is unknown ({how}) — assumed £0; pass isa_subscribed_gbp if you know it.")
            sub = 0.0
        else:
            notes.append(f"ISA subscriptions this year: {gbp(sub)} ({how}).")
    else:
        sub = isa_subscribed_gbp
    allowance_left = uk_tax.isa_headroom(sub)
    isa_cash = book.cash_gbp.get("stocks_isa", 0.0)
    buys = [t for t in trades if str(t.get("side") or "buy").lower() == "buy"]
    sells = [t for t in trades if str(t.get("side") or "").lower() == "sell"]
    items_out: list[dict[str, Any]] = []
    vols: dict[str, float] = {}
    if buys:
        try:
            rs = gbp_returns([b["ticker"] for b in buys], lookback, book, min_obs=60)
            vols = covariance.covariance(rs.frame, "sample", min_obs=60).vols().to_dict() if len(rs.frame.columns) else {}
        except QuantDataError as exc:
            notes.append(f"volatility unavailable ({exc}); growth proxy defaults to 10%.")
        specs = []
        for b in buys:
            t = _norm(b["ticker"])
            yld = b.get("dividend_yield")
            if yld is None:
                try:
                    f = get_fundamentals_snapshot(t) or {}
                    # yfinance >= 0.2.5x reports dividendYield in percent (0.44 = 0.44%)
                    yld = float(f.get("dividend_yield") or 0.0) / 100.0
                except Exception:  # noqa: BLE001
                    yld = 0.0
            v = vols.get(t, 0.30)
            specs.append({"ticker": t, "amount_gbp": float(b["amount_gbp"]), "dividend_yield": yld,
                          # higher vol → fatter right tail of gains; a crude, monotone proxy
                          "expected_growth": max(0.05, min(0.5, v * 0.5)),
                          "isa_only": bool(b.get("isa_only")), "speculative": bool(b.get("speculative"))})
        res = uk_tax.place_buys(specs, isa_cash_gbp=isa_cash, allowance_left_gbp=allowance_left, band=tax_band)
        for it in res.items:
            items_out.append({"ticker": it.ticker, "side": "buy", "amount_gbp": r2(it.amount_gbp), "account": it.account,
                              "isa_gbp": r2(it.isa_gbp), "invest_gbp": r2(it.invest_gbp),
                              "gia_tax_drag_gbp_yr": r2(it.annual_tax_drag_gbp), "reason": it.reason})
        notes.extend(res.notes)
    gains_so_far = gains_realised_gbp
    for s in sells:
        t = _norm(s["ticker"])
        h = book.holdings.get(t)
        if not h:
            items_out.append({"ticker": t, "side": "sell", "error": "not held"})
            continue
        amt = float(s.get("amount_gbp") or (h.value_gbp * float(s.get("fraction_of_holding") or 1.0)))
        gia_val, isa_val = h.by_account.get("invest", 0.0), h.by_account.get("stocks_isa", 0.0)
        gia_gain_frac = (h.ppl_by_account.get("invest", 0.0) / gia_val) if gia_val else 0.0
        if gia_val > 0 and gia_gain_frac <= 0:
            acct, why = "invest", f"Invest holding is at a {pct(gia_gain_frac)} loss — selling there books a loss to offset gains"
        elif gia_val > 0 and uk_tax.tax_free_sale_gbp(gia_val, gia_gain_frac * gia_val, other_gains_gbp=gains_so_far) >= min(amt, gia_val):
            acct, why = "invest", "gain fits inside the remaining CGT exemption — uses it, keeps ISA shelter intact"
        elif isa_val >= amt:
            acct, why = "stocks_isa", "selling in the ISA is tax-free; the Invest lot would realise a taxable gain"
        else:
            acct, why = ("invest" if gia_val >= isa_val else "stocks_isa"), "split across accounts as held"
        gain = gia_gain_frac * min(amt, gia_val) if acct == "invest" else 0.0
        tax = uk_tax.cgt_due(gain, other_gains_gbp=gains_so_far, band=tax_band) if acct == "invest" else 0.0
        gains_so_far += gain
        items_out.append({"ticker": t, "side": "sell", "amount_gbp": r2(amt), "account": acct, "realised_gain_gbp": r2(gain),
                          "cgt_gbp": r2(tax), "reason": why})
    # Bed & ISA room: biggest Invest holdings with gains, up to the CGT exemption.
    bed = []
    room_cgt = max(uk_tax.CGT_ANNUAL_EXEMPT_GBP - max(gains_so_far, 0.0), 0.0)
    for t, h in book.holdings.items():
        gv = h.by_account.get("invest", 0.0)
        if gv <= 0:
            continue
        g = h.ppl_by_account.get("invest", 0.0)
        free_sale = uk_tax.tax_free_sale_gbp(gv, g, other_gains_gbp=gains_so_far)
        bed.append({"ticker": t, "invest_value_gbp": r2(gv), "unrealised_gain_gbp": r2(g),
                    "tax_free_sale_gbp": r2(min(free_sale, allowance_left))})
    bed.sort(key=lambda b: -(b["tax_free_sale_gbp"] or 0))
    days = uk_tax.days_to_year_end(today)
    summary = [f"Tax year {uk_tax.tax_year_label(today)}: {gbp(allowance_left)} ISA allowance left, {days} days to 5 April; "
               f"ISA free cash {gbp(isa_cash)}; CGT exemption left after these trades {gbp(room_cgt)} "
               f"(assumes {gbp(gains_realised_gbp)} realised earlier this year)."]
    for it in items_out:
        if it.get("error"):
            summary.append(f"{it['ticker']}: {it['error']}.")
        elif it["side"] == "buy":
            where = {"stocks_isa": "ISA", "invest": "Invest"}.get(it["account"], f"split ISA {gbp(it['isa_gbp'])} / Invest {gbp(it['invest_gbp'])}")
            summary.append(f"Buy {gbp(it['amount_gbp'])} {it['ticker']} in {where} — {it['reason']}.")
        else:
            summary.append(f"Sell {gbp(it['amount_gbp'])} {it['ticker']} from {ACCOUNT_LABEL.get(it['account'], it['account'])}: "
                           f"gain {gbp(it['realised_gain_gbp'])}, CGT ≈{gbp(it['cgt_gbp'])} — {it['reason']}.")
    if bed and bed[0]["tax_free_sale_gbp"] and allowance_left > 0:
        summary.append(f"Bed & ISA: {bed[0]['ticker']} — up to {gbp(bed[0]['tax_free_sale_gbp'])} can move to the ISA with no CGT this year.")
    summary.append("Estimates, not tax advice; you move money between accounts in the Trading 212 app.")
    return {"ok": True, "summary": summary + notes, "items": items_out,
            "isa": {"allowance_left_gbp": r2(allowance_left), "subscribed_gbp": r2(sub), "free_cash_gbp": r2(isa_cash),
                    "days_to_year_end": days},
            "cgt": {"exempt_left_gbp": r2(room_cgt), "band": tax_band, "rate": uk_tax.CGT_RATE[tax_band]},
            "bed_and_isa": bed[:5]}
