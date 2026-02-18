import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dayjs from 'dayjs'

import {
  deleteSchedulerTask,
  getArtifact,
  getSchedulerTaskLogs,
  getSchedulerTasks,
  runSchedulerTask,
  updateSchedulerTask,
} from '../api/client'
import { RichMarkdown } from './RichMarkdown'
import type { ArtifactDetail, SchedulerTask, SchedulerTaskLog } from '../types'

interface Props {
  onError: (message: string | null) => void
}

const MODEL_OPTIONS = [
  'claude-opus-4-6',
  'claude-sonnet-4-6',
  'claude-haiku-4-5',
]

function parseCronHuman(expr: string): string {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [min, hour, , , dow] = parts

  if (min.startsWith('*/') && hour === '*') {
    const n = min.slice(2)
    return `Every ${n} min`
  }

  if (min === '0' && hour.startsWith('*/')) {
    const n = hour.slice(2)
    return `Every ${n} hours`
  }

  const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

  if (/^\d+$/.test(min) && /^\d+$/.test(hour)) {
    const hh = hour.padStart(2, '0')
    const mm = min.padStart(2, '0')
    const time = `${hh}:${mm}`

    if (dow === '1-5') return `Weekdays at ${time}`

    if (/^\d$/.test(dow)) {
      const dayIdx = parseInt(dow, 10)
      const dayName = DAY_NAMES[dayIdx] ?? dow
      return `Weekly on ${dayName} at ${time}`
    }

    return `Daily at ${time}`
  }

  if (min === '0' && hour.includes(',')) {
    const times = hour
      .split(',')
      .map((h) => `${h.padStart(2, '0')}:00`)
      .join(', ')
    return `Daily at ${times}`
  }

  return expr
}

/** Shorten model ID for display: "claude-sonnet-4-20250514" → "sonnet-4" */
function shortModel(model: string): string {
  const m = model.replace(/^claude-/, '')
  // Strip date suffixes like -20250514
  return m.replace(/-\d{8}$/, '')
}

/* ── Inline SVG icon components ── */

function IconPlay({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="5,3 13,8 5,13" fill="currentColor" stroke="none" />
    </svg>
  )
}

function IconPause({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" stroke="none">
      <rect x="3.5" y="3" width="3" height="10" rx="0.8" />
      <rect x="9.5" y="3" width="3" height="10" rx="0.8" />
    </svg>
  )
}

function IconResume({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" stroke="none">
      <polygon points="4,3 12,8 4,13" />
    </svg>
  )
}

function IconTrash({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 4.5h11" />
      <path d="M5.5 4.5V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5" />
      <path d="M3.5 4.5l.7 8.5a1 1 0 0 0 1 .9h5.6a1 1 0 0 0 1-.9l.7-8.5" />
      <path d="M6.5 7v4" />
      <path d="M9.5 7v4" />
    </svg>
  )
}

function IconClose({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 4l8 8" />
      <path d="M12 4l-8 8" />
    </svg>
  )
}

interface RunResult {
  status: string
  error?: string
}

type SortMode = 'status' | 'next_run' | 'task_kind' | 'name'

const POLL_INTERVAL_MS = 3_000
const RESULT_DISPLAY_MS = 10_000

export function ScheduledJobsWorkspace({ onError }: Props) {
  const [tasks, setTasks] = useState<SchedulerTask[]>([])
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState<Set<string>>(new Set())
  const [sortMode, setSortMode] = useState<SortMode>('status')

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

  // Content tab and artifact viewer
  const [contentTab, setContentTab] = useState<'output' | 'prompt'>('prompt')
  const [activeArtifact, setActiveArtifact] = useState<{ loading: boolean; detail: ArtifactDetail | null; error: string | null } | null>(null)
  const [selectedLogId, setSelectedLogId] = useState<number | null>(null)

  async function loadArtifactForLog(log: SchedulerTaskLog) {
    if (!log.output_path) return
    setSelectedLogId(log.id)
    setContentTab('output')

    // Path resolution logic
    let relativePath: string
    const artIdx = log.output_path.indexOf('artifacts/')
    if (artIdx >= 0) {
      relativePath = log.output_path.slice(artIdx + 'artifacts/'.length)
    } else {
      const cronIdx = log.output_path.indexOf('cron_logs/')
      if (cronIdx >= 0) {
        relativePath = log.output_path.slice(cronIdx + 'cron_logs/'.length)
      } else {
        const runtimeIdx = log.output_path.indexOf('.claude/runtime/')
        relativePath = runtimeIdx >= 0 ? log.output_path.slice(runtimeIdx + '.claude/runtime/'.length) : log.output_path
      }
    }

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

  return (
    <div className="jobs-workspace">
      {/* Header */}
      <section className="glass-card jobs-header-card">
        <div className="section-heading-row">
          <div className="jobs-header-left">
            <h2>Scheduled Jobs</h2>
            <div className="jobs-header-stats">
              <span className="jobs-stat">
                <span className="jobs-stat-count">{tasks.length}</span> total
              </span>
              <span className="jobs-stat">
                <span className="jobs-stat-count up">{activeCount}</span> active
              </span>
              {pausedCount > 0 && (
                <span className="jobs-stat">
                  <span className="jobs-stat-count muted">{pausedCount}</span> paused
                </span>
              )}
            </div>
          </div>
          <div className="jobs-header-actions">
            <select
              className="jobs-sort-select"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
            >
              <option value="status">Sort: Status</option>
              <option value="next_run">Sort: Next Run</option>
              <option value="task_kind">Sort: Kind</option>
              <option value="name">Sort: Name</option>
            </select>
            <button className="btn" onClick={() => void loadTasks()} disabled={busy}>
              {busy ? 'Loading...' : 'Refresh'}
            </button>
          </div>
        </div>
      </section>

      {/* Main content area */}
      <div className={`jobs-body${selectedTask ? ' with-detail' : ''}`}>
      {/* Task list */}
      <section className="glass-card jobs-list-card">
        {sortedTasks.length === 0 && !busy && (
          <div className="jobs-empty-state">
            <div className="jobs-empty-icon">&#128197;</div>
            <p className="jobs-empty-title">No scheduled jobs yet</p>
            <p className="jobs-empty-hint">
              Ask Archie to set up recurring tasks for you. For example:
              "Schedule a daily portfolio health check at 8am" or
              "Run a leveraged scan every weekday morning at 7:30."
            </p>
          </div>
        )}

        {sortedTasks.length > 0 && (
          <div className="jobs-task-list">
            {sortedTasks.map((task) => {
              const running = isTaskRunning(task)
              const result = runResults.get(task.id)
              const taskKind = (task.meta?.task_kind as string) || null
              const taskDesc = (task.meta?.description as string) || null
              const isSelected = selectedTaskId === task.id

              return (
                <article
                  key={task.id}
                  className={`sched-task-card clickable${isSelected ? ' selected' : ''}${running ? ' running' : ''}`}
                  onClick={() => selectTask(task.id)}
                >
                  {/* Header row */}
                  <div className="sched-task-header">
                    {running
                      ? <span className="sched-run-spinner" />
                      : <span className={`sched-status-dot ${task.enabled ? 'active' : 'paused'}`} />
                    }
                    <strong>{task.name}</strong>
                    <span className="sched-badge">{parseCronHuman(task.cron_expr)}</span>
                    {taskKind && <span className="sched-task-kind-badge">{taskKind}</span>}
                    {running && <span className="sched-running-badge">running</span>}
                  </div>

                  {/* Meta row */}
                  <div className="sched-task-meta">
                    <span>
                      <span className="sched-meta-label">Last run:</span>
                      {task.last_run_at
                        ? <>{dayjs(task.last_run_at).format('ddd D MMM HH:mm')} <span className={`sched-last-status ${task.last_status === 'ok' ? 'ok' : task.last_status === 'error' ? 'err' : ''}`}>{task.last_status === 'running' ? '' : (task.last_status ?? '')}</span></>
                        : '\u2014'}
                    </span>
                    <span>
                      <span className="sched-meta-label">Next:</span>
                      {task.next_run_at ? dayjs(task.next_run_at).format('ddd D MMM HH:mm') : '\u2014'}
                    </span>
                    <span>
                      <span className="sched-meta-label">runs:</span>
                      <span className="sched-count-ok">{task.run_count ?? 0}</span>
                    </span>
                    <span>
                      <span className="sched-meta-label">fails:</span>
                      <span className="sched-count-fail">{task.failure_count ?? 0}</span>
                    </span>
                  </div>

                  {/* Description */}
                  {taskDesc && <p className="sched-task-desc">{taskDesc}</p>}

                  {/* Transient run result */}
                  {!running && result && (
                    <div className="sched-run-indicator">
                      {result.status === 'ok' && (
                        <>
                          <span className="sched-run-check">&#10003;</span>
                          <span className="sched-run-label ok">Completed</span>
                        </>
                      )}
                      {result.status === 'error' && (
                        <>
                          <span className="sched-run-x">&#10007;</span>
                          <span className="sched-run-label err">{result.error || 'Failed'}</span>
                        </>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        )}
      </section>

      {/* Task detail pane */}
      {selectedTask && (
        <section className="glass-card jobs-detail-pane">
          {/* Header */}
          <div className="jobs-detail-header">
            <div className="jobs-detail-title-row">
              {isTaskRunning(selectedTask)
                ? <span className="sched-run-spinner" />
                : <span className={`sched-status-dot ${selectedTask.enabled ? 'active' : 'paused'}`} />
              }
              <strong>{selectedTask.name}</strong>
              <span className="sched-badge">{parseCronHuman(selectedTask.cron_expr)}</span>
              {isTaskRunning(selectedTask) && <span className="sched-running-badge">running</span>}
            </div>
            <div className="jobs-detail-icon-actions">
              <button className="icon-btn run" title="Run now" disabled={isTaskRunning(selectedTask)} onClick={() => void handleRunTask(selectedTask.id)}>
                {isTaskRunning(selectedTask) ? <span className="sched-run-spinner small" /> : <IconPlay size={15} />}
              </button>
              <button className="icon-btn" title={selectedTask.enabled ? 'Pause' : 'Resume'} onClick={() => void toggleTask(selectedTask)}>
                {selectedTask.enabled ? <IconPause size={15} /> : <IconResume size={15} />}
              </button>
              <button className="icon-btn danger" title="Delete" disabled={deleting.has(selectedTask.id)} onClick={() => void deleteTask(selectedTask)}>
                <IconTrash size={15} />
              </button>
              <button className="icon-btn close" title="Close" onClick={() => { setSelectedTaskId(null); setTaskLogs([]); setActiveArtifact(null); setSelectedLogId(null); }}>
                <IconClose size={15} />
              </button>
            </div>
          </div>

          {/* Run result banner */}
          {runResults.has(selectedTask.id) && !isTaskRunning(selectedTask) && (
            <div className={`sched-run-indicator ${runResults.get(selectedTask.id)?.status === 'ok' ? 'ok' : 'err'}`}>
              {runResults.get(selectedTask.id)?.status === 'ok' ? (
                <><span className="sched-run-check">&#10003;</span><span className="sched-run-label ok">Completed</span></>
              ) : (
                <><span className="sched-run-x">&#10007;</span><span className="sched-run-label err">{runResults.get(selectedTask.id)?.error || 'Failed'}</span></>
              )}
            </div>
          )}

          {/* Meta */}
          <div className="jobs-detail-meta">
            <span className="jobs-detail-meta-item">
              <span className="muted">Model</span>
              <select className="jobs-model-select" value={MODEL_OPTIONS.includes(selectedTask.model) ? selectedTask.model : ''} onChange={(e) => void handleModelChange(selectedTask.id, e.target.value)}>
                {MODEL_OPTIONS.map((m) => (<option key={m} value={m}>{shortModel(m)}</option>))}
                {!MODEL_OPTIONS.includes(selectedTask.model) && (<option value="" disabled>{selectedTask.model}</option>)}
              </select>
            </span>
            <span><span className="muted">Timezone</span> <strong>{selectedTask.timezone}</strong></span>
            <span><span className="muted">Cron</span> <code>{selectedTask.cron_expr}</code></span>
            {selectedTask.meta?.task_kind ? (
              <span><span className="muted">Kind</span> <strong>{String(selectedTask.meta.task_kind)}</strong></span>
            ) : null}
          </div>

          {/* Tab bar */}
          <div className="jobs-content-tabs">
            <button
              className={`jobs-content-tab${contentTab === 'output' ? ' active' : ''}`}
              onClick={() => setContentTab('output')}
            >
              Output
            </button>
            <button
              className={`jobs-content-tab${contentTab === 'prompt' ? ' active' : ''}`}
              onClick={() => setContentTab('prompt')}
            >
              Prompt
            </button>
          </div>

          {/* Content area */}
          <div className="jobs-content-area">
            {contentTab === 'prompt' ? (
              <div className="chat-markdown">
                <RichMarkdown markdown={selectedTask.prompt} />
              </div>
            ) : (
              <>
                {activeArtifact?.loading && <p className="muted">Loading output...</p>}
                {activeArtifact?.error && <p className="error-text">{activeArtifact.error}</p>}
                {activeArtifact?.detail ? (
                  <div className="chat-markdown">
                    <RichMarkdown markdown={activeArtifact.detail.content} />
                  </div>
                ) : !activeArtifact?.loading && (
                  <p className="jobs-no-output muted">No output available. Run the task to generate output.</p>
                )}
              </>
            )}
          </div>

          {/* Run history */}
          {taskLogs.length > 0 && (
            <div className="jobs-run-history">
              <h4>Run History</h4>
              <div className="jobs-run-history-list">
                {taskLogs.map((log) => (
                  <div
                    key={log.id}
                    className={`jobs-run-history-entry${log.status === 'error' ? ' error' : ''}${selectedLogId === log.id ? ' active' : ''}${log.output_path ? ' clickable' : ''}`}
                    onClick={() => log.output_path && void loadArtifactForLog(log)}
                  >
                    <span className="jobs-run-history-time">{dayjs(log.created_at).format('MMM D HH:mm')}</span>
                    <span className={`sched-log-status ${log.status === 'ok' ? 'ok' : 'err'}`}>{log.status}</span>
                    <span className="jobs-run-history-msg">{log.message}</span>
                    {log.output_path && <span className="jobs-run-history-has-output" title="Has output">&#9679;</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
      </div>
    </div>
  )
}
