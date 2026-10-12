"""Filesystem storage for Views (agent-built, sandboxed pages).

Layout under ``VIEWS_DATA_DIR`` (default ``backend/data/views``)::

    <id>/meta.json      id, title, description, icon, pinned, order, timestamps,
                        current_version, versions[], history[] (prompts)
    <id>/v<N>.html      immutable version N of the view document
    <id>/state.json     small per-view key/value state (``openpf.state``)
    <id>/draft.html     the builder's scratch file while a build is running

Plain files keep views portable (copy a folder to share one) and let the builder
agent edit a real file with its normal Write/Edit tools.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_LOCK = threading.RLock()
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ICON_RE = re.compile(r"^[a-z0-9-]{1,40}$")

MAX_STATE_BYTES = 64 * 1024
DEFAULT_ICON = "layout-dashboard"


class ViewNotFound(LookupError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir() -> Path:
    raw = Path(get_settings().views_data_dir).expanduser()
    if not raw.is_absolute():
        raw = Path(__file__).resolve().parents[2] / raw  # backend/
    raw.mkdir(parents=True, exist_ok=True)
    return raw


def _check_id(view_id: str) -> str:
    if not isinstance(view_id, str) or not _ID_RE.match(view_id):
        raise ViewNotFound(view_id)
    return view_id


def view_dir(view_id: str) -> Path:
    return data_dir() / _check_id(view_id)


def draft_path(view_id: str) -> Path:
    return view_dir(view_id) / "draft.html"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40].strip("-") or "view"


def _read_meta(view_id: str) -> dict[str, Any]:
    path = view_dir(view_id) / "meta.json"
    if not path.exists():
        raise ViewNotFound(view_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_meta(meta: dict[str, Any]) -> None:
    path = view_dir(meta["id"]) / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(path)


def public(meta: dict[str, Any]) -> dict[str, Any]:
    """Meta as returned by the API (no file paths)."""
    return {
        "id": meta["id"],
        "title": meta.get("title") or "Untitled view",
        "description": meta.get("description") or "",
        "icon": meta.get("icon") or DEFAULT_ICON,
        "pinned": bool(meta.get("pinned", True)),
        "order": meta.get("order", 0),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "current_version": meta.get("current_version", 0),
        "versions": meta.get("versions", []),
        "history": meta.get("history", []),
        "starter": meta.get("starter"),
    }


def list_views() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for child in data_dir().iterdir():
        if child.is_dir() and (child / "meta.json").exists() and _ID_RE.match(child.name):
            try:
                out.append(public(_read_meta(child.name)))
            except (OSError, ValueError):
                continue
    out.sort(key=lambda m: (m["order"], m["created_at"] or ""))
    return out


def get_view(view_id: str) -> dict[str, Any]:
    return public(_read_meta(view_id))


_STOP = {"a", "an", "the", "me", "my", "of", "by", "for", "and", "to", "in", "on", "show", "build", "make", "create", "give", "view", "page", "please", "i", "want", "with", "what", "how", "is", "are", "can", "you"}


def prompt_slug(prompt: str) -> str:
    """'show me my dividend income by month' → 'dividend-income-month'."""
    words = [w for w in re.findall(r"[a-z0-9]+", (prompt or "").lower()) if w not in _STOP]
    return _slug("-".join(words[:4])) if words else "view"


def create_view(*, title: str, description: str = "", icon: str = DEFAULT_ICON, prompt: str = "", starter: str | None = None, slug: str | None = None) -> dict[str, Any]:
    """Create an empty view shell (no versions yet — the builder writes v1)."""
    with _LOCK:
        base = _slug(slug or title or prompt)
        view_id = base
        while (data_dir() / view_id).exists():
            view_id = f"{base[:32]}-{uuid.uuid4().hex[:6]}"
        (data_dir() / view_id).mkdir(parents=True)
        existing = list_views()
        meta = {
            "id": view_id,
            "title": (title or "New view").strip()[:80],
            "description": (description or "").strip()[:240],
            "icon": icon if _ICON_RE.match(icon or "") else DEFAULT_ICON,
            "pinned": True,
            "order": (max((v["order"] for v in existing), default=-1) + 1),
            "created_at": _now(),
            "updated_at": _now(),
            "current_version": 0,
            "versions": [],
            "history": [{"kind": "create", "prompt": prompt, "at": _now()}] if prompt else [],
            "starter": starter,
        }
        _write_meta(meta)
        return public(meta)


def update_meta(view_id: str, **patch: Any) -> dict[str, Any]:
    allowed = {"title", "description", "icon", "pinned", "order"}
    with _LOCK:
        meta = _read_meta(view_id)
        for key, value in patch.items():
            if key not in allowed or value is None:
                continue
            if key == "icon" and not _ICON_RE.match(str(value)):
                continue
            if key in ("title", "description"):
                value = str(value).strip()[: 80 if key == "title" else 240]
            meta[key] = value
        meta["updated_at"] = _now()
        _write_meta(meta)
        return public(meta)


def reorder(ids: list[str]) -> list[dict[str, Any]]:
    with _LOCK:
        for idx, view_id in enumerate(ids):
            try:
                meta = _read_meta(view_id)
            except ViewNotFound:
                continue
            meta["order"] = idx
            _write_meta(meta)
    return list_views()


def add_history(view_id: str, kind: str, prompt: str) -> None:
    with _LOCK:
        meta = _read_meta(view_id)
        meta.setdefault("history", []).append({"kind": kind, "prompt": prompt[:2000], "at": _now()})
        meta["history"] = meta["history"][-50:]
        _write_meta(meta)


def read_version(view_id: str, version: int | None = None) -> str:
    meta = _read_meta(view_id)
    n = version or meta.get("current_version") or 0
    path = view_dir(view_id) / f"v{int(n)}.html"
    if not n or not path.exists():
        raise ViewNotFound(f"{view_id}@v{n}")
    return path.read_text(encoding="utf-8")


def save_version(view_id: str, html: str, *, note: str = "", title: str | None = None, description: str | None = None, icon: str | None = None) -> dict[str, Any]:
    """Persist ``html`` as a new version and make it current."""
    with _LOCK:
        meta = _read_meta(view_id)
        n = max((v["version"] for v in meta.get("versions", [])), default=0) + 1
        (view_dir(view_id) / f"v{n}.html").write_text(html, encoding="utf-8")
        meta.setdefault("versions", []).append({"version": n, "created_at": _now(), "note": note[:200], "bytes": len(html.encode())})
        meta["current_version"] = n
        meta["updated_at"] = _now()
        if title:
            meta["title"] = title.strip()[:80]
        if description:
            meta["description"] = description.strip()[:240]
        if icon and _ICON_RE.match(icon):
            meta["icon"] = icon
        _write_meta(meta)
        return public(meta)


def revert(view_id: str, version: int) -> dict[str, Any]:
    with _LOCK:
        meta = _read_meta(view_id)
        if not any(v["version"] == version for v in meta.get("versions", [])):
            raise ViewNotFound(f"{view_id}@v{version}")
        meta["current_version"] = version
        meta["updated_at"] = _now()
        _write_meta(meta)
        return public(meta)


def delete_view(view_id: str) -> None:
    with _LOCK:
        path = view_dir(view_id)
        if not (path / "meta.json").exists():
            raise ViewNotFound(view_id)
        shutil.rmtree(path)


def get_state(view_id: str) -> dict[str, Any]:
    path = view_dir(view_id) / "state.json"
    if not (view_dir(view_id) / "meta.json").exists():
        raise ViewNotFound(view_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def set_state(view_id: str, state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    raw = json.dumps(state)
    if len(raw.encode()) > MAX_STATE_BYTES:
        raise ValueError(f"state exceeds {MAX_STATE_BYTES // 1024}KB")
    with _LOCK:
        get_state(view_id)  # existence check
        (view_dir(view_id) / "state.json").write_text(raw, encoding="utf-8")
    return state
