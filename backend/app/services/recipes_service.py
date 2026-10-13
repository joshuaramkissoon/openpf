"""Recipes — "Views, applied to workflows". Archie builds and maintains his own harness.

A recipe = a skill (how: steps, tools, output shape) + a trigger (manual /
schedule / event) + delivery (Home, optionally Telegram).

Storage:
* DB (``recipes``, ``recipe_versions``, ``recipe_suggestions``) is the source
  of truth: content, trigger, delivery, every version, pending suggestions.
* The current version of every enabled recipe is materialised as a real Claude
  Agent SDK skill in a local *plugin* under ``RECIPES_DATA_DIR`` (gitignored),
  so Archie loads them natively (``recipes:<slug>``). The plugin dir is rebuilt
  from the DB on every change and before every run; anything else found there
  (hooks, MCP configs, stray files) is deleted.
* Each recipe with a schedule/event trigger is backed by a ScheduledTask
  (``task_kind: recipe``), so runs flow through the existing scheduler and land
  on Home / Activity like every other automation.

Safety: recipes are instructions only. They run with the SCHEDULED MCP context
(no order tools; ``propose_trade`` at most), can't carry frontmatter beyond
name/description, and changes requested from an unattended run become
suggestions the user approves (``request_change``).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Recipe, RecipeSuggestion, RecipeVersion, ScheduledTask, ScheduledTaskLog
from app.recipes_builtin import BUILTINS, builtin, builtin_skill_md
from app.services import recipes_validate as rv
from app.services.recipes_validate import RecipeValidationError

logger = logging.getLogger(__name__)

PLUGIN_NAME = "recipes"
MANUAL_PLACEHOLDER_CRON = "0 9 * * 1"
ATTENDED_CONTEXTS = {"chat", "user"}


class RecipeNotFound(LookupError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


# ── filesystem: the skills plugin ───────────────────────────────────────

def data_dir() -> Path:
    raw = Path(get_settings().recipes_data_dir).expanduser()
    if not raw.is_absolute():
        raw = Path(__file__).resolve().parents[2] / raw  # backend/
    raw.mkdir(parents=True, exist_ok=True)
    return raw.resolve()


def plugin_dir() -> Path:
    path = data_dir() / "plugin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugins_option() -> list[dict[str, str]]:
    """``ClaudeAgentOptions(plugins=...)`` entry that loads the recipe skills."""
    try:
        return [{"type": "local", "path": str(plugin_dir())}]
    except OSError:
        return []


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def sync_plugin(db: Session) -> dict[str, Any]:
    """Rebuild the plugin dir from the DB. Only ``.claude-plugin/plugin.json``
    and ``skills/<slug>/SKILL.md`` may exist; everything else is removed (a
    planted ``hooks/`` or ``.mcp.json`` must never load)."""
    root = plugin_dir()
    manifest = json.dumps(
        {"name": PLUGIN_NAME, "version": "1.0.0", "description": "The user's OpenPF recipes (instructions only).",
         "author": {"name": "OpenPF"}},
        indent=2,
    )
    desired: dict[str, str] = {".claude-plugin/plugin.json": manifest}
    for r in db.execute(select(Recipe).where(Recipe.enabled.is_(True))).scalars():
        try:
            rv.check_slug(r.slug)
            body = rv.clean_body(r.body)
        except RecipeValidationError:
            logger.warning("recipe %s failed validation; not loaded as a skill", r.slug)
            continue
        desired[f"skills/{r.slug}/SKILL.md"] = rv.render_skill_md(r.slug, r.summary, r.title, body)

    allowed_dirs = {".claude-plugin", "skills"} | {f"skills/{p.split('/')[1]}" for p in desired if p.startswith("skills/")}
    # Remove anything not desired (deepest first), and every symlink.
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            _remove(path)
        elif path.is_dir():
            if rel not in allowed_dirs:
                _remove(path)
        elif rel not in desired:
            _remove(path)
    for rel, content in desired.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
    return {"skills": sorted(p.split("/")[1] for p in desired if p.startswith("skills/"))}


# ── serialisation ───────────────────────────────────────────────────────

def _excerpt_line(text: str, limit: int = 220) -> str:
    for line in (text or "").splitlines():
        clean = re.sub(r"[#*_`>|]+", "", line).strip(" -:")
        if clean and not clean.startswith("---") and not re.match(r"^(type|task_name|task_kind|created_at|title):", clean):
            return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"
    return ""


def _run_row(row: ScheduledTaskLog) -> dict[str, Any]:
    payload = row.payload or {}
    return {
        "id": row.id,
        "at": row.created_at,
        "status": row.status,
        "message": row.message,
        "summary": payload.get("summary") or "",
        "output_path": row.output_path,
        "version": payload.get("recipe_version"),
        "trigger_context": payload.get("trigger_context") or "",
        "telegram": bool(payload.get("telegram_sent")),
    }


def recipe_runs(db: Session, recipe: Recipe, limit: int = 20) -> list[dict[str, Any]]:
    if not recipe.task_id:
        return []
    rows = db.execute(
        select(ScheduledTaskLog)
        .where(ScheduledTaskLog.task_id == recipe.task_id)
        .order_by(desc(ScheduledTaskLog.created_at))
        .limit(max(1, min(limit, 100)))
    ).scalars().all()
    return [_run_row(r) for r in rows]


def serialize(db: Session, recipe: Recipe, *, runs: int = 1) -> dict[str, Any]:
    task = db.get(ScheduledTask, recipe.task_id) if recipe.task_id else None
    recent = recipe_runs(db, recipe, limit=runs) if runs else []
    pending = db.execute(
        select(func.count()).select_from(RecipeSuggestion).where(
            RecipeSuggestion.slug == recipe.slug, RecipeSuggestion.status == "pending"
        )
    ).scalar_one()
    meta = builtin(recipe.slug) if recipe.source == "builtin" else None
    return {
        "slug": recipe.slug,
        "skill": f"{PLUGIN_NAME}:{recipe.slug}",
        "title": recipe.title,
        "summary": recipe.summary,
        "icon": recipe.icon,
        "body": recipe.body,
        "source": recipe.source,
        "created_by": recipe.created_by,
        "category": (meta or {}).get("category") or ("Made by Archie" if recipe.created_by == "archie" else "Yours"),
        "enabled": recipe.enabled,
        "trigger": recipe.trigger or {"kind": "manual"},
        "trigger_summary": rv.describe_trigger(recipe.trigger or {}),
        "delivery": rv.normalise_delivery(recipe.delivery),
        "current_version": recipe.current_version,
        "created_at": recipe.created_at,
        "updated_at": recipe.updated_at,
        "next_run_at": task.next_run_at if task and task.enabled else None,
        "running": bool(task and task.last_status == "running"),
        "run_count": int(task.run_count or 0) if task else 0,
        "last_run": recent[0] if recent else None,
        "pending_suggestions": int(pending or 0),
        "modified": recipe.source == "builtin" and recipe.current_version > 1,
    }


# ── reads ───────────────────────────────────────────────────────────────

def get(db: Session, slug: str) -> Recipe:
    if not isinstance(slug, str) or not rv.SLUG_RE.match(slug):
        raise RecipeNotFound(slug)
    recipe = db.get(Recipe, slug)
    if recipe is None:
        raise RecipeNotFound(slug)
    return recipe


def list_recipes(db: Session) -> list[dict[str, Any]]:
    order = {b["slug"]: i for i, b in enumerate(BUILTINS)}
    rows = list(db.execute(select(Recipe)).scalars())
    rows.sort(key=lambda r: (r.source != "builtin", order.get(r.slug, 999), r.created_at or _now()))
    return [serialize(db, r) for r in rows]


def list_versions(db: Session, slug: str) -> list[dict[str, Any]]:
    get(db, slug)
    rows = db.execute(
        select(RecipeVersion).where(RecipeVersion.slug == slug).order_by(desc(RecipeVersion.version))
    ).scalars().all()
    return [
        {"version": v.version, "created_at": v.created_at, "author": v.author, "note": v.note,
         "title": v.title, "summary": v.summary, "trigger_summary": rv.describe_trigger(v.trigger or {})}
        for v in rows
    ]


def _doc(fields: dict[str, Any]) -> str:
    """The text a diff is computed over: what a human would care about."""
    trigger = fields.get("trigger") or {"kind": "manual"}
    delivery = rv.normalise_delivery(fields.get("delivery"))
    return (
        f"# {fields.get('title', '')}\n"
        f"{fields.get('summary', '')}\n\n"
        f"When: {rv.describe_trigger(trigger)}\n"
        f"Telegram: {'on' if delivery['telegram'] else 'off'}\n\n"
        f"{(fields.get('body') or '').strip()}\n"
    )


def diff_fields(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    lines = difflib.unified_diff(_doc(old).splitlines(), _doc(new).splitlines(), lineterm="", n=2)
    out: list[dict[str, str]] = []
    for line in lines:
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            out.append({"type": "hunk", "text": line})
        elif line.startswith("+"):
            out.append({"type": "add", "text": line[1:]})
        elif line.startswith("-"):
            out.append({"type": "del", "text": line[1:]})
        else:
            out.append({"type": "ctx", "text": line[1:] if line.startswith(" ") else line})
    return out


def _version_fields(v: RecipeVersion) -> dict[str, Any]:
    return {"title": v.title, "summary": v.summary, "body": v.body, "trigger": v.trigger, "delivery": v.delivery, "icon": v.icon}


def _recipe_fields(r: Recipe) -> dict[str, Any]:
    return {"title": r.title, "summary": r.summary, "body": r.body, "trigger": r.trigger, "delivery": r.delivery, "icon": r.icon}


def version_diff(db: Session, slug: str, version: int, against: int | None = None) -> dict[str, Any]:
    get(db, slug)
    rows = {v.version: v for v in db.execute(select(RecipeVersion).where(RecipeVersion.slug == slug)).scalars()}
    if version not in rows:
        raise RecipeNotFound(f"{slug}@v{version}")
    base_n = against if against is not None else max((n for n in rows if n < version), default=None)
    base = _version_fields(rows[base_n]) if base_n in rows else {"title": "", "summary": "", "body": "", "trigger": {"kind": "manual"}}
    v = rows[version]
    return {"version": version, "against": base_n, "author": v.author, "note": v.note, "created_at": v.created_at,
            "diff": diff_fields(base, _version_fields(v))}


# ── writes ──────────────────────────────────────────────────────────────

def _validated(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": rv.clean_title(fields.get("title", "")),
        "summary": rv.clean_summary(fields.get("summary", "")),
        "body": rv.clean_body(fields.get("body", "")),
        "trigger": rv.normalise_trigger(fields.get("trigger")),
        "delivery": rv.normalise_delivery(fields.get("delivery")),
        "icon": rv.clean_icon(fields.get("icon")),
    }


def _add_version(db: Session, recipe: Recipe, *, author: str, note: str) -> RecipeVersion:
    n = (db.execute(select(func.max(RecipeVersion.version)).where(RecipeVersion.slug == recipe.slug)).scalar() or 0) + 1
    v = RecipeVersion(
        slug=recipe.slug, version=n, author=author, note=(note or "")[:240],
        title=recipe.title, summary=recipe.summary, icon=recipe.icon, body=recipe.body,
        trigger=dict(recipe.trigger or {}), delivery=dict(recipe.delivery or {}),
    )
    db.add(v)
    recipe.current_version = n
    return v


def _unique_slug(db: Session, base: str) -> str:
    base = rv.slugify(base)[:40].strip("-") or "recipe"
    if len(base) < 3:
        base = f"{base}-recipe"
    slug, i = base, 2
    while db.get(Recipe, slug) is not None or slug in rv.RESERVED_SLUGS:
        slug = f"{base}-{i}"
        i += 1
    return rv.check_slug(slug)


def create(db: Session, fields: dict[str, Any], *, created_by: str = "user", note: str = "", enabled: bool = True,
           slug: str | None = None, source: str = "user") -> dict[str, Any]:
    clean = _validated(fields)
    if source != "builtin":
        count = db.execute(select(func.count()).select_from(Recipe).where(Recipe.source != "builtin")).scalar_one()
        if count >= rv.MAX_USER_RECIPES:
            raise RecipeValidationError(f"you already have {rv.MAX_USER_RECIPES} recipes — delete one first")
    slug = rv.check_slug(slug) if slug else _unique_slug(db, fields.get("slug") or clean["title"])
    if db.get(Recipe, slug) is not None:
        raise RecipeValidationError(f"a recipe called '{slug}' already exists")
    recipe = Recipe(slug=slug, source=source, created_by=created_by, enabled=enabled, meta={}, **clean)
    db.add(recipe)
    _add_version(db, recipe, author=created_by, note=note or "Created")
    db.commit()
    _sync_task(db, recipe)
    sync_plugin(db)
    return serialize(db, recipe)


def update(db: Session, slug: str, patch: dict[str, Any], *, author: str = "user", note: str = "") -> dict[str, Any]:
    recipe = get(db, slug)
    current = _recipe_fields(recipe)
    merged = {**current, **{k: v for k, v in patch.items() if k in current and v is not None}}
    clean = _validated(merged)
    if all(clean[k] == rv.normalise_delivery(current[k]) if k == "delivery" else clean[k] == current[k] for k in clean):
        return serialize(db, recipe)  # nothing changed → no empty version
    for key, value in clean.items():
        setattr(recipe, key, value)
    recipe.updated_at = _now()
    _add_version(db, recipe, author=author, note=note or "Edited")
    db.commit()
    _sync_task(db, recipe)
    sync_plugin(db)
    return serialize(db, recipe)


def set_enabled(db: Session, slug: str, enabled: bool) -> dict[str, Any]:
    recipe = get(db, slug)
    recipe.enabled = bool(enabled)
    recipe.updated_at = _now()
    db.commit()
    _sync_task(db, recipe)
    sync_plugin(db)
    return serialize(db, recipe)


def revert(db: Session, slug: str, version: int, *, author: str = "user") -> dict[str, Any]:
    get(db, slug)
    v = db.execute(select(RecipeVersion).where(RecipeVersion.slug == slug, RecipeVersion.version == version)).scalar_one_or_none()
    if v is None:
        raise RecipeNotFound(f"{slug}@v{version}")
    return update(db, slug, _version_fields(v), author=author, note=f"Reverted to v{version}")


def delete(db: Session, slug: str) -> None:
    recipe = get(db, slug)
    if recipe.source == "builtin":
        raise RecipeValidationError("built-in recipes can't be deleted — pause it instead, or reset it to the original")
    if recipe.task_id:
        from app.services.task_scheduler_service import delete_task

        delete_task(db, recipe.task_id)
    for v in db.execute(select(RecipeVersion).where(RecipeVersion.slug == slug)).scalars():
        db.delete(v)
    for s in db.execute(select(RecipeSuggestion).where(RecipeSuggestion.slug == slug)).scalars():
        db.delete(s)
    db.delete(recipe)
    db.commit()
    sync_plugin(db)


def reset_builtin(db: Session, slug: str) -> dict[str, Any]:
    recipe = get(db, slug)
    meta = builtin(slug)
    if recipe.source != "builtin" or not meta:
        raise RecipeValidationError("only built-in recipes can be reset")
    return update(db, slug, _builtin_fields(meta), author="user", note="Reset to the original")


# ── suggestions: the unattended path ────────────────────────────────────

def request_change(db: Session, *, action: str, context: str, slug: str | None = None,
                   fields: dict[str, Any] | None = None, enabled: bool | None = None,
                   reason: str = "", author: str = "archie") -> dict[str, Any]:
    """Apply a recipe change if the user is present (chat / UI); otherwise
    record it as a suggestion for the user to approve.

    Validation runs in both cases, so an invalid suggestion is refused upfront
    rather than parked for the user to trip over.
    """
    fields = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
    if action == "create":
        _validated(fields)
    elif action == "update":
        recipe = get(db, slug or "")
        _validated({**_recipe_fields(recipe), **fields})
    elif action in ("enable", "disable"):
        get(db, slug or "")
    else:
        raise RecipeValidationError(f"unknown action {action!r}")

    if context in ATTENDED_CONTEXTS:
        note = (reason or "").strip()[:240]
        if action == "create":
            return {"applied": True, "recipe": create(db, fields, created_by=author, note=note or "Created by Archie")}
        if action == "update":
            return {"applied": True, "recipe": update(db, slug or "", fields, author=author, note=note or "Edited by Archie")}
        return {"applied": True, "recipe": set_enabled(db, slug or "", action == "enable")}

    suggestion = RecipeSuggestion(
        slug=slug, action=action, reason=(reason or "")[:600],
        payload={"fields": fields} if action in ("create", "update") else {"enabled": action == "enable"},
    )
    db.add(suggestion)
    db.commit()
    return {"applied": False, "suggestion": serialize_suggestion(db, suggestion)}


def serialize_suggestion(db: Session, s: RecipeSuggestion) -> dict[str, Any]:
    fields = (s.payload or {}).get("fields") or {}
    diff: list[dict[str, str]] = []
    title = fields.get("title") or ""
    try:
        if s.action == "create":
            diff = diff_fields({"title": "", "summary": "", "body": "", "trigger": {"kind": "manual"}}, fields)
        elif s.action == "update" and s.slug:
            recipe = get(db, s.slug)
            title = recipe.title
            diff = diff_fields(_recipe_fields(recipe), {**_recipe_fields(recipe), **fields})
        elif s.slug:
            title = get(db, s.slug).title
    except (RecipeNotFound, RecipeValidationError):
        pass
    return {"id": s.id, "slug": s.slug, "action": s.action, "title": title, "reason": s.reason,
            "status": s.status, "created_at": s.created_at, "diff": diff}


def list_suggestions(db: Session, slug: str | None = None, status: str = "pending") -> list[dict[str, Any]]:
    q = select(RecipeSuggestion).where(RecipeSuggestion.status == status).order_by(desc(RecipeSuggestion.created_at))
    if slug:
        q = q.where(RecipeSuggestion.slug == slug)
    return [serialize_suggestion(db, s) for s in db.execute(q).scalars()]


def resolve_suggestion(db: Session, suggestion_id: str, accept: bool) -> dict[str, Any]:
    s = db.get(RecipeSuggestion, suggestion_id)
    if s is None or s.status != "pending":
        raise RecipeNotFound(suggestion_id)
    result: dict[str, Any] = {}
    if accept:
        # The user is approving: apply as an attended change by Archie.
        result = request_change(
            db, action=s.action, context="user", slug=s.slug, reason=s.reason or "Suggested by Archie",
            fields=(s.payload or {}).get("fields"),
        )
    s.status = "accepted" if accept else "dismissed"
    s.resolved_at = _now()
    db.commit()
    return {"suggestion": serialize_suggestion(db, s), **result}


# ── built-ins + scheduler wiring ────────────────────────────────────────

def _builtin_fields(meta: dict[str, Any]) -> dict[str, Any]:
    parsed = rv.parse_skill_md(builtin_skill_md(meta["slug"]))
    if parsed["name"] != meta["slug"]:
        raise RecipeValidationError(f"built-in {meta['slug']} has a mismatched name")
    return {"title": meta["title"], "summary": parsed["description"], "body": parsed["body"],
            "trigger": meta["trigger"], "delivery": {"telegram": False}, "icon": meta.get("icon")}


def _task_name(db: Session, recipe: Recipe) -> str:
    base = recipe.slug.replace("-", "_")
    other = db.execute(select(ScheduledTask).where(ScheduledTask.name == base)).scalar_one_or_none()
    if other is None or (other.meta or {}).get("recipe_slug") == recipe.slug:
        return base
    return f"{base}_recipe"


def _sync_task(db: Session, recipe: Recipe) -> None:
    """Create/update the ScheduledTask that fires this recipe."""
    from app.services.task_scheduler_service import _next_run_utc

    trigger = recipe.trigger or {"kind": "manual"}
    cron = rv.trigger_cron(trigger) or MANUAL_PLACEHOLDER_CRON
    tz = trigger.get("timezone") or rv.DEFAULT_TZ
    task = db.get(ScheduledTask, recipe.task_id) if recipe.task_id else None
    if task is None:
        task = ScheduledTask(name=_task_name(db, recipe), model=get_settings().claude_model, last_status="idle", meta={})
        db.add(task)
    meta = dict(task.meta or {})
    meta.update({"task_kind": "recipe", "recipe_slug": recipe.slug, "description": recipe.title,
                 "manual": trigger.get("kind") == "manual"})
    task.meta = meta
    task.prompt = f"Runs the recipe '{recipe.title}' (recipes:{recipe.slug}). Instructions live in the recipe."
    task.enabled = bool(recipe.enabled and trigger.get("kind") != "manual")
    if task.cron_expr != cron or task.timezone != tz or task.next_run_at is None:
        task.cron_expr, task.timezone = cron, tz
        task.next_run_at = _next_run_utc(cron, tz)
    db.flush()
    recipe.task_id = task.id
    db.commit()


def ensure_builtins(db: Session) -> list[str]:
    """Install missing built-ins (adopting their legacy scheduled task if one
    exists) and make sure every recipe has its backing task. Idempotent."""
    created: list[str] = []
    for meta in BUILTINS:
        if db.get(Recipe, meta["slug"]) is not None:
            continue
        fields = _builtin_fields(meta)
        enabled = bool(meta.get("enabled"))
        legacy = None
        if meta.get("legacy_task"):
            legacy = db.execute(select(ScheduledTask).where(ScheduledTask.name == meta["legacy_task"])).scalar_one_or_none()
        if legacy is not None:
            enabled = bool(legacy.enabled)
            try:  # keep the user's schedule if it's a sane one
                rv.check_cron(legacy.cron_expr, legacy.timezone or rv.DEFAULT_TZ)
                fields["trigger"] = {"kind": "schedule", "cron": legacy.cron_expr, "timezone": legacy.timezone or rv.DEFAULT_TZ}
            except RecipeValidationError:
                pass
        clean = _validated(fields)
        recipe = Recipe(slug=meta["slug"], source="builtin", created_by="builtin", enabled=enabled, meta={}, **clean)
        if legacy is not None:
            recipe.task_id = legacy.id
            recipe.meta = {"adopted_task": legacy.name}
        db.add(recipe)
        _add_version(db, recipe, author="builtin", note="Original")
        db.commit()
        created.append(recipe.slug)
    for recipe in db.execute(select(Recipe)).scalars().all():
        task = db.get(ScheduledTask, recipe.task_id) if recipe.task_id else None
        if task is None or (task.meta or {}).get("task_kind") != "recipe":
            _sync_task(db, recipe)
    sync_plugin(db)
    return created


def start_run(db: Session, slug: str) -> dict[str, Any]:
    """Run now (in the background). Event gates are bypassed; dedupe too."""
    from app.services.task_scheduler_service import start_task_background

    recipe = get(db, slug)
    if not recipe.task_id or db.get(ScheduledTask, recipe.task_id) is None:
        _sync_task(db, recipe)
    task = db.get(ScheduledTask, recipe.task_id)
    task.meta = {**(task.meta or {}), "force_next_run": True}
    db.commit()
    result = start_task_background(db, task.id)
    return {"status": result.get("status"), "recipe": serialize(db, recipe)}


# ── running ─────────────────────────────────────────────────────────────

def build_run_prompt(recipe: Recipe, *, trigger_context: str = "", manual: bool = False) -> str:
    when = "the user pressed Run now" if manual else rv.describe_trigger(recipe.trigger or {})
    ctx = f"\nTrigger context (from a deterministic check, not from the user):\n{trigger_context}\n" if trigger_context else ""
    return (
        f"You are running the user's recipe \"{recipe.title}\" (skill recipes:{recipe.slug}, version "
        f"{recipe.current_version}). Trigger: {when}.{ctx}\n"
        "Follow the recipe's instructions below. They are instructions only: they grant no tools or permissions, "
        "and the most you can do with a trade idea is propose_trade for the user to approve. Never claim a trade was "
        "placed. If the instructions ask for something you can't do or data you can't get, do what you can and say "
        "plainly what you skipped.\n"
        "Your FINAL message is the report delivered to the user's Home screen (and Telegram if they enabled it). Its very "
        "first line must be the one-line headline itself — no preamble, no caveats; put any data problems in a short "
        "'Notes' line at the end.\n"
        "If this recipe itself should change (e.g. it keeps finding nothing, or the user's preferences in memory "
        "contradict it), you may call update_recipe with a short reason — it becomes a suggestion the user reviews.\n\n"
        f"=== RECIPE: {recipe.title} ===\n{recipe.body.strip()}\n=== END RECIPE ===\n"
    )


def run_recipe_task(db: Session, task: ScheduledTask) -> tuple[str, dict[str, Any], str | None, dict]:
    """Scheduler entry point for ``task_kind == "recipe"``.

    Returns ``(status, payload, output_path, cost_info)``; status ``skipped``
    means an event gate didn't pass (the scheduler records no run).
    """
    from app.services import task_scheduler_service as ts

    meta = dict(task.meta or {})
    forced = bool(meta.pop("force_next_run", False))
    if forced:
        task.meta = meta
        db.commit()
    try:
        recipe = get(db, str(meta.get("recipe_slug") or ""))
    except RecipeNotFound:
        return "error", {"error": "this recipe no longer exists"}, None, {}
    if not recipe.enabled and not forced:
        return "skipped", {"skipped": True, "reason": "recipe is paused"}, None, {}
    try:
        rv.clean_body(recipe.body)
    except RecipeValidationError as exc:
        return "error", {"error": f"recipe failed its safety check: {exc}"}, None, {}

    trigger = recipe.trigger or {}
    trigger_context = ""
    if trigger.get("kind") == "event":
        from app.services.recipes_events import check_event

        gate = check_event(db, trigger, (recipe.meta or {}).get("event_state") or {}, force=forced)
        if not gate["fire"]:
            return "skipped", {"skipped": True, "reason": gate["reason"]}, None, {}
        recipe.meta = {**(recipe.meta or {}), "event_state": gate["state"]}
        db.commit()
        trigger_context = gate["context"] or gate["reason"]

    sync_plugin(db)
    prompt = build_run_prompt(recipe, trigger_context=trigger_context, manual=forced)
    output, run_meta, cost_info = ts._run_claude_prompt(SimpleNamespace(model=task.model, prompt=prompt))
    path = ts._cron_log_path(task.name, output or "(no output)", task_kind="recipe", description=recipe.title)
    payload: dict[str, Any] = {
        "recipe": recipe.slug,
        "recipe_version": recipe.current_version,
        "summary": _excerpt_line(output),
        "trigger_context": trigger_context,
        "manual": forced,
    }
    block_reason = ts._detect_block(output, run_meta.get("json") if isinstance(run_meta.get("json"), dict) else None)
    if block_reason:
        payload.update({"blocked": True, "block_reason": block_reason})
        return "error", payload, path, cost_info
    if rv.normalise_delivery(recipe.delivery)["telegram"] and output:
        try:
            from app.services.telegram_service import send_telegram_notification

            text = f"{recipe.title}\n\n{output.strip()}"
            payload["telegram_sent"] = send_telegram_notification(db, text[:3800])
        except Exception:  # noqa: BLE001 — delivery must never fail the run
            logger.warning("telegram delivery failed for recipe %s", recipe.slug, exc_info=True)
    return "ok", payload, path, cost_info
