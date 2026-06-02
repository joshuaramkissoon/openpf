"""External cashflow ingestion from the T212 transactions feed.

Deposits, withdrawals and transfers are the money you *put in or took out* — to
turn the equity curve into a return curve we net these out, so growth from
contributions isn't mistaken for performance. Stored idempotently (deduped by
``account_kind`` + ``reference``); a sync walks newest-first and stops once it
hits an already-stored reference, so steady-state syncs touch only one page.

T212 history endpoints are aggressively rate-limited, hence the page sleep and
the early-stop. FEE rows are stored for audit but excluded from contribution
math (a fee is a return drag already reflected in account value, not capital).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CashflowEvent
from app.services.config_store import AccountKind, ConfigStore
from app.services.t212_client import T212Error, T212RateLimitError, build_t212_client

logger = logging.getLogger(__name__)

# Types that move external capital in/out of an account (signed). Internal
# Invest↔ISA transfers self-cancel when summed across accounts for the All view.
CONTRIBUTION_TYPES = {"DEPOSIT", "WITHDRAW", "TRANSFER"}

_TX_PATH = "/history/transactions"


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _next_params(next_page_path: str) -> dict[str, str]:
    """T212 returns nextPagePath like 'limit=50&cursor=...&time=...'."""
    return dict(kv.split("=", 1) for kv in next_page_path.split("&") if "=" in kv)


def sync_cashflows(
    db: Session,
    account_kind: AccountKind,
    *,
    max_pages: int = 30,
    page_sleep: float = 0.6,
) -> dict[str, Any]:
    """Ingest transactions for one account, newest-first, deduped by reference.
    Stops early once a page is fully known (steady state). Returns a summary."""
    config = ConfigStore(db)
    try:
        client = build_t212_client(config, account_kind=account_kind)
    except T212Error as exc:
        return {"account_kind": account_kind, "ok": False, "error": str(exc), "added": 0}

    known = set(db.execute(
        select(CashflowEvent.reference).where(CashflowEvent.account_kind == account_kind)
    ).scalars().all())

    added = 0
    pages = 0
    params: dict[str, Any] = {"limit": 50}
    rate_limited = False
    try:
        for _ in range(max_pages):
            data, _meta = client._request("GET", _TX_PATH, params=params)
            items = data.get("items", []) or []
            pages += 1
            page_had_new = False
            for it in items:
                ref = str(it.get("reference") or "").strip()
                if not ref or ref in known:
                    continue
                occurred = _parse_dt(it.get("dateTime", ""))
                if occurred is None:
                    continue
                db.add(CashflowEvent(
                    account_kind=account_kind,
                    reference=ref,
                    type=str(it.get("type") or "").upper().strip(),
                    amount=float(it.get("amount") or 0.0),
                    currency=str(it.get("currency") or "").upper().strip() or "USD",
                    occurred_at=occurred,
                ))
                known.add(ref)
                added += 1
                page_had_new = True
            nxt = data.get("nextPagePath")
            # Steady-state stop: a full page of already-known refs means everything
            # older is known too (feed is newest-first).
            if not nxt or not items or not page_had_new:
                break
            params = _next_params(nxt)
            time.sleep(page_sleep)
    except T212RateLimitError as exc:
        rate_limited = True
        logger.warning("cashflow sync rate-limited for %s after %d pages: %s", account_kind, pages, exc)
    except T212Error as exc:
        if added:
            db.commit()
        return {"account_kind": account_kind, "ok": False, "error": str(exc), "added": added}

    if added:
        db.commit()
    return {
        "account_kind": account_kind,
        "ok": True,
        "added": added,
        "pages": pages,
        "rate_limited": rate_limited,
        "total_stored": len(known),
    }


def sync_all(db: Session) -> dict[str, Any]:
    """Sync every enabled account."""
    config = ConfigStore(db)
    results = [sync_cashflows(db, kind) for kind in config.enabled_account_kinds()]
    return {"accounts": results, "added": sum(r.get("added", 0) for r in results)}


def backfill_cashflows(
    db: Session,
    account_kind: AccountKind,
    *,
    max_pages: int = 400,
    page_sleep: float = 1.0,
    rate_limit_sleep: float = 65.0,
    max_retries_per_page: int = 6,
) -> dict[str, Any]:
    """Deep one-time backfill: walk the FULL transaction history to the start.

    Unlike ``sync_cashflows`` (which stops at the first fully-known page for cheap
    steady-state syncs), this never stops early — it pages to ``nextPagePath is
    None`` so it fills historical gaps. Crucially it BACKS OFF and retries on a
    429 instead of bailing: a rate-limit mid-backfill is exactly how the ISA feed
    got truncated at ~page 6 (newest-first means the *oldest* deposits are last,
    so an early abort silently drops years of history). Idempotent (dedup by
    reference); safe to re-run."""
    config = ConfigStore(db)
    try:
        client = build_t212_client(config, account_kind=account_kind)
    except T212Error as exc:
        return {"account_kind": account_kind, "ok": False, "error": str(exc), "added": 0}

    known = set(db.execute(
        select(CashflowEvent.reference).where(CashflowEvent.account_kind == account_kind)
    ).scalars().all())

    added = 0
    pages = 0
    params: dict[str, Any] = {"limit": 50}
    while pages < max_pages:
        for attempt in range(max_retries_per_page):
            try:
                data, _meta = client._request("GET", _TX_PATH, params=params)
                break
            except T212RateLimitError as exc:
                if attempt == max_retries_per_page - 1:
                    logger.warning("cashflow backfill[%s]: giving up on page %d after %d retries: %s",
                                   account_kind, pages + 1, max_retries_per_page, exc)
                    if added:
                        db.commit()
                    return {"account_kind": account_kind, "ok": False, "error": "rate_limited",
                            "added": added, "pages": pages, "incomplete": True}
                logger.info("cashflow backfill[%s]: 429 on page %d, backing off %.0fs",
                            account_kind, pages + 1, rate_limit_sleep)
                time.sleep(rate_limit_sleep)
            except T212Error as exc:
                if added:
                    db.commit()
                return {"account_kind": account_kind, "ok": False, "error": str(exc), "added": added}
        else:  # pragma: no cover — loop exhausted without break
            break

        items = data.get("items", []) or []
        pages += 1
        page_added = 0
        for it in items:
            ref = str(it.get("reference") or "").strip()
            if not ref or ref in known:
                continue
            occurred = _parse_dt(it.get("dateTime", ""))
            if occurred is None:
                continue
            db.add(CashflowEvent(
                account_kind=account_kind,
                reference=ref,
                type=str(it.get("type") or "").upper().strip(),
                amount=float(it.get("amount") or 0.0),
                currency=str(it.get("currency") or "").upper().strip() or "USD",
                occurred_at=occurred,
            ))
            known.add(ref)
            added += 1
            page_added += 1
        if page_added:
            db.commit()  # persist progress page-by-page so a later failure can't lose it
        nxt = data.get("nextPagePath")
        if not nxt or not items:
            break  # reached the true start of history
        params = _next_params(nxt)
        time.sleep(page_sleep)

    return {"account_kind": account_kind, "ok": True, "added": added, "pages": pages,
            "total_stored": len(known)}


def backfill_all_cashflows(db: Session) -> dict[str, Any]:
    """Deep backfill every enabled account (one-time / gap recovery)."""
    config = ConfigStore(db)
    results = [backfill_cashflows(db, kind) for kind in config.enabled_account_kinds()]
    return {"accounts": results, "added": sum(r.get("added", 0) for r in results)}


_last_sync_monotonic: float | None = None


def maybe_sync_all(db: Session, *, min_interval_seconds: int = 6 * 3600) -> dict[str, Any] | None:
    """Throttled best-effort sync, safe to call from hot paths (e.g. refresh).
    Steady-state this is ~one request per account; new deposits surface at the
    top of the feed so they're picked up. Swallows errors — never blocks refresh."""
    global _last_sync_monotonic
    now = time.monotonic()
    if _last_sync_monotonic is not None and (now - _last_sync_monotonic) < min_interval_seconds:
        return None
    _last_sync_monotonic = now
    try:
        return sync_all(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("throttled cashflow sync failed: %s", exc)
        return None


def get_cashflows(db: Session, account_kind: str = "all") -> list[CashflowEvent]:
    q = select(CashflowEvent).order_by(CashflowEvent.occurred_at.asc())
    if account_kind != "all":
        q = q.where(CashflowEvent.account_kind == account_kind)
    return list(db.execute(q).scalars().all())
