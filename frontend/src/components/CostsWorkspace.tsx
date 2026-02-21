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
