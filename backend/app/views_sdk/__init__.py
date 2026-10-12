"""View SDK assets: the builder's reference doc and the hand-written starter views
(shipped as installable templates and used as few-shot examples)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.views_validate import parse_meta

SDK_DIR = Path(__file__).resolve().parent
DOC_PATH = SDK_DIR / "VIEW_SDK.md"
STARTERS_DIR = SDK_DIR / "starters"

# Display order in the gallery.
_STARTER_ORDER = ("dividend-income", "drift-vs-targets", "concentration")


@lru_cache(maxsize=1)
def sdk_doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def starters() -> list[dict[str, Any]]:
    out = []
    for path in sorted(STARTERS_DIR.glob("*.html"), key=lambda p: (_STARTER_ORDER.index(p.stem) if p.stem in _STARTER_ORDER else 99, p.stem)):
        html = path.read_text(encoding="utf-8")
        meta = parse_meta(html)
        out.append({"slug": path.stem, "title": meta.get("title", path.stem), "description": meta.get("description", ""), "icon": meta.get("icon", "layout-dashboard"), "html": html})
    return out


def starter(slug: str) -> dict[str, Any] | None:
    return next((s for s in starters() if s["slug"] == slug), None)
