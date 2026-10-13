"""Filesystem storage for Views (agent-built, sandboxed pages).

Layout under ``VIEWS_DATA_DIR`` (default ``backend/data/views``)::

    <id>/meta.json      id, title, description, icon, pinned, order, timestamps,
                        current_version, versions[], history[] (prompts)
    <id>/v<N>.json      immutable version N — a declarative viz spec (kind "spec")
    <id>/v<N>.html      immutable version N — a legacy code view (kind "code")
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
        "kind": _current_kind(meta),
        "last_good_version": meta.get("last_good_version"),
        "build": meta.get("build"),
    }


def _current_kind(meta: dict[str, Any]) -> str:
    n = meta.get("current_version") or 0
    for v in meta.get("versions", []):
        if v.get("version") == n:
            return v.get("kind") or "code"
    return meta.get("kind") or "spec"


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


_STOP = {
    "a", "an", "the", "me", "my", "mine", "our", "of", "by", "for", "and", "or", "to", "in", "on", "at", "from", "with", "about",
    "show", "build", "make", "create", "give", "get", "see", "view", "page", "dashboard", "chart", "please", "pls", "i", "id",
    "want", "would", "like", "need", "what", "whats", "how", "is", "are", "was", "can", "could", "you", "your", "it", "its",
    "that", "this", "these", "those", "which", "all", "every", "some", "just", "also", "be", "do", "does", "there", "here",
    "up", "into", "over", "per", "so", "much", "many", "lot", "lots", "really", "very", "new", "nice", "simple", "quick",
}
_FILLER_RE = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay)\s+archie[,!.\s]*)?(?:(?:can|could|would)\s+you\s+|please\s+|i(?:'d| would)?\s+(?:like|want|need)\s+(?:to\s+see\s+)?|"
    r"(?:show|give|build|make|create|draw|plot|chart)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:(?:view|page|dashboard|chart)\s+(?:of|for|showing|with)\s+)?)+",
    re.I,
)


def prompt_title(prompt: str) -> str:
    """Deterministic short title from a prompt (used until the builder names the view).

    'can you show me my dividend income by month please' → 'Dividend income by month'.
    """
    text = _FILLER_RE.sub("", (prompt or "").strip().splitlines()[0] if (prompt or "").strip() else "")
    text = re.sub(r"\b(?:please|pls|thanks?|thank you)\b[.!]*", "", text, flags=re.I)
    text = re.sub(r"[^\w\s&%/+'-]", " ", text)
    text = re.sub(r"^(?:my|the|a|an)\s+", "", text.strip(), flags=re.I)
    words = text.split()
    title = " ".join(words[:6])
    if len(title) > 40:
        title = title[:40].rsplit(" ", 1)[0]
    title = title.strip(" -&/")
    return (title[:1].upper() + title[1:]) if title else "New view"


def prompt_slug(prompt: str) -> str:
    """'show me my dividend income by month' → 'dividend-income-month'. URL-safe ASCII."""
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", prompt or "").encode("ascii", "ignore").decode()
    words = [w for w in re.findall(r"[a-z0-9]+", ascii_text.lower()) if w not in _STOP and len(w) < 24]
    return _slug("-".join(words[:4])) if words else "view"


def create_view(*, title: str, description: str = "", icon: str = DEFAULT_ICON, prompt: str = "", starter: str | None = None, slug: str | None = None) -> dict[str, Any]:
    """Create an empty view shell (no versions yet — the builder writes v1).

    The id is a short, unique, URL-safe slug: ``dividend-income``, then
    ``dividend-income-2``…
    """
    with _LOCK:
        base = _slug(slug or title or prompt)[:36].strip("-") or "view"
        view_id = base
        n = 2
        while (data_dir() / view_id).exists() or view_id.startswith("_"):
            view_id = f"{base}-{n}"
            n += 1
            if n > 500:
                view_id = f"{base}-{uuid.uuid4().hex[:6]}"
                break
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


def read_document(view_id: str, version: int | None = None) -> tuple[int, str, str]:
    """Return ``(version, kind, content)`` — kind is ``spec`` (JSON) or ``code`` (HTML)."""
    meta = _read_meta(view_id)
    n = int(version or meta.get("current_version") or 0)
    if not n:
        raise ViewNotFound(f"{view_id}@v0")
    for kind, ext in (("spec", "json"), ("code", "html")):
        path = view_dir(view_id) / f"v{n}.{ext}"
        if path.exists():
            return n, kind, path.read_text(encoding="utf-8")
    raise ViewNotFound(f"{view_id}@v{n}")


def read_version(view_id: str, version: int | None = None) -> str:
    return read_document(view_id, version)[2]


def save_version(view_id: str, html: str, *, note: str = "", title: str | None = None, description: str | None = None, icon: str | None = None, kind: str = "code") -> dict[str, Any]:
    """Persist a new version (HTML for ``code``, JSON text for ``spec``) and make it current."""
    if kind not in ("code", "spec"):
        raise ValueError("kind must be code or spec")
    with _LOCK:
        meta = _read_meta(view_id)
        n = max((v["version"] for v in meta.get("versions", [])), default=0) + 1
        ext = "json" if kind == "spec" else "html"
        (view_dir(view_id) / f"v{n}.{ext}").write_text(html, encoding="utf-8")
        meta.setdefault("versions", []).append({"version": n, "created_at": _now(), "note": note[:200], "bytes": len(html.encode()), "kind": kind})
        meta["current_version"] = n
        meta["kind"] = kind
        meta["build"] = {"status": "done", "at": _now()}
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


def set_build(view_id: str, status: str, *, error: str | None = None, prompt: str | None = None, suggestions: list[dict[str, Any]] | None = None, job_id: str | None = None) -> None:
    """Persist the latest build outcome on the view so it survives restarts and
    crosses processes (the queue worker) — the UI never waits on a build that no
    longer exists."""
    with _LOCK:
        try:
            meta = _read_meta(view_id)
        except ViewNotFound:
            return
        build: dict[str, Any] = {"status": status, "at": _now()}
        if error:
            build["error"] = error[:600]
        if prompt:
            build["prompt"] = prompt[:2000]
        if suggestions:
            build["suggestions"] = suggestions[:4]
        if job_id:
            build["job_id"] = job_id
        meta["build"] = build
        _write_meta(meta)


def mark_good(view_id: str, version: int) -> dict[str, Any]:
    """Remember the newest version that rendered cleanly (for one-click recovery)."""
    with _LOCK:
        meta = _read_meta(view_id)
        if not any(v["version"] == version for v in meta.get("versions", [])):
            raise ViewNotFound(f"{view_id}@v{version}")
        meta["last_good_version"] = version
        _write_meta(meta)
        return public(meta)


def fail_stale_builds(max_age_s: float = 0) -> int:
    """Mark builds still 'queued'/'running' as failed — called at startup (any
    in-flight build died with the previous process). Returns how many."""
    n = 0
    now = datetime.now(timezone.utc)
    for v in list_views():
        b = v.get("build") or {}
        if b.get("status") not in ("queued", "running") or not b.get("job_id"):
            continue  # no job id = still waiting in the cross-process queue
        try:
            age = (now - datetime.fromisoformat(b.get("at"))).total_seconds()
        except (TypeError, ValueError):
            age = max_age_s + 1
        if age >= max_age_s:
            set_build(v["id"], "error", error="The build was interrupted (the app restarted). Retry to build it again.", prompt=b.get("prompt"))
            n += 1
    return n
