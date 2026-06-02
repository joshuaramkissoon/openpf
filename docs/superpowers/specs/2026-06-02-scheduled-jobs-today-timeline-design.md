# Scheduled Jobs — "Today" Timeline

**Date:** 2026-06-02
**Status:** Design approved (pending spec review)

## Problem

The Scheduled Jobs page (`ScheduledJobsWorkspace`) shows a flat list of tasks with
per-task cron/next-run/last-run fields and a per-task run-history table. There is no
*time-of-day* view: you can't glance at "what's already run today" and "what's coming
up next today" across all tasks on one timeline, nor jump straight from a past run to
its output.

## Goal

Add a **"Today" timeline** to the Scheduled Jobs page:

- A vertical timeline for the current day in the display timezone (default
  `Europe/London`), with a **"now" divider** separating past from upcoming.
- **Past runs** (above the divider): grouped by task. Recurring tasks (e.g.
  `watch_cycle` running hourly) collapse to one row showing the run count; the row
  **expands** to reveal every individual run, each clickable through to its output.
  Single-run tasks render as one clickable row.
- **Upcoming fires** (below the divider): one row per enabled task with a fire left
  today, showing the next fire time plus a cadence/count label (e.g. "hourly · 6 more
  today"); expandable to list the remaining fire times.
- Clicking a past run opens its **markdown output artifact** in a side drawer.

## Non-goals

- No multi-day / week view, no calendar grid. Today only.
- No changes to how tasks are created, edited, scheduled, or executed.
- No new persistence — everything is derived from existing `scheduled_tasks` and
  `scheduled_task_logs` rows.
- Disabled (paused) tasks are excluded from the upcoming list.

## Architecture

One new read-only backend endpoint aggregates the day; the frontend adds a tab to the
existing workspace and renders the timeline. No schema/migration changes.

### Backend

**New endpoint:** `GET /scheduler/today?tz=Europe/London`

- `tz` query param (IANA name), defaults to `Europe/London`. "Today" = the calendar day
  containing `now` in that timezone; the window is `[day_start, day_end]` in `tz`,
  converted to UTC for DB queries / cron expansion.
- Response (all timestamps ISO-8601 with offset, in `tz`):

```jsonc
{
  "date": "2026-06-02",
  "timezone": "Europe/London",
  "now": "2026-06-02T14:32:00+01:00",
  "past": [            // groups, one per task that ran today; ordered by last_ran_at asc
    {
      "task_id": "uuid",
      "name": "Morning brief",
      "task_kind": "claude",
      "run_count": 1,
      "first_ran_at": "2026-06-02T07:00:11+01:00",
      "last_ran_at":  "2026-06-02T07:00:11+01:00",
      "status_summary": { "ok": 1, "error": 0, "running": 0 },
      "runs": [          // individual runs, ordered by ran_at desc
        { "log_id": 123, "ran_at": "2026-06-02T07:00:11+01:00",
          "status": "ok", "message": "…short…", "has_output": true }
      ]
    }
  ],
  "upcoming": [         // one per enabled task with a fire left today; ordered by next_fire_at asc
    {
      "task_id": "uuid",
      "name": "Watch cycle",
      "task_kind": "watch_cycle",
      "cron_expr": "0 8-21 * * 1-5",
      "next_fire_at": "2026-06-02T15:00:00+01:00",
      "remaining_today": 6,
      "fires": [        // all fires from now..day_end, inclusive of next_fire_at, asc
        "2026-06-02T15:00:00+01:00", "2026-06-02T16:00:00+01:00" /* … */
      ]
    }
  ]
}
```

**Past computation:** query `scheduled_task_logs` where `created_at` ∈ [day_start_utc, now],
join `scheduled_tasks` for `name` + `meta.task_kind`. Group by `task_id`. Per group:
`run_count`, `first/last_ran_at`, `status_summary` (counts by status, including
`running`), and `runs[]` (each: `log_id`, `ran_at`, `status`, `message`,
`has_output = output_path is not None`). Logs whose task was deleted still appear, keyed
by `task_id` with a best-effort name (fallback to the log's `payload`/task_id).

**Upcoming computation:** for each **enabled** task, expand its cron over the window
`(now, day_end]` via a new helper `fires_in_window(cron_expr, tz, start, end)` built on
APScheduler `CronTrigger.from_crontab(cron_expr, timezone=tz)` (the service already uses
`CronTrigger`). Tasks with zero fires left are dropped. `next_fire_at = fires[0]`,
`remaining_today = len(fires) - 1`.

**New code:**
- `backend/app/services/task_scheduler_service.py`:
  - `fires_in_window(cron_expr: str, tz: str, start: datetime, end: datetime) -> list[datetime]`
    (pure helper; iterates `get_next_fire_time` until past `end`; bounded).
  - `build_today_timeline(db, tz: str = "Europe/London") -> dict` (assembles the response).
- `backend/app/schemas/scheduler.py`: `TimelineRun`, `TimelinePastGroup`,
  `TimelineUpcoming`, `SchedulerTodayResponse`.
- `backend/app/api/scheduler.py`: `GET /scheduler/today` calling `build_today_timeline`.

### Frontend

**Tab switch** in `ScheduledJobsWorkspace.tsx` using shadcn `Tabs`: `[ Today | All jobs ]`.
**Default tab = Today.** "All jobs" renders the current workspace UI unchanged.

**New `frontend/src/components/scheduled-jobs/TodayTimeline.tsx`:**
- Fetches `getSchedulerTimeline()` on mount; polls every 30s; refetches on window focus.
- Renders, top to bottom: past groups → `── now · HH:MM ──` divider → upcoming rows →
  `── end of day ──` cap.
- **Past group row:** status icon (✓ ok / ✗ error / spinner running; mixed → ✗ if any
  error), `last_ran_at` time, task name, and for `run_count > 1` a `· 7 runs` count +
  error badge. `run_count > 1` rows are expandable (chevron) → list of `runs` (each:
  time, status dot, truncated message, clickable if `has_output`). `run_count === 1`
  rows open the output drawer directly on click.
- **Upcoming row:** hollow circle, `next_fire_at` time, task name, cadence label derived
  from `cron_expr` via the existing `parseCronHuman` + `· N more today` when
  `remaining_today > 0`. Expandable → remaining `fires` (times only, not clickable).
- Empty states: "No runs yet today" (past) / "Nothing left to run today" (upcoming).

**New `frontend/src/components/scheduled-jobs/RunOutputDrawer.tsx`:**
- shadcn `Sheet` (right side). Given a past run (`task_id`, `log_id`, `output_path`/`has_output`),
  fetches and renders the markdown artifact using the **same artifact-fetch the existing
  `loadArtifactForLog` uses** (reuse, don't duplicate). Header: task name, status, ran-at.
  Footer link "View task →" switches to the All jobs tab with that task selected.
- Runs without output show their log `message` instead of opening a drawer.

**API client** (`frontend/src/api/client.ts`): `getSchedulerTimeline(tz?: string)`.
**Types** (`frontend/src/types/index.ts`): mirror the response models above.

## Data flow

1. `TodayTimeline` mounts → `GET /scheduler/today` → backend reads tasks + today's logs,
   expands crons, returns grouped past + collapsed upcoming.
2. User clicks a past run → `RunOutputDrawer` fetches that run's artifact (existing
   endpoint) → renders markdown.
3. Poll (30s) / focus → re-fetch keeps the now-divider and past groups current. A
   manual "Run now" or a scheduled run produces a new log, which appears in `past` on the
   next fetch (no special-casing needed).

## Error handling

- Bad/unknown `tz` → 400 with a clear message; frontend falls back to `Europe/London`.
- Unparseable `cron_expr` on a task → that task is skipped in `upcoming` (logged
  server-side), never 500s the whole endpoint.
- Artifact fetch failure in the drawer → inline "Couldn't load output" with the log
  message as fallback.
- Empty day → 200 with empty `past`/`upcoming`; frontend shows empty states.

## Testing

**Backend (pytest, run from `backend/`):**
- `fires_in_window`: hourly cron `0 8-21 * * 1-5` over a mid-day window → correct count
  and ordering; window with no fires → `[]`; respects `tz` (BST offset).
- `build_today_timeline` / endpoint: seed one task + 2 logs today (one with
  `output_path`, one without) + 1 log yesterday + a second recurring task with 3 logs
  today; assert: yesterday excluded; recurring task grouped with `run_count == 3` and
  `runs` desc; `has_output` correct; a disabled task is absent from `upcoming`; an
  enabled hourly task has `remaining_today` ≥ 0 and `next_fire_at > now`.

**Frontend:**
- If a test harness exists, render `TodayTimeline` with mock data → asserts the three
  sections, that a `run_count > 1` group expands, and that clicking a run with output
  opens the drawer. Otherwise verify manually on alt ports (frontend 5174 → backend
  8010) per the dev-environment convention, without disturbing the live :5173/:8000 stack.

## Files touched

**Backend:** `app/schemas/scheduler.py`, `app/services/task_scheduler_service.py`,
`app/api/scheduler.py`
**Frontend:** `src/types/index.ts`, `src/api/client.ts`,
`src/components/ScheduledJobsWorkspace.tsx` (add Tabs), new
`src/components/scheduled-jobs/TodayTimeline.tsx`, new
`src/components/scheduled-jobs/RunOutputDrawer.tsx`

## Open choices (easy to flip)

- Default tab = **Today** (vs. All jobs).
- Past collapsed rows anchored to **last** run time on the timeline (vs. first).
