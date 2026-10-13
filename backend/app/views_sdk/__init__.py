"""View SDK assets: the spec reference (VIZ_SPEC.md), the hand-made preset specs,
and the legacy code-view doc + example (VIEW_SDK.md, presets/*.html) that the
code fallback builder uses."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.views_validate import parse_meta

SDK_DIR = Path(__file__).resolve().parent
DOC_PATH = SDK_DIR / "VIEW_SDK.md"
SPEC_DOC_PATH = SDK_DIR / "VIZ_SPEC.md"
PRESETS_DIR = SDK_DIR / "presets"


@lru_cache(maxsize=1)
def sdk_doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def spec_doc() -> str:
    return SPEC_DOC_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def preset_specs() -> dict[str, dict[str, Any]]:
    """slug → spec, for every ``presets/*.json`` (hand-made, validated in tests)."""
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(PRESETS_DIR.glob("*.json"))}


def starter(slug: str) -> dict[str, Any] | None:
    """A legacy code-view example (``presets/<slug>.html``) — few-shot for the code builder."""
    path = PRESETS_DIR / f"{slug}.html"
    if not path.exists():
        return None
    html = path.read_text(encoding="utf-8")
    meta = parse_meta(html)
    return {"slug": slug, "title": meta.get("title", slug), "description": meta.get("description", ""), "icon": meta.get("icon", "layout-dashboard"), "html": html}
