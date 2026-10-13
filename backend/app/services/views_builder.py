"""The Views builder: turns "show me my dividend income by month" into a page.

Every request is triaged first (``views_triage``: empty, nonsense, trading,
unavailable data, injection → a clear decline with the closest presets). Then:

* **spec engine (default)** — ``views_spec_builder`` asks a fast model for a
  declarative viz spec (no code), validated + one repair pass, in seconds. With
  no model configured, a new view falls back to the closest preset.
* **code engine (fallback)** — for requests the spec can't express ("needs
  custom code") and for editing legacy code views: the original guarded
  code-writing session below.

Every job ends ``done`` or ``error`` with a message; the outcome is also
persisted on the view (``views_store.set_build``) so a restart or another
process never leaves the UI waiting on a build that no longer exists.

The code session can do exactly three things:

* read the SDK doc / starter examples and the view's own draft file,
* write or edit that ONE draft file (enforced by a PreToolUse hook + a deny-all
  ``can_use_tool``), and
* call ``sample_data`` — an in-process MCP tool returning truncated real data
  from the same whitelisted resolver views use at runtime.

No Bash, no web, no other files. The draft is then statically validated (and
syntax-checked with Node when available); problems are fed back to the same
session for up to two fix rounds before the result is saved as a new version.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.config import get_settings
from app.services import views_store as store
from app.services.views_validate import parse_meta, syntax_check, validate_html
from app.views_sdk import SDK_DIR, sdk_doc, starter

logger = logging.getLogger(__name__)

JobKind = Literal["create", "edit", "fix"]
Engine = Literal["spec", "code"]
BUILD_TIMEOUT_S = 300
SPEC_TIMEOUT_S = 120
MAX_FIX_ROUNDS = 2

_FRIENDLY_SOURCE = {
    "portfolio": "your portfolio",
    "history": "your performance history",
    "orders": "your order history",
    "dividends": "your dividend history",
    "prices": "price history",
    "quotes": "live quotes",
    "fundamentals": "company fundamentals",
    "watchlist": "your watchlist",
    "proposals": "Archie's proposals",
    "plays": "your plays",
}


@dataclass
class Job:
    id: str
    view_id: str
    kind: JobKind
    prompt: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    progress: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    version: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cost_usd: float | None = None
    created_view: bool = False
    engine: Engine = "spec"
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def say(self, text: str) -> None:
        if self.progress and self.progress[-1]["text"] == text:
            return
        self.progress.append({"at": round(time.time() - self.started_at, 1), "text": text})

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "view_id": self.view_id,
            "kind": self.kind,
            "prompt": self.prompt,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "version": self.version,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1),
            "cost_usd": self.cost_usd,
            "engine": self.engine,
            "suggestions": self.suggestions,
        }


_jobs: dict[str, Job] = {}
_tasks: set[asyncio.Task] = set()


class BuildConflict(RuntimeError):
    pass


class ViewDeclined(ValueError):
    """The request can't become a view; carries the explanation + suggestions."""

    def __init__(self, decline: Any):
        super().__init__(decline.message)
        self.decline = decline


def _engine_for(view_id: str, kind: str) -> Engine:
    if kind == "create":
        return "spec"
    try:
        meta = store.get_view(view_id)
    except store.ViewNotFound:
        return "spec"
    if not meta.get("current_version"):
        return "spec"
    return "spec" if meta.get("kind") == "spec" else "code"


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def active_job(view_id: str) -> Job | None:
    return next((j for j in _jobs.values() if j.view_id == view_id and j.status in ("queued", "running")), None)


def prepare_job(prompt: str, *, view_id: str | None = None, kind: JobKind | None = None) -> Job:
    """Triage the request, create the view shell if new, and register a job."""
    from app.services.views_triage import triage

    prompt = (prompt or "").strip()
    if view_id:
        store.get_view(view_id)  # raises ViewNotFound before anything else
    if kind != "fix":  # runtime error reports are machine-written, not user requests
        declined = triage(prompt)
        if declined:
            raise ViewDeclined(declined)
    created = False
    if view_id:
        meta = store.get_view(view_id)
        if active_job(view_id):
            raise BuildConflict("this view is already being built")
        # A shell with no versions yet (a failed create) is rebuilt from scratch.
        kind = "create" if not meta.get("current_version") else (kind if kind in ("edit", "fix") else "edit")
    else:
        meta = store.create_view(title=store.prompt_title(prompt), prompt=prompt, slug=store.prompt_slug(prompt))
        view_id = meta["id"]
        kind = "create"
        created = True
    if kind != "create":
        store.add_history(view_id, kind, prompt)
    # Prune finished jobs so the registry doesn't grow forever.
    if len(_jobs) > 200:
        for jid in [j.id for j in _jobs.values() if j.status in ("done", "error")][:100]:
            _jobs.pop(jid, None)
    job = Job(id=uuid.uuid4().hex[:12], view_id=view_id, kind=kind, prompt=prompt, created_view=created, engine=_engine_for(view_id, kind))
    _jobs[job.id] = job
    store.set_build(view_id, "queued", prompt=prompt, job_id=job.id)
    return job


def start_job(job: Job) -> None:
    """Run ``job`` in the background on the current event loop."""
    task = asyncio.create_task(run_job(job))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── cross-process queue ─────────────────────────────────────────────────
# Archie asks for views from its MCP subprocess. Running a whole builder session
# inside that tool call blocks the chat turn (and nests one agent inside
# another), so the MCP server only *enqueues*: it creates the view shell and
# drops a request file; the API process's worker picks it up and builds it like
# any UI request. The sidebar shows the view as soon as the build lands.

def _queue_dir() -> Path:
    path = store.data_dir() / "_queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def enqueue(prompt: str, *, view_id: str | None = None) -> dict[str, Any]:
    """Queue a build from another process. Returns the target view id/path.

    Raises ``ViewDeclined`` (a ValueError) for requests that can't be views."""
    from app.services.views_triage import triage

    prompt = (prompt or "").strip()
    declined = triage(prompt)
    if declined:
        raise ViewDeclined(declined)
    if view_id:
        store.get_view(view_id)  # raises ViewNotFound
        kind = "edit"
    else:
        meta = store.create_view(title=store.prompt_title(prompt), prompt=prompt, slug=store.prompt_slug(prompt))
        view_id = meta["id"]
        kind = "create"
    store.set_build(view_id, "queued", prompt=prompt)
    req = {"prompt": prompt, "view_id": view_id, "kind": kind, "queued_at": time.time()}
    tmp = _queue_dir() / f".{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(req))
    tmp.replace(tmp.with_name(tmp.name[1:].replace(".tmp", ".json")))
    return {"view_id": view_id, "kind": kind, "path": f"/views/{view_id}"}


def _job_for_request(req: dict[str, Any]) -> Job:
    if req.get("kind") == "create":
        job = Job(id=uuid.uuid4().hex[:12], view_id=req["view_id"], kind="create", prompt=req["prompt"], created_view=True)
        _jobs[job.id] = job
        store.set_build(job.view_id, "queued", prompt=job.prompt, job_id=job.id)
        return job
    return prepare_job(req["prompt"], view_id=req["view_id"])


async def queue_worker(poll_seconds: float = 2.0) -> None:
    """Run in the API process: start builds that other processes queued."""
    while True:
        try:
            for path in sorted(_queue_dir().glob("*.json")):
                try:
                    req = json.loads(path.read_text())
                finally:
                    path.unlink(missing_ok=True)
                try:
                    start_job(_job_for_request(req))
                except Exception as exc:  # noqa: BLE001 — a bad request must not kill the worker
                    logger.warning("queued view build failed to start", exc_info=True)
                    if req.get("view_id"):
                        store.set_build(str(req["view_id"]), "error", error=f"The build couldn't start: {str(exc)[:200]}", prompt=req.get("prompt"))
        except Exception:  # noqa: BLE001
            logger.warning("view queue poll failed", exc_info=True)
        await asyncio.sleep(poll_seconds)


# ── prompt ──────────────────────────────────────────────────────────────
_SYSTEM = """You are the OpenPF view builder. You turn a user's request into one polished, self-contained HTML view that runs inside OpenPF's sandbox, on the user's live portfolio data. Speed matters: aim to finish in a few turns.

Workflow:
1. Call `sample_data` for every source you need — in ONE turn, in parallel — to see the real shapes. Never guess fields.
2. Create: write the complete file with a single Write. Edit/fix: Read the file, then make targeted Edit calls (rewrite with Write only if most of it changes).
3. Reply with one short sentence describing the view. No code in the reply, no questions — make sensible choices yourself.

You may only touch the one file path you are given. You have no network, shell or other files. Follow the SDK below exactly; it is the whole API.

<view_sdk>
{doc}
</view_sdk>

<example_view file="drift-vs-targets.html">
{example}
</example_view>"""


def _system_prompt() -> str:
    ex = starter("drift-vs-targets")
    return _SYSTEM.format(doc=sdk_doc(), example=ex["html"] if ex else "")


def _user_prompt(job: Job, draft: Path, version: int | None) -> str:
    if job.kind == "create":
        return f"Build a view for this request:\n\n{job.prompt}\n\nWrite it to: {draft}"
    if job.kind == "fix":
        return (
            f"The view in {draft} (version {version}) fails at runtime:\n\n{job.prompt}\n\n"
            "Read it, find the cause (check field names against sample_data) and fix it with minimal edits."
        )
    return (
        f"The current view is in {draft} (version {version}). Change request:\n\n{job.prompt}\n\n"
        "Read it and apply the change. Keep everything else as is; update the header comment if the purpose changed."
    )


# ── guardrails ──────────────────────────────────────────────────────────
def _deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


def check_tool_path(tool_name: str, tool_input: dict[str, Any], draft: Path) -> str | None:
    """Return a denial reason, or None if the call is allowed."""
    if tool_name.startswith("mcp__views__"):
        return None if tool_name == "mcp__views__sample_data" else "only sample_data is available"
    if tool_name not in ("Read", "Write", "Edit"):
        return f"{tool_name} is not available to the view builder"
    raw = str(tool_input.get("file_path") or "")
    if not raw:
        return "file_path is required"
    try:
        target = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return "invalid path"
    if target == draft.resolve():
        return None
    if tool_name == "Read":
        try:
            target.relative_to(SDK_DIR.resolve())
            return None
        except ValueError:
            pass
    return f"you may only {'read the SDK or ' if tool_name == 'Read' else ''}write {draft}"


def _hook_for(draft: Path):
    async def guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        reason = check_tool_path(str(input_data.get("tool_name") or ""), input_data.get("tool_input") or {}, draft)
        return _deny(reason) if reason else {}

    return guard


def _sample_data_server(job: Job):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    from app.core.database import SessionLocal
    from app.services.views_data import SOURCES, DataSourceError, resolve, truncate_sample

    schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": list(SOURCES)},
            "params": {"type": "object", "description": "Source params, e.g. {\"range\": \"1y\"} or {\"ticker\": \"AAPL\"}"},
        },
        "required": ["source"],
    }

    @tool("sample_data", "Fetch a truncated live sample from a view data source (same shape openpf.data returns).", schema)
    async def sample_data(args: dict[str, Any]) -> dict[str, Any]:
        source = str(args.get("source") or "")
        params = args.get("params") or {}
        job.say(f"Reading {_FRIENDLY_SOURCE.get(source, source)}…")

        def run() -> Any:
            with SessionLocal() as db:
                return resolve(db, source, params)

        try:
            data = await asyncio.to_thread(run)
            text = json.dumps(truncate_sample(data, max_items=3), default=str)
        except DataSourceError as exc:
            text = f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 — the agent should see it and adapt
            text = f"error: source failed ({type(exc).__name__}: {str(exc)[:200]})"
        return {"content": [{"type": "text", "text": text[:12000]}]}

    return create_sdk_mcp_server(name="views", version="1.0.0", tools=[sample_data])


def _describe_tool(name: str, tool_input: dict[str, Any], draft: Path) -> str | None:
    if name == "Write":
        return "Writing the view…"
    if name == "Edit":
        return "Refining the view…"
    if name == "Read":
        return "Reading the current view…" if str(tool_input.get("file_path")) == str(draft) else "Reviewing the view kit…"
    return None


# ── run ─────────────────────────────────────────────────────────────────
async def run_job(job: Job) -> Job:
    store.set_build(job.view_id, "running", prompt=job.prompt, job_id=job.id)
    try:
        if job.engine == "spec":
            await asyncio.wait_for(_run_spec(job), timeout=SPEC_TIMEOUT_S)
        else:
            await asyncio.wait_for(_run(job), timeout=BUILD_TIMEOUT_S)
    except asyncio.TimeoutError:
        _fail(job, "The build took too long and was stopped. Try a simpler request, or start from a preset.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("view build %s failed", job.id)
        _fail(job, f"The builder failed: {str(exc)[:300]}")
    finally:
        if job.status not in ("done", "error"):  # belt and braces: never leave a job hanging
            _fail(job, "The build stopped unexpectedly. Retry, or start from a preset.")
        job.finished_at = job.finished_at or time.time()
        try:
            store.draft_path(job.view_id).unlink(missing_ok=True)
        except (OSError, store.ViewNotFound):
            pass
    return job


def _fail(job: Job, message: str, *, suggest: bool = True) -> None:
    """End a job as failed. The view shell of a failed create is kept (marked
    failed) so the user sees why and can Retry or dismiss it."""
    from app.services.views_presets import closest_presets

    if suggest and not job.suggestions:
        job.suggestions = [{"slug": p["slug"], "title": p["title"], "why": p.get("why")} for p in closest_presets(job.prompt, 3)]
    job.status = "error"
    job.error = message
    job.finished_at = time.time()
    job.say(message)
    store.set_build(job.view_id, "error", error=message, prompt=job.prompt, suggestions=job.suggestions, job_id=job.id)


def _done(job: Job, version: int, line: str) -> None:
    job.version = version
    job.status = "done"
    job.finished_at = time.time()
    job.say(line)


async def _run_spec(job: Job) -> None:
    """Spec engine: fast model → validated spec. Falls back to the code engine
    for "needs custom code", and to the closest preset when no model exists."""
    from app.services import views_spec_builder as sb
    from app.services.views_presets import best_match, closest_presets
    from app.views_sdk import preset_specs

    job.status = "running"
    current = None
    if job.kind != "create":
        n, kind, content = store.read_document(job.view_id)
        if kind == "spec":
            current = json.loads(content)
    job.say("Designing the view…" if job.kind == "create" else "Applying your change…")
    try:
        result = await sb.build_spec(job.prompt, current)
    except sb.NoSpecProvider:
        if job.kind != "create":
            _fail(job, "No AI model is configured to edit views. Add a TOGETHER_API_KEY (or sign in to Claude) and try again.")
            return
        pick = best_match(job.prompt) or closest_presets(job.prompt, 1)[0]
        spec = preset_specs()[pick["slug"]]
        job.say(f"No AI model is configured — using the closest preset: {pick['title']}.")
        saved = store.save_version(job.view_id, json.dumps(spec, indent=1), note=f"Closest preset (no model): {pick['title']}", title=spec["title"], description=spec.get("description"), icon=spec.get("icon"), kind="spec")
        _done(job, saved["current_version"], "Done — built from a preset. Add a TOGETHER_API_KEY to build custom views.")
        return
    except sb.SpecBuildError as exc:
        logger.warning("spec build failed for %s: %s", job.view_id, exc)
        _fail(job, "The builder couldn't turn that into a view. Try rephrasing it as a chart of your portfolio, or start from a preset.")
        return

    if result.kind == "decline":
        _fail(job, result.reason or "That isn't something a view can show.")
        return
    if result.kind == "needs_code":
        job.say("This needs custom code — building it the slower way…")
        job.engine = "code"
        if current is not None:
            job.kind = "create"
            job.prompt = f"{job.prompt}\n\n(Context: this replaces a chart view titled '{current.get('title', '')}'.)"
        await _run(job)
        return

    spec = result.spec or {}
    job.say("Checking it renders safely…")
    saved = store.save_version(
        job.view_id,
        json.dumps(spec, indent=1),
        note=job.prompt[:200],
        title=spec.get("title"),
        description=spec.get("description"),
        icon=spec.get("icon"),
        kind="spec",
    )
    _done(job, saved["current_version"], f"Done — version {saved['current_version']} saved.")


async def _run(job: Job) -> None:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        HookMatcher,
        PermissionResultDeny,
        ResultMessage,
        StreamEvent,
        ToolUseBlock,
    )

    from app.services.claude_sdk_config import configure_sdk_auth

    configure_sdk_auth()
    settings = get_settings()
    job.status = "running"
    job.say("Planning the view…" if job.kind == "create" else "Opening the current view…")

    draft = store.draft_path(job.view_id)
    version = None
    if job.kind != "create":
        meta = store.get_view(job.view_id)
        version = meta["current_version"] or None
        draft.write_text(store.read_version(job.view_id, version) if version and meta.get("kind") == "code" else "", encoding="utf-8")
    else:
        draft.unlink(missing_ok=True)

    async def deny_rest(tool_name: str, tool_input: dict[str, Any], context: Any):
        return PermissionResultDeny(message=f"{tool_name} is not available to the view builder")

    options = ClaudeAgentOptions(
        system_prompt=_system_prompt(),
        model=settings.views_model,
        cwd=str(draft.parent),
        tools=["Read", "Write", "Edit"],
        allowed_tools=["Read", "Write", "Edit", "mcp__views__sample_data"],
        disallowed_tools=["Bash", "WebFetch", "WebSearch", "Task", "Glob", "Grep", "NotebookEdit", "TodoWrite"],
        mcp_servers={"views": _sample_data_server(job)},
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_hook_for(draft)])]},
        can_use_tool=deny_rest,
        setting_sources=[],
        max_turns=24,
        max_budget_usd=2.0,
        include_partial_messages=True,
    )

    async def converse(client: Any, message: str) -> None:
        await client.query(message)
        async for msg in client.receive_response():
            if isinstance(msg, StreamEvent):
                # Announce a tool as soon as the model starts writing it — a Write
                # streams for most of the build, so this is the line users watch.
                ev = msg.event or {}
                block = ev.get("content_block") or {}
                if ev.get("type") == "content_block_start" and block.get("type") == "tool_use":
                    line = {"Write": "Writing the view…", "Edit": "Refining the view…"}.get(block.get("name"))
                    if line:
                        job.say(line)
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        line = _describe_tool(block.name, block.input or {}, draft)
                        if line:
                            job.say(line)
            elif isinstance(msg, ResultMessage):
                cost = getattr(msg, "total_cost_usd", None)
                if cost is not None:
                    job.cost_usd = round(cost, 4)
                if getattr(msg, "is_error", False):
                    raise RuntimeError(getattr(msg, "result", None) or getattr(msg, "subtype", "error"))

    async with ClaudeSDKClient(options=options) as client:
        await converse(client, _user_prompt(job, draft, version))
        for round_no in range(MAX_FIX_ROUNDS + 1):
            html = draft.read_text(encoding="utf-8") if draft.exists() else ""
            job.say("Checking it's safe and valid…")
            problems = validate_html(html)
            if not problems:
                problems = await asyncio.to_thread(syntax_check, html)
            if not problems:
                break
            if round_no == MAX_FIX_ROUNDS:
                raise RuntimeError("the generated view failed validation: " + "; ".join(problems[:3]))
            job.say(f"Fixing {len(problems)} issue{'s' if len(problems) != 1 else ''}…")
            await converse(client, f"The file {draft} failed validation. Fix every problem:\n- " + "\n- ".join(problems))

    meta = parse_meta(html)
    saved = store.save_version(
        job.view_id,
        html,
        note=job.prompt[:200],
        title=meta.get("title"),
        description=meta.get("description"),
        icon=meta.get("icon"),
    )
    _done(job, saved["current_version"], f"Done — version {saved['current_version']} saved.")
