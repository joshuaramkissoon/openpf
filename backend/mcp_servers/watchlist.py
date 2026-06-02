"""Watchlist MCP server for Archie.

Lets Archie CRUD the tracked-ideas board and flag items into the Attention feed.
Backed by the same SQLite tables as the app (DATABASE_URL in the env). When Archie
recommends a name in chat, it calls `add_to_watchlist` so the rec lands on the board
with its rationale; the deterministic watches + daily review then keep it alive.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal, init_db
from app.services import watchlist_service as wl

# ── Logging (file-based — stdout is reserved for the MCP protocol) ──
_LOG_DIR = Path(os.environ.get("MCP_LOG_DIR") or (Path(__file__).resolve().parent.parent / "logs"))
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile

    _LOG_DIR = Path(tempfile.gettempdir()) / "mypf-mcp-logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("watchlist-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False
_fh = logging.FileHandler(_LOG_DIR / "watchlist.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
logger.addHandler(_fh)

mcp = FastMCP(
    "watchlist",
    instructions=(
        "Watchlist tools for Archie. Use these to list/add/update/remove tracked ideas, "
        "and to flag an item into Josh's Attention feed when something material happens. "
        "When you recommend a name, add_to_watchlist it with a concise note of *why*."
    ),
)


def _fmt(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def list_watchlist(status: str = "watching") -> str:
    """List watchlist items.

    Args:
        status: watching | acted | archived | all (default: watching).
    """
    logger.info("list_watchlist status=%s", status)
    init_db()
    db = SessionLocal()
    try:
        items = wl.list_items(db, status=status or "watching")
        return _fmt({"ok": True, "items": [wl.serialize(i) for i in items]})
    finally:
        db.close()


@mcp.tool()
def add_to_watchlist(
    symbol: str,
    note: str = "",
    conviction: str = "",
    target_price: float = 0.0,
    target_direction: str = "",
) -> str:
    """Add a symbol to the watchlist (idempotent — updates if already watched).

    Args:
        symbol: Ticker, e.g. "KEYS".
        note: Why it's on the list — the reason that the watch service later checks against.
        conviction: low | medium | high (optional).
        target_price: Optional alert level; <= 0 means none.
        target_direction: above | below — required if target_price is set.
    """
    logger.info("add_to_watchlist symbol=%s", symbol)
    init_db()
    db = SessionLocal()
    try:
        item = wl.add_item(
            db,
            symbol,
            note=note,
            conviction=(conviction or None),
            target_price=(target_price if target_price and target_price > 0 else None),
            target_direction=(target_direction or None),
            source="archie",
        )
        return _fmt({"ok": True, "item": wl.serialize(item)})
    except ValueError as exc:
        return _fmt({"ok": False, "error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def update_watchlist_item(
    item_id: str,
    note: str = "",
    conviction: str = "",
    status: str = "",
    target_price: float = 0.0,
    target_direction: str = "",
    monitor: str = "",
) -> str:
    """Update fields on a watchlist item. Empty/zero args are left unchanged.

    Args:
        item_id: The item id (from list_watchlist).
        note: New rationale (non-empty to change).
        conviction: low | medium | high.
        status: watching | acted | archived.
        target_price: Alert level; > 0 to set.
        target_direction: above | below.
        monitor: "true" | "false" to toggle monitoring (empty = unchanged).
    """
    logger.info("update_watchlist_item id=%s", item_id)
    init_db()
    db = SessionLocal()
    try:
        patch: dict[str, Any] = {}
        if note.strip():
            patch["note"] = note
        if conviction.strip():
            patch["conviction"] = conviction
        if status.strip():
            patch["status"] = status
        if target_price and target_price > 0:
            patch["target_price"] = target_price
        if target_direction.strip():
            patch["target_direction"] = target_direction
        if monitor.strip().lower() in {"true", "false"}:
            patch["monitor"] = monitor.strip().lower() == "true"
        item = wl.update_item(db, item_id, patch)
        if not item:
            return _fmt({"ok": False, "error": "item not found"})
        return _fmt({"ok": True, "item": wl.serialize(item)})
    finally:
        db.close()


@mcp.tool()
def remove_from_watchlist(item_id: str) -> str:
    """Remove an item from the watchlist permanently. To keep history instead, use
    update_watchlist_item with status='archived'."""
    logger.info("remove_from_watchlist id=%s", item_id)
    init_db()
    db = SessionLocal()
    try:
        ok = wl.remove_item(db, item_id)
        return _fmt({"ok": ok, "id": item_id})
    finally:
        db.close()


@mcp.tool()
def flag_watchlist_item(
    symbol: str,
    title: str,
    detail: str,
    consider: str = "",
    severity: str = "info",
) -> str:
    """Flag a watchlist item into Josh's Attention feed — use ONLY when something
    material has happened relative to why the item is on the list. Deduped, so a
    repeat of the same flag is a no-op.

    Args:
        symbol: The ticker.
        title: One-line headline (e.g. "KEYS entry setup triggered").
        detail: What happened and why it matters, grounded in fresh data.
        consider: Optional suggested action.
        severity: info | warning | critical.
    """
    logger.info("flag_watchlist_item symbol=%s sev=%s", symbol, severity)
    init_db()
    db = SessionLocal()
    try:
        alert = wl.raise_flag(
            db, symbol, title=title, detail=detail,
            consider=(consider or None), severity=severity, source="watchlist_review",
        )
        # Tie the review to the item so the board shows it was just looked at.
        existing = wl.get_active_by_symbol(db, symbol)
        if existing:
            wl.touch_reviewed(db, existing.id)
        if alert is None:
            return _fmt({"ok": True, "flagged": False, "reason": "duplicate (already open)"})
        return _fmt({"ok": True, "flagged": True, "alert_id": alert.id})
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
