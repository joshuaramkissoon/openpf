"""Home layout: which widgets sit on Home, in what order and at what size.

Stored in app config under ``home_layout``. The hero (total + equity chart) and
Decisions are fixed and never appear here; everything else is a widget:

* ``builtin`` — one of the native Home cards (``ref`` in ``BUILTINS``)
* ``preset``  — a starter viz widget from the frontend's ``WIDGET_PRESETS`` (``ref`` = its id)
* ``view``    — one block of a saved spec view (``ref`` = view id, ``block`` = block index)
* ``spec``    — a viz spec stored inline (legacy). "Add to Home" from chat now saves the
  spec as a view and stores a ``view`` widget, so every widget deep-links to a page;
  inline specs left in older layouts are migrated the same way on read
  (``migrate_inline_specs``).

Sizes follow Apple widgets: S (1 column), M (2 columns), L (full row).
"""

from __future__ import annotations

import re
import secrets
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.services.config_store import ConfigStore
from app.services.viz_spec import validate_spec

KEY = "home_layout"
VERSION = 1
MAX_WIDGETS = 24
SIZES = ("S", "M", "L")
KINDS = ("builtin", "preset", "view", "spec")

# Native cards and the sizes each one reads well at.
BUILTINS: dict[str, tuple[str, ...]] = {
    "book": ("M", "L"),
    "risk": ("M", "L"),
    "today": ("M", "L"),
    "permissions": ("M", "L"),
}

_ID = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

# Matches today's Home: the book and risk side by side, then the latest report
# and the standing-permissions digest (which hides itself when nothing is granted).
DEFAULT_LAYOUT: dict[str, Any] = {
    "version": VERSION,
    "widgets": [
        {"id": "book", "kind": "builtin", "ref": "book", "size": "M"},
        {"id": "risk", "kind": "builtin", "ref": "risk", "size": "M"},
        {"id": "today", "kind": "builtin", "ref": "today", "size": "M"},
        {"id": "permissions", "kind": "builtin", "ref": "permissions", "size": "M"},
    ],
}


class LayoutError(ValueError):
    """A layout that can't be saved; the message is user-facing."""


def _new_id() -> str:
    return f"w_{secrets.token_hex(4)}"


def _widget(raw: Any, index: int) -> dict[str, Any]:
    where = f"Widget {index + 1}"
    if not isinstance(raw, dict):
        raise LayoutError(f"{where} isn't an object")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise LayoutError(f"{where} has an unknown kind {kind!r}")
    size = raw.get("size", "M")
    if size not in SIZES:
        raise LayoutError(f"{where} has an unknown size {size!r} (use S, M or L)")
    wid = raw.get("id") or _new_id()
    if not isinstance(wid, str) or not _ID.match(wid):
        raise LayoutError(f"{where} has an invalid id")
    out: dict[str, Any] = {"id": wid, "kind": kind, "size": size}

    if kind == "spec":
        spec, problems = validate_spec(raw.get("spec"))
        if problems or spec is None:
            raise LayoutError(f"{where}'s chart isn't valid: {problems[0] if problems else 'empty'}")
        out["spec"] = spec
        title = raw.get("title")
        if isinstance(title, str) and title.strip():
            out["title"] = title.strip()[:60]
        return out

    ref = raw.get("ref")
    if not isinstance(ref, str) or not _REF.match(ref):
        raise LayoutError(f"{where} needs a valid ref")
    out["ref"] = ref
    if kind == "builtin":
        allowed = BUILTINS.get(ref)
        if allowed is None:
            raise LayoutError(f"{where} refers to an unknown card {ref!r}")
        if size not in allowed:
            out["size"] = allowed[0] if size == "S" else allowed[-1]
    if kind == "view":
        block = raw.get("block", 0)
        if not isinstance(block, int) or isinstance(block, bool) or not 0 <= block < 50:
            raise LayoutError(f"{where} has an invalid block index")
        out["block"] = block
    return out


def _identity(w: dict[str, Any]) -> str | None:
    """Builtins, presets and views appear at most once; inline chat specs may repeat."""
    if w["kind"] == "spec":
        return None
    if w["kind"] == "view":
        return f"view:{w['ref']}:{w['block']}"
    return f"{w['kind']}:{w['ref']}"


def normalise(raw: Any) -> dict[str, Any]:
    """Validate a layout, returning its canonical form. Raises ``LayoutError``."""
    if not isinstance(raw, dict):
        raise LayoutError("A layout is an object with a widgets list")
    widgets = raw.get("widgets")
    if not isinstance(widgets, list):
        raise LayoutError("A layout needs a widgets list")
    if len(widgets) > MAX_WIDGETS:
        raise LayoutError(f"Home holds at most {MAX_WIDGETS} widgets")
    out: list[dict[str, Any]] = []
    ids: set[str] = set()
    seen: set[str] = set()
    for i, w in enumerate(widgets):
        item = _widget(w, i)
        while item["id"] in ids:
            item["id"] = _new_id()
        ident = _identity(item)
        if ident and ident in seen:
            continue
        ids.add(item["id"])
        if ident:
            seen.add(ident)
        out.append(item)
    return {"version": VERSION, "widgets": out}


def get_layout(db: Session) -> dict[str, Any]:
    stored = ConfigStore(db).get(KEY, DEFAULT_LAYOUT)
    try:
        return normalise(stored)
    except LayoutError:
        # A stored layout that no longer validates falls back rather than breaking Home.
        return deepcopy(DEFAULT_LAYOUT)


def save_layout(db: Session, raw: Any) -> dict[str, Any]:
    layout = normalise(raw)
    ConfigStore(db).set(KEY, layout)
    return layout


def reset_layout(db: Session) -> dict[str, Any]:
    return save_layout(db, deepcopy(DEFAULT_LAYOUT))


def headline_block(spec: dict[str, Any]) -> int:
    """The block a view contributes to Home: its first one that isn't prose."""
    for i, b in enumerate(spec.get("blocks") or []):
        if b.get("type") != "note":
            return i
    return 0


def _as_view(item: dict[str, Any], note: str) -> dict[str, Any]:
    """An inline-spec widget saved as a view, so it has a page to link to."""
    from app.services import views_store

    spec = deepcopy(item["spec"])
    if item.get("title"):
        spec["title"] = item["title"]
    meta = views_store.create_spec_view(spec, note=note)
    return {"id": item["id"], "kind": "view", "ref": meta["id"], "block": headline_block(spec), "size": item["size"]}


def migrate_inline_specs(db: Session) -> dict[str, Any]:
    """The layout with any legacy inline ``spec`` widgets saved as views (persisted
    once). A widget that can't be saved stays inline rather than disappearing."""
    layout = get_layout(db)
    if not any(w["kind"] == "spec" for w in layout["widgets"]):
        return layout
    widgets = []
    for w in layout["widgets"]:
        if w["kind"] == "spec":
            try:
                w = _as_view(w, "Added to Home from chat")
            except Exception:  # noqa: BLE001 — keep the inline widget; try again next read
                pass
        widgets.append(w)
    return save_layout(db, {"widgets": widgets})


def add_widget(db: Session, raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append one widget (deduped). Returns ``(layout, the widget as stored)``.
    A chart from chat (``kind: spec``) is saved as a view first and added as a
    ``view`` widget, so the tile links to its page."""
    layout = get_layout(db)
    item = _widget(raw, len(layout["widgets"]))
    if item["kind"] == "spec":
        if len(layout["widgets"]) >= MAX_WIDGETS:
            raise LayoutError(f"Home holds at most {MAX_WIDGETS} widgets — remove one first")
        item = _as_view(item, "Added to Home from chat")
    ident = _identity(item)
    for w in layout["widgets"]:
        if ident and _identity(w) == ident:
            return layout, w
    if len(layout["widgets"]) >= MAX_WIDGETS:
        raise LayoutError(f"Home holds at most {MAX_WIDGETS} widgets — remove one first")
    layout = save_layout(db, {"widgets": [*layout["widgets"], item]})
    return layout, layout["widgets"][-1]
