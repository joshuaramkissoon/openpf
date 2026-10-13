"""Archie's track record: every proposal tracked forward, whatever happened to it.

Each TradeIntent gets a ``ProposalOutcome`` holding its reference price at
proposal time and marks at 1d / 1w / 1m / 3m (plus at close, for a taken buy
that was later sold) against a benchmark (SPY by default). Rejected and expired
proposals are marked too: that's the counterfactual "if you'd taken it".

Returns are *directional* and in the instrument's own currency: a buy scores
the price change, a sell scores the inverse (the drop it avoided). "Excess" is
the directional return relative to the benchmark over the same window, so a
hit means the call beat simply holding the index.

Prices come from ``market_data.fetch_history`` (the same source the marketdata
MCP uses). Pure computation lives in small functions so tests can inject a
price history.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import ProposalOutcome, TradeIntent
from app.services import intent_category
from app.services.config_store import ConfigStore

logger = logging.getLogger(__name__)

HORIZONS: dict[str, int] = {"1d": 1, "1w": 7, "1m": 30, "3m": 91}
HORIZON_LABELS = {"1d": "1 day", "1w": "1 week", "1m": "1 month", "3m": "3 months", "close": "at close"}
CONFIG_KEY = "track_record"
DEFAULT_CONFIG = {"benchmark": "SPY"}
# How far back the daily job keeps tracking (3m horizon + slack).
_TRACK_WINDOW_DAYS = 120

History = list[tuple[date, float]]
HistoryFn = Callable[[str, int], History]


# ── prices ───────────────────────────────────────────────────────────────────

def _default_history(ticker: str, lookback_days: int) -> History:
    from app.services.market_data import fetch_history

    frame = fetch_history(ticker, lookback_days=lookback_days)
    out: History = []
    for d, c in zip(frame["date"], frame["close"]):
        try:
            price = float(c)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        out.append((d.date() if isinstance(d, datetime) else d, price))
    return out


class _PriceBook:
    """Caches one history per ticker for a marking pass."""

    def __init__(self, history_fn: HistoryFn, lookback_days: int):
        self._fn = history_fn
        self._days = lookback_days
        self._cache: dict[str, History | None] = {}

    def get(self, ticker: str | None) -> History | None:
        if not ticker:
            return None
        if ticker not in self._cache:
            try:
                rows = sorted(self._fn(ticker, self._days))
                self._cache[ticker] = rows or None
            except Exception as exc:  # noqa: BLE001 — pricing is best-effort per ticker
                logger.info("track record: no history for %s (%s)", ticker, exc)
                self._cache[ticker] = None
        return self._cache[ticker]


def close_on_or_before(history: History, day: date) -> tuple[date, float] | None:
    best = None
    for d, p in history:
        if d <= day:
            best = (d, p)
        else:
            break
    return best


def close_on_or_after(history: History, day: date) -> tuple[date, float] | None:
    for d, p in history:
        if d >= day:
            return d, p
    return None


def _market_ticker(intent: TradeIntent) -> str | None:
    meta = intent.meta or {}
    if meta.get("market_ticker"):
        return str(meta["market_ticker"])
    try:
        from app.services.portfolio_service import market_yfinance_ticker

        return market_yfinance_ticker(intent.instrument_code or intent.symbol, None, meta.get("currency"))
    except Exception:  # noqa: BLE001
        return intent.symbol or None


# ── outcome rows ─────────────────────────────────────────────────────────────

def fate_of(intent: TradeIntent) -> str:
    status = intent.status
    if status == "executed":
        return "taken"
    if status == "rejected":
        return "rejected"
    if status == "expired":
        reason = (intent.failure_reason or "").lower()
        return "superseded" if "supersed" in reason else "expired"
    if status == "failed":
        return "failed"
    return "open"


def _sign(side: str) -> int:
    return -1 if side == "sell" else 1


def compute_mark(side: str, ref: float, price: float, bench_ref: float | None, bench_price: float | None) -> dict[str, Any]:
    ret = price / ref - 1.0
    bench_ret = (bench_price / bench_ref - 1.0) if bench_ref and bench_price else None
    sign = _sign(side)
    out: dict[str, Any] = {"price": round(price, 6), "ret": round(ret, 6), "dir_ret": round(sign * ret, 6)}
    if bench_ret is not None:
        out["bench_ret"] = round(bench_ret, 6)
        out["excess"] = round(sign * (ret - bench_ret), 6)
    return out


def _closing_sell(db: Session, intent: TradeIntent) -> TradeIntent | None:
    """A later executed sell of the same instrument closes a taken buy."""
    if intent.side != "buy" or intent.status != "executed" or not intent.executed_at:
        return None
    return db.execute(
        select(TradeIntent)
        .where(
            TradeIntent.status == "executed",
            TradeIntent.side == "sell",
            TradeIntent.instrument_code == intent.instrument_code,
            TradeIntent.executed_at > intent.executed_at,
        )
        .order_by(TradeIntent.executed_at)
        .limit(1)
    ).scalar_one_or_none()


def mark_intent(
    db: Session,
    intent: TradeIntent,
    prices: _PriceBook,
    *,
    benchmark: str,
    today: date | None = None,
) -> ProposalOutcome:
    today = today or date.today()
    meta = intent.meta or {}
    row = db.execute(select(ProposalOutcome).where(ProposalOutcome.intent_id == intent.id)).scalar_one_or_none()
    if row is None:
        row = ProposalOutcome(intent_id=intent.id, proposed_at=intent.created_at, symbol=intent.symbol,
                              side=intent.side, marks={})
        db.add(row)
    row.proposed_at = intent.created_at
    row.category = intent_category.of_intent(intent)
    row.fate = fate_of(intent)
    row.symbol = intent.symbol
    row.instrument_code = intent.instrument_code or ""
    row.side = intent.side
    row.notional_gbp = float(intent.estimated_notional or 0.0)
    row.approved_by = getattr(intent, "approved_by", None)
    row.benchmark = benchmark
    row.market_ticker = row.market_ticker or _market_ticker(intent)

    proposed_day = intent.created_at.date()
    history = prices.get(row.market_ticker)
    bench = prices.get(benchmark)

    if row.ref_price is None:
        # Prefer the price the proposal was sized on (last close at proposal
        # time, same source) unless the order carried its own limit/stop price.
        ref = meta.get("reference_price") if intent.limit_price is None and intent.stop_price is None else None
        try:
            ref = float(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref = None
        if (ref is None or ref <= 0) and history:
            hit = close_on_or_before(history, proposed_day)
            ref = hit[1] if hit else None
        if ref and ref > 0:
            row.ref_price = ref
            row.currency = str(meta.get("currency") or "") or None
            gbp = meta.get("price_gbp")
            if gbp:
                row.ref_price_gbp = float(gbp)
            elif intent.quantity:
                row.ref_price_gbp = round(float(intent.estimated_notional or 0.0) / float(intent.quantity), 6) or None
    if row.bench_ref_price is None and bench:
        hit = close_on_or_before(bench, proposed_day)
        row.bench_ref_price = hit[1] if hit else None

    if not row.ref_price or not history:
        row.state = "unpriced"
        row.note = "no price history for this instrument" if not history else "no reference price"
        return row

    marks = dict(row.marks or {})
    for key, days in HORIZONS.items():
        if key in marks:
            continue
        target = proposed_day + timedelta(days=days)
        if target > today:
            continue
        hit = close_on_or_after(history, target)
        if not hit:
            continue
        b_hit = close_on_or_after(bench, target) if bench else None
        mark = compute_mark(intent.side, row.ref_price, hit[1], row.bench_ref_price, b_hit[1] if b_hit else None)
        mark["date"] = hit[0].isoformat()
        marks[key] = mark

    if "close" not in marks:
        closer = _closing_sell(db, intent)
        if closer is not None:
            closed_day = closer.executed_at.date()
            price = float(closer.execution_price or 0.0)
            if price <= 0:
                hit = close_on_or_after(history, closed_day) or close_on_or_before(history, closed_day)
                price = hit[1] if hit else 0.0
            if price > 0:
                b_hit = (close_on_or_after(bench, closed_day) or close_on_or_before(bench, closed_day)) if bench else None
                mark = compute_mark(intent.side, row.ref_price, price, row.bench_ref_price, b_hit[1] if b_hit else None)
                mark["date"] = closed_day.isoformat()
                marks["close"] = mark

    row.marks = marks
    row.state = "complete" if "3m" in marks or "close" in marks else "tracking"
    row.note = None
    return row


def get_config(db: Session) -> dict[str, Any]:
    return {**DEFAULT_CONFIG, **(ConfigStore(db).get(CONFIG_KEY, DEFAULT_CONFIG) or {})}


def set_config(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    bench = str(patch.get("benchmark") or "").strip().upper()[:16]
    cfg = get_config(db)
    if bench:
        cfg["benchmark"] = bench
    return ConfigStore(db).set(CONFIG_KEY, cfg)


def mark_outcomes(
    db: Session,
    *,
    history_fn: HistoryFn | None = None,
    today: date | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """Create/refresh outcome rows. The daily job runs this; ``backfill`` widens
    the window to every intent ever proposed."""
    today = today or date.today()
    cfg = get_config(db)
    benchmark = str(cfg.get("benchmark") or "SPY").upper()
    # Held-back asks were never proposals (a limit stopped them).
    q = select(TradeIntent).where(TradeIntent.status != "held").order_by(TradeIntent.created_at)
    if not backfill:
        q = q.where(TradeIntent.created_at >= datetime.combine(today - timedelta(days=_TRACK_WINDOW_DAYS), datetime.min.time()))
    intents = list(db.execute(q).scalars().all())
    done = {
        r.intent_id: r
        for r in db.execute(select(ProposalOutcome)).scalars().all()
    }
    oldest = min((i.created_at.date() for i in intents), default=today)
    prices = _PriceBook(history_fn or _default_history, max(30, (today - oldest).days + 15))
    counts = {"seen": 0, "marked": 0, "unpriced": 0, "complete": 0}
    for intent in intents:
        # Open proposals get a row too, so the reference price is captured close
        # to proposal time; they join the stats once they resolve.
        existing = done.get(intent.id)
        if existing is not None and existing.state == "complete" and existing.fate == fate_of(intent):
            counts["complete"] += 1
            continue
        counts["seen"] += 1
        row = mark_intent(db, intent, prices, benchmark=benchmark, today=today)
        if row.state == "unpriced":
            counts["unpriced"] += 1
        else:
            counts["marked"] += 1
    db.commit()
    return counts


# ── summary ──────────────────────────────────────────────────────────────────

def caveat(n: int) -> str:
    if n == 0:
        return "Nothing marked yet."
    if n < 10:
        return f"n={n} — too early to tell."
    if n < 30:
        return f"n={n} — an early read; treat with caution."
    return f"n={n} — a meaningful sample, still not proof."


def latest_mark(marks: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for key in ("close", "3m", "1m", "1w", "1d"):
        if key in (marks or {}):
            return key, marks[key]
    return None


def _stats(rows: list[ProposalOutcome], horizon: str | None) -> dict[str, Any]:
    picks: list[tuple[ProposalOutcome, dict[str, Any]]] = []
    for r in rows:
        if horizon == "latest":
            lm = latest_mark(r.marks)
            if lm:
                picks.append((r, lm[1]))
        elif horizon and horizon in (r.marks or {}):
            picks.append((r, r.marks[horizon]))
    with_bench = [m for _, m in picks if m.get("excess") is not None]
    hits = sum(1 for m in with_bench if m["excess"] > 0)
    return {
        "n": len(picks),
        "n_vs_benchmark": len(with_bench),
        "hits": hits,
        "hit_rate": (hits / len(with_bench)) if with_bench else None,
        "avg_return": statistics.fmean(m["dir_ret"] for _, m in picks) if picks else None,
        "avg_excess": statistics.fmean(m["excess"] for m in with_bench) if with_bench else None,
        "caveat": caveat(len(with_bench)),
    }


def _pnl(rows: list[ProposalOutcome]) -> dict[str, Any]:
    """£ outcome at each proposal's latest mark: the move on its notional, and
    the move net of the benchmark."""
    gain = excess = notional = 0.0
    n = 0
    for r in rows:
        lm = latest_mark(r.marks)
        if not lm:
            continue
        _, m = lm
        n += 1
        notional += r.notional_gbp
        gain += r.notional_gbp * float(m["dir_ret"])
        if m.get("excess") is not None:
            excess += r.notional_gbp * float(m["excess"])
    return {"n": n, "notional_gbp": round(notional, 2), "gain_gbp": round(gain, 2), "vs_benchmark_gbp": round(excess, 2)}


def _serialize(r: ProposalOutcome, labels: dict[str, str]) -> dict[str, Any]:
    lm = latest_mark(r.marks)
    return {
        "intent_id": r.intent_id,
        "proposed_at": r.proposed_at.isoformat() if r.proposed_at else None,
        "symbol": r.symbol,
        "side": r.side,
        "category": r.category,
        "category_label": intent_category.CATEGORIES.get(r.category, r.category),
        "fate": r.fate,
        "approved_by": r.approved_by,
        "permission_label": labels.get(r.approved_by or ""),
        "notional_gbp": r.notional_gbp,
        "ref_price": r.ref_price,
        "currency": r.currency,
        "ref_price_gbp": r.ref_price_gbp,
        "benchmark": r.benchmark,
        "state": r.state,
        "note": r.note,
        "marks": {k: r.marks[k] for k in ("1d", "1w", "1m", "3m", "close") if k in (r.marks or {})},
        "latest": {"horizon": lm[0], **lm[1]} if lm else None,
    }


def summary(db: Session, *, limit: int = 200) -> dict[str, Any]:
    cfg = get_config(db)
    rows = list(db.execute(select(ProposalOutcome).order_by(desc(ProposalOutcome.proposed_at))).scalars().all())
    resolved = [r for r in rows if r.fate != "open"]

    by_horizon = {h: _stats(resolved, h) for h in list(HORIZONS) + ["latest"]}
    # Headline: the longest horizon with a usable sample, else whatever has most.
    headline_h = next((h for h in ("3m", "1m", "1w") if by_horizon[h]["n_vs_benchmark"] >= 5), None)
    if headline_h is None:
        headline_h = max(HORIZONS, key=lambda h: (by_horizon[h]["n_vs_benchmark"], -HORIZONS[h]))
    headline = {**by_horizon[headline_h], "horizon": headline_h, "horizon_label": HORIZON_LABELS[headline_h]}

    categories = []
    for key, label in intent_category.CATEGORIES.items():
        rs = [r for r in resolved if r.category == key]
        if not rs:
            continue
        s = _stats(rs, headline_h)
        categories.append({"category": key, "label": label, "proposals": len(rs),
                           "taken": sum(1 for r in rs if r.fate == "taken"), **s})

    taken = [r for r in resolved if r.fate == "taken"]
    passed = [r for r in resolved if r.fate in {"rejected", "expired", "superseded"}]
    auto = [r for r in taken if (r.approved_by or "").startswith("permission:")]

    labels: dict[str, str] = {}
    try:
        from app.services.permissions_service import permission_labels

        labels = permission_labels(db)
    except Exception:  # noqa: BLE001
        pass

    return {
        "benchmark": cfg.get("benchmark", "SPY"),
        "counts": {
            "proposals": len(rows),
            "resolved": len(resolved),
            "open": len(rows) - len(resolved),
            "taken": len(taken),
            "passed": len(passed),
            "auto": len(auto),
            "unpriced": sum(1 for r in rows if r.state == "unpriced"),
        },
        "headline": headline,
        "by_horizon": by_horizon,
        "comparison": {
            "following_archie": _pnl(resolved),
            "what_you_did": _pnl(taken),
            "passed_on": _pnl(passed),
        },
        "categories": categories,
        "items": [_serialize(r, labels) for r in rows[:limit]],
        "method": (
            "Each proposal is tracked from its reference price at proposal time, whatever happened to it. "
            "Returns are in the instrument's currency and directional (a sell scores the drop it avoided). "
            "A hit means the call beat the benchmark over the same window."
        ),
    }


def category_record(db: Session, category: str) -> dict[str, Any]:
    """Compact record for one category (used by permission suggestions)."""
    rows = [
        r for r in db.execute(select(ProposalOutcome).where(ProposalOutcome.category == category)).scalars().all()
        if r.fate != "open"
    ]
    s = _stats(rows, "latest")
    notionals = [r.notional_gbp for r in rows if r.notional_gbp > 0]
    s["median_notional_gbp"] = statistics.median(notionals) if notionals else None
    return s


def for_agent(db: Session) -> dict[str, Any]:
    """What the MCP tool returns: the summary without the long list, plus the
    plain-English caveat Archie must quote."""
    s = summary(db, limit=15)
    h = s["headline"]
    s["how_to_cite"] = (
        f"Quote the numbers with their sample size ({h['caveat']}). Don't round a small sample into a claim, "
        "and mention misses and the passed-on counterfactual as readily as hits."
    )
    return s
