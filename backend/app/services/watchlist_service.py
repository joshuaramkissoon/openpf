"""Watchlist service — the tracked-ideas layer between a passing mention and a
full Thesis.

Single source of truth for what's on the watchlist (it replaced the old
``app_config['watchlist']`` symbol list). Items carry the *reason* they're watched
(``note``) plus the knobs the watch service uses to resurface them. CRUD'd by Josh
(REST API) or Archie (watchlist MCP server). ``active_symbols`` is consumed by the
agent run + leveraged scan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import Alert, AppConfig, WatchlistItem

# Symbols seeded into the table on first boot if it's empty AND no old config list
# exists — matches the previous WATCHLIST_DEFAULT so behaviour is unchanged.
_SEED_FALLBACK = ["SPY", "QQQ", "MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META"]

_VALID_STATUS = {"watching", "acted", "archived"}
_VALID_CONVICTION = {"low", "medium", "high"}
_VALID_DIRECTION = {"above", "below"}
_VALID_SOURCE = {"manual", "archie", "agent_run", "watchlist_review"}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _norm_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ── CRUD ──────────────────────────────────────────────────────────────────────

def list_items(db: Session, status: str | None = "watching", limit: int = 200) -> list[WatchlistItem]:
    q = select(WatchlistItem)
    if status and status != "all":
        q = q.where(WatchlistItem.status == status)
    q = q.order_by(desc(WatchlistItem.created_at)).limit(max(1, min(limit, 500)))
    return list(db.execute(q).scalars().all())


def get_item(db: Session, item_id: str) -> WatchlistItem | None:
    return db.get(WatchlistItem, item_id)


def get_active_by_symbol(db: Session, symbol: str) -> WatchlistItem | None:
    sym = _norm_symbol(symbol)
    return db.execute(
        select(WatchlistItem).where(WatchlistItem.symbol == sym, WatchlistItem.status == "watching")
    ).scalars().first()


def add_item(
    db: Session,
    symbol: str,
    *,
    note: str = "",
    name: str = "",
    conviction: str | None = None,
    target_price: float | None = None,
    target_direction: str | None = None,
    source: str = "manual",
) -> WatchlistItem:
    """Add a symbol to the watchlist. Idempotent on the *active* symbol: if it's
    already being watched, the existing item is updated (note appended-or-set,
    fields filled in) rather than duplicated."""
    sym = _norm_symbol(symbol)
    if not sym:
        raise ValueError("symbol is required")

    conviction = _clean(conviction)
    if conviction and conviction not in _VALID_CONVICTION:
        conviction = None
    target_direction = _clean(target_direction)
    if target_direction and target_direction not in _VALID_DIRECTION:
        target_direction = None
    source = source if source in _VALID_SOURCE else "manual"

    existing = get_active_by_symbol(db, sym)
    if existing:
        if note.strip():
            existing.note = note.strip()
        if name.strip():
            existing.name = name.strip()
        if conviction:
            existing.conviction = conviction
        if target_price is not None:
            existing.target_price = float(target_price)
        if target_direction:
            existing.target_direction = target_direction
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    item = WatchlistItem(
        symbol=sym,
        name=name.strip(),
        note=note.strip(),
        conviction=conviction,
        target_price=float(target_price) if target_price is not None else None,
        target_direction=target_direction,
        source=source,
        status="watching",
        monitor=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, item_id: str, patch: dict[str, Any]) -> WatchlistItem | None:
    item = db.get(WatchlistItem, item_id)
    if not item:
        return None

    if "note" in patch:
        item.note = str(patch["note"] or "").strip()
    if "name" in patch:
        item.name = str(patch["name"] or "").strip()
    if "conviction" in patch:
        c = _clean(patch["conviction"])
        item.conviction = c if (c in _VALID_CONVICTION) else None
    if "status" in patch:
        s = _clean(patch["status"])
        if s in _VALID_STATUS:
            item.status = s
    if "target_price" in patch:
        tp = patch["target_price"]
        item.target_price = float(tp) if tp not in (None, "") else None
    if "target_direction" in patch:
        d = _clean(patch["target_direction"])
        item.target_direction = d if (d in _VALID_DIRECTION) else None
    if "monitor" in patch:
        item.monitor = bool(patch["monitor"])

    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def remove_item(db: Session, item_id: str) -> bool:
    item = db.get(WatchlistItem, item_id)
    if not item:
        return False
    db.delete(item)
    db.commit()
    return True


def touch_reviewed(db: Session, item_id: str) -> None:
    item = db.get(WatchlistItem, item_id)
    if item:
        item.last_reviewed_at = _utcnow()
        db.add(item)
        db.commit()


# ── Consumers / helpers ─────────────────────────────────────────────────────

def active_symbols(db: Session) -> list[str]:
    """Distinct upper-cased symbols of items being actively watched. Consumed by
    the agent run + leveraged scan (replaces the old config symbol list)."""
    rows = db.execute(
        select(WatchlistItem.symbol).where(WatchlistItem.status == "watching")
    ).scalars().all()
    seen: list[str] = []
    for s in rows:
        u = _norm_symbol(s)
        if u and u not in seen:
            seen.append(u)
    return seen


def monitored_items(db: Session) -> list[WatchlistItem]:
    """Active items with monitoring enabled — the set the watch service checks."""
    return list(db.execute(
        select(WatchlistItem).where(
            WatchlistItem.status == "watching", WatchlistItem.monitor.is_(True)
        )
    ).scalars().all())


def serialize(item: WatchlistItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "symbol": item.symbol,
        "name": item.name or "",
        "note": item.note or "",
        "source": item.source,
        "conviction": item.conviction,
        "status": item.status,
        "target_price": item.target_price,
        "target_direction": item.target_direction,
        "monitor": bool(item.monitor),
        "last_reviewed_at": item.last_reviewed_at.isoformat() if item.last_reviewed_at else None,
    }


# ── Seeding / migration off the old config list ──────────────────────────────

def seed_from_config_if_empty(db: Session) -> int:
    """One-time migration: if the table is empty, populate it from the legacy
    ``app_config['watchlist']`` symbol list (falling back to the historical
    defaults). Returns the number of items created. Idempotent — a no-op once any
    item exists."""
    has_any = db.execute(select(WatchlistItem.id).limit(1)).first()
    if has_any:
        return 0

    symbols: list[str] = []
    cfg = db.execute(select(AppConfig).where(AppConfig.key == "watchlist")).scalar_one_or_none()
    if cfg and isinstance(cfg.value, dict):
        symbols = [s for s in (cfg.value.get("symbols") or []) if str(s).strip()]
    if not symbols:
        symbols = list(_SEED_FALLBACK)

    created = 0
    for sym in symbols:
        u = _norm_symbol(sym)
        if not u:
            continue
        db.add(WatchlistItem(symbol=u, source="manual", status="watching", monitor=True,
                             note="", meta={"seeded": True}))
        created += 1
    if created:
        db.commit()
    return created


# ── Attention flags (used by the watchlist_review MCP tool) ──────────────────

def _open_dedupe_keys(db: Session) -> set[str]:
    rows = db.execute(
        select(Alert.dedupe_key).where(Alert.status.in_(["new", "seen"]))
    ).scalars().all()
    return set(rows)


def raise_flag(
    db: Session,
    symbol: str,
    *,
    title: str,
    detail: str,
    consider: str | None = None,
    severity: str = "info",
    dedupe_key: str | None = None,
    source: str = "watchlist_review",
) -> Alert | None:
    """Raise a deduped Attention alert tied to a watchlist symbol. Returns the new
    Alert, or None if an equivalent open alert already exists. This is the single
    channel the LLM watchlist review uses to flag something worth noticing."""
    sym = _norm_symbol(symbol)
    sev = severity if severity in {"info", "warning", "critical"} else "info"
    today = _utcnow().strftime("%Y-%m-%d")
    key = dedupe_key or f"watchlist:{source}:{sym}:{title.strip()[:60]}:{today}"
    if key in _open_dedupe_keys(db):
        return None
    alert = Alert(
        category="watchlist", severity=sev, ticker=sym or None, dedupe_key=key, source=source,
        title=title.strip()[:240], detail=detail.strip(), consider=(consider or None),
        meta={"symbol": sym},
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def open_flag_counts(db: Session, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """For the board: per-symbol open `watchlist`-category alert count + newest title."""
    if not symbols:
        return {}
    syms = {_norm_symbol(s) for s in symbols}
    rows = db.execute(
        select(Alert).where(
            Alert.category == "watchlist",
            Alert.status.in_(["new", "seen"]),
            Alert.ticker.in_(list(syms)),
        ).order_by(desc(Alert.created_at))
    ).scalars().all()
    out: dict[str, dict[str, Any]] = {}
    for a in rows:
        tk = _norm_symbol(a.ticker or "")
        slot = out.setdefault(tk, {"open_flags": 0, "latest_flag": None, "latest_severity": None})
        slot["open_flags"] += 1
        if slot["latest_flag"] is None:
            slot["latest_flag"] = a.title
            slot["latest_severity"] = a.severity
    return out
