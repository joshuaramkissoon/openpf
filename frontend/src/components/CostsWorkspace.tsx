import { useCallback, useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { Receipt, RefreshCw } from 'lucide-react'

import { Money, SectionCard, StatCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

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
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight">Usage</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Estimated token cost on your Claude subscription — not billed per call.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={busy}>
          <RefreshCw className={busy ? 'animate-spin' : undefined} />
          {busy ? 'Loading…' : 'Refresh'}
        </Button>
      </div>

      {summary ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard label="All time" value={<Money value={summary.all_time_usd} currency="USD" />} />
          <StatCard label="This week" value={<Money value={summary.this_week_usd} currency="USD" />} />
          <StatCard label="This month" value={<Money value={summary.this_month_usd} currency="USD" />} />
          <StatCard label="Chat" value={<Money value={summary.by_source.chat} currency="USD" />} />
          <StatCard label="Scheduled" value={<Money value={summary.by_source.scheduled} currency="USD" />} />
          <StatCard label="Agent runs" value={<Money value={summary.by_source.agent_run} currency="USD" />} />
        </div>
      ) : busy ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[88px] rounded-xl" />
          ))}
        </div>
      ) : null}

      <SectionCard
        title="Recent Records"
        description="Per-invocation token usage"
        action={<span className="text-xs text-muted-foreground">{records.length} entries</span>}
        noPadding
      >
        {records.length === 0 ? (
          busy ? (
            <div className="space-y-2 p-5">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 px-5 py-12 text-center">
              <Receipt className="size-5 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                No usage records yet. Records appear after the next Archie invocation.
              </p>
            </div>
          )
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs text-muted-foreground">Date</TableHead>
                <TableHead className="text-xs text-muted-foreground">Source</TableHead>
                <TableHead className="text-xs text-muted-foreground">ID</TableHead>
                <TableHead className="text-xs text-muted-foreground">Model</TableHead>
                <TableHead className="text-right text-xs text-muted-foreground">Cost</TableHead>
                <TableHead className="text-right text-xs text-muted-foreground">Duration</TableHead>
                <TableHead className="text-right text-xs text-muted-foreground">Turns</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((r) => (
                <TableRow key={r.id} className="hover:bg-muted/40">
                  <TableCell className="text-muted-foreground">{dayjs(r.recorded_at).format('MMM D HH:mm')}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{sourceLabel(r.source)}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground" title={r.source_id}>
                    {r.source_id.slice(0, 12)}…
                  </TableCell>
                  <TableCell className="font-mono text-xs">{r.model}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {r.total_cost_usd != null ? fmtCost(r.total_cost_usd) : '—'}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                    {fmtDuration(r.duration_ms)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                    {r.num_turns ?? '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>
    </div>
  )
}
