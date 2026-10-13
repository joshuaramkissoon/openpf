"""Smart money service: config, storage, refresh, queries and alerts."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.sandbox import is_sandbox
from app.models.entities import Alert
from app.models.smart_money import Disclosure
from app.services.smart_money import congress, edgar, net, trends

logger = logging.getLogger(__name__)

CONFIG_KEY = "smart_money"

# Well-known 13F filers (SEC CIKs). The user picks which to follow.
KNOWN_MANAGERS: list[dict[str, Any]] = [
    {"cik": 1067983, "name": "Berkshire Hathaway"},
    {"cik": 1336528, "name": "Pershing Square (Ackman)"},
    {"cik": 1536411, "name": "Duquesne Family Office (Druckenmiller)"},
    {"cik": 1656456, "name": "Appaloosa (Tepper)"},
    {"cik": 1061768, "name": "Baupost Group (Klarman)"},
    {"cik": 1649339, "name": "Scion Asset Management (Burry)"},
    {"cik": 1029160, "name": "Soros Fund Management"},
    {"cik": 1350694, "name": "Bridgewater Associates"},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "sec_contact": "",  # email for the SEC User-Agent (required by SEC); env SEC_CONTACT_EMAIL also works
    "managers": [m for m in KNOWN_MANAGERS if m["cik"] in (1067983, 1336528, 1536411, 1656456, 1061768)],
    "extra_tickers": [],  # watched for Form 4 beyond holdings/plays/watchlist
    "congress_provider": "bargo",  # bargo | capitol_api | none
    "capitol_api_url": "",
    "bargo_api_key": "",
    "max_alerts_per_refresh": 3,
}

ALERT_CATEGORY = "smart_money"


# ── Config ──────────────────────────────────────────────────────────────────

def get_config(db: Session) -> dict[str, Any]:
    from app.services.config_store import ConfigStore

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(ConfigStore(db).get(CONFIG_KEY, {}) or {})
    return cfg


def public_config(db: Session) -> dict[str, Any]:
    cfg = get_config(db)
    cfg["bargo_api_key"] = "set" if cfg.get("bargo_api_key") else ""
    cfg["known_managers"] = KNOWN_MANAGERS
    return cfg


def update_config(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    from app.services.config_store import ConfigStore

    store = ConfigStore(db)
    cur = store.get(CONFIG_KEY, {}) or {}
    for k in ("enabled", "sec_contact", "managers", "extra_tickers", "congress_provider", "capitol_api_url",
              "bargo_api_key", "max_alerts_per_refresh"):
        if k in patch:
            if k == "bargo_api_key" and patch[k] == "set":
                continue  # masked echo from the UI
            cur[k] = patch[k]
    if "managers" in cur:
        cur["managers"] = [{"cik": int(m["cik"]), "name": str(m.get("name") or "")} for m in cur["managers"] if m.get("cik")]
    if "extra_tickers" in cur:
        cur["extra_tickers"] = sorted({str(t).strip().upper() for t in cur["extra_tickers"] if str(t).strip()})[:60]
    store.set(CONFIG_KEY, cur)
    return public_config(db)


# ── Storage ─────────────────────────────────────────────────────────────────

_COLUMNS = ("source", "external_id", "actor", "actor_type", "actor_role", "ticker", "instrument_code", "issuer", "sector",
            "side", "kind", "size_low", "size_high", "size_label", "shares", "price", "trade_date", "disclosed_date",
            "delay_days", "url", "informative", "noise_reason", "provider", "meta")


def upsert_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    """Insert new rows; existing external_ids are left alone (dedupe). Returns new count."""
    if not rows:
        return 0
    ids = [r["external_id"] for r in rows]
    known: set[str] = set()
    for i in range(0, len(ids), 500):
        known |= set(db.execute(select(Disclosure.external_id).where(Disclosure.external_id.in_(ids[i:i + 500]))).scalars())
    added = 0
    for r in rows:
        if r["external_id"] in known:
            continue
        known.add(r["external_id"])
        db.add(Disclosure(**{k: r.get(k) for k in _COLUMNS if k in r}))
        added += 1
    db.flush()
    return added


def row_dict(d: Disclosure) -> dict[str, Any]:
    return {k: getattr(d, k) for k in _COLUMNS} | {"id": d.id}


def serialize(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    for k in ("trade_date", "disclosed_date"):
        if isinstance(out.get(k), date):
            out[k] = out[k].isoformat()
    return out


def load_rows(db: Session, *, since_days: int = 200, ticker: str | None = None) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=since_days)
    q = select(Disclosure).where(or_(Disclosure.trade_date >= cutoff, Disclosure.disclosed_date >= cutoff))
    if ticker:
        q = q.where(Disclosure.ticker == ticker.upper())
    return [row_dict(d) for d in db.execute(q).scalars()]


def fund_snapshots(db: Session) -> list[dict[str, Any]]:
    cfg = get_config(db)
    managers = cfg.get("managers") or []
    if is_sandbox():
        from app.services.smart_money.fixtures import DEMO_FUNDS

        managers = DEMO_FUNDS
    out = []
    for m in managers:
        snap = net.cache_get(db, f"13f:snapshot:{int(m['cik'])}")
        if snap:
            out.append(snap)
    return out


# ── The user's context ──────────────────────────────────────────────────────

def user_context(db: Session, today: date | None = None) -> trends.Context:
    ctx = trends.Context(today=today or date.today())
    try:
        from app.services.plays_service import book_context

        for p in book_context(db).get("positions", []):
            t = (p.get("ticker") or "").upper()
            if t:
                ctx.held.add(t)
    except Exception as exc:  # noqa: BLE001 — broker down → still useful without holdings
        logger.info("smart money: holdings unavailable: %s", exc)
    try:
        from app.models.entities import Play

        for play in db.execute(select(Play).where(Play.status.in_(["active", "draft", "paused"]))).scalars():
            for group in (play.legs or [], play.related or [], play.bench or []):
                for leg in group:
                    t = str(leg.get("ticker") or "").upper()
                    if t:
                        ctx.plays.setdefault(t, [])
                        if play.title not in ctx.plays[t]:
                            ctx.plays[t].append(play.title)
    except Exception as exc:  # noqa: BLE001
        logger.info("smart money: plays unavailable: %s", exc)
    try:
        from app.services import watchlist_service

        ctx.watchlist = {s.upper() for s in watchlist_service.active_symbols(db)}
    except Exception as exc:  # noqa: BLE001
        logger.info("smart money: watchlist unavailable: %s", exc)
    return ctx


def _t212_code(db: Session, ticker: str | None) -> str | None:
    if not ticker:
        return None
    try:
        from app.services.portfolio_service import _instrument_meta_map

        meta = _instrument_meta_map(db)
    except Exception:  # noqa: BLE001
        return None
    base = ticker.upper()
    for cand in (f"{base}_US_EQ", f"{base.replace('.', '_')}_US_EQ", f"{base.replace('-', '_')}_US_EQ"):
        if cand in meta:
            return str(meta[cand].get("ticker") or cand)
    return None


# ── Refresh ─────────────────────────────────────────────────────────────────

_refresh_lock = threading.Lock()


def status(db: Session) -> dict[str, Any]:
    cfg = get_config(db)
    last = net.cache_get(db, "smart_money:last_refresh") or {}
    provider = None
    try:
        provider = congress.provider_from_config(cfg)
    except net.SourceUnavailable as exc:
        last.setdefault("errors", []).append(str(exc))
    count = db.query(Disclosure).count()
    sec_ok = True
    try:
        net.sec_user_agent(cfg.get("sec_contact"))
    except net.SourceUnavailable:
        sec_ok = False
    return {
        "sandbox": is_sandbox(),
        "enabled": bool(cfg.get("enabled")),
        "rows": count,
        "last_refresh": last,
        "sources": {
            "form4": {"label": "SEC Form 4 (insiders)", "delay": "filed within ~2 business days", "configured": sec_ok or is_sandbox()},
            "13f": {"label": "SEC 13F (funds)", "delay": "quarterly, up to 45 days after quarter end", "configured": sec_ok or is_sandbox(),
                    "managers": cfg.get("managers")},
            "congress": {"label": provider.label if provider else "disabled", "delay": "up to 45 days after the trade (STOCK Act)",
                         "configured": provider is not None, "attribution": getattr(provider, "attribution", None)},
        },
    }


def _universe(db: Session, ctx: trends.Context, cfg: dict[str, Any], limit: int = 40) -> list[str]:
    ordered = list(ctx.held) + [t for t in ctx.plays if t not in ctx.held] + sorted(ctx.watchlist) + list(cfg.get("extra_tickers") or [])
    seen: list[str] = []
    for t in ordered:
        t = t.upper()
        # Form 4 is US-only: skip LSE/other suffixed tickers and T212-only symbols.
        if "." in t and not t.endswith((".A", ".B")):
            continue
        if t not in seen:
            seen.append(t)
    return seen[:limit]


def refresh(db: Session, *, raise_alerts_too: bool = True, tickers: list[str] | None = None) -> dict[str, Any]:
    """Fetch new disclosures (Form 4 for the user's names, 13F for followed
    managers, Congress via the configured provider), store them, run the
    detector and raise alerts. Each source fails independently."""
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "error": "a refresh is already running"}
    try:
        return _refresh(db, raise_alerts_too=raise_alerts_too, tickers=tickers)
    finally:
        _refresh_lock.release()


def _refresh(db: Session, *, raise_alerts_too: bool, tickers: list[str] | None) -> dict[str, Any]:
    cfg = get_config(db)
    started = datetime.utcnow()
    report: dict[str, Any] = {"ok": True, "added": {}, "errors": [], "started_at": started.isoformat()}
    if not cfg.get("enabled"):
        return {"ok": False, "error": "smart money is disabled in config"}
    if is_sandbox():
        from app.services.smart_money import fixtures

        report["added"] = fixtures.load(db)
        report["sandbox"] = True
    else:
        ctx = user_context(db)
        universe = tickers or _universe(db, ctx, cfg)
        try:
            ua = net.sec_user_agent(cfg.get("sec_contact"))
        except net.SourceUnavailable as exc:
            ua = None
            report["errors"].append(str(exc))
        if ua:
            added = 0
            for tk in universe:
                try:
                    added += upsert_rows(db, edgar.fetch_form4_rows(db, tk, ua))
                    db.commit()
                except net.SourceUnavailable as exc:
                    report["errors"].append(f"form4 {tk}: {exc}")
                    break
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append(f"form4 {tk}: {exc}")
            report["added"]["form4"] = added
            report["form4_tickers"] = len(universe)
            added = 0
            for m in cfg.get("managers") or []:
                try:
                    rows, snap = edgar.fetch_13f_rows(db, int(m["cik"]), ua, name_hint=m.get("name") or "")
                    added += upsert_rows(db, rows)
                    if snap:
                        net.cache_put(db, f"13f:snapshot:{int(m['cik'])}", snap)
                    db.commit()
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    report["errors"].append(f"13f {m.get('name')}: {exc}")
            report["added"]["13f"] = added
        try:
            provider = congress.provider_from_config(cfg)
            if provider:
                rows = provider.fetch(db, tickers=universe)
                report["added"]["congress"] = upsert_rows(db, rows)
                db.commit()
        except net.SourceUnavailable as exc:
            db.commit()
            report["errors"].append(f"congress: {exc}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            report["errors"].append(f"congress: {exc}")
        if ua:
            _fill_sectors(db, ua)
        _map_instruments(db)
    db.commit()
    if raise_alerts_too:
        report["alerts"] = raise_alerts(db)
    report["finished_at"] = datetime.utcnow().isoformat()
    net.cache_put(db, "smart_money:last_refresh", {k: report[k] for k in ("started_at", "finished_at", "added", "errors")})
    db.commit()
    return report


def _fill_sectors(db: Session, ua: str, budget: int = 60) -> None:
    """Sector (from SEC SIC codes) for 13F / Congress rows that lack one."""
    missing = sorted({t for t in db.execute(select(Disclosure.ticker).where(Disclosure.sector.is_(None), Disclosure.ticker.is_not(None))).scalars()})
    if not missing:
        return
    try:
        cmap = edgar.cik_map(db, ua)
    except net.SourceUnavailable:
        return
    for tk in missing[:budget]:
        entry = cmap.get(tk) or cmap.get(tk.replace(".", "-"))
        if not entry:
            continue
        sector = edgar.sector_for_cik(db, int(entry[0]), ua)
        if sector:
            for d in db.execute(select(Disclosure).where(Disclosure.ticker == tk, Disclosure.sector.is_(None))).scalars():
                d.sector = sector
    db.commit()


def _map_instruments(db: Session) -> None:
    rows = db.execute(select(Disclosure).where(Disclosure.instrument_code.is_(None), Disclosure.ticker.is_not(None))).scalars().all()
    cache: dict[str, str | None] = {}
    for d in rows:
        if d.ticker not in cache:
            cache[d.ticker] = _t212_code(db, d.ticker)
        if cache[d.ticker]:
            d.instrument_code = cache[d.ticker]
    db.commit()


def ensure_loaded(db: Session) -> None:
    """Sandbox: load the offline fixtures the first time anything asks."""
    if is_sandbox() and db.query(Disclosure).count() == 0:
        from app.services.smart_money import fixtures

        fixtures.load(db)
        db.commit()


# ── Alerts ──────────────────────────────────────────────────────────────────

def detections(db: Session, *, window: int = 90) -> list[dict[str, Any]]:
    ensure_loaded(db)
    ctx = user_context(db)
    rows = load_rows(db, since_days=max(window, 200))
    out = trends.detect(rows, ctx)
    if window < 90:
        cutoff = (ctx.today - timedelta(days=window)).isoformat()
        out = [d for d in out if (d.get("last_trade") or "") >= cutoff or "fund" in d["kind"]]
    return out


def raise_alerts(db: Session) -> dict[str, Any]:
    """Strongest qualifying detections → Alerts (category ``smart_money``).

    Deduped by detection id across *all* statuses, so a dismissed signal never
    re-fires; it comes back only if the evidence changes (more actors → new id),
    and then supersedes the older one for that ticker."""
    from app.services.watch_service import supersede_duplicates

    cfg = get_config(db)
    dets = [d for d in detections(db) if trends.should_alert(d)]
    cap = int(cfg.get("max_alerts_per_refresh") or 3)
    created = []
    for d in dets:
        if len(created) >= cap:
            break
        key = d["id"][:160]
        if db.execute(select(Alert.id).where(Alert.dedupe_key == key)).first():
            continue
        caveat = d["caveats"][0] if d["caveats"] else ""
        touches = ", ".join(d["touches"])
        db.add(Alert(
            category=ALERT_CATEGORY, severity=trends.alert_severity(d), ticker=d.get("ticker"), dedupe_key=key,
            source="smart_money",
            title=d["title"][:240],
            detail=(f"{d['detail']} Evidence: {d['strength']} ({d['score']}/100)." + (f" Touches your {touches}." if touches else "")
                    + (f" Caveat: {caveat}" if caveat else ""))[:2000],
            consider="A disclosure is a lead to research, not a reason to trade. Ask Archie what it means for your book.",
            meta={"detection": d["kind"], "score": d["score"], "strength": d["strength"], "touches": d["touches"],
                  "url": d.get("url"), "sector": d.get("sector"), "caveats": d["caveats"]},
        ))
        created.append(d["id"])
    db.flush()
    supersede_duplicates(db)
    db.commit()
    return {"created": len(created), "ids": created, "qualifying": len(dets)}


# ── Queries (API + MCP) ─────────────────────────────────────────────────────

def query(db: Session, *, ticker: str | None = None, sector: str | None = None, actor: str | None = None,
          days: int = 90, include_noise: bool = False, limit: int = 60) -> dict[str, Any]:
    ensure_loaded(db)
    cutoff = date.today() - timedelta(days=days)
    q = select(Disclosure).where(or_(Disclosure.trade_date >= cutoff, Disclosure.disclosed_date >= cutoff))
    if ticker:
        q = q.where(Disclosure.ticker == ticker.strip().upper())
    if sector:
        q = q.where(Disclosure.sector.ilike(f"%{sector.strip()}%"))
    if actor:
        q = q.where(Disclosure.actor.ilike(f"%{actor.strip()}%"))
    if not include_noise:
        q = q.where(Disclosure.informative.is_(True))
    rows = [row_dict(d) for d in db.execute(q.order_by(Disclosure.disclosed_date.desc()).limit(max(1, min(limit, 300)))).scalars()]
    noise = 0
    if not include_noise and ticker:
        noise = db.query(Disclosure).filter(Disclosure.ticker == ticker.strip().upper(), Disclosure.informative.is_(False),
                                            Disclosure.trade_date >= cutoff).count()
    out: dict[str, Any] = {
        "filters": {"ticker": ticker, "sector": sector, "actor": actor, "days": days},
        "count": len(rows),
        "filtered_noise": noise,
        "rows": [serialize(r) for r in rows],
        "delays": {"form4": "≈2 business days", "13f": "quarter-end snapshot, ≤45 days late", "congress": "≤45 days late"},
    }
    if ticker:
        out["summary"] = ticker_summary(db, ticker)
    return out


def ticker_summary(db: Session, ticker: str) -> dict[str, Any]:
    ensure_loaded(db)
    rows = load_rows(db, since_days=200, ticker=ticker)
    summary = trends.ticker_summary(ticker, rows, fund_snapshots(db), date.today())
    ctx_dets = [d for d in detections(db) if (d.get("ticker") or "") == ticker.upper()]
    summary["detections"] = [{k: d[k] for k in ("kind", "title", "score", "strength", "caveats", "touches")} for d in ctx_dets[:3]]
    cfg = get_config(db)
    provider = None
    try:
        provider = congress.provider_from_config(cfg)
    except net.SourceUnavailable:
        pass
    summary["attribution"] = getattr(provider, "attribution", None) if summary["congress"]["buys_90d"] or summary["congress"]["sells_90d"] else None
    summary["sandbox"] = is_sandbox()
    summary["instrument_code"] = next((r.get("instrument_code") for r in rows if r.get("instrument_code")), None)
    last = net.cache_get(db, f"smart_money:ticker_fetch:{ticker.upper()}")
    summary["fetched_at"] = (last or {}).get("at")
    return summary


def trends_report(db: Session, *, window: int = 90) -> dict[str, Any]:
    dets = detections(db, window=window)
    return {
        "window_days": window,
        "count": len(dets),
        "detections": dets[:25],
        "how_to_read": "score = evidence quality (0-100), not a recommendation. Read the caveats. "
                       "touches = which part of the user's book it concerns.",
        "status": status(db),
    }


# ── On-demand ticker fetch (stock sheet) ────────────────────────────────────

_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def maybe_refresh_ticker(db: Session, ticker: str) -> bool:
    """Kick a background Form 4 fetch for one ticker if it hasn't been looked
    at in 24h. Returns True if a fetch was started."""
    t = ticker.strip().upper()
    if is_sandbox() or not net.network_allowed() or not t or "." in t:
        return False
    if net.cache_get(db, f"smart_money:ticker_fetch:{t}", timedelta(hours=24)) is not None:
        return False
    cfg = get_config(db)
    try:
        ua = net.sec_user_agent(cfg.get("sec_contact"))
    except net.SourceUnavailable:
        return False
    with _inflight_lock:
        if t in _inflight:
            return True
        _inflight.add(t)

    def run() -> None:
        from app.core.database import SessionLocal

        try:
            with SessionLocal() as s:
                upsert_rows(s, edgar.fetch_form4_rows(s, t, ua, since_days=120, max_filings=25))
                _map_instruments(s)
                net.cache_put(s, f"smart_money:ticker_fetch:{t}", {"at": datetime.utcnow().isoformat()})
                s.commit()
        except Exception as exc:  # noqa: BLE001
            logger.info("smart money on-demand fetch %s failed: %s", t, exc)
        finally:
            with _inflight_lock:
                _inflight.discard(t)

    threading.Thread(target=run, name=f"smart-money-{t}", daemon=True).start()
    return True
