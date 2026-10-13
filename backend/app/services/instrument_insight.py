"""Instrument insight: everything the stock sheet needs, in one call.

The sheet answers an investor's questions in order: what is it doing (and why),
what does it mean for me, the chart, should I worry, what's coming, why do I own
it, news, fundamentals. Each answer is a *section* here, and every section is
optional: a missing or slow source hides that section rather than failing the
call.

Speed: DB-local sections (identity, position, plays, theses, watchlist) are
computed inline. Network sections (price history, fundamentals, earnings, news,
orders, the risk model, the "why it moved" line) run on a small thread pool
behind a TTL cache. The request waits a short budget for them; anything still
running is reported in ``pending`` so the client can re-poll, and the result
lands in the cache for the next call. The "why it moved" LLM call is never
waited on.

Currency: the chart, the header price and the average-cost line are all in the
instrument's *native* major currency (USD for NVDA; GBP, not pence, for LSE
lines), so the header and the axis always agree. GBP values sit alongside as
clearly labelled secondary figures.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
from statistics import median
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import Alert, Play, Thesis
from app.services import leveraged_market, plays_service, portfolio_service, watchlist_service
from app.services.config_store import ConfigStore
from app.services.fx import get_fx_rate

logger = logging.getLogger(__name__)

# ── Thread pool + TTL cache ──────────────────────────────────────────────────

_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="insight")
# Leaf fetches issued from inside a pool task (e.g. one history per holding for
# the risk model) go to a separate pool so a saturated _POOL can't deadlock.
_LEAF_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="insight-leaf")
_lock = threading.Lock()
_cache: dict[tuple, tuple[float, float, Any]] = {}  # key -> (stored_at, ttl, value)
_inflight: dict[tuple, Future] = {}
_CACHE_MAX = 512

TTL_HISTORY = 15 * 60
TTL_FUNDAMENTALS = 6 * 3600
TTL_NEWS = 10 * 60
TTL_ORDERS = 10 * 60
TTL_RISK = 30 * 60
TTL_WHY = 6 * 3600
TTL_PLAYS = 10 * 60
TTL_FAIL = 90  # a failed fetch is retried after this

DEFAULT_WAIT_S = 2.5
WHY_MOVE_THRESHOLD = 0.01  # only explain moves of 1%+


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _inflight.clear()


def _cache_peek(key: tuple) -> tuple[bool, Any]:
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return False, None
        stored, ttl, value = hit
        if time.time() - stored > ttl:
            _cache.pop(key, None)
            return False, None
        return True, value


def _cache_put(key: tuple, value: Any, ttl: float) -> None:
    with _lock:
        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
            _cache.pop(oldest, None)
        _cache[key] = (time.time(), ttl, value)


def _cached_future(key: tuple, ttl: float, fn: Callable[[], Any], pool: ThreadPoolExecutor = _POOL) -> Future:
    """A future for ``fn()``'s value: completed immediately on a cache hit, the
    shared in-flight future when someone already asked, else a new pool task.
    Exceptions are swallowed into ``None`` (cached briefly) — sections degrade."""
    found, value = _cache_peek(key)
    if found:
        done: Future = Future()
        done.set_result(value)
        return done
    with _lock:
        running = _inflight.get(key)
        if running is not None:
            return running

        def _run() -> Any:
            try:
                out = fn()
                _cache_put(key, out, ttl)
                return out
            except Exception as exc:  # noqa: BLE001 — a failed source hides its section
                logger.info("insight %s failed: %s", key[0], exc)
                _cache_put(key, None, TTL_FAIL)
                return None
            finally:
                with _lock:
                    _inflight.pop(key, None)

        fut = pool.submit(_run)
        _inflight[key] = fut
        return fut


def _result(fut: Future | None) -> tuple[bool, Any]:
    if fut is None or not fut.done():
        return False, None
    try:
        return True, fut.result()
    except Exception:  # noqa: BLE001
        return True, None


# ── Small helpers ────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


_MINOR = {"GBX": ("GBP", 0.01), "GBP_PENCE": ("GBP", 0.01), "ZAC": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}


def major_currency(code: str | None) -> tuple[str, float]:
    """(major currency, multiplier from the quoted unit to it). GBX → (GBP, 0.01)."""
    c = _norm(code) or "USD"
    return _MINOR.get(c, (c, 1.0))


def _fx(base: str, quote: str) -> float | None:
    try:
        rate = float(get_fx_rate(base, quote))
        return rate if rate > 0 else None
    except Exception:  # noqa: BLE001
        return None


# ── Data hygiene for price history ──────────────────────────────────────────

def clean_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC, one row per date, glitches removed.

    yfinance occasionally returns: a duplicated session (the live bar plus a
    settled one), zero/NaN closes, and one-day bad prints — a single close 10x
    or 100x off (pence vs pounds) that reverts the next day. Each of those draws
    a spike that looks like a crash. Keep the last row per date, drop
    non-positive closes, and drop an isolated point that jumps >25% and fully
    reverts on the next bar.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], utc=True, errors="coerce")
    out = out.dropna(subset=["date", "close"])
    out = out[out["close"].astype(float) > 0]
    out["day"] = out["date"].dt.strftime("%Y-%m-%d")
    out = out.sort_values("date").drop_duplicates("day", keep="last").reset_index(drop=True)

    closes = out["close"].astype(float).tolist()
    keep = [True] * len(closes)
    for i in range(1, len(closes) - 1):
        prev, cur, nxt = closes[i - 1], closes[i], closes[i + 1]
        jump = cur / prev - 1.0
        back = nxt / cur - 1.0
        reverts = abs(nxt / prev - 1.0) < 0.10
        if abs(jump) > 0.25 and abs(back) > 0.20 and (jump > 0) != (back > 0) and reverts:
            keep[i] = False
    out = out[keep].reset_index(drop=True)

    # A bar whose OHLC are missing/zero renders as a wick to 0 on candles.
    for col in ("open", "high", "low"):
        if col not in out.columns:
            out[col] = out["close"]
        bad = ~(out[col].astype(float) > 0)
        out.loc[bad, col] = out.loc[bad, "close"]
    out["high"] = out[["high", "open", "close"]].astype(float).max(axis=1)
    out["low"] = out[["low", "open", "close"]].astype(float).min(axis=1)
    return out[["day", "open", "high", "low", "close"]]


# ── External fetchers (module-level so tests can stub them) ──────────────────

def fetch_daily_history(yf_ticker: str, period: str = "5y") -> pd.DataFrame:
    """Daily OHLC from Yahoo. ``df.attrs['currency']`` carries the unit Yahoo
    quotes *this series* in (e.g. ``GBp`` vs ``GBP`` for London lines — the
    venue suffix alone can't tell, and getting it wrong is a 100x error)."""
    import yfinance as yf

    t = yf.Ticker(yf_ticker)
    frame = t.history(period=period, interval="1d", auto_adjust=True, timeout=10)
    if frame is None or frame.empty:
        # The shared downloader retries with a cookie reset.
        return leveraged_market._download_history_frame(yf_ticker, period, "1d")
    df = frame.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    if "date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "date"})
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    out = df[keep].copy()
    try:
        out.attrs["currency"] = (t.history_metadata or {}).get("currency")
    except Exception:  # noqa: BLE001
        pass
    return out


def series_unit(df: pd.DataFrame | None, fallback_ccy: str) -> tuple[str, float]:
    """(major currency, multiplier) for a fetched series: Yahoo's own unit when
    it reported one, else the instrument's quote currency."""
    raw = str((getattr(df, "attrs", None) or {}).get("currency") or "").strip()
    if raw in ("GBp", "GBX"):
        return "GBP", 0.01
    if raw in ("ZAc", "ZAC"):
        return "ZAR", 0.01
    if raw in ("ILA",):
        return "ILS", 0.01
    if raw:
        return raw.upper(), 1.0
    return major_currency(fallback_ccy)


def fetch_info(yf_ticker: str) -> dict[str, Any]:
    import yfinance as yf

    info = yf.Ticker(yf_ticker).info
    return info if isinstance(info, dict) else {}


def fetch_earnings_frame(yf_ticker: str) -> pd.DataFrame | None:
    import yfinance as yf

    frame = yf.Ticker(yf_ticker).get_earnings_dates(limit=20)
    return frame if isinstance(frame, pd.DataFrame) else None


def fetch_news(symbol: str) -> list[dict[str, Any]]:
    from app.services.intel_service import get_company_news

    items = get_company_news(symbol, since_days=7, limit=15)
    if items:
        return items
    # Without a Finnhub key intel_service falls back to ``Ticker.news``, which
    # current Yahoo responses leave empty; the search endpoint still carries news.
    import yfinance as yf

    out: list[dict[str, Any]] = []
    for it in (yf.Search(symbol, news_count=12).news or []):
        if not isinstance(it, dict) or not it.get("title"):
            continue
        out.append({
            "headline": str(it.get("title")).strip(),
            "source": it.get("publisher") or "Yahoo",
            "url": it.get("link") or "",
            "ts": int(it.get("providerPublishTime") or 0),
            "summary": "",
            "related": it.get("relatedTickers") or [],
        })
    return out


def order_clients(store: ConfigStore) -> list[tuple[str, Any]]:
    """Read-key broker clients per configured account (built on the request
    thread: building one reads config from the DB session)."""
    from app.services.config_store import ACCOUNT_KINDS
    from app.services.t212_client import build_t212_client

    out: list[tuple[str, Any]] = []
    for kind in ACCOUNT_KINDS:
        try:
            if not str(store.get_account_credentials(kind).get("t212_api_key", "")).strip():
                continue
            out.append((kind, build_t212_client(store, account_kind=kind)))
        except Exception as exc:  # noqa: BLE001
            logger.info("insight orders client %s failed: %s", kind, exc)
    return out


def fetch_orders(clients: list[tuple[str, Any]], instrument_code: str) -> list[dict[str, Any]]:
    """Filled orders for one instrument across accounts, oldest first."""
    out: list[dict[str, Any]] = []
    for kind, client in clients:
        try:
            items = client.get_orders_for_ticker(instrument_code, max_pages=3, page_sleep=0.5)
        except Exception as exc:  # noqa: BLE001
            logger.info("insight orders %s/%s failed: %s", kind, instrument_code, exc)
            continue
        for item in items:
            trade = _normalize_fill(item, kind)
            if trade:
                out.append(trade)
    out.sort(key=lambda t: t["date"])
    return out


def _normalize_fill(item: dict[str, Any], account_kind: str) -> dict[str, Any] | None:
    order = item.get("order") if isinstance(item.get("order"), dict) else item
    fill = item.get("fill") if isinstance(item.get("fill"), dict) else {}
    qty = _f(fill.get("quantity")) or _f(order.get("filledQuantity")) or _f(order.get("quantity"))
    price = _f(fill.get("price")) or _f(order.get("fillPrice"))
    status = _norm(order.get("status") or fill.get("status"))
    when = fill.get("filledAt") or fill.get("date") or order.get("dateExecuted") or order.get("dateModified") or order.get("dateCreated") or order.get("createdAt")
    if not qty or not price or not when or (status and status not in {"FILLED", "EXECUTED", "PARTIALLY_FILLED"}):
        return None
    side_raw = _norm(order.get("side"))
    side = "sell" if (side_raw == "SELL" or qty < 0) else "buy"
    try:
        day = pd.to_datetime(when, utc=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return {"date": day, "side": side, "quantity": abs(qty), "price": price, "account_kind": account_kind}


# ── Identity + position (DB-local) ───────────────────────────────────────────

def resolve(ticker: str, db: Session) -> dict[str, Any]:
    """Identity for a typed ticker, corrected by the held position when there is
    one (the holding is ground truth), plus the matching snapshot rows."""
    sym = _norm(ticker)
    ident = portfolio_service.resolve_primary_instrument(ticker, db) or {}
    out: dict[str, Any] = {
        "sym": sym,
        "known": bool(ident),
        "instrument_code": ident.get("instrument_code"),
        "name": ident.get("name") or None,
        "ticker": ident.get("ticker") or sym,
        "display_ticker": ident.get("display_ticker") or None,
        "yfinance_ticker": ident.get("yfinance_ticker"),
        "currency": ident.get("currency"),
        "matched": [],
        "snapshot": None,
    }
    try:
        snap = portfolio_service.get_portfolio_snapshot(db, "all", "GBP")
    except Exception:  # noqa: BLE001 — no broker data: the sheet still works for research
        snap = None
    out["snapshot"] = snap
    matched: list[dict[str, Any]] = []
    for p in (snap or {}).get("positions", []):
        if _norm(p.get("ticker")) in {sym, _norm(out["ticker"])} or _norm(p.get("display_ticker")) == sym or (
            out["instrument_code"] and _norm(p.get("instrument_code")) == _norm(out["instrument_code"])
        ):
            matched.append(p)
    out["matched"] = matched
    if matched:
        first = matched[0]
        out["known"] = True
        out["name"] = first.get("name") or out["name"]
        out["instrument_code"] = first.get("instrument_code") or out["instrument_code"]
        out["display_ticker"] = first.get("display_ticker") or first.get("ticker") or out["display_ticker"]
        out["yfinance_ticker"] = first.get("yfinance_ticker") or out["yfinance_ticker"]
        out["currency"] = first.get("instrument_currency") or out["currency"]
        if first.get("ticker"):
            out["ticker"] = first["ticker"]
    code = out["instrument_code"]
    if not out["yfinance_ticker"] and code and str(code).upper().endswith("_US_EQ"):
        legacy = str(code).upper().split("_", 1)[0]
        dt = out["display_ticker"]
        out["yfinance_ticker"] = dt.upper() if dt and dt.upper() != legacy and "." not in dt else legacy
    if not out["yfinance_ticker"]:
        out["yfinance_ticker"] = leveraged_market.to_yfinance_ticker(code or sym)
    if not out["currency"]:
        out["currency"] = leveraged_market.infer_currency(out["yfinance_ticker"])
    out["display_ticker"] = out["display_ticker"] or out["ticker"] or sym
    return out


def _book(snap: dict[str, Any] | None, db: Session) -> dict[str, Any]:
    risk = ConfigStore(db).get_risk()
    metrics = (snap or {}).get("metrics", {})
    total = float(metrics.get("total_value") or 0.0)
    return {
        "total_gbp": round(total, 2),
        "free_cash_gbp": round(float(metrics.get("free_cash") or 0.0), 2),
        "free_cash_by_account": {
            a.get("account_kind"): round(float(a.get("free_cash") or 0.0), 2) for a in (snap or {}).get("accounts", [])
        },
        "position_cap": _f(risk.get("max_position_weight")),
        "max_order_gbp": _f(risk.get("max_single_order_notional")),
    }


def _position(matched: list[dict[str, Any]], major: str) -> dict[str, Any] | None:
    if not matched:
        return None
    qty = sum(float(r.get("quantity") or 0.0) for r in matched)
    value = sum(float(r.get("value") or 0.0) for r in matched)
    ppl = sum(float(r.get("ppl") or 0.0) for r in matched)
    cost = sum(float(r.get("total_cost") or 0.0) for r in matched)
    weight = sum(float(r.get("weight") or 0.0) for r in matched)
    by_account: dict[str, float] = {}
    for r in matched:
        by_account[str(r.get("account_kind"))] = by_account.get(str(r.get("account_kind")), 0.0) + float(r.get("value") or 0.0)
    # average_price in the snapshot is converted to GBP; take it back to the
    # instrument's major currency so the chart's cost line shares the axis.
    avg_gbp = (sum(float(r.get("average_price") or 0.0) * float(r.get("quantity") or 0.0) for r in matched) / qty) if qty else None
    to_gbp = _fx(major, "GBP")
    avg_native = (avg_gbp / to_gbp) if (avg_gbp and to_gbp) else None
    return {
        "quantity": qty,
        "value_gbp": round(value, 2),
        "cost_gbp": round(cost, 2),
        "ppl_gbp": round(ppl, 2),
        "ppl_pct": (ppl / cost) if cost > 0 else None,
        "weight": weight,
        "avg_cost": avg_native,
        "avg_cost_gbp": avg_gbp,
        "accounts": sorted(by_account),
        "value_by_account": {k: round(v, 2) for k, v in by_account.items()},
    }


# ── Plays + theses (DB-local; kill-rule states computed off-thread) ──────────

def _keys_for(ident: dict[str, Any]) -> set[str]:
    keys = {ident["sym"], _norm(ident.get("ticker")), _norm(ident.get("display_ticker")), _norm(ident.get("yfinance_ticker"))}
    code = _norm(ident.get("instrument_code"))
    if code:
        keys |= {code, code.split("_", 1)[0]}
    keys |= {k.split(".")[0] for k in list(keys) if k}
    return {k for k in keys if k}


def _ticker_in(value: Any, keys: set[str]) -> bool:
    """True when ``value`` (a ticker string, a dict with a ticker, or a list of
    either) names this instrument. Tolerant of shapes the plays model may grow."""
    if isinstance(value, str):
        v = _norm(value)
        return v in keys or v.split(".")[0] in keys
    if isinstance(value, dict):
        return any(_ticker_in(value.get(k), keys) for k in ("ticker", "symbol", "instrument_code"))
    if isinstance(value, (list, tuple, set)):
        return any(_ticker_in(v, keys) for v in value)
    return False


_RELATION_RANK = {"owner": 0, "bench": 1, "related": 2}
_STATE_WORD = {"intact": "fine", "near": "watch", "tripped": "triggered", "manual": "judgement", "unknown": "unknown"}


def _play_relation(play: Play, keys: set[str]) -> tuple[str, dict[str, Any] | None] | None:
    for leg in play.legs or []:
        if _ticker_in(leg, keys):
            rel = str(leg.get("relation") or leg.get("ownership") or "").lower()
            if leg.get("bench") or str(leg.get("status") or "").lower() == "bench" or rel == "bench":
                return "bench", leg
            if rel in ("related", "also"):
                return "related", leg
            return "owner", leg
    meta = play.meta or {}
    for rel in ("owner", "bench", "related"):
        attr = getattr(play, rel, None)
        if _ticker_in(attr, keys) or _ticker_in(meta.get(rel), keys):
            return rel, None
    if _ticker_in(getattr(play, "related_tickers", None), keys) or _ticker_in(meta.get("related_tickers"), keys):
        return "related", None
    return None


def _plays(db: Session, ident: dict[str, Any], book: dict[str, Any]) -> list[dict[str, Any]]:
    keys = _keys_for(ident)
    rows = db.execute(select(Play).where(Play.status.in_(["active", "paused", "draft"])).order_by(desc(Play.updated_at))).scalars().all()
    # Explicit ownership (Home's book-by-play) beats basket membership: a holding
    # moved into a play is owned by it, and a basket play that doesn't own it only
    # relates to it.
    owner_play: str | None = None
    try:
        from app.services.book_service import explicit_rows

        for (_acct, ticker), row in explicit_rows(db).items():
            if _ticker_in(ticker, keys):
                owner_play = row.play_id if row.owner_kind == "play" else ""
                break
    except Exception:  # noqa: BLE001 — ownership is a nicety; never break the sheet
        owner_play = None
    out: list[dict[str, Any]] = []
    for p in rows:
        rel = _play_relation(p, keys)
        if owner_play and p.id == owner_play:
            rel = ("owner", rel[1] if rel else None)
        if not rel:
            continue
        relation, leg = rel
        if relation == "owner" and owner_play is not None and p.id != owner_play:
            relation = "related"
        play_share = _f(p.budget_pct_of_book)
        if not play_share and p.budget_gbp and book.get("total_gbp"):
            play_share = float(p.budget_gbp) / float(book["total_gbp"])
        leg_target = _f((leg or {}).get("target_weight"))
        review = p.review or {}
        out.append({
            "id": p.id,
            "title": p.title,
            "status": p.status,
            "relation": relation,
            "role": (leg or {}).get("role"),
            "rationale": (leg or {}).get("rationale"),
            "leg_target_weight": leg_target,
            "target_weight_of_book": (leg_target * play_share) if (leg_target is not None and play_share) else None,
            "thesis": (p.thesis or "").strip(),
            "what_would_change_my_mind": (p.what_would_change_my_mind or "").strip(),
            "verdict": review.get("verdict"),
            "reviewed_at": review.get("last_reviewed_at"),
            "review_notes": review.get("notes"),
            "rules": [
                {"text": c.get("text") or "", "state": None, "status": None, "detail": None}
                for c in (p.kill_criteria or [])
            ],
            "_snapshot": SimpleNamespace(**{
                "id": p.id, "legs": list(p.legs or []), "kill_criteria": list(p.kill_criteria or []),
                "status": p.status, "activated_at": p.activated_at, "created_at": p.created_at,
                "meta": dict(p.meta or {}), "sizing": dict(p.sizing or {}), "budget_gbp": p.budget_gbp,
                "account_kind": p.account_kind,
            }),
        })
    out.sort(key=lambda r: (_RELATION_RANK.get(r["relation"], 9), r["status"] != "active"))
    return out


def _play_book(snap: dict[str, Any] | None) -> dict[str, Any]:
    """The ``book_context`` shape plays_service expects, from a snapshot we
    already hold (no second snapshot, and no DB session needed off-thread)."""
    metrics = (snap or {}).get("metrics", {})
    positions = [
        {
            "ticker": (p.get("display_ticker") or p.get("ticker") or "").upper(),
            "raw_ticker": (p.get("ticker") or "").upper(),
            "instrument_code": p.get("instrument_code"),
            "yfinance_ticker": p.get("yfinance_ticker"),
            "name": p.get("name"),
            "account_kind": p.get("account_kind"),
            "value": float(p.get("value") or 0.0),
            "ppl": float(p.get("ppl") or 0.0),
            "weight": float(p.get("weight") or 0.0),
        }
        for p in (snap or {}).get("positions", [])
    ]
    return {
        "total_gbp": float(metrics.get("total_value") or 0.0),
        "free_cash_gbp": {a.get("account_kind"): float(a.get("free_cash") or 0.0) for a in (snap or {}).get("accounts", [])},
        "positions": positions,
    }


def _theses(db: Session, ident: dict[str, Any]) -> list[dict[str, Any]]:
    keys = list(_keys_for(ident))
    rows = db.execute(
        select(Thesis).where(Thesis.symbol.in_(keys), Thesis.status == "active").order_by(desc(Thesis.created_at)).limit(3)
    ).scalars().all()
    return [
        {
            "id": t.id, "title": t.title or "", "thesis": (t.thesis or "").strip(), "invalidation": t.invalidation or "",
            "catalysts": list(t.catalysts or []), "confidence": t.confidence, "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in rows
    ]


def _alerts(db: Session, ident: dict[str, Any]) -> list[dict[str, Any]]:
    keys = list(_keys_for(ident))
    rows = db.execute(
        select(Alert).where(Alert.ticker.in_(keys), Alert.status.in_(["new", "seen"])).order_by(desc(Alert.created_at)).limit(5)
    ).scalars().all()
    return [{"id": a.id, "severity": a.severity, "title": a.title, "detail": a.detail or "", "consider": a.consider} for a in rows]


# ── Network sections (pure functions of fetched data) ────────────────────────

def build_chart(df: pd.DataFrame, multiplier: float) -> dict[str, Any] | None:
    clean = clean_daily(df)
    if clean.empty:
        return None
    m = float(multiplier)
    points = [
        [r.day, round(float(r.open) * m, 6), round(float(r.high) * m, 6), round(float(r.low) * m, 6), round(float(r.close) * m, 6)]
        for r in clean.itertuples(index=False)
    ]
    return {"points": points}


def quote_from_points(points: list[list[Any]]) -> dict[str, Any] | None:
    if not points:
        return None
    last = points[-1]
    prev = points[-2] if len(points) > 1 else None
    price = float(last[4])
    prev_close = float(prev[4]) if prev else None
    change = (price - prev_close) if prev_close else None
    return {
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": (change / prev_close) if (change is not None and prev_close) else None,
        "as_of": last[0],
    }


def _closes(df: pd.DataFrame) -> pd.Series:
    clean = clean_daily(df)
    s = pd.Series(clean["close"].astype(float).values, index=pd.to_datetime(clean["day"]))
    return s[~s.index.duplicated(keep="last")]


def risk_model(holdings: list[tuple[str, float]], subject: str, fetch: Callable[[str], pd.DataFrame]) -> dict[str, Any] | None:
    """Risk share, correlations and volatility for ``subject`` against the book.

    ``holdings``: [(yfinance ticker, GBP value share of the whole book incl. cash)].
    Returns in the book's covariance (1y daily returns, annualised). Cash carries
    no risk, so risk shares sum to 1 across the held names.
    """
    tickers = list(dict.fromkeys([t for t, _ in holdings] + [subject, "SPY"]))
    futures = {t: _LEAF_POOL.submit(fetch, t) for t in tickers}
    closes: dict[str, pd.Series] = {}
    for t, fut in futures.items():
        try:
            s = _closes(fut.result(timeout=20))
            if len(s) >= 40:
                closes[t] = s
        except Exception:  # noqa: BLE001
            continue
    if subject not in closes:
        return None
    frame = pd.DataFrame(closes).sort_index().ffill(limit=3)
    rets = frame.pct_change(fill_method=None).iloc[1:]
    sub = rets[subject].dropna()
    if len(sub) < 30:
        return None

    daily_sd = float(sub.std())
    vol = daily_sd * math.sqrt(252)
    window = closes[subject].iloc[-252:]
    max_dd = float((window / window.cummax() - 1.0).min()) if len(window) else None
    spy_vol = float(rets["SPY"].dropna().std() * math.sqrt(252)) if "SPY" in rets else None
    beta = None
    if "SPY" in rets:
        pair = rets[[subject, "SPY"]].dropna()
        if len(pair) > 30 and float(pair["SPY"].var()) > 0:
            beta = float(pair[subject].cov(pair["SPY"]) / pair["SPY"].var())

    weights = {t: w for t, w in holdings if t in closes and w > 0}
    moves_with: list[dict[str, Any]] = []
    for t in weights:
        if t == subject:
            continue
        pair = rets[[subject, t]].dropna()
        if len(pair) < 30:
            continue
        c = float(pair[subject].corr(pair[t]))
        if math.isfinite(c):
            moves_with.append({"ticker": t, "corr": round(c, 3)})
    moves_with.sort(key=lambda r: r["corr"], reverse=True)

    risk_share = None
    portfolio_vol = None
    if subject in weights and len(weights) >= 1:
        names = list(weights)
        cov = rets[names].fillna(0.0).cov() * 252
        w = pd.Series(weights)[names]
        marginal = cov.values @ w.values
        total_var = float(w.values @ marginal)
        if total_var > 0:
            contrib = dict(zip(names, (w.values * marginal) / total_var))
            risk_share = float(contrib[subject])
            portfolio_vol = math.sqrt(total_var)
    return {
        "vol_annual": vol,
        "typical_daily_move": daily_sd,
        "market_vol_annual": spy_vol,
        "vol_vs_market": (vol / spy_vol) if spy_vol else None,
        "beta": beta,
        "max_drawdown_1y": max_dd,
        "risk_share": risk_share,
        "portfolio_vol_annual": portfolio_vol,
        "moves_with": moves_with[:3],
        "holdings_considered": len(weights),
    }


def _epoch_to_date(v: Any) -> str | None:
    n = _f(v)
    if not n or n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def build_fundamentals(info: dict[str, Any] | None, pe_history: dict[str, Any] | None) -> dict[str, Any] | None:
    if not info:
        return None
    pick = {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _f(info.get("marketCap")),
        "pe_trailing": _f(info.get("trailingPE")),
        "pe_forward": _f(info.get("forwardPE")),
        "price_to_sales": _f(info.get("priceToSalesTrailing12Months")),
        "peg": _f(info.get("pegRatio") or info.get("trailingPegRatio")),
        "revenue_growth": _f(info.get("revenueGrowth")),
        "earnings_growth": _f(info.get("earningsGrowth")),
        "gross_margin": _f(info.get("grossMargins")),
        "operating_margin": _f(info.get("operatingMargins")),
        "profit_margin": _f(info.get("profitMargins")),
        "dividend_yield": _f(info.get("dividendYield")),
        "summary": (info.get("longBusinessSummary") or "").strip()[:600] or None,
        "pe_history": pe_history,
    }
    meaningful = [k for k, v in pick.items() if v is not None and k not in {"sector", "industry", "summary"}]
    return pick if meaningful else None


def pe_history(frame: pd.DataFrame | None, closes: pd.Series | None) -> dict[str, Any] | None:
    """Trailing P/E at each past report date (price / sum of the last four
    reported EPS) — the stock's valuation against its own recent history. Uses
    the same EPS basis for "now", so the comparison is like-for-like."""
    if frame is None or frame.empty or closes is None or closes.empty:
        return None
    col = next((c for c in ("Reported EPS", "reported_eps") if c in frame.columns), None)
    if not col:
        return None
    rows = []
    for idx, rec in frame.iterrows():
        eps = _f(rec.get(col))
        if eps is None:
            continue
        try:
            d = pd.Timestamp(idx).tz_localize(None) if pd.Timestamp(idx).tzinfo else pd.Timestamp(idx)
        except (TypeError, ValueError):
            continue
        rows.append((d.normalize(), eps))
    rows.sort()
    if len(rows) < 5:
        return None
    idx_naive = closes.index.tz_localize(None) if closes.index.tz is not None else closes.index
    series = pd.Series(closes.values, index=idx_naive)
    points: list[tuple[str, float]] = []
    for i in range(3, len(rows)):
        d = rows[i][0]
        ttm = sum(e for _, e in rows[i - 3 : i + 1])
        if ttm <= 0:
            continue
        upto = series[series.index <= d]
        if upto.empty:
            continue
        points.append((d.strftime("%Y-%m-%d"), float(upto.iloc[-1]) / ttm))
    last4 = sum(e for _, e in rows[-4:])
    now = (float(series.iloc[-1]) / last4) if last4 > 0 else None
    if len(points) < 3 or now is None:
        return None
    vals = [v for _, v in points]
    return {
        "now": now,
        "min": min(vals),
        "median": median(vals),
        "max": max(vals),
        "since": points[0][0],
        "observations": len(points),
        "basis": "price ÷ last four reported EPS",
    }


def build_events(info: dict[str, Any] | None, frame: pd.DataFrame | None, today: date | None = None) -> dict[str, Any] | None:
    today = today or date.today()
    out: dict[str, Any] = {}
    nxt = None
    if frame is not None and not frame.empty:
        future = []
        for idx in frame.index:
            try:
                d = pd.Timestamp(idx).date()
            except (TypeError, ValueError):
                continue
            if d >= today:
                future.append(d)
        if future:
            nxt = min(future)
    if nxt is None and info:
        ts = _epoch_to_date(info.get("earningsTimestampStart") or info.get("earningsTimestamp"))
        if ts and date.fromisoformat(ts) >= today:
            nxt = date.fromisoformat(ts)
    if nxt:
        out["next_earnings"] = {"date": nxt.isoformat(), "days_away": (nxt - today).days}
    if info:
        exd = _epoch_to_date(info.get("exDividendDate"))
        if exd and date.fromisoformat(exd) >= today:
            out["ex_dividend"] = {
                "date": exd,
                "days_away": (date.fromisoformat(exd) - today).days,
                "amount": _f(info.get("lastDividendValue")),
            }
    return out or None


def rank_news(items: list[dict[str, Any]] | None, keys: set[str], name: str | None, limit: int = 8) -> list[dict[str, Any]]:
    if not items:
        return []
    words = {k for k in keys if len(k) >= 2}
    first = (name or "").split(" ")[0].strip(",.").upper() if name else ""
    if len(first) >= 4:
        words.add(first)

    def score(it: dict[str, Any]) -> tuple[int, int]:
        head = _norm(it.get("headline"))
        hit = any(w in head for w in words)
        tagged = any(_norm(r) in keys for r in (it.get("related") or []))
        return (int(hit) + int(tagged), int(it.get("ts") or 0))

    seen: set[str] = set()
    ranked = []
    for it in sorted(items, key=score, reverse=True):
        h = (it.get("headline") or "").strip()
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        ts = int(it.get("ts") or 0)
        when = it.get("datetime")
        if ts and not when:
            when = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        ranked.append({"headline": h, "source": it.get("source") or "", "url": it.get("url") or "", "published_at": when, "summary": (it.get("summary") or "")[:280]})
        if len(ranked) >= limit:
            break
    return ranked


# ── "Why it moved" (LLM, never waited on) ────────────────────────────────────

def explain_move(symbol: str, name: str | None, change_pct: float, headlines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One line on why the stock moved today, grounded in the headlines given.
    Returns None when no model is configured or the news doesn't explain it."""
    from pydantic import BaseModel, Field

    from app.core.config import get_settings
    from app.services.gemini_client import generate_structured

    settings = get_settings()
    if not settings.gemini_api_key or not headlines:
        return None

    class Why(BaseModel):
        explained: bool = Field(description="True only if the headlines plausibly explain today's move.")
        reason: str = Field(description="One plain-English sentence, max 18 words, no hedging filler, no ticker prefix.")

    lines = "\n".join(f"- {h['headline']} ({h.get('source') or 'news'})" for h in headlines[:8])
    prompt = (
        f"{name or symbol} ({symbol}) moved {change_pct * 100:+.1f}% today.\n"
        f"Recent headlines:\n{lines}\n\n"
        "In one short sentence, say why it moved, using only these headlines. "
        "If they don't explain the move, set explained=false."
    )
    parsed = asyncio.run(generate_structured(model=settings.gemini_title_model, contents=prompt, response_schema=Why))
    if not parsed or not parsed.explained or not parsed.reason.strip():
        return {"text": None}
    return {"text": parsed.reason.strip().rstrip("."), "sources": [h for h in headlines[:3] if h.get("url")][:2]}


# ── Aggregate ────────────────────────────────────────────────────────────────

class UnknownInstrument(LookupError):
    pass


def build_insight(ticker: str, db: Session, *, wait_s: float = DEFAULT_WAIT_S) -> dict[str, Any]:
    started = time.time()
    ident = resolve(ticker, db)
    yf = ident["yfinance_ticker"]
    quote_ccy = _norm(ident.get("currency")) or "USD"
    snap = ident.pop("snapshot")
    matched = ident.pop("matched")
    book = _book(snap, db)
    keys = _keys_for(ident)

    # Holdings for the risk model, by yfinance ticker (accounts merged).
    holdings: dict[str, float] = {}
    labels: dict[str, str] = {}
    for p in (snap or {}).get("positions", []):
        t = p.get("yfinance_ticker") or p.get("ticker")
        if not t:
            continue
        holdings[t] = holdings.get(t, 0.0) + float(p.get("weight") or 0.0)
        labels[t] = p.get("display_ticker") or p.get("ticker") or t
    holding_list = sorted(((t, round(w, 6)) for t, w in holdings.items()), key=lambda r: r[0])

    store = ConfigStore(db)
    code = ident.get("instrument_code")

    futures: dict[str, Future] = {
        "chart": _cached_future(("hist", yf, "5y"), TTL_HISTORY, lambda: fetch_daily_history(yf, "5y")),
        "info": _cached_future(("info", yf), TTL_FUNDAMENTALS, lambda: fetch_info(yf)),
        "earnings": _cached_future(("earn", yf), TTL_FUNDAMENTALS, lambda: fetch_earnings_frame(yf)),
        "news": _cached_future(("news", yf), TTL_NEWS, lambda: fetch_news(yf)),
        "risk": _cached_future(
            ("risk", yf, tuple(holding_list)), TTL_RISK,
            lambda: risk_model(holding_list, yf, lambda t: fetch_daily_history(t, "1y")),
        ),
    }
    if matched and code:
        found_orders, _ = _cache_peek(("orders", code))
        clients = [] if found_orders else order_clients(store)
        futures["trades"] = _cached_future(("orders", code), TTL_ORDERS, lambda: fetch_orders(clients, code))

    plays = _plays(db, ident, book)
    play_book = _play_book(snap)
    for p in plays:
        snapshot = p.pop("_snapshot")
        if snapshot.status in ("active", "paused") and snapshot.kill_criteria:
            futures[f"play:{p['id']}"] = _cached_future(
                ("play", p["id"], tuple(holding_list)), TTL_PLAYS,
                lambda s=snapshot: plays_service.play_performance(None, s, book=play_book),  # type: ignore[arg-type]
            )

    remaining = max(0.0, wait_s - (time.time() - started))
    wait(list(futures.values()), timeout=remaining)

    pending: list[str] = []

    def take(name: str) -> Any:
        done, value = _result(futures.get(name))
        if not done and name in futures:
            pending.append(name)
        return value

    # Chart + quote: one history feeds both so the header and axis agree.
    hist = take("chart")
    # The chart's unit comes from the series itself (Yahoo quotes some London
    # lines in pence, others in pounds); everything on its axis converts to it.
    major, mult = series_unit(hist if isinstance(hist, pd.DataFrame) else None, quote_ccy)
    chart = build_chart(hist, mult) if isinstance(hist, pd.DataFrame) else None
    quote = quote_from_points(chart["points"]) if chart else None
    if not ident["known"] and chart is None and "chart" not in pending:
        raise UnknownInstrument(ticker)

    to_gbp = _fx(major, "GBP")
    if quote:
        quote["currency"] = major
        quote["price_gbp"] = quote["price"] * to_gbp if to_gbp else None
        quote["fx_to_gbp"] = to_gbp

    position = _position(matched, major)
    if position and quote and position["quantity"]:
        # Re-price the holding off the same quote as the header (snapshot prices
        # can be a few minutes older), so header, chart and P/L agree.
        position["last_price"] = quote["price"]

    trades = take("trades") if "trades" in futures else None
    if chart:
        chart["currency"] = major
        chart["unit_multiplier"] = mult
        chart["avg_cost"] = position["avg_cost"] if position else None
        # Broker fills are in the broker's instrument currency (often GBX).
        fill_major, fill_mult = major_currency(quote_ccy)
        fill_fx = 1.0 if fill_major == major else (_fx(fill_major, major) or 0.0)
        chart["trades"] = [
            {**t, "price": t["price"] * fill_mult * fill_fx} for t in (trades or [])
        ] if (trades and fill_fx) else []

    info = take("info")
    earnings = take("earnings")
    closes = _closes(hist) * mult if isinstance(hist, pd.DataFrame) and not hist.empty else None
    same_ccy = not info or _norm(info.get("financialCurrency") or major) == major
    fundamentals = build_fundamentals(info, pe_history(earnings, closes) if (earnings is not None and same_ccy) else None)
    events = build_events(info, earnings)

    news_raw = take("news")
    news = rank_news(news_raw, keys, ident.get("name"))

    risk = take("risk")
    if risk:
        risk = dict(risk)
        risk["moves_with"] = [{**m, "label": labels.get(m["ticker"], m["ticker"])} for m in risk.get("moves_with", [])]
        risk["value_share"] = position["weight"] if position else None

    for p in plays:
        name = f"play:{p['id']}"
        if name not in futures:
            continue
        perf = take(name)
        if perf:
            states = perf.get("kill_status") or []
            p["rules"] = [
                {"text": k.get("text") or "", "state": k.get("state"), "status": _STATE_WORD.get(k.get("state") or "", "unknown"), "detail": k.get("detail")}
                for k in states
            ]
            p["verdict"] = plays_service.verdict_from_states([k.get("state") for k in states]) if states else p["verdict"]

    # Why it moved: only for a real move, only from news, never blocking.
    why = None
    if quote and quote.get("change_pct") is not None and abs(quote["change_pct"]) >= WHY_MOVE_THRESHOLD and news:
        why_key = ("why", yf, quote["as_of"], round(quote["change_pct"], 2))
        found, why = _cache_peek(why_key)
        if not found:
            fut = _cached_future(why_key, TTL_WHY, lambda: explain_move(ident["display_ticker"], ident.get("name"), quote["change_pct"], news))
            done, why = _result(fut)
            if not done:
                pending.append("why")
        if why and not why.get("text"):
            why = None

    watch = watchlist_service.get_active_by_symbol(db, ident["sym"]) or watchlist_service.get_active_by_symbol(db, ident.get("ticker") or "")

    return {
        "ticker": ident.get("ticker") or ident["sym"],
        "display_ticker": ident["display_ticker"],
        "name": ident.get("name"),
        "instrument_code": code,
        "yfinance_ticker": yf,
        "quote_currency": quote_ccy,
        "quote": quote,
        "why_moved": why,
        "held": bool(position),
        "position": position,
        "book": book if snap else None,
        "plays": plays,
        "theses": _theses(db, ident),
        "alerts": _alerts(db, ident),
        "watchlist": {"id": watch.id, "note": watch.note or "", "target_price": watch.target_price} if watch else None,
        "chart": chart,
        "risk": risk,
        "events": events,
        "news": news,
        "fundamentals": fundamentals,
        "pending": pending,
    }
