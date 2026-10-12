"""The Views builder: turns "show me my dividend income by month" into a page.

Each build is a short, dedicated Claude Agent SDK session that can do exactly
three things:

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
BUILD_TIMEOUT_S = 300
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
        }


_jobs: dict[str, Job] = {}
_tasks: set[asyncio.Task] = set()


class BuildConflict(RuntimeError):
    pass


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def active_job(view_id: str) -> Job | None:
    return next((j for j in _jobs.values() if j.view_id == view_id and j.status in ("queued", "running")), None)


def prepare_job(prompt: str, *, view_id: str | None = None, kind: JobKind | None = None) -> Job:
    """Validate the request, create the view shell if new, and register a job."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    if len(prompt) > 4000:
        raise ValueError("prompt is too long (4000 characters max)")
    created = False
    if view_id:
        store.get_view(view_id)  # raises ViewNotFound
        if active_job(view_id):
            raise BuildConflict("this view is already being built")
        kind = kind if kind in ("edit", "fix") else "edit"
    else:
        meta = store.create_view(title="New view", prompt=prompt, slug=store.prompt_slug(prompt))
        view_id = meta["id"]
        kind = "create"
        created = True
    if kind != "create":
        store.add_history(view_id, kind, prompt)
    # Prune finished jobs so the registry doesn't grow forever.
    if len(_jobs) > 200:
        for jid in [j.id for j in _jobs.values() if j.status in ("done", "error")][:100]:
            _jobs.pop(jid, None)
    job = Job(id=uuid.uuid4().hex[:12], view_id=view_id, kind=kind, prompt=prompt, created_view=created)
    _jobs[job.id] = job
    return job


def start_job(job: Job) -> None:
    """Run ``job`` in the background on the current event loop."""
    task = asyncio.create_task(run_job(job))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


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
    try:
        await asyncio.wait_for(_run(job), timeout=BUILD_TIMEOUT_S)
    except asyncio.TimeoutError:
        _fail(job, "The build took too long and was stopped. Try a simpler request.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("view build %s failed", job.id)
        _fail(job, f"The builder failed: {str(exc)[:300]}")
    finally:
        job.finished_at = job.finished_at or time.time()
        try:
            store.draft_path(job.view_id).unlink(missing_ok=True)
        except (OSError, store.ViewNotFound):
            pass
    return job


def _fail(job: Job, message: str) -> None:
    job.status = "error"
    job.error = message
    job.finished_at = time.time()
    job.say(message)
    if job.created_view:
        try:
            store.delete_view(job.view_id)
        except store.ViewNotFound:
            pass


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
        draft.write_text(store.read_version(job.view_id, version) if version else "", encoding="utf-8")
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
    job.version = saved["current_version"]
    job.status = "done"
    job.finished_at = time.time()
    job.say(f"Done — version {job.version} saved.")
