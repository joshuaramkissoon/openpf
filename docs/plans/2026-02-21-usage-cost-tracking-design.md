# Usage & Cost Tracking — Design

**Date:** 2026-02-21
**Status:** Approved

---

## Context

The Claude Agent SDK (`ResultMessage`) exposes `total_cost_usd`, `duration_ms`, `num_turns`, and `session_id` at the end of every agent run. Currently none of this data is persisted. The web app has no cost visibility. Three runtime entry points exist: chat sessions, scheduled jobs, and agent analyst runs.

---

## Goals

- Persist per-invocation cost data for all three runtime entry points
- Surface cost totals (all-time, monthly, weekly) broken down by source in the UI
- Keep implementation minimal — no token-level granularity needed, just cost + duration + turns

---

## Data Model

New table `usage_records` — one row per completed SDK invocation:

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | autoincrement |
| `recorded_at` | datetime | indexed |
| `source` | str | `"chat"` \| `"scheduled"` \| `"agent_run"` |
| `source_id` | str | session_id, task name, or agent_run_id |
| `model` | str | model name used |
| `total_cost_usd` | float? | from ResultMessage.total_cost_usd |
| `duration_ms` | int? | from ResultMessage.duration_ms |
| `num_turns` | int? | from ResultMessage.num_turns |

No existing tables modified. `Base.metadata.create_all()` handles the new table automatically.

---

## Backend

### New files

**`backend/app/models/entities.py`** — add `UsageRecord` class

**`backend/app/services/costs_service.py`**
```
record(db, *, source, source_id, model, total_cost_usd, duration_ms, num_turns)
get_summary(db) → CostSummary
list_records(db, limit=100) → list[UsageRecord]
```

**`backend/app/api/costs.py`** — new FastAPI router
```
GET /api/costs/summary   → totals: all_time_usd, this_month_usd, this_week_usd, by_source
GET /api/costs/records   → paginated list of recent records
```

**`backend/app/schemas/costs.py`** — Pydantic response models

### Instrumentation points

**A. Chat (`claude_chat_runtime.py`)**
- `stream_reply()` already reads `ResultMessage`. Extend to also extract `total_cost_usd`, `duration_ms`, `num_turns`.
- Add these fields to `ReplyResult`.
- The WebSocket handler in `agent.py` persists via `costs_service.record(source="chat", source_id=session_id)`.

**B. Scheduled jobs (`task_scheduler_service.py`)**
- In `_run_claude_prompt()` async loop, detect `type == "result"` message and capture cost fields.
- Return cost dict alongside `output, meta`.
- `_record_log()` / caller persists via `costs_service.record(source="scheduled", source_id=task.name)`.

**C. Agent analyst runs (`claude_agent_runtime.py`)**
- Same pattern — capture `ResultMessage` fields during message iteration.
- Persist after run via `costs_service.record(source="agent_run", source_id=run_id)`.

---

## Frontend

### New files

**`frontend/src/api/costs.ts`** — typed API calls for summary + records
**`frontend/src/components/CostsWorkspace.tsx`** — workspace component
**Types** added to `frontend/src/types/index.ts`

### UI layout

New nav tab **"Costs"** between Artifacts and Archie.

```
┌─────────────────────────────────────────┐
│  Summary Cards                          │
│  All-time | This month | This week      │
│  Chat     | Scheduled  | Agent runs     │
└─────────────────────────────────────────┘
┌─────────────────────────────────────────┐
│  Recent Records Table                   │
│  Date | Source | ID | Model | $ | Turns │
└─────────────────────────────────────────┘
```

Follows the existing MetricGrid card + table pattern.

---

## Out of Scope

- Token-level breakdown (input/output/cache tokens)
- Per-subagent cost attribution within a single chat turn
- Cost alerts or budget limits
- Cost projection / forecasting
