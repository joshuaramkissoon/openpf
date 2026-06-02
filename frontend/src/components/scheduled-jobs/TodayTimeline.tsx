import { useCallback, useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
  RefreshCw,
  XCircle,
} from 'lucide-react'

import { getSchedulerTimeline } from '../../api/client'
import type { SchedulerToday, TimelinePastGroup, TimelineRun, TimelineUpcoming } from '../../types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { parseCronHuman } from './cron'
import { RunOutputDrawer, type DrawerRun } from './RunOutputDrawer'

const POLL_INTERVAL_MS = 30_000

interface Props {
  onError: (message: string | null) => void
  onViewTask: (taskId: string) => void
}

const fmtTime = (iso: string) => dayjs(iso).format('HH:mm')

/** Status marker for a single run. */
function RunMarker({ status }: { status: string }) {
  if (status === 'running') return <Loader2 className="size-3.5 animate-spin text-positive" />
  if (status === 'error') return <XCircle className="size-3.5 text-negative" />
  return <CheckCircle2 className="size-3.5 text-positive" />
}

/** A row in the timeline: [time] [marker] [content], linked by a left spine. */
function TimelineRow({
  time,
  marker,
  children,
  onClick,
  selected,
  muted,
}: {
  time: string
  marker: React.ReactNode
  children: React.ReactNode
  onClick?: () => void
  selected?: boolean
  muted?: boolean
}) {
  const inner = (
    <>
      <span className={cn('w-11 shrink-0 pt-0.5 text-right font-mono text-xs tabular-nums', muted ? 'text-muted-foreground/70' : 'text-muted-foreground')}>
        {time}
      </span>
      <span className="relative flex w-4 shrink-0 justify-center pt-0.5">
        {/* spine */}
        <span className="absolute top-0 bottom-[-0.6rem] left-1/2 w-px -translate-x-1/2 bg-border/60" aria-hidden />
        <span className="relative z-10 bg-background">{marker}</span>
      </span>
      <div className="min-w-0 flex-1 pb-1">{children}</div>
    </>
  )

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/40',
          selected && 'bg-muted/50'
        )}
      >
        {inner}
      </button>
    )
  }
  return <div className="flex items-start gap-3 px-2 py-1.5">{inner}</div>
}

function PastGroupRow({
  group,
  expanded,
  onToggle,
  onOpenRun,
}: {
  group: TimelinePastGroup
  expanded: boolean
  onToggle: () => void
  onOpenRun: (run: TimelineRun) => void
}) {
  const errors = group.status_summary.error ?? 0
  const groupStatus = errors > 0 ? 'error' : 'ok'
  const recurring = group.run_count > 1

  if (!recurring) {
    const run = group.runs[0]
    return (
      <TimelineRow
        time={fmtTime(group.last_ran_at)}
        marker={<RunMarker status={run.status} />}
        onClick={run.has_output ? () => onOpenRun(run) : undefined}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium">{group.name}</span>
          {run.has_output && <span className="size-1.5 rounded-full bg-primary" title="Has output" />}
        </div>
        {run.message && <p className="truncate text-xs text-muted-foreground">{run.message}</p>}
      </TimelineRow>
    )
  }

  return (
    <div>
      <TimelineRow
        time={fmtTime(group.last_ran_at)}
        marker={<RunMarker status={groupStatus} />}
        onClick={onToggle}
        selected={expanded}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {expanded ? (
            <ChevronDown className="size-3.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3.5 text-muted-foreground" />
          )}
          <span className="text-sm font-medium">{group.name}</span>
          <Badge variant="outline" className="text-muted-foreground">{group.run_count} runs</Badge>
          {errors > 0 && (
            <Badge variant="destructive">{errors} failed</Badge>
          )}
        </div>
      </TimelineRow>

      {expanded && (
        <div className="ml-[3.75rem] mb-1 space-y-0.5 border-l border-border/60 pl-3">
          {group.runs.map((run) => (
            <button
              key={run.log_id}
              type="button"
              disabled={!run.has_output}
              onClick={() => run.has_output && onOpenRun(run)}
              className={cn(
                'flex w-full items-center gap-2.5 rounded-md px-2 py-1 text-left text-xs transition-colors',
                run.has_output ? 'hover:bg-muted/40' : 'cursor-default'
              )}
            >
              <span className="font-mono tabular-nums text-muted-foreground">{fmtTime(run.ran_at)}</span>
              <span className={cn(run.status === 'ok' ? 'text-positive' : run.status === 'error' ? 'text-negative' : 'text-muted-foreground')}>
                {run.status}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{run.message}</span>
              {run.has_output && <span className="size-1.5 shrink-0 rounded-full bg-primary" title="Has output" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function UpcomingRow({
  item,
  expanded,
  onToggle,
}: {
  item: TimelineUpcoming
  expanded: boolean
  onToggle: () => void
}) {
  const more = item.remaining_today
  const schedule = parseCronHuman(item.cron_expr)
  const showSchedule = schedule !== item.cron_expr // only when human-readable
  const expandable = more > 0

  return (
    <div>
      <TimelineRow
        time={fmtTime(item.next_fire_at)}
        marker={<Circle className="size-3 text-muted-foreground/50" />}
        muted
        onClick={expandable ? onToggle : undefined}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {expandable &&
            (expanded ? (
              <ChevronDown className="size-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3.5 text-muted-foreground" />
            ))}
          <span className="text-sm text-foreground/90">{item.name}</span>
          {more > 0 && (
            <span className="text-xs text-muted-foreground">+{more} more today</span>
          )}
          {showSchedule && (
            <span className="text-xs text-muted-foreground/70">· {schedule}</span>
          )}
        </div>
      </TimelineRow>

      {expanded && expandable && (
        <div className="ml-[3.75rem] mb-1 flex flex-wrap gap-1.5 border-l border-border/60 pl-3 pt-0.5">
          {item.fires.map((fire) => (
            <span
              key={fire}
              className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-muted-foreground"
            >
              {fmtTime(fire)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function TodayTimeline({ onError, onViewTask }: Props) {
  const [data, setData] = useState<SchedulerToday | null>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [drawerRun, setDrawerRun] = useState<DrawerRun | null>(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const rows = await getSchedulerTimeline()
      setData(rows)
      if (!silent) onError(null)
    } catch (err) {
      if (!silent) onError(err instanceof Error ? err.message : 'Failed to load timeline')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [onError])

  useEffect(() => {
    void load()
    const id = setInterval(() => void load(true), POLL_INTERVAL_MS)
    const onFocus = () => void load(true)
    window.addEventListener('focus', onFocus)
    return () => {
      clearInterval(id)
      window.removeEventListener('focus', onFocus)
    }
  }, [load])

  function toggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function openRun(group: TimelinePastGroup, run: TimelineRun) {
    setDrawerRun({
      taskId: group.task_id,
      name: group.name,
      taskKind: group.task_kind,
      status: run.status,
      ranAt: run.ran_at,
      message: run.message,
      outputPath: run.output_path,
    })
  }

  if (loading && !data) {
    return (
      <div className="space-y-2 p-5">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-4 w-10" />
            <Skeleton className="size-3.5 rounded-full" />
            <Skeleton className="h-4 w-48" />
          </div>
        ))}
      </div>
    )
  }

  const past = data?.past ?? []
  const upcoming = data?.upcoming ?? []
  const nothing = past.length === 0 && upcoming.length === 0

  return (
    <div className="space-y-3">
      {/* Header: date + tz + manual refresh */}
      <div className="flex items-center justify-between px-1">
        <div className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">
            {data ? dayjs(data.now).format('dddd D MMM') : 'Today'}
          </span>
          {data ? <span className="ml-2">· {data.timezone}</span> : null}
        </div>
        <Button variant="ghost" size="icon-sm" title="Refresh" onClick={() => void load()}>
          <RefreshCw className={cn('size-3.5', loading && 'animate-spin')} />
        </Button>
      </div>

      {nothing ? (
        <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
          <CalendarClock className="size-8 text-muted-foreground" />
          <p className="text-sm font-medium">Nothing scheduled today</p>
          <p className="max-w-md text-xs text-muted-foreground">
            No jobs have run yet and none are due for the rest of today.
          </p>
        </div>
      ) : (
        <div className="px-1">
          {/* PAST */}
          {past.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">No runs yet today.</p>
          ) : (
            past.map((group) => (
              <PastGroupRow
                key={group.task_id}
                group={group}
                expanded={expanded.has(`past:${group.task_id}`)}
                onToggle={() => toggle(`past:${group.task_id}`)}
                onOpenRun={(run) => openRun(group, run)}
              />
            ))
          )}

          {/* NOW divider */}
          <div className="flex items-center gap-3 py-2">
            <span className="w-11 shrink-0 text-right font-mono text-xs font-medium tabular-nums text-primary">
              {data ? fmtTime(data.now) : ''}
            </span>
            <span className="flex w-4 shrink-0 justify-center">
              <span className="size-2 rounded-full bg-primary ring-4 ring-primary/15" />
            </span>
            <span className="flex flex-1 items-center gap-2 text-xs font-medium uppercase tracking-wider text-primary">
              now
              <span className="h-px flex-1 bg-primary/30" />
            </span>
          </div>

          {/* UPCOMING */}
          {upcoming.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">Nothing left to run today.</p>
          ) : (
            <>
              {upcoming.map((item) => (
                <UpcomingRow
                  key={item.task_id}
                  item={item}
                  expanded={expanded.has(`up:${item.task_id}`)}
                  onToggle={() => toggle(`up:${item.task_id}`)}
                />
              ))}
              {/* end-of-day cap */}
              <div className="flex items-center gap-3 pt-1">
                <span className="w-11 shrink-0" />
                <span className="flex w-4 shrink-0 justify-center">
                  <span className="size-1.5 rounded-full bg-border" />
                </span>
                <span className="text-[11px] uppercase tracking-wider text-muted-foreground/60">end of day</span>
              </div>
            </>
          )}
        </div>
      )}

      <RunOutputDrawer run={drawerRun} onClose={() => setDrawerRun(null)} onViewTask={onViewTask} />
    </div>
  )
}
