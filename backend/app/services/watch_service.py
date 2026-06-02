"""Watch service — the 'Spot' layer.

Deterministic, portfolio-scoped checks that raise ranked Alerts: concentration
breaches, thesis invalidation, imminent earnings, and big intraday moves on
holdings. Each alert is deduped via a stable key so the same condition doesn't
re-fire every cycle. Run on a schedule (watch_cycle) or on demand.

Materiality-gated by design: a watch only raises an alert when a real threshold
is crossed — never a stream of "FYI" noise.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Alert, Thesis, TradeIntent

logger = logging.getLogger(__name__)

_BIG_MOVE = 0.05      # ±5% on the day → alert
_BIG_MOVE_WARN = 0.08  # ±8% → bump severity
_EARNINGS_WINDOW_DAYS = 3


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _open_dedupe_keys(db: Session) -> set[str]:
    rows = db.execute(
        select(Alert.dedupe_key).where(Alert.status.in_(["new", "seen"]))
    ).scalars().all()
    return set(rows)


def _holdings(db: Session, top: int = 10) -> list[dict[str, Any]]:
    """Top holdings by value, ticker-aggregated across accounts."""
    from app.services.portfolio_optimizer import _aggregate_by_ticker
    from app.services.portfolio_service import get_portfolio_snapshot

    snap = get_portfolio_snapshot(db, account_kind="all", display_currency="GBP")
    pos = _aggregate_by_ticker(snap.get("positions", []))
    pos.sort(key=lambda p: float(p.get("value") or 0.0), reverse=True)
    return pos[:top]


# ── Individual watches (each appends Alert objects to `out`) ──────────────────

def _watch_concentration(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services.portfolio_optimizer import compute_rebalance

    plan = compute_rebalance(db, account_kind="all")
    for t in plan.get("trades", []):
        if t.get("side") != "sell":
            continue
        tk = t.get("ticker")
        key = f"concentration:{tk}:{_today()}"
        if key in seen:
            continue
        seen.add(key)
        cw = (t.get("current_weight") or 0) * 100
        tw = (t.get("target_weight") or 0) * 100
        out.append(Alert(
            category="concentration", severity="warning", ticker=tk, dedupe_key=key, source="concentration",
            title=f"{tk} over its concentration cap",
            detail=f"{tk} is {cw:.0f}% of the book (cap {tw:.0f}%).",
            consider=f"Trim ~£{float(t.get('est_notional') or 0):,.0f} back toward target.",
            meta={"current_weight": t.get("current_weight"), "target_weight": t.get("target_weight")},
        ))


def _watch_earnings(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services import intel_service

    for p in _holdings(db, top=10):
        tk = p.get("ticker")
        if not tk:
            continue
        nxt = (intel_service.get_earnings(tk) or {}).get("next")
        if not nxt or nxt.get("days_away") is None:
            continue
        if not (0 <= nxt["days_away"] <= _EARNINGS_WINDOW_DAYS):
            continue
        key = f"earnings:{tk}:{nxt.get('date')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Alert(
            category="earnings", severity="info", ticker=tk, dedupe_key=key, source="earnings",
            title=f"{tk} reports in {nxt['days_away']}d ({nxt.get('date')})",
            detail=f"{tk} earnings on {nxt.get('date')} ({nxt.get('hour') or 'time TBD'}). Expect a volatility bump.",
            consider="Review exposure / size before the print.",
            meta={"date": nxt.get("date")},
        ))


def _watch_big_move(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services.leveraged_market import LeveragedMarketError, get_price

    for p in _holdings(db, top=10):
        tk = p.get("ticker")
        yf = p.get("yfinance_ticker") or tk
        if not yf:
            continue
        try:
            chg = float(get_price(yf).get("change_pct") or 0.0)
        except (LeveragedMarketError, Exception):  # noqa: BLE001
            continue
        if abs(chg) < _BIG_MOVE:
            continue
        key = f"big_move:{tk}:{_today()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Alert(
            category="big_move", severity="warning" if abs(chg) >= _BIG_MOVE_WARN else "info",
            ticker=tk, dedupe_key=key, source="big_move",
            title=f"{tk} {chg * 100:+.1f}% today",
            detail=f"{tk} has moved {chg * 100:+.1f}% on the day — worth a look.",
            meta={"change_pct": round(chg, 4)},
        ))


_LEVEL_RE = re.compile(r"[£$]?\s*(\d+(?:\.\d+)?)")
_DOWN_WORDS = ("below", "under", "break", "breaks", "drops", "falls", "loses", "<")
_UP_WORDS = ("above", "over", "exceeds", "rises", ">")


def _watch_thesis_invalidation(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services.leveraged_market import get_price

    rows = db.execute(select(Thesis).where(Thesis.status == "active")).scalars().all()
    for th in rows:
        inv = (th.invalidation or "").strip()
        m = _LEVEL_RE.search(inv)
        if not inv or not m:
            continue  # no parseable price level → don't guess
        level = float(m.group(1))
        low = inv.lower()
        is_down = any(w in low for w in _DOWN_WORDS)
        is_up = any(w in low for w in _UP_WORDS)
        if not (is_down or is_up):
            continue
        try:
            price = float(get_price(th.symbol).get("price") or 0.0)
        except Exception:  # noqa: BLE001
            continue
        if price <= 0:
            continue
        breached = (is_down and price < level) or (is_up and price > level)
        if not breached:
            continue
        key = f"thesis_inval:{th.id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Alert(
            category="thesis_invalidation", severity="critical", ticker=th.symbol,
            dedupe_key=key, source="thesis",
            title=f"Thesis may be invalidated: {th.title or th.symbol}",
            detail=f"{th.symbol} at {price:.2f} vs your invalidation: \"{inv}\".",
            consider="Re-check the thesis — consider closing or re-sizing the position.",
            meta={"thesis_id": th.id, "level": level, "price": price},
        ))


# ── Watchlist watches — keep the watchlist from being a graveyard ─────────────
# Watchlist items are first-class watched entities alongside holdings. These
# raise `watchlist`-category alerts (which the board surfaces inline) so a tracked
# idea resurfaces the moment its target is hit, it moves hard, earnings loom, or
# material news lands — without Josh having to check it.

_WL_NEWS_LOOKBACK_DAYS = 1


def _watch_watchlist_target(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services.leveraged_market import get_price, is_minor_unit_currency
    from app.services import watchlist_service

    for item in watchlist_service.monitored_items(db):
        level = item.target_price
        direction = (item.target_direction or "").lower()
        if level is None or direction not in ("above", "below"):
            continue
        try:
            quote = get_price(item.symbol)
            price = float(quote.get("price") or 0.0)
        except Exception:  # noqa: BLE001
            continue
        if price <= 0:
            continue
        # GBX/pence (and other minor-unit) venues quote ~100x the major unit, so a
        # target typed in the major unit would mis-fire by 100x. Skip rather than
        # raise a wrong alert — targets are reliable only on major-unit quotes.
        if is_minor_unit_currency(quote.get("currency")):
            continue
        breached = (direction == "above" and price >= level) or (direction == "below" and price <= level)
        if not breached:
            continue
        key = f"wl_target:{item.symbol}:{direction}:{level}"  # stable — fires once until dismissed
        if key in seen:
            continue
        seen.add(key)
        arrow = "above" if direction == "above" else "below"
        out.append(Alert(
            category="watchlist", severity="warning", ticker=item.symbol,
            dedupe_key=key, source="wl_target",
            title=f"{item.symbol} hit your {level:g} level",
            detail=f"{item.symbol} is at {price:.2f}, now {arrow} your watchlist target of {level:g}.",
            consider=item.note.strip() or None,
            meta={"watchlist_item_id": item.id, "level": level, "price": price, "direction": direction},
        ))


def _watch_watchlist_big_move(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services.leveraged_market import get_price
    from app.services import watchlist_service

    for item in watchlist_service.monitored_items(db):
        tk = item.symbol
        # Don't double-fire if the holdings big-move watch already alerted this name today.
        if f"big_move:{tk}:{_today()}" in seen:
            continue
        try:
            chg = float(get_price(tk).get("change_pct") or 0.0)
        except Exception:  # noqa: BLE001
            continue
        if abs(chg) < _BIG_MOVE:
            continue
        key = f"wl_big_move:{tk}:{_today()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Alert(
            category="watchlist", severity="warning" if abs(chg) >= _BIG_MOVE_WARN else "info",
            ticker=tk, dedupe_key=key, source="wl_big_move",
            title=f"{tk} {chg * 100:+.1f}% today (watchlist)",
            detail=f"{tk} on your watchlist has moved {chg * 100:+.1f}% on the day.",
            consider=item.note.strip() or None,
            meta={"watchlist_item_id": item.id, "change_pct": round(chg, 4)},
        ))


def _watch_watchlist_earnings(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services import intel_service, watchlist_service

    for item in watchlist_service.monitored_items(db):
        tk = item.symbol
        nxt = (intel_service.get_earnings(tk) or {}).get("next")
        if not nxt or nxt.get("days_away") is None:
            continue
        if not (0 <= nxt["days_away"] <= _EARNINGS_WINDOW_DAYS):
            continue
        # Skip if the holdings earnings watch already covered this date.
        if f"earnings:{tk}:{nxt.get('date')}" in seen:
            continue
        key = f"wl_earnings:{tk}:{nxt.get('date')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Alert(
            category="watchlist", severity="info", ticker=tk, dedupe_key=key, source="wl_earnings",
            title=f"{tk} reports in {nxt['days_away']}d ({nxt.get('date')})",
            detail=f"{tk} on your watchlist reports on {nxt.get('date')} ({nxt.get('hour') or 'time TBD'}).",
            consider=item.note.strip() or None,
            meta={"watchlist_item_id": item.id, "date": nxt.get("date")},
        ))


def _watch_watchlist_news(db: Session, seen: set[str], out: list[Alert]) -> None:
    from app.services import intel_service, watchlist_service

    for item in watchlist_service.monitored_items(db):
        tk = item.symbol
        try:
            news = intel_service.get_company_news(tk, since_days=_WL_NEWS_LOOKBACK_DAYS, limit=5)
        except Exception:  # noqa: BLE001
            continue
        if not news:
            continue
        top = news[0]  # newest only — keep it high-signal, one per cycle per item
        url = (top.get("url") or top.get("id") or "").strip()
        headline = (top.get("headline") or "").strip()
        if not headline or not url:
            continue
        key = f"wl_news:{tk}:{url}"  # dedupe by article URL
        if key in seen:
            continue
        # A news article is a point-in-time event: once flagged it should never
        # re-fire, even after Josh dismisses it (dismissed keys are absent from
        # `seen`). So check ALL statuses for this exact article, not just open ones.
        if db.execute(select(Alert.id).where(Alert.dedupe_key == key)).first():
            continue
        seen.add(key)
        out.append(Alert(
            category="watchlist", severity="info", ticker=tk, dedupe_key=key, source="wl_news",
            title=f"{tk}: {headline[:140]}",
            detail=(top.get("summary") or headline).strip()[:500] + (f"\n\n{top.get('source')}" if top.get("source") else ""),
            consider=None,
            meta={"watchlist_item_id": item.id, "url": url, "source": top.get("source")},
        ))


_WATCHES: list[tuple[str, Callable[[Session, set[str], list[Alert]], None]]] = [
    ("thesis_invalidation", _watch_thesis_invalidation),
    ("concentration", _watch_concentration),
    ("earnings", _watch_earnings),
    ("big_move", _watch_big_move),
    ("watchlist_target", _watch_watchlist_target),
    ("watchlist_big_move", _watch_watchlist_big_move),
    ("watchlist_earnings", _watch_watchlist_earnings),
    ("watchlist_news", _watch_watchlist_news),
]


def run_watches(db: Session) -> dict[str, Any]:
    """Run all watches; persist new (deduped) alerts. Never raises."""
    seen = _open_dedupe_keys(db)
    out: list[Alert] = []
    errors: list[str] = []
    for name, fn in _WATCHES:
        try:
            fn(db, seen, out)
        except Exception as exc:  # noqa: BLE001 — one watch failing must not sink the rest
            logger.warning("watch %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
    for a in out:
        db.add(a)
    db.commit()
    return {"created": len(out), "errors": errors,
            "by_category": _counts([a.category for a in out])}


def _counts(items: list[str]) -> dict[str, int]:
    d: dict[str, int] = {}
    for x in items:
        d[x] = d.get(x, 0) + 1
    return d


# ── Read / mutate (API layer) ────────────────────────────────────────────────

_SEV_RANK = {"critical": 0, "warning": 1, "info": 2}


def serialize_alert(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id, "created_at": a.created_at.isoformat() if a.created_at else None,
        "category": a.category, "severity": a.severity, "title": a.title,
        "detail": a.detail, "consider": a.consider, "ticker": a.ticker,
        "status": a.status, "source": a.source, "meta": a.meta or {},
    }


def list_alerts(db: Session, status: str | None = "open", limit: int = 100) -> list[dict[str, Any]]:
    q = select(Alert)
    if status == "open":
        q = q.where(Alert.status.in_(["new", "seen"]))
    elif status and status != "all":
        q = q.where(Alert.status == status)
    rows = db.execute(q.order_by(Alert.created_at.desc())).scalars().all()
    rows.sort(key=lambda a: (_SEV_RANK.get(a.severity, 3), -(a.created_at.timestamp() if a.created_at else 0)))
    return [serialize_alert(a) for a in rows[:limit]]


def set_alert_status(db: Session, alert_id: str, status: str) -> dict[str, Any] | None:
    a = db.get(Alert, alert_id)
    if not a:
        return None
    a.status = status
    db.add(a)
    db.commit()
    return serialize_alert(a)


def mark_all_seen(db: Session) -> int:
    rows = db.execute(select(Alert).where(Alert.status == "new")).scalars().all()
    for a in rows:
        a.status = "seen"
        db.add(a)
    db.commit()
    return len(rows)


def attention_summary(db: Session) -> dict[str, Any]:
    """The unified 'what needs my attention' payload for the inbox."""
    alerts = list_alerts(db, status="open", limit=100)
    new_alerts = sum(1 for a in alerts if a["status"] == "new")
    pending_intents = db.execute(
        select(TradeIntent).where(TradeIntent.status == "proposed")
    ).scalars().all()
    return {
        "alerts": alerts,
        "counts": {
            "alerts_open": len(alerts),
            "alerts_new": new_alerts,
            "critical": sum(1 for a in alerts if a["severity"] == "critical"),
            "pending_intents": len(pending_intents),
        },
    }
