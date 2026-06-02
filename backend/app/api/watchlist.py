from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.watchlist import WatchlistCreate, WatchlistItemView, WatchlistUpdate
from app.services import watchlist_service as wl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

_SEV_RANK = {"critical": 0, "warning": 1, "info": 2}


def _fetch_quote(symbol: str) -> dict[str, Any]:
    """Best-effort live price + day change for one symbol. Never raises."""
    try:
        from app.services.leveraged_market import get_price

        q = get_price(symbol) or {}
        return {
            "price": float(q["price"]) if q.get("price") is not None else None,
            "change_pct": float(q["change_pct"]) if q.get("change_pct") is not None else None,
            "currency": q.get("currency"),
        }
    except Exception as exc:  # noqa: BLE001 — enrichment must never sink the list
        logger.debug("watchlist quote failed for %s: %s", symbol, exc)
        return {"price": None, "change_pct": None, "currency": None}


def _quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        results = list(pool.map(_fetch_quote, symbols))
    return dict(zip(symbols, results))


def _to_view(item, quote: dict[str, Any], flags: dict[str, Any]) -> WatchlistItemView:
    base = wl.serialize(item)
    return WatchlistItemView(
        **base,
        price=quote.get("price"),
        change_pct=quote.get("change_pct"),
        currency=quote.get("currency"),
        open_flags=int(flags.get("open_flags", 0)),
        latest_flag=flags.get("latest_flag"),
        latest_severity=flags.get("latest_severity"),
    )


@router.get("", response_model=list[WatchlistItemView])
def list_watchlist(
    db: Session = Depends(get_db),
    status: str = Query("watching", description="watching | acted | archived | all"),
    enrich: bool = Query(True, description="Attach live price + day change"),
) -> list[WatchlistItemView]:
    items = wl.list_items(db, status=status)
    symbols = [i.symbol for i in items]
    quotes = _quotes(symbols) if enrich else {}
    flag_map = wl.open_flag_counts(db, symbols)
    views = [_to_view(i, quotes.get(i.symbol, {}), flag_map.get(i.symbol, {})) for i in items]
    # Two stable passes: baseline newest-first, then float flagged (and, within
    # equal flag counts, more-severe) items to the top — so the board resurfaces
    # activity while keeping recent items above older ones in the unflagged tail.
    views.sort(key=lambda v: v.created_at or "", reverse=True)
    views.sort(key=lambda v: (-v.open_flags, _SEV_RANK.get(v.latest_severity or "", 3)))
    return views


@router.post("", response_model=WatchlistItemView)
def create_watchlist_item(payload: WatchlistCreate, db: Session = Depends(get_db)) -> WatchlistItemView:
    if not payload.symbol.strip():
        raise HTTPException(status_code=422, detail="symbol is required")
    item = wl.add_item(
        db,
        payload.symbol,
        note=payload.note or "",
        conviction=payload.conviction,
        target_price=payload.target_price,
        target_direction=payload.target_direction,
        source="manual",
    )
    quote = _fetch_quote(item.symbol)
    flags = wl.open_flag_counts(db, [item.symbol]).get(item.symbol, {})
    return _to_view(item, quote, flags)


@router.patch("/{item_id}", response_model=WatchlistItemView)
def update_watchlist_item(item_id: str, payload: WatchlistUpdate, db: Session = Depends(get_db)) -> WatchlistItemView:
    patch = payload.model_dump(exclude_unset=True)
    item = wl.update_item(db, item_id, patch)
    if not item:
        raise HTTPException(status_code=404, detail="watchlist item not found")
    quote = _fetch_quote(item.symbol)
    flags = wl.open_flag_counts(db, [item.symbol]).get(item.symbol, {})
    return _to_view(item, quote, flags)


@router.delete("/{item_id}")
def delete_watchlist_item(item_id: str, db: Session = Depends(get_db)) -> dict:
    ok = wl.remove_item(db, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="watchlist item not found")
    return {"ok": True, "id": item_id}
