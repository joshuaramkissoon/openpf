import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dayjs from 'dayjs'
import {
  CalendarClock,
  CheckCircle2,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
  XCircle,
} from 'lucide-react'

import {
  createSchedulerTask,
  deleteSchedulerTask,
  getArtifact,
  getSchedulerTaskLogs,
  getSchedulerTasks,
  runSchedulerTask,
  seedSchedulerDefaults,
  updateSchedulerTask,
} from '../api/client'
import { RichMarkdown } from './RichMarkdown'
import { TodayTimeline } from './scheduled-jobs/TodayTimeline'
import { parseCronHuman } from './scheduled-jobs/cron'
import { resolveArtifactRelativePath } from './scheduled-jobs/artifact'
import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import type { ArtifactDetail, SchedulerTask, SchedulerTaskLog } from '../types'

interface Props {
  onError: (message: string | null) => void
}

const MODEL_OPTIONS = [
  'claude-opus-4-6',
  'claude-sonnet-4-6',
  'claude-haiku-4-5',
]

const DEFAULT_MODEL = 'claude-sonnet-4-6'

const TASK_KIND_OPTIONS = [
  'claude',
  'claude_with_goal',
  'leveraged_cycle',
  'leveraged_monitor',
  'leveraged_scan',
] as const

type TaskKind = (typeof TASK_KIND_OPTIONS)[number]

/** Shorten model ID for display: "claude-sonnet-4-20250514" → "sonnet-4" */
function shortModel(model: string): string {
  const m = model.replace(/^claude-/, '')
  // Strip date suffixes like -20250514
  return m.replace(/-\d{8}$/, '')
}

interface RunResult {
  status: string
  error?: string
}

type SortMode = 'status' | 'next_run' | 'task_kind' | 'name'

const SORT_LABELS: Record<SortMode, string> = {
  status: 'Status',
  next_run: 'Next Run',
  task_kind: 'Kind',
  name: 'Name',
}

const POLL_INTERVAL_MS = 3_000
const RESULT_DISPLAY_MS = 10_000

/** Markdown wrapper matching the dashboard prose style. */
const MARKDOWN_PROSE =
  'space-y-2 text-sm leading-relaxed [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:font-semibold [&_strong]:font-semibold [&_table]:w-full [&_ul]:list-disc [&_ul]:pl-5'

export function ScheduledJobsWorkspace({ onError }: Props) {
  const [tasks, setTasks] = useState<SchedulerTask[]>([])
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState<Set<string>>(new Set())
  const [sortMode, setSortMode] = useState<SortMode>('status')
  const [view, setView] = useState<'today' | 'alljobs'>('today')

  // Per-task log viewing
  const [taskLogs, setTaskLogs] = useState<SchedulerTaskLog[]>([])
  const [logsLoading, setLogsLoading] = useState(false)

  // Transient run results (shown after task completes, auto-cleared)
  const [runResults, setRunResults] = useState<Map<string, RunResult>>(new Map())
  const clearTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  // Track which tasks were running last poll cycle (to detect transitions)
  const prevRunningRef = useRef<Set<string>>(new Set())

  // Detail pane — selected task
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)

  // New Job dialog
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [form, setForm] = useState({
    name: '',
    cron_expr: '30 7 * * 1-5',
    timezone: 'Europe/London',
    model: DEFAULT_MODEL,
    prompt: '',
    enabled: true,
    task_kind: 'claude' as TaskKind,
    goal_target_gbp: '',
  })
  const [formError, setFormError] = useState<string | null>(null)

  function resetForm() {
    setForm({
      name: '',
      cron_expr: '30 7 * * 1-5',
      timezone: 'Europe/London',
      model: DEFAULT_MODEL,
      prompt: '',
      enabled: true,
      task_kind: 'claude',
      goal_target_gbp: '',
    })
    setFormError(null)
  }

  // Content tab and artifact viewer
  const [contentTab, setContentTab] = useState<'output' | 'prompt'>('prompt')
  const [activeArtifact, setActiveArtifact] = useState<{ loading: boolean; detail: ArtifactDetail | null; error: string | null } | null>(null)
  const [selectedLogId, setSelectedLogId] = useState<number | null>(null)

  async function loadArtifactForLog(log: SchedulerTaskLog) {
    if (!log.output_path) return
    setSelectedLogId(log.id)
    setContentTab('output')

    const relativePath = resolveArtifactRelativePath(log.output_path)

    setActiveArtifact({ loading: true, detail: null, error: null })
    try {
      const d = await getArtifact(relativePath)
      setActiveArtifact({ loading: false, detail: d, error: null })
    } catch (err) {
      setActiveArtifact({ loading: false, detail: null, error: err instanceof Error ? err.message : 'Failed to load artifact' })
    }
  }

  // ── Load tasks (silent variant for polling without flashing the busy state) ──

  const loadTasks = useCallback(async (silent = false) => {
    if (!silent) setBusy(true)
    try {
      const rows = await getSchedulerTasks()
      setTasks(rows)
      if (!silent) onError(null)
    } catch (err) {
      if (!silent) onError(err instanceof Error ? err.message : 'Failed to load scheduled tasks')
    } finally {
      if (!silent) setBusy(false)
    }
  }, [onError])

  useEffect(() => {
    void loadTasks()
    return () => {
      clearTimers.current.forEach((t) => clearTimeout(t))
    }
  }, [loadTasks])

  // ── Detect running → completed transitions and show transient results ──

  useEffect(() => {
    const prev = prevRunningRef.current
    const currentRunning = new Set<string>()

    for (const t of tasks) {
      if (t.last_status === 'running') {
        currentRunning.add(t.id)
      } else if (prev.has(t.id)) {
        // This task was running and now it's not — show result
        const result: RunResult = t.last_status === 'ok'
          ? { status: 'ok' }
          : { status: 'error', error: t.last_status }

        setRunResults((m) => new Map(m).set(t.id, result))

        // Auto-clear after display period
        const existing = clearTimers.current.get(t.id)
        if (existing) clearTimeout(existing)
        const timer = setTimeout(() => {
          setRunResults((m) => { const n = new Map(m); n.delete(t.id); return n })
          clearTimers.current.delete(t.id)
        }, RESULT_DISPLAY_MS)
        clearTimers.current.set(t.id, timer)

        // Refresh logs if we're viewing this task
        if (selectedTaskId === t.id) {
          void fetchTaskLogs(t.id)
        }
      }
    }

    prevRunningRef.current = currentRunning
  }, [tasks]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Poll while any task is running ──

  const hasRunningTask = useMemo(
    () => tasks.some((t) => t.last_status === 'running'),
    [tasks]
  )

  useEffect(() => {
    if (!hasRunningTask) return
    const id = setInterval(() => { void loadTasks(true) }, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [hasRunningTask, loadTasks])

  // ── Sorting ──

  const sortedTasks = useMemo(() => {
    const sorted = [...tasks]
    switch (sortMode) {
      case 'status':
        sorted.sort((a, b) => {
          if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
          const aNext = a.next_run_at ? new Date(a.next_run_at).getTime() : Infinity
          const bNext = b.next_run_at ? new Date(b.next_run_at).getTime() : Infinity
          return aNext - bNext
        })
        break
      case 'next_run':
        sorted.sort((a, b) => {
          const aNext = a.next_run_at ? new Date(a.next_run_at).getTime() : Infinity
          const bNext = b.next_run_at ? new Date(b.next_run_at).getTime() : Infinity
          return aNext - bNext
        })
        break
      case 'task_kind':
        sorted.sort((a, b) => {
          const aKind = String((a.meta?.task_kind as string) || 'zzz')
          const bKind = String((b.meta?.task_kind as string) || 'zzz')
          return aKind.localeCompare(bKind)
        })
        break
      case 'name':
        sorted.sort((a, b) => a.name.localeCompare(b.name))
        break
    }
    return sorted
  }, [tasks, sortMode])

  const activeCount = useMemo(() => tasks.filter((t) => t.enabled).length, [tasks])
  const pausedCount = useMemo(() => tasks.filter((t) => !t.enabled).length, [tasks])

  // ── Actions ──

  async function toggleTask(task: SchedulerTask) {
    try {
      await updateSchedulerTask(task.id, { enabled: !task.enabled })
      await loadTasks()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update task')
    }
  }

  async function handleCreate() {
    const name = form.name.trim()
    const cron = form.cron_expr.trim()
    const prompt = form.prompt.trim()
    if (!name) return setFormError('Name is required.')
    if (cron.split(/\s+/).length !== 5) {
      return setFormError('Cron expression must have 5 fields (e.g. "30 7 * * 1-5").')
    }
    if (!prompt) return setFormError('Prompt is required.')

    const meta: Record<string, unknown> = { task_kind: form.task_kind }
    if (form.task_kind === 'claude_with_goal') {
      const target = parseFloat(form.goal_target_gbp)
      if (Number.isFinite(target) && target > 0) {
        meta.goal = { target_gbp: target }
      }
    }

    setCreating(true)
    setFormError(null)
    try {
      await createSchedulerTask({
        name,
        cron_expr: cron,
        timezone: form.timezone.trim() || 'Europe/London',
        model: form.model.trim() || DEFAULT_MODEL,
        prompt,
        enabled: form.enabled,
        meta,
      })
      setCreateOpen(false)
      resetForm()
      await loadTasks()
      onError(null)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to create job')
    } finally {
      setCreating(false)
    }
  }

  async function handleSeedDefaults() {
    setSeeding(true)
    try {
      await seedSchedulerDefaults()
      await loadTasks()
      onError(null)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to seed default jobs')
    } finally {
      setSeeding(false)
    }
  }

  async function deleteTask(task: SchedulerTask) {
    setDeleting((prev) => new Set(prev).add(task.id))
    try {
      await deleteSchedulerTask(task.id)
      if (selectedTaskId === task.id) {
        setSelectedTaskId(null)
        setTaskLogs([])
        setActiveArtifact(null)
        setSelectedLogId(null)
      }
      await loadTasks()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to delete task')
    } finally {
      setDeleting((prev) => {
        const next = new Set(prev)
        next.delete(task.id)
        return next
      })
    }
  }

  const handleRunTask = useCallback(async (taskId: string) => {
    try {
      await runSchedulerTask(taskId)
      // Endpoint returns immediately with status "started".
      // Reload to pick up the "running" last_status — polling takes over from there.
      await loadTasks(true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'run failed'
      setRunResults((m) => new Map(m).set(taskId, { status: 'error', error: msg }))
      const timer = setTimeout(() => {
        setRunResults((m) => { const n = new Map(m); n.delete(taskId); return n })
        clearTimers.current.delete(taskId)
      }, RESULT_DISPLAY_MS)
      clearTimers.current.set(taskId, timer)
    }
  }, [loadTasks])

  async function handleModelChange(taskId: string, newModel: string) {
    try {
      await updateSchedulerTask(taskId, { model: newModel })
      await loadTasks(true)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update model')
    }
  }

  async function fetchTaskLogs(taskId: string) {
    setLogsLoading(true)
    try {
      const logs = await getSchedulerTaskLogs(taskId, 10)
      setTaskLogs(logs)
    } catch {
      setTaskLogs([])
    } finally {
      setLogsLoading(false)
    }
  }

  const selectedTask = useMemo(
    () => (selectedTaskId ? tasks.find((t) => t.id === selectedTaskId) ?? null : null),
    [selectedTaskId, tasks]
  )

  function selectTask(taskId: string) {
    if (selectedTaskId === taskId) {
      setSelectedTaskId(null)
      setTaskLogs([])
      setActiveArtifact(null)
      setSelectedLogId(null)
    } else {
      setSelectedTaskId(taskId)
      setTaskLogs([])
      setActiveArtifact(null)
      setSelectedLogId(null)
      setContentTab('prompt') // default, will switch if output exists
      void fetchTaskLogs(taskId) // auto-fetch logs
    }
  }

  function closeDetail() {
    setSelectedTaskId(null)
    setTaskLogs([])
    setActiveArtifact(null)
    setSelectedLogId(null)
  }

  // ── Jump from the Today timeline into a task's detail in the All jobs view ──

  function viewTaskFromTimeline(taskId: string) {
    setView('alljobs')
    if (selectedTaskId !== taskId) {
      setSelectedTaskId(taskId)
      setTaskLogs([])
      setActiveArtifact(null)
      setSelectedLogId(null)
      setContentTab('prompt')
      void fetchTaskLogs(taskId)
    }
  }

  // ── Auto-load latest output when logs arrive ──

  useEffect(() => {
    if (!selectedTaskId || taskLogs.length === 0) return
    // Find the latest log with output_path
    const latestWithOutput = taskLogs.find(log => log.output_path)
    if (latestWithOutput) {
      void loadArtifactForLog(latestWithOutput)
    }
  }, [taskLogs, selectedTaskId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Helper: is a task currently running? ──

  function isTaskRunning(task: SchedulerTask): boolean {
    return task.last_status === 'running'
  }

  const headerStats = (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      <span>
        <span className="font-mono tabular-nums text-foreground">{tasks.length}</span> total
      </span>
      <span>
        <span className="font-mono tabular-nums text-positive">{activeCount}</span> active
      </span>
      {pausedCount > 0 && (
        <span>
          <span className="font-mono tabular-nums text-foreground">{pausedCount}</span> paused
        </span>
      )}
    </div>
  )

  const headerActions = (
    <>
      <Select value={sortMode} onValueChange={(v) => setSortMode(v as SortMode)}>
        <SelectTrigger size="sm" className="w-[120px] sm:w-[150px]">
          <span className="hidden text-muted-foreground sm:inline">Sort:</span>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {(Object.keys(SORT_LABELS) as SortMode[]).map((mode) => (
            <SelectItem key={mode} value={mode}>{SORT_LABELS[mode]}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button variant="outline" size="sm" onClick={() => void loadTasks()} disabled={busy} title="Refresh" className="px-2 sm:px-3">
        <RefreshCw className={cn('size-3.5', busy && 'animate-spin')} />
        <span className="hidden sm:inline">Refresh</span>
      </Button>
      <Button
        size="sm"
        title="New Job"
        className="px-2 sm:px-3"
        onClick={() => {
          resetForm()
          setCreateOpen(true)
        }}
      >
        <Plus className="size-3.5" />
        <span className="hidden sm:inline">New Job</span>
      </Button>
    </>
  )

  return (
    <div className="space-y-6">
      <Tabs value={view} onValueChange={(v) => setView(v as 'today' | 'alljobs')}>
        <TabsList>
          <TabsTrigger value="today">Today</TabsTrigger>
          <TabsTrigger value="alljobs">All jobs</TabsTrigger>
        </TabsList>

        <TabsContent value="today">
          <SectionCard title="Today">
            <TodayTimeline onError={onError} onViewTask={viewTaskFromTimeline} />
          </SectionCard>
        </TabsContent>

        <TabsContent value="alljobs">
          <div className={cn('grid gap-4', selectedTask && 'lg:grid-cols-2')}>
        {/* Task list */}
        <SectionCard title="Scheduled Jobs" action={<div className="flex items-center gap-2">{headerActions}</div>} noPadding>
          <div className="flex items-center justify-between border-b border-border/60 px-5 py-2.5">
            {headerStats}
          </div>

          {busy && sortedTasks.length === 0 ? (
            <div className="divide-y divide-border/60">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="space-y-2 px-5 py-4">
                  <div className="flex items-center gap-2">
                    <Skeleton className="size-2 rounded-full" />
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="h-5 w-24 rounded-full" />
                  </div>
                  <Skeleton className="h-3 w-64" />
                </div>
              ))}
            </div>
          ) : sortedTasks.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
              <CalendarClock className="size-8 text-muted-foreground" />
              <p className="text-sm font-medium">No scheduled jobs yet</p>
              <p className="max-w-md text-xs text-muted-foreground">
                Create a job manually, seed the recommended defaults, or ask Archie to set up
                recurring tasks for you — e.g. "Schedule a daily portfolio health check at 8am"
                or "Run a leveraged scan every weekday morning at 7:30."
              </p>
              <div className="mt-1 flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => {
                    resetForm()
                    setCreateOpen(true)
                  }}
                >
                  <Plus className="size-3.5" />
                  New Job
                </Button>
                <Button variant="outline" size="sm" disabled={seeding} onClick={() => void handleSeedDefaults()}>
                  {seeding ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
                  Seed defaults
                </Button>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-border/60">
              {sortedTasks.map((task) => {
                const running = isTaskRunning(task)
                const result = runResults.get(task.id)
                const taskKind = (task.meta?.task_kind as string) || null
                const taskDesc = (task.meta?.description as string) || null
                const isSelected = selectedTaskId === task.id
                const lastStatus = task.last_status

                return (
                  <div
                    key={task.id}
                    className={cn(
                      'flex items-start gap-2 px-5 py-4 transition-colors hover:bg-muted/40',
                      isSelected && 'bg-muted/50'
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => selectTask(task.id)}
                      className="flex min-w-0 flex-1 flex-col gap-2 text-left"
                    >
                    {/* Header row */}
                    <div className="flex flex-wrap items-center gap-2">
                      {running ? (
                        <RefreshCw className="size-3.5 shrink-0 animate-spin text-positive" />
                      ) : (
                        <span
                          className={cn(
                            'size-2 shrink-0 rounded-full',
                            task.enabled ? 'bg-positive' : 'bg-muted-foreground/40'
                          )}
                        />
                      )}
                      <span className="truncate text-sm font-medium">{task.name}</span>
                      <Badge variant="outline" className="text-muted-foreground">
                        {parseCronHuman(task.cron_expr)}
                      </Badge>
                      {taskKind && <Badge variant="secondary">{taskKind}</Badge>}
                      {running && (
                        <Badge variant="outline" className="border-positive/30 bg-positive/10 text-positive">
                          running
                        </Badge>
                      )}
                    </div>

                    {/* Meta row */}
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      <span>
                        Last run{' '}
                        {task.last_run_at ? (
                          <>
                            <span className="font-mono tabular-nums text-foreground">
                              {dayjs(task.last_run_at).format('ddd D MMM HH:mm')}
                            </span>{' '}
                            {lastStatus && lastStatus !== 'running' && (
                              <span
                                className={cn(
                                  lastStatus === 'ok' ? 'text-positive' : lastStatus === 'error' ? 'text-negative' : ''
                                )}
                              >
                                {lastStatus}
                              </span>
                            )}
                          </>
                        ) : (
                          '—'
                        )}
                      </span>
                      <span>
                        Next{' '}
                        <span className="font-mono tabular-nums text-foreground">
                          {task.next_run_at ? dayjs(task.next_run_at).format('ddd D MMM HH:mm') : '—'}
                        </span>
                      </span>
                      <span>
                        runs{' '}
                        <span className="font-mono tabular-nums text-positive">{task.run_count ?? 0}</span>
                      </span>
                      <span>
                        fails{' '}
                        <span className="font-mono tabular-nums text-negative">{task.failure_count ?? 0}</span>
                      </span>
                    </div>

                    {/* Description */}
                    {taskDesc && <p className="text-xs text-muted-foreground">{taskDesc}</p>}

                    {/* Transient run result */}
                    {!running && result && (
                      <div className="flex items-center gap-1.5 text-xs">
                        {result.status === 'ok' ? (
                          <>
                            <CheckCircle2 className="size-3.5 text-positive" />
                            <span className="text-positive">Completed</span>
                          </>
                        ) : (
                          <>
                            <XCircle className="size-3.5 text-negative" />
                            <span className="text-negative">{result.error || 'Failed'}</span>
                          </>
                        )}
                      </div>
                    )}
                    </button>

                    {/* Enable / disable */}
                    <label
                      className="flex shrink-0 cursor-pointer items-center gap-1.5 pt-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground"
                      title={task.enabled ? 'Enabled — click to pause' : 'Paused — click to enable'}
                    >
                      <span className="hidden sm:inline">{task.enabled ? 'On' : 'Off'}</span>
                      <Switch
                        checked={task.enabled}
                        onCheckedChange={() => void toggleTask(task)}
                      />
                    </label>
                  </div>
                )
              })}
            </div>
          )}
        </SectionCard>

        {/* Task detail pane */}
        {selectedTask && (
          <SectionCard
            title={
              <span className="flex items-center gap-2">
                {isTaskRunning(selectedTask) ? (
                  <RefreshCw className="size-3.5 animate-spin text-positive" />
                ) : (
                  <span
                    className={cn(
                      'size-2 rounded-full',
                      selectedTask.enabled ? 'bg-positive' : 'bg-muted-foreground/40'
                    )}
                  />
                )}
                <span className="truncate">{selectedTask.name}</span>
              </span>
            }
            description={parseCronHuman(selectedTask.cron_expr)}
            action={
              <>
                <Button
                  variant="outline"
                  size="icon-sm"
                  title="Run now"
                  disabled={isTaskRunning(selectedTask)}
                  onClick={() => void handleRunTask(selectedTask.id)}
                >
                  {isTaskRunning(selectedTask) ? (
                    <RefreshCw className="size-3.5 animate-spin" />
                  ) : (
                    <Play className="size-3.5" />
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="icon-sm"
                  title={selectedTask.enabled ? 'Pause' : 'Resume'}
                  onClick={() => void toggleTask(selectedTask)}
                >
                  {selectedTask.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                </Button>
                <Button
                  variant="destructive"
                  size="icon-sm"
                  title="Delete"
                  disabled={deleting.has(selectedTask.id)}
                  onClick={() => void deleteTask(selectedTask)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
                <Button variant="ghost" size="icon-sm" title="Close" onClick={closeDetail}>
                  <X className="size-3.5" />
                </Button>
              </>
            }
          >
            <div className="space-y-4">
              {/* Run result banner */}
              {runResults.has(selectedTask.id) && !isTaskRunning(selectedTask) && (
                <div className="flex items-center gap-1.5 text-xs">
                  {runResults.get(selectedTask.id)?.status === 'ok' ? (
                    <>
                      <CheckCircle2 className="size-3.5 text-positive" />
                      <span className="text-positive">Completed</span>
                    </>
                  ) : (
                    <>
                      <XCircle className="size-3.5 text-negative" />
                      <span className="text-negative">{runResults.get(selectedTask.id)?.error || 'Failed'}</span>
                    </>
                  )}
                </div>
              )}

              {/* Meta */}
              <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
                <span className="flex items-center gap-2">
                  <span>Model</span>
                  <Select
                    value={MODEL_OPTIONS.includes(selectedTask.model) ? selectedTask.model : ''}
                    onValueChange={(v) => void handleModelChange(selectedTask.id, v as string)}
                  >
                    <SelectTrigger size="sm" className="w-[130px]">
                      <SelectValue placeholder={selectedTask.model} />
                    </SelectTrigger>
                    <SelectContent>
                      {MODEL_OPTIONS.map((m) => (
                        <SelectItem key={m} value={m}>{shortModel(m)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </span>
                <span>
                  Timezone <span className="font-medium text-foreground">{selectedTask.timezone}</span>
                </span>
                <span className="flex items-center gap-1.5">
                  Cron{' '}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-foreground">
                    {selectedTask.cron_expr}
                  </code>
                </span>
                {selectedTask.meta?.task_kind ? (
                  <span>
                    Kind <span className="font-medium text-foreground">{String(selectedTask.meta.task_kind)}</span>
                  </span>
                ) : null}
              </div>

              {/* Content tabs */}
              <Tabs value={contentTab} onValueChange={(v) => setContentTab(v as 'output' | 'prompt')}>
                <TabsList>
                  <TabsTrigger value="output">Output</TabsTrigger>
                  <TabsTrigger value="prompt">Prompt</TabsTrigger>
                </TabsList>

                <TabsContent value="prompt">
                  <div className="max-h-[420px] overflow-y-auto overflow-x-auto rounded-lg border border-border/60 bg-muted/10 p-3.5">
                    <div className={cn(MARKDOWN_PROSE, 'min-w-0')}>
                      <RichMarkdown markdown={selectedTask.prompt} />
                    </div>
                  </div>
                </TabsContent>

                <TabsContent value="output">
                  <div className="max-h-[420px] overflow-y-auto overflow-x-auto rounded-lg border border-border/60 bg-muted/10 p-3.5">
                    {activeArtifact?.loading && (
                      <div className="space-y-2">
                        <Skeleton className="h-4 w-3/4" />
                        <Skeleton className="h-4 w-full" />
                        <Skeleton className="h-4 w-5/6" />
                      </div>
                    )}
                    {activeArtifact?.error && <p className="text-sm text-negative">{activeArtifact.error}</p>}
                    {activeArtifact?.detail ? (
                      <div className={MARKDOWN_PROSE}>
                        <RichMarkdown markdown={activeArtifact.detail.content} />
                      </div>
                    ) : (
                      !activeArtifact?.loading && (
                        <p className="text-sm text-muted-foreground">
                          No output available. Run the task to generate output.
                        </p>
                      )
                    )}
                  </div>
                </TabsContent>
              </Tabs>

              {/* Run history */}
              {(logsLoading || taskLogs.length > 0) && (
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Run History
                  </h3>
                  {logsLoading && taskLogs.length === 0 ? (
                    <div className="space-y-1.5">
                      {Array.from({ length: 3 }).map((_, i) => (
                        <Skeleton key={i} className="h-7 w-full" />
                      ))}
                    </div>
                  ) : (
                    <div className="divide-y divide-border/60 overflow-hidden rounded-lg border border-border/60">
                      {taskLogs.map((log) => (
                        <button
                          key={log.id}
                          type="button"
                          disabled={!log.output_path}
                          onClick={() => log.output_path && void loadArtifactForLog(log)}
                          className={cn(
                            'flex w-full items-center gap-3 px-3 py-1.5 text-left text-xs transition-colors',
                            log.output_path ? 'hover:bg-muted/40' : 'cursor-default',
                            selectedLogId === log.id && 'bg-muted/50'
                          )}
                        >
                          <span className="font-mono tabular-nums text-muted-foreground">
                            {dayjs(log.created_at).format('MMM D HH:mm')}
                          </span>
                          <span className={cn(log.status === 'ok' ? 'text-positive' : 'text-negative')}>
                            {log.status}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-muted-foreground">{log.message}</span>
                          {log.output_path && (
                            <span className="size-1.5 shrink-0 rounded-full bg-primary" title="Has output" />
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </SectionCard>
        )}
          </div>
        </TabsContent>
      </Tabs>

      {/* New Job dialog */}
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) resetForm()
        }}
      >
        <DialogContent className="max-h-[88vh] w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>New scheduled job</DialogTitle>
            <DialogDescription>
              Create a recurring task. Cron is evaluated in the job's timezone.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="nj-name">Name</Label>
              <Input
                id="nj-name"
                placeholder="e.g. daily_briefing"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="nj-cron">
                  Cron <span className="font-normal text-muted-foreground">(min hr dom mon dow)</span>
                </Label>
                <Input
                  id="nj-cron"
                  className="font-mono"
                  placeholder="30 7 * * 1-5"
                  value={form.cron_expr}
                  onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
                />
                {form.cron_expr.trim().split(/\s+/).length === 5 ? (
                  <p className="text-xs text-muted-foreground">{parseCronHuman(form.cron_expr)}</p>
                ) : null}
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="nj-tz">Timezone</Label>
                <Input
                  id="nj-tz"
                  value={form.timezone}
                  onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="nj-model">Model</Label>
                <Select value={form.model} onValueChange={(v) => setForm((f) => ({ ...f, model: v as string }))}>
                  <SelectTrigger id="nj-model" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MODEL_OPTIONS.map((m) => (
                      <SelectItem key={m} value={m}>{shortModel(m)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="nj-kind">Task kind</Label>
                <Select
                  value={form.task_kind}
                  onValueChange={(v) => setForm((f) => ({ ...f, task_kind: v as TaskKind }))}
                >
                  <SelectTrigger id="nj-kind" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TASK_KIND_OPTIONS.map((k) => (
                      <SelectItem key={k} value={k}>{k}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {form.task_kind === 'claude_with_goal' ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="nj-goal">
                  Goal target <span className="font-normal text-muted-foreground">(£ per window — optional)</span>
                </Label>
                <Input
                  id="nj-goal"
                  type="number"
                  min={0}
                  step="any"
                  placeholder="e.g. 40"
                  value={form.goal_target_gbp}
                  onChange={(e) => setForm((f) => ({ ...f, goal_target_gbp: e.target.value }))}
                />
              </div>
            ) : null}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="nj-prompt">Prompt</Label>
              <Textarea
                id="nj-prompt"
                rows={5}
                placeholder="What should Archie do on each run?"
                value={form.prompt}
                onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
              />
            </div>

            <div className="flex items-center gap-2">
              <Switch
                id="nj-enabled"
                checked={form.enabled}
                onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
              />
              <Label htmlFor="nj-enabled" className="text-sm font-normal text-muted-foreground">
                Enabled — run on schedule
              </Label>
            </div>

            {formError ? (
              <p className="flex items-center gap-1.5 text-xs text-negative">
                <XCircle className="size-3.5 shrink-0" />
                {formError}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="outline" disabled={creating} onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button disabled={creating} onClick={() => void handleCreate()}>
              {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              Create job
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
