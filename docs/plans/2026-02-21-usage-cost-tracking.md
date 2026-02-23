# Usage & Cost Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist `total_cost_usd`, `duration_ms`, and `num_turns` from the Claude Agent SDK's `ResultMessage` for every Archie invocation (chat, scheduled job, agent run) and expose a `Costs` workspace tab in the web app.

**Architecture:** New `UsageRecord` SQLAlchemy entity (no existing tables modified) → `costs_service.py` for write/read → `/api/costs` FastAPI router → `CostsWorkspace.tsx` React component in a new nav tab. Three existing runtime services are each extended to extract and persist cost data from the `ResultMessage` they already receive but currently discard.

**Tech Stack:** Python 3.12, SQLAlchemy (mapped_column style), FastAPI, Pydantic v2, React 18 + TypeScript, axios, dayjs

---

## Key File References

| File | Role |
|------|------|
| `backend/app/models/entities.py` | Add `UsageRecord` entity |
| `backend/app/core/database.py` | Migration runs `create_all` — no changes needed |
| `backend/app/schemas/costs.py` | New: Pydantic response schemas |
| `backend/app/services/costs_service.py` | New: write + query helpers |
| `backend/app/api/costs.py` | New: FastAPI router |
| `backend/app/main.py` | Register the new router (line 59 area) |
| `backend/app/services/claude_chat_runtime.py` | Extend `ReplyResult` (line 266) + capture cost in `ResultMessage` block (line 488) + return at line 636 |
| `backend/app/api/agent.py` | Persist cost after `stream_reply` completes (after line 369) |
| `backend/app/services/task_scheduler_service.py` | Capture cost in `_run_claude_prompt` (line 327) + thread through `_run_task_impl` (line 396) + persist in `run_task_now` / `_run_scheduled_task` |
| `backend/app/services/claude_agent_runtime.py` | Capture cost in `_run_query` (line 324) + return from caller |
| `frontend/src/types/index.ts` | Add `UsageRecord`, `CostSummary` interfaces |
| `frontend/src/api/costs.ts` | New: API calls |
| `frontend/src/components/CostsWorkspace.tsx` | New: workspace component |
| `frontend/src/App.tsx` | Add nav button + section render |

---

## Task 1: Add `UsageRecord` entity

**Files:**
- Modify: `backend/app/models/entities.py` (after line 66, after `AgentRun`)

**Step 1: Add the entity**

In `backend/app/models/entities.py`, after the `AgentRun` class (after line 66), add:

```python
class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)   # chat | scheduled | agent_run
    source_id: Mapped[str] = mapped_column(String(240), index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

**Step 2: Verify entity is visible to `create_all`**

`init_db()` in `database.py` calls `from app.models import entities` then `Base.metadata.create_all()`. Because `UsageRecord` is defined in that module, no other change is needed for the table to be created on next startup.

**Step 3: Commit**

```bash
git add backend/app/models/entities.py
git commit -m "feat: add UsageRecord entity for SDK cost tracking"
```

---

## Task 2: Add Pydantic schemas

**Files:**
- Create: `backend/app/schemas/costs.py`

**Step 1: Create the file**

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UsageRecordItem(BaseModel):
    id: int
    recorded_at: datetime
    source: str
    source_id: str
    model: str
    total_cost_usd: float | None
    duration_ms: int | None
    num_turns: int | None

    model_config = {"from_attributes": True}


class CostBySource(BaseModel):
    chat: float
    scheduled: float
    agent_run: float


class CostSummary(BaseModel):
    all_time_usd: float
    this_month_usd: float
    this_week_usd: float
    by_source: CostBySource
    record_count: int
```

**Step 2: Commit**

```bash
git add backend/app/schemas/costs.py
git commit -m "feat: add cost Pydantic schemas"
```

---

## Task 3: Add `costs_service.py`

**Files:**
- Create: `backend/app/services/costs_service.py`

**Step 1: Create the file**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import UsageRecord


def record(
    db: Session,
    *,
    source: str,
    source_id: str,
    model: str,
    total_cost_usd: float | None,
    duration_ms: int | None,
    num_turns: int | None,
) -> UsageRecord:
    row = UsageRecord(
        source=source,
        source_id=source_id,
        model=model,
        total_cost_usd=total_cost_usd,
        duration_ms=duration_ms,
        num_turns=num_turns,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_summary(db: Session) -> dict:
    rows = db.scalars(select(UsageRecord)).all()

    now_utc = datetime.now(timezone.utc)
    week_ago = now_utc - timedelta(days=7)
    month_ago = now_utc - timedelta(days=30)

    def _cost(r: UsageRecord) -> float:
        return r.total_cost_usd or 0.0

    def _ts(r: UsageRecord) -> datetime:
        return r.recorded_at.replace(tzinfo=timezone.utc) if r.recorded_at.tzinfo is None else r.recorded_at

    all_time = sum(_cost(r) for r in rows)
    this_month = sum(_cost(r) for r in rows if _ts(r) >= month_ago)
    this_week = sum(_cost(r) for r in rows if _ts(r) >= week_ago)

    by_source = {
        "chat": sum(_cost(r) for r in rows if r.source == "chat"),
        "scheduled": sum(_cost(r) for r in rows if r.source == "scheduled"),
        "agent_run": sum(_cost(r) for r in rows if r.source == "agent_run"),
    }

    return {
        "all_time_usd": round(all_time, 6),
        "this_month_usd": round(this_month, 6),
        "this_week_usd": round(this_week, 6),
        "by_source": by_source,
        "record_count": len(rows),
    }


def list_records(db: Session, limit: int = 100) -> list[UsageRecord]:
    return list(
        db.scalars(
            select(UsageRecord).order_by(UsageRecord.recorded_at.desc()).limit(limit)
        ).all()
    )
```

**Step 2: Commit**

```bash
git add backend/app/services/costs_service.py
git commit -m "feat: add costs_service for usage persistence and querying"
```

---

## Task 4: Add `/api/costs` router

**Files:**
- Create: `backend/app/api/costs.py`
- Modify: `backend/app/main.py`

**Step 1: Create the router**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.costs import CostSummary, CostBySource, UsageRecordItem
from app.services import costs_service

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("/summary", response_model=CostSummary)
def get_summary(db: Session = Depends(get_db)):
    data = costs_service.get_summary(db)
    data["by_source"] = CostBySource(**data["by_source"])
    return CostSummary(**data)


@router.get("/records", response_model=list[UsageRecordItem])
def get_records(limit: int = 100, db: Session = Depends(get_db)):
    rows = costs_service.list_records(db, limit=limit)
    return [UsageRecordItem.model_validate(r) for r in rows]
```

**Step 2: Register the router in `main.py`**

In `backend/app/main.py`:
- Line 9: change `from app.api import agent, broker, charts, config, health, leveraged, portfolio, scheduler, strategy, telegram, theses`
  to `from app.api import agent, broker, charts, config, costs, health, leveraged, portfolio, scheduler, strategy, telegram, theses`
- After line 59 (`app.include_router(charts.router, ...)`), add:
  `app.include_router(costs.router, prefix=settings.api_prefix)`

**Step 3: Commit**

```bash
git add backend/app/api/costs.py backend/app/main.py
git commit -m "feat: add /api/costs router with summary and records endpoints"
```

---

## Task 5: Instrument `claude_chat_runtime.py`

**Files:**
- Modify: `backend/app/services/claude_chat_runtime.py`

The `ReplyResult` dataclass is at line 266. The `ResultMessage` handler is at line 488. The return is at line 636.

**Step 1: Extend `ReplyResult`**

Replace the `ReplyResult` dataclass (lines 266–270):

```python
@dataclass
class ReplyResult:
    """Result from a chat reply including stop metadata."""
    text: str
    stop_reason: str | None = None
    result_subtype: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
```

**Step 2: Capture cost fields in the `ResultMessage` handler**

Find the `if msg_type == "result":` block (line 488). It currently reads `stop_reason`, `result_subtype`, `result_text`. Add three more lines:

```python
    if msg_type == "result":
        stop_reason = getattr(message, "stop_reason", None)
        result_subtype = getattr(message, "subtype", None)
        cost_usd = getattr(message, "total_cost_usd", None)
        duration_ms_val = getattr(message, "duration_ms", None)
        num_turns_val = getattr(message, "num_turns", None)
        result_text = getattr(message, "result", None)
        if isinstance(result_text, str) and result_text.strip() and not chunks:
            chunks.append(result_text.strip())
            await on_delta(result_text.strip())
        continue
```

You'll also need to declare `cost_usd: float | None = None`, `duration_ms_val: int | None = None`, `num_turns_val: int | None = None` near the top of the `async with state.lock:` block alongside `stop_reason` (line 447).

**Step 3: Include cost in the return**

Change the `return ReplyResult(...)` at line 636 to:

```python
return ReplyResult(
    text=out or "No response generated.",
    stop_reason=stop_reason,
    result_subtype=result_subtype,
    cost_usd=cost_usd,
    duration_ms=duration_ms_val,
    num_turns=num_turns_val,
)
```

**Step 4: Commit**

```bash
git add backend/app/services/claude_chat_runtime.py
git commit -m "feat: expose cost fields from ResultMessage in ReplyResult"
```

---

## Task 6: Persist chat cost in `agent.py`

**Files:**
- Modify: `backend/app/api/agent.py`

**Step 1: Import costs_service and SessionLocal**

`SessionLocal` is already imported on line 8. Add the costs import after the existing imports (around line 46):

```python
from app.services import costs_service
from app.core.config import get_settings
```

(Note: `get_settings` is already imported at line 9 — skip if so.)

**Step 2: Persist after `stream_reply` completes**

After line 369 (`result_subtype = reply.result_subtype`), add:

```python
                if reply.cost_usd is not None or reply.duration_ms is not None:
                    with SessionLocal() as _cost_db:
                        costs_service.record(
                            _cost_db,
                            source="chat",
                            source_id=session.id,
                            model=settings.claude_model,
                            total_cost_usd=reply.cost_usd,
                            duration_ms=reply.duration_ms,
                            num_turns=reply.num_turns,
                        )
```

**Step 3: Commit**

```bash
git add backend/app/api/agent.py
git commit -m "feat: persist chat session cost to usage_records"
```

---

## Task 7: Instrument `task_scheduler_service.py`

**Files:**
- Modify: `backend/app/services/task_scheduler_service.py`

**Step 1: Import costs_service**

Add at top of file (near other service imports):

```python
from app.services import costs_service
```

**Step 2: Capture cost in `_run_claude_prompt`**

The `_run` inner function (line 322) currently returns just a string. Change it to also return cost data:

```python
async def _run() -> tuple[str, dict]:
    last_text = ""
    cost_info: dict = {}
    async for message in query(prompt=task.prompt, options=options):
        if getattr(message, "type", None) == "result":
            cost_info = {
                "total_cost_usd": getattr(message, "total_cost_usd", None),
                "duration_ms": getattr(message, "duration_ms", None),
                "num_turns": getattr(message, "num_turns", None),
            }
        text = _extract_text_from_sdk_message(message)
        if text:
            last_text = text
    return last_text.strip(), cost_info
```

Change `output = anyio.run(_run)` (line 333) to:

```python
output, cost_info = anyio.run(_run)
```

Change the return at line 338 to:

```python
meta: dict[str, Any] = {}
parsed = _extract_json_block(output)
if parsed:
    meta["json"] = parsed
return output, meta, cost_info
```

**Step 3: Thread `cost_info` through `_run_task_impl`**

Change the function signature at line 374 to `-> tuple[str, dict[str, Any], str | None, dict]`.

Change line 396:
```python
output, meta, cost_info = _run_claude_prompt(task)
```

Change the return at line 410:
```python
return "ok", payload, path, cost_info
```

For the non-Claude branches (lines 378–394), return an empty dict as cost_info:
```python
return "ok", {"result": result}, path, {}
```

**Step 4: Persist cost in `run_task_now`**

In `run_task_now` (line 426), change:
```python
status, payload, output_path = _run_task_impl(db, task)
```
to:
```python
status, payload, output_path, cost_info = _run_task_impl(db, task)
```

After `_record_log(...)`, add:
```python
if cost_info.get("total_cost_usd") is not None or cost_info.get("duration_ms") is not None:
    costs_service.record(
        db,
        source="scheduled",
        source_id=task.name,
        model=task.model or settings.claude_model,
        total_cost_usd=cost_info.get("total_cost_usd"),
        duration_ms=cost_info.get("duration_ms"),
        num_turns=cost_info.get("num_turns"),
    )
```

Apply the same pattern to the scheduled execution path (look for the other call site of `_run_task_impl` in the scheduler thread — search for `_run_task_impl` to find all callers).

**Step 5: Commit**

```bash
git add backend/app/services/task_scheduler_service.py
git commit -m "feat: persist scheduled job cost to usage_records"
```

---

## Task 8: Instrument `claude_agent_runtime.py`

**Files:**
- Modify: `backend/app/services/claude_agent_runtime.py`

**Step 1: Capture cost in `_run_query`**

The inner function at line 320–328:

```python
async def _run_query() -> tuple[str, dict]:
    chunks: list[str] = []
    cost_info: dict = {}
    async with ClaudeSDKClient(options=options) as client:
        await client.query(json.dumps(prompt_payload))
        async for message in client.receive_response():
            if getattr(message, "type", None) == "result":
                cost_info = {
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "duration_ms": getattr(message, "duration_ms", None),
                    "num_turns": getattr(message, "num_turns", None),
                }
            text = _extract_text_from_sdk_message(message)
            if text:
                chunks.append(text)
    return "\n".join(chunks), cost_info
```

Change line 336:
```python
response_text, cost_info = anyio.run(_run_query)
```

**Step 2: Persist cost**

After `response_text, cost_info = anyio.run(_run_query)`, add:

```python
if cost_info.get("total_cost_usd") is not None or cost_info.get("duration_ms") is not None:
    from app.services import costs_service
    from app.core.database import SessionLocal
    with SessionLocal() as _cost_db:
        costs_service.record(
            _cost_db,
            source="agent_run",
            source_id=str(run_id) if "run_id" in dir() else "unknown",
            model=settings.claude_model,
            total_cost_usd=cost_info.get("total_cost_usd"),
            duration_ms=cost_info.get("duration_ms"),
            num_turns=cost_info.get("num_turns"),
        )
```

Note: check what identifier is available in this function's scope for `run_id` — look at the caller (`run_agent` in `agent_service.py`) to see if it passes a run ID. If no run ID is in scope, use the `session_id` from the ResultMessage via `getattr(result_message, "session_id", "unknown")` — capture it alongside cost_info.

**Step 3: Commit**

```bash
git add backend/app/services/claude_agent_runtime.py
git commit -m "feat: persist agent run cost to usage_records"
```

---

## Task 9: Frontend types

**Files:**
- Modify: `frontend/src/types/index.ts`

**Step 1: Add types at the end of the file**

```typescript
export interface UsageRecord {
  id: number
  recorded_at: string
  source: 'chat' | 'scheduled' | 'agent_run' | string
  source_id: string
  model: string
  total_cost_usd: number | null
  duration_ms: number | null
  num_turns: number | null
}

export interface CostBySource {
  chat: number
  scheduled: number
  agent_run: number
}

export interface CostSummary {
  all_time_usd: number
  this_month_usd: number
  this_week_usd: number
  by_source: CostBySource
  record_count: number
}
```

**Step 2: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add CostSummary and UsageRecord frontend types"
```

---

## Task 10: Frontend API client

**Files:**
- Create: `frontend/src/api/costs.ts`

**Step 1: Create the file**

```typescript
import axios from 'axios'
import type { CostSummary, UsageRecord } from '../types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  timeout: 30000,
})

export async function getCostSummary(): Promise<CostSummary> {
  const { data } = await api.get<CostSummary>('/costs/summary')
  return data
}

export async function getCostRecords(limit = 100): Promise<UsageRecord[]> {
  const { data } = await api.get<UsageRecord[]>('/costs/records', { params: { limit } })
  return data
}
```

**Step 2: Commit**

```bash
git add frontend/src/api/costs.ts
git commit -m "feat: add costs API client"
```

---

## Task 11: `CostsWorkspace` component

**Files:**
- Create: `frontend/src/components/CostsWorkspace.tsx`

**Step 1: Create the component**

```tsx
import { useCallback, useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { getCostSummary, getCostRecords } from '../api/costs'
import type { CostSummary, UsageRecord } from '../types'

interface Props {
  onError: (message: string | null) => void
}

function fmtCost(usd: number): string {
  if (usd === 0) return '$0.00'
  if (usd < 0.001) return `$${usd.toFixed(6)}`
  return `$${usd.toFixed(4)}`
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function sourceLabel(source: string): string {
  const MAP: Record<string, string> = {
    chat: 'Chat',
    scheduled: 'Scheduled',
    agent_run: 'Agent Run',
  }
  return MAP[source] ?? source
}

export function CostsWorkspace({ onError }: Props) {
  const [summary, setSummary] = useState<CostSummary | null>(null)
  const [records, setRecords] = useState<UsageRecord[]>([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const [s, r] = await Promise.all([getCostSummary(), getCostRecords(50)])
      setSummary(s)
      setRecords(r)
      onError(null)
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : 'Failed to load cost data')
    } finally {
      setBusy(false)
    }
  }, [onError])

  useEffect(() => { void load() }, [load])

  return (
    <div className="costs-workspace">
      <div className="costs-header-row">
        <h2>API Costs</h2>
        <button className="btn-sm" onClick={() => void load()} disabled={busy}>
          {busy ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {summary && (
        <div className="metric-grid costs-summary">
          <div className="metric-card">
            <span className="metric-label">All time</span>
            <span className="metric-value">{fmtCost(summary.all_time_usd)}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">This month</span>
            <span className="metric-value">{fmtCost(summary.this_month_usd)}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">This week</span>
            <span className="metric-value">{fmtCost(summary.this_week_usd)}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Chat</span>
            <span className="metric-value">{fmtCost(summary.by_source.chat)}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Scheduled</span>
            <span className="metric-value">{fmtCost(summary.by_source.scheduled)}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Agent runs</span>
            <span className="metric-value">{fmtCost(summary.by_source.agent_run)}</span>
          </div>
        </div>
      )}

      <section className="glass-card costs-table-card">
        <div className="section-heading-row">
          <h2>Recent Records</h2>
          <span className="hint">{records.length} entries</span>
        </div>
        {records.length === 0 ? (
          <p className="empty-state">No usage records yet. Records appear after the next Archie invocation.</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Source</th>
                  <th>ID</th>
                  <th>Model</th>
                  <th>Cost</th>
                  <th>Duration</th>
                  <th>Turns</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <tr key={r.id}>
                    <td>{dayjs(r.recorded_at).format('MMM D HH:mm')}</td>
                    <td><span className={`source-badge source-${r.source}`}>{sourceLabel(r.source)}</span></td>
                    <td className="mono truncate-id" title={r.source_id}>{r.source_id.slice(0, 12)}…</td>
                    <td className="mono">{r.model}</td>
                    <td className="cost-value">{r.total_cost_usd != null ? fmtCost(r.total_cost_usd) : '—'}</td>
                    <td>{fmtDuration(r.duration_ms)}</td>
                    <td>{r.num_turns ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
```

**Step 2: Commit**

```bash
git add frontend/src/components/CostsWorkspace.tsx
git commit -m "feat: add CostsWorkspace component"
```

---

## Task 12: Wire up nav tab in `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`

**Step 1: Import the component**

Add to the existing import block (around line 37):

```tsx
import { CostsWorkspace } from './components/CostsWorkspace'
```

**Step 2: Add to `sectionLabels`**

In `sectionLabels` (line 467), add:

```tsx
costs: 'Costs',
```

**Step 3: Add nav button in the left sidebar**

Add a new `<button>` after the Artifacts button (around line 524) and before the Archie button:

```tsx
<button
  className={`nav-btn ${activeSection === 'costs' ? 'active' : ''}`}
  onClick={() => setActiveSection('costs')}
>
  <span className="nav-icon">&#128178;</span> Costs
</button>
```

**Step 4: Add to `<main>` className logic**

In the ternary for `className` on `<main>` (line 656), add a `costs` case:

```tsx
activeSection === 'costs'
  ? 'costs-stage'
  : activeSection === 'leveraged'
    ? 'leveraged-stage'
    : ...
```

**Step 5: Add section render**

After the `{activeSection === 'artifacts' && ...}` block (line 717), add:

```tsx
{activeSection === 'costs' && (
  <CostsWorkspace onError={setError} />
)}
```

**Step 6: Add to mobile "More" menu**

In the More menu dropdown (around line 827), add:

```tsx
<button className={activeSection === 'costs' ? 'active' : ''} onClick={() => { setActiveSection('costs'); setMoreMenuOpen(false) }}>
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="1" x2="12" y2="23" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></svg>
  Costs
</button>
```

**Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: add Costs nav tab to App"
```

---

## Task 13: Manual smoke test

1. Start the backend: `cd backend && uvicorn app.main:app --reload`
2. Confirm the `usage_records` table is created (SQLite: `sqlite3 mypf.db ".tables"`)
3. Call `GET /api/costs/summary` — should return `{"all_time_usd": 0, ...}`
4. Send a chat message to Archie in the UI
5. Call `GET /api/costs/records` — should return one record with a non-null `total_cost_usd`
6. Open the Costs workspace in the UI — summary cards and table should populate
7. Run a scheduled job manually (`POST /api/scheduler/tasks/{id}/run-now`) and verify a new record appears

---

## Notes for the implementer

- **`claude_agent_runtime.py` run ID**: Look at `run_agent` in `backend/app/services/agent_service.py`. The `AgentRun` entity's ID is created there — pass it down to the runtime or capture the `ResultMessage.session_id` as the `source_id`.
- **`_run_task_impl` callers**: Besides `run_task_now`, search for other calls in the file (the scheduler cron path). Apply the same unpacking change.
- **CSS for new elements**: The existing `metric-grid`, `glass-card`, `section-heading-row`, `data-table` classes from `styles.css` should cover most of the UI. Add `.costs-stage { padding: 1.5rem; }` and `.source-badge` styles inline or at the end of `styles.css` if needed.
- **Float precision**: `total_cost_usd` values from the SDK are very small (e.g. `0.000042`). The `fmtCost` function handles this with 6 decimal places for tiny values.
