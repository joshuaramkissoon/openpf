# Archie-Builds — Phase 2 Spec

**Date**: 2026-02-19
**Scope**: Persistent async development agent that Archie proposes tasks to; Josh approves before execution
**Execute in**: Separate Claude Code session — this spec is self-contained

---

## Overview

Archie **proposes** software development tasks based on real friction it encounters. Josh approves proposals. Only approved proposals become active engineer tasks.

The engineer agent:
- Works in an isolated Docker sandbox with a git clone of the repo
- Uses git worktrees for parallel task isolation
- Posts all progress to `#archie-builds` on Slack
- Raises PRs, responds to review comments, and cleans up on merge

**The key invariant**: Archie never directly triggers a build. It proposes → Josh approves → engineer executes. This keeps Josh as the decision node on what gets built, while Archie owns the roadmap thinking.

### Archie's operating principles for proposals

- Only propose when there's a **specific, concrete gap** actually encountered — not hypothetical improvements
- One well-scoped proposal at a time with a clear "why now" and expected outcome
- Self-police vanity features — if it doesn't make Archie materially more useful to Josh, don't propose it
- Log proposals during sessions rather than raising them ad-hoc mid-conversation (see Proposal Queue below)
- Track what's been proposed and shipped to learn what's worth raising

---

## Architecture

```
Archie Chat
  └─ (end of session or explicit trigger)
       └─ mcp__engineer__propose_task(title, rationale, scope, priority)
              ↓  returns {proposal_id, status: "proposed"}
              ↓  posts proposal to #archie-builds Slack for Josh to review

Josh (in Slack or via API)
  └─ approves proposal → mcp__engineer__approve_proposal(proposal_id)
                          OR rejects → mcp__engineer__reject_proposal(proposal_id, reason)

Engineer Service (FastAPI router + background workers)
  ├─ EngineerProposal DB table tracks proposals
  ├─ EngineerTask DB table tracks approved, running tasks
  ├─ On approval → create EngineerTask → start execution:
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
-- Proposals: Archie proposes, Josh approves/rejects
CREATE TABLE engineer_proposals (
    id              TEXT PRIMARY KEY,        -- UUID
    title           TEXT NOT NULL,
    rationale       TEXT NOT NULL,           -- Why Archie is proposing this
    scope           TEXT NOT NULL,           -- What would change
    priority        TEXT NOT NULL DEFAULT 'medium',  -- high | medium | low
    status          TEXT NOT NULL DEFAULT 'proposed',  -- proposed | approved | rejected
    rejection_reason TEXT,                   -- Set if rejected
    task_id         TEXT REFERENCES engineer_tasks(id),  -- Set when approved+started
    proposed_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at      TIMESTAMP,
    slack_message_ts TEXT                    -- Slack message ts for the proposal notification
);

-- Tasks: created only when a proposal is approved
CREATE TABLE engineer_tasks (
    id              TEXT PRIMARY KEY,        -- UUID
    proposal_id     TEXT REFERENCES engineer_proposals(id),
    status          TEXT NOT NULL DEFAULT 'running',
                                             -- running | awaiting_review | addressing_comments | complete | failed
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

CREATE INDEX idx_engineer_proposals_status ON engineer_proposals(status);
CREATE INDEX idx_engineer_tasks_pr ON engineer_tasks(pr_number);
CREATE INDEX idx_engineer_tasks_status ON engineer_tasks(status);
```

SQLAlchemy models: `backend/app/models/engineer_proposal.py`, `backend/app/models/engineer_task.py`
Alembic migration: `backend/alembic/versions/xxxx_add_engineer_tables.py`

---

## Proposal Queue (Archie-side memory)

Archie logs proposal ideas to `.claude/runtime/memory/proposals.md` during sessions rather than raising them mid-conversation. Format:

```markdown
## Pending proposals

- **[date] Live position refresh before destructive actions** — currently relies on stale context before cancellations/sells. A "fetch fresh state" helper would eliminate a class of errors. Priority: high.
- **[date] Instrument cache refresh tool** — all-instruments.json goes stale. MCP tool to trigger refresh. Priority: medium.
```

At end of conversation (or when explicitly asked), Archie surfaces pending proposals via `propose_task`. Once submitted, it moves the entry from `## Pending proposals` to `## Submitted proposals` in memory.

**Trigger mechanism**: hybrid — log ideas mid-session, surface at natural conversation end-points or on user request ("any proposals?"). Never interrupt a response mid-stream to propose.

---

## Initial Backlog (seed proposals from Archie)

These are real friction points Archie identified from actual usage, in priority order:

1. **Live position refresh before destructive actions** — Archie currently relies on stale context data before cancellations/sells. A "fetch fresh state" helper or mandatory pre-execution position fetch would eliminate a class of errors.
2. **Portfolio data freshness** — Context data can be 1-2 days stale. Real-time positions on demand would make analysis significantly more reliable.
3. **Instrument cache refresh MCP tool** — `all-instruments.json` goes stale. An MCP tool to trigger a refresh would unblock several instrument-lookup workflows.
4. **Proposal queue memory structure** — A structured `.claude/runtime/memory/proposals.md` with clear pending/submitted/shipped sections, so Archie can track its own roadmap coherently.

---

## MCP Server: `engineer`

New MCP server at `backend/mcp_servers/engineer.py`. Exposed to Archie via the existing MCP server pattern.

**Archie-facing tools** (propose only — no direct execution):

```python
@tool
def propose_task(title: str, rationale: str, scope: str, priority: str = "medium") -> dict:
    """
    Propose a development task for Josh's approval.
    Posts proposal to #archie-builds Slack. Does NOT start execution.

    Args:
        title: Short task title (< 80 chars)
        rationale: Why this matters — specific friction encountered, not hypothetical
        scope: What would change — files, features, expected outcome
        priority: "high" | "medium" | "low"

    Returns:
        {"proposal_id": str, "status": "proposed"}
    """

@tool
def get_proposal_status(proposal_id: str) -> dict:
    """
    Check if a proposal has been approved, rejected, or is pending.

    Returns:
        {"proposal_id": str, "status": "proposed|approved|rejected", "reason": str|None, "task_id": str|None}
    """

@tool
def list_proposals(status: str = "all") -> list[dict]:
    """
    List proposals. status: "proposed" | "approved" | "rejected" | "all"

    Returns:
        [{"proposal_id": str, "title": str, "status": str, "priority": str, "task_id": str|None}]
    """

@tool
def get_task_status(task_id: str) -> dict:
    """
    Get execution status of an approved+running engineer task.

    Returns:
        {"task_id": str, "status": str, "pr_url": str|None, "pr_number": int|None, "error": str|None}
    """
```

**Josh-facing approval tools** (called via API endpoint or Slack slash command — NOT exposed to Archie):

```python
# REST endpoints only (not MCP):
POST /engineer/proposals/{id}/approve
POST /engineer/proposals/{id}/reject   body: {"reason": str}
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
    allowed_tools.extend([
        "mcp__engineer__propose_task",
        "mcp__engineer__get_proposal_status",
        "mcp__engineer__list_proposals",
        "mcp__engineer__get_task_status",
    ])
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

```
# Proposals
GET  /engineer/proposals                        — list all proposals (filterable by status)
GET  /engineer/proposals/{id}                   — get proposal detail
POST /engineer/proposals/{id}/approve           — approve → kicks off engineer task
POST /engineer/proposals/{id}/reject            — reject, body: {"reason": str}

# Tasks (read-only — execution is internal)
GET  /engineer/tasks                            — list active tasks
GET  /engineer/tasks/{id}                       — task detail + PR status
POST /engineer/tasks/{id}/retry                 — retry a failed task
DELETE /engineer/tasks/{id}                     — cancel (removes worktree, closes PR)
```

**Approval flow in Slack**: The #archie-builds proposal notification should include quick-action buttons (via Slack's Block Kit) so Josh can approve/reject directly in Slack without opening a browser. The Slack webhook handler processes button clicks and calls the internal approve/reject logic.

---

## Archie's Instructions Addition

Add to `.claude/runtime/.claude/CLAUDE.md`:

```markdown
## Archie-Builds (Self-Improvement Pipeline)

You own this platform's development roadmap. When you encounter real friction — tools that are clunky,
data that's stale, workflows that break — you can propose improvements to archie-builds.

**You propose. Josh approves. Then it gets built.**

### How to propose

1. Log the idea to `.claude/runtime/memory/proposals.md` during the session (don't interrupt mid-response)
2. At end of conversation, or when asked, surface pending proposals using `mcp__engineer__propose_task`
3. The proposal appears in #archie-builds on Slack for Josh to approve or reject

### What makes a good proposal

- Rooted in **specific friction you actually encountered** in this or a recent session
- Narrow scope — one clear thing, not a sweeping refactor
- Clear "why now" — what's the concrete cost of not having this?
- Expected outcome — what will be different / better?

### What NOT to propose

- Hypothetical improvements you haven't actually needed
- UI/UX changes without a clear user-facing friction point
- Features that make you more capable in abstract — only propose what makes you more useful to Josh specifically
- Anything you're unsure Josh would value — when in doubt, log it and raise it next session after more evidence

### Proposal memory format

Keep `.claude/runtime/memory/proposals.md` with two sections:
- `## Pending` — ideas to raise
- `## Submitted` — raised proposals with their status

### Check proposal status

Use `mcp__engineer__list_proposals` to see what's pending, approved, or in flight.
Use `mcp__engineer__get_task_status` to check execution progress on approved tasks.
```

---

## File Map

| File | Action |
|---|---|
| `engineer-sandbox/Dockerfile` | New — sandbox container definition |
| `engineer-sandbox/init.sh` | New — first-boot repo clone + gh auth |
| `docker-compose.yml` | Add `engineer-sandbox` service |
| `backend/app/models/engineer_proposal.py` | New — SQLAlchemy model for proposals |
| `backend/app/models/engineer_task.py` | New — SQLAlchemy model for tasks |
| `backend/alembic/versions/xxxx_add_engineer_tables.py` | New — DB migration (proposals + tasks) |
| `.claude/runtime/memory/proposals.md` | New — Archie's proposal queue (pending/submitted) |
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
