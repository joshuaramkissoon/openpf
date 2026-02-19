# Archie-Builds — Phase 2 Spec

**Date**: 2026-02-19
**Scope**: Persistent async development agent that Archie delegates software tasks to
**Execute in**: Separate Claude Code session — this spec is self-contained

---

## Overview

Archie can delegate software development tasks to an async engineer agent via a simple MCP tool call. The engineer agent:
- Works in an isolated Docker sandbox with a git clone of the repo
- Uses git worktrees for parallel task isolation
- Posts all progress to `#archie-builds` on Slack
- Raises PRs, responds to review comments, and cleans up on merge

From Archie's perspective: one MCP call → `{task_id}` returned immediately → everything else happens asynchronously via Slack.

---

## Architecture

```
Archie Chat / Scheduler
  └─ mcp__engineer__submit_task(description, context)
        ↓  returns {task_id, status: "submitted"}

Engineer Service (FastAPI router + background workers)
  ├─ EngineerTask DB table tracks all tasks
  ├─ Per task:
  │    1. git pull (on main branch in sandbox)
  │    2. git worktree add worktrees/{task_id}/ -b feat/{task_id}
  │    3. Claude Agent SDK session (CWD = worktree path in sandbox)
  │         ├─ setting_sources=["project"]  → reads .claude/CLAUDE.md (best practices)
  │         ├─ tools: Bash (→ docker exec), Read, Write, Edit, Glob, Grep, WebSearch, Slack MCP
  │         ├─ model: sonnet
  │         └─ no max_turns cap
  │    4. Agent: reads CLAUDE.md → makes changes → runs tests → posts progress to Slack
  │    5. Agent: raises PR via gh CLI → posts PR link to Slack
  │    6. Persist: {session_id, agent_id, worktree_path, pr_url, pr_number} to DB
  │    7. Yield → task status = "awaiting_review"
  │
  ├─ Slack Webhook Handler  POST /webhooks/slack
  │    ├─ Receives PR review comment events from GitHub → posted to #archie-builds
  │    ├─ Looks up task by pr_number → gets session_id + agent_id
  │    ├─ Resumes Claude SDK session (resume=session_id)
  │    ├─ Engineer addresses comment → pushes → re-requests review → yields
  │    └─ Loop until approved
  │
  └─ PR Merge Handler  POST /webhooks/github
       ├─ Detects pull_request.closed where merged=true
       ├─ Looks up task by pr_number
       ├─ git worktree remove worktrees/{task_id}/
       ├─ Posts "✅ Merged: {pr_url}" to Slack thread
       └─ Task status = "complete"
```

---

## Sandbox Container

**Container name**: `mypf-engineer-sandbox`

**Dockerfile** (`engineer-sandbox/Dockerfile`):
```dockerfile
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    git curl wget python3.12 python3-pip nodejs npm \
    && npm install -g bun \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh

WORKDIR /workspace
# Startup: clone repo on first boot
CMD ["tail", "-f", "/dev/null"]
```

**docker-compose addition**:
```yaml
engineer-sandbox:
  build: ./engineer-sandbox
  container_name: mypf-engineer-sandbox
  volumes: []  # No host mounts — fully isolated
  environment:
    - GITHUB_TOKEN=${GITHUB_TOKEN}
    - GIT_AUTHOR_NAME=Archie
    - GIT_AUTHOR_EMAIL=archie@mypf.ai
  restart: unless-stopped
```

**Init script** (`engineer-sandbox/init.sh`) — run once on first boot:
```bash
#!/bin/bash
git clone https://${GITHUB_TOKEN}@github.com/<owner>/mypf.git /workspace/repo
gh auth login --with-token <<< "${GITHUB_TOKEN}"
```

---

## Worktree Management

Each engineer task gets an isolated git worktree in the sandbox:

```
/workspace/
  repo/                     # main clone (main branch only)
  worktrees/
    task-abc123/             # feat/task-abc123 branch
    task-def456/             # feat/task-def456 branch (parallel)
```

**At task start**:
```bash
docker exec mypf-engineer-sandbox bash -c "
  cd /workspace/repo && git pull origin main
  git worktree add /workspace/worktrees/{task_id} -b feat/{task_id}
"
```

**At task complete** (PR merged):
```bash
docker exec mypf-engineer-sandbox bash -c "
  git -C /workspace/repo worktree remove /workspace/worktrees/{task_id} --force
  git -C /workspace/repo branch -d feat/{task_id}
"
```

**SDK `cwd`**: `/workspace/worktrees/{task_id}/` (passed via docker exec prefix in Bash tool)

---

## Database Schema

Add to existing SQLite DB:

```sql
CREATE TABLE engineer_tasks (
    id              TEXT PRIMARY KEY,        -- UUID
    description     TEXT NOT NULL,
    context         TEXT,                    -- JSON string: context passed from Archie
    status          TEXT NOT NULL DEFAULT 'submitted',
                                             -- submitted | running | awaiting_review | addressing_comments | complete | failed
    session_id      TEXT,                    -- Claude SDK session ID (for resumption)
    agent_id        TEXT,                    -- Claude SDK agent ID (for resumption)
    worktree_path   TEXT,                    -- Absolute path in sandbox container
    branch          TEXT,                    -- git branch name
    pr_url          TEXT,
    pr_number       INTEGER,
    slack_thread_ts TEXT,                    -- Slack thread timestamp for replies
    error           TEXT,                    -- Set on failure
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_engineer_tasks_pr ON engineer_tasks(pr_number);
CREATE INDEX idx_engineer_tasks_status ON engineer_tasks(status);
```

SQLAlchemy model: `backend/app/models/engineer_task.py`
Alembic migration: `backend/alembic/versions/xxxx_add_engineer_tasks.py`

---

## MCP Server: `engineer`

New MCP server at `backend/mcp_servers/engineer.py`. Exposed to Archie via the existing MCP server pattern.

**Tools**:

```python
@tool
def submit_task(description: str, context: str = "") -> dict:
    """
    Submit a software development task to the engineer agent.
    Returns immediately with task_id. Task runs asynchronously.

    Args:
        description: What to build or fix (be specific)
        context: Relevant context — current behaviour, desired behaviour, affected files

    Returns:
        {"task_id": str, "status": "submitted"}
    """

@tool
def get_task_status(task_id: str) -> dict:
    """
    Get current status of an engineer task.

    Returns:
        {"task_id": str, "status": str, "pr_url": str|None, "pr_number": int|None, "error": str|None}
    """

@tool
def list_active_tasks() -> list[dict]:
    """
    List all non-completed engineer tasks.

    Returns:
        [{"task_id": str, "description": str, "status": str, "pr_url": str|None}]
    """
```

MCP server config (add to `claude_chat_runtime.py` and `claude_agent_runtime.py`):
```python
engineer_script = _MCP_SERVER_DIR / "engineer.py"
if engineer_script.is_file():
    mcp_servers["engineer"] = {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(engineer_script)],
        "env": _mcp_env,
    }
    allowed_tools.extend(["mcp__engineer__submit_task", "mcp__engineer__get_task_status", "mcp__engineer__list_active_tasks"])
```

---

## Engineer Agent Service

New service: `backend/app/services/engineer_agent_service.py`

**Responsibilities**:
- Accept tasks from the MCP server
- Provision worktree in sandbox
- Run Claude Agent SDK session in the worktree
- Persist session state after PR raised
- Resume session on review comment events
- Clean up worktree on merge

**Session configuration**:
```python
ClaudeAgentOptions(
    system_prompt=ENGINEER_SYSTEM_PROMPT,
    model="claude-sonnet-4-6",
    cwd=f"/workspace/worktrees/{task_id}",  # In sandbox via docker exec wrapper
    max_turns=None,    # No cap
    allowed_tools=[
        "Read", "Write", "Edit", "Glob", "Grep", "Bash", "WebSearch", "WebFetch",
        *SLACK_MCP_TOOLS,
    ],
    setting_sources=["project"],  # Reads .claude/CLAUDE.md — best practices, conventions
    hooks=build_security_hooks(),
)
```

**Bash tool wrapper**: All Bash commands are prefixed with `docker exec mypf-engineer-sandbox` via a custom hook or a thin wrapper script that the agent uses. Alternatively, the agent is instructed in its system prompt to always prefix commands with `docker exec mypf-engineer-sandbox bash -c "..."`.

**Engineer System Prompt**:
```
You are Archie's engineer, responsible for improving the MyPF platform.

Your environment:
- You are working in a git worktree at /workspace/worktrees/{task_id}/
- Run commands with: docker exec mypf-engineer-sandbox bash -c "<cmd>"
- You have gh CLI for GitHub operations and git for version control
- Post Slack updates to #archie-builds as you work

Your workflow:
1. Read CLAUDE.md to understand project conventions and architecture
2. Understand the task fully before starting
3. Make focused, minimal changes — avoid scope creep
4. Run tests after making changes: `docker exec mypf-engineer-sandbox bash -c "cd /workspace/worktrees/{task_id} && <test cmd>"`
5. Commit changes with clear commit messages
6. Raise a PR: `gh pr create --title "..." --body "..."`
7. Post the PR link to #archie-builds
8. When addressing review comments: make changes, re-run tests, push, re-request review

Post Slack updates at key milestones — don't spam. Always follow CLAUDE.md conventions.
```

---

## Slack Integration

**Slack MCP**: Use the existing Claude Code Slack MCP (already in developer's global config). Add to engineer agent's allowed tools.

**Slack tools used**:
- `mcp__slack__post_message` — post to #archie-builds
- `mcp__slack__reply_to_thread` — reply in task thread

**Events posted by the engineer**:

| Event | Message |
|---|---|
| Task started | `🔨 New task received: {description}` |
| Working | `⚙️ {what it's doing}` (sparse updates, not every step) |
| PR raised | `🚀 PR raised: {pr_url} — awaiting review` |
| Addressing comment | `💬 Addressing: "{comment_excerpt}"` |
| Re-review requested | `✅ Changes pushed, re-review requested` |
| Merged | `🎉 Merged: {pr_url}` |
| Failed | `❌ Task failed: {error}` |

Each task uses a **Slack thread** (reply to the original task message). Persist `slack_thread_ts` to DB.

---

## Webhook Handlers

### GitHub Webhook — `POST /webhooks/github`

**Events to handle**:

1. `pull_request_review_comment.created` — review comment on a PR
   - Extract PR number → look up task → resume SDK session
   - Pass comment text + PR context to resumed session
   - Engineer addresses, pushes, requests re-review, yields

2. `pull_request.closed` where `merged=true`
   - Look up task by PR number
   - `git worktree remove` + branch delete in sandbox
   - Post "Merged!" to Slack thread
   - Task status = "complete"

3. `pull_request_review.submitted` where `state=changes_requested`
   - Similar to review comment — resume session with full review context

**Authentication**: Validate `X-Hub-Signature-256` header with shared secret.

### Route

```python
# backend/app/routers/webhooks.py
@router.post("/webhooks/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    ...
```

---

## FastAPI Router: `backend/app/routers/engineer.py`

REST endpoints for managing engineer tasks (primarily for debugging/monitoring):

```
GET  /engineer/tasks           — list all tasks
GET  /engineer/tasks/{id}      — get task detail
POST /engineer/tasks/{id}/retry — retry a failed task
DELETE /engineer/tasks/{id}    — cancel a task (removes worktree)
```

---

## Archie's Instructions Addition

Add to `.claude/runtime/.claude/CLAUDE.md`:

```markdown
## Engineer Agent (Archie-Builds)

Use `mcp__engineer__submit_task` to delegate platform development tasks.

The engineer runs asynchronously. You get back a `task_id` immediately; work happens in the background. Track progress in #archie-builds on Slack, or use `mcp__engineer__get_task_status`.

**Good delegation candidates**:
- Bug fixes (describe: current behaviour, expected behaviour, affected file/feature)
- New features (describe: what it should do, where it fits in the existing architecture)
- Refactors (describe: what to change and why)
- UI improvements (describe: current UX problem and desired outcome)

**Always provide context**:
- What is broken or missing
- Relevant file paths if known
- Expected behaviour
- Any constraints or related features

**Do not delegate**:
- Tasks requiring real-time data or live portfolio state
- Tasks that need your ongoing reasoning (analysis, recommendations)
```

---

## File Map

| File | Action |
|---|---|
| `engineer-sandbox/Dockerfile` | New — sandbox container definition |
| `engineer-sandbox/init.sh` | New — first-boot repo clone + gh auth |
| `docker-compose.yml` | Add `engineer-sandbox` service |
| `backend/app/models/engineer_task.py` | New — SQLAlchemy model |
| `backend/alembic/versions/xxxx_add_engineer_tasks.py` | New — DB migration |
| `backend/app/services/engineer_agent_service.py` | New — core agent runner + lifecycle |
| `backend/mcp_servers/engineer.py` | New — MCP server (3 tools) |
| `backend/app/routers/engineer.py` | New — REST monitoring endpoints |
| `backend/app/routers/webhooks.py` | New — GitHub + Slack webhook handlers |
| `backend/app/main.py` | Register new routers |
| `backend/app/services/claude_chat_runtime.py` | Add engineer MCP server config |
| `backend/app/services/claude_agent_runtime.py` | Add engineer MCP server config |
| `.claude/runtime/.claude/CLAUDE.md` | Add engineer agent section |

---

## Non-Goals (Phase 2)

- Frontend UI for task monitoring (use Slack)
- Multi-repo support
- Automatic rollback on test failure
- Engineer spawning sub-engineers
- Notification channels other than Slack
