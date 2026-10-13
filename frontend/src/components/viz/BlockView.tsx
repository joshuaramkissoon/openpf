import { useEffect, useState } from 'react'
import { ChartNoAxesColumn, Table2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import { num, resolveMeasure, resolveRows, type EngineCtx } from './engine'
import { useVizEnv } from './shared'
import type { Block, Row, VizSpec } from './types'
import { Allocation } from './blocks/Allocation'
import { Bars } from './blocks/Bars'
import { Hero, Kpis, Meter } from './blocks/Figures'
import { Heatgrid } from './blocks/Heatgrid'
import { Multiples } from './blocks/Multiples'
import { DataTable, type TableColumn } from './blocks/Table'
import { Timeseries } from './blocks/Timeseries'
import { Treemap } from './blocks/Treemap'

type Loaded = { rows: Row[]; measures: { value: number | null; delta: number | null }[]; trend?: number[] }

const SKELETON_H: Record<Block['type'], number> = {
  hero: 92,
  meter: 96,
  kpis: 84,
  treemap: 420,
  allocation: 140,
  bars: 280,
  timeseries: 260,
  multiples: 220,
  heatgrid: 260,
  table: 240,
  note: 40,
}

/** Table-view twin of a chart block (accessibility + exact values). */
function tableFor(block: Block, rows: Row[]): { columns: TableColumn[]; rows: Row[] } | null {
  switch (block.type) {
    case 'treemap':
      return {
        columns: [
          ...(block.group ? [{ field: block.group, label: block.group.replace(/_/g, ' '), format: 'text' as const }] : []),
          { field: block.label, label: 'Name', format: 'text' },
          { field: block.size, label: 'Value', format: block.format ?? 'money' },
          ...(block.color ? [{ field: block.color, label: block.color === 'day_change_pct' ? 'Today' : block.color.replace(/_/g, ' '), format: block.colorFormat ?? 'signedPct' }] : []),
        ],
        rows,
      }
    case 'allocation': {
      const total = rows.reduce((a, r) => a + (num(r[block.value]) ?? 0), 0)
      return {
        columns: [
          { field: block.label, label: 'Name', format: 'text' },
          { field: block.value, label: 'Value', format: block.format ?? 'money' },
          { field: '__share', label: 'Share', format: 'pct' },
        ],
        rows: rows.map((r) => ({ ...r, __share: total ? (num(r[block.value]) ?? 0) / total : null })),
      }
    }
    case 'bars':
      return {
        columns: [
          { field: block.label, label: 'Name', format: 'text' },
          { field: block.value, label: block.title ?? 'Value', format: block.format ?? 'money' },
          ...(block.secondary ? [{ field: block.secondary, label: block.secondary.replace(/_/g, ' '), format: block.secondaryFormat ?? 'signedPct' }] : []),
        ],
        rows,
      }
    case 'timeseries':
      return { columns: [{ field: block.x ?? 'date', label: 'Date', format: 'date' }, ...block.series.map((s) => ({ field: s.field, label: s.label, format: block.format ?? 'money' }))], rows: [...rows].reverse() }
    case 'multiples':
      return { columns: [{ field: block.label, label: 'Name', format: 'text' }, ...(block.value ? [{ field: block.value, label: block.value.replace(/_/g, ' '), format: block.valueFormat ?? 'signedPct' }] : [])], rows }
    default:
      return null
  }
}

export function BlockView({ spec, block, ctx, chrome = true }: { spec: VizSpec; block: Block; ctx: EngineCtx; chrome?: boolean }) {
  const env = useVizEnv()
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [asTable, setAsTable] = useState(false)
  const key = `${ctx.account}|${ctx.currency}|${JSON.stringify(block)}`

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    const run = async (): Promise<Loaded> => {
      if (block.type === 'note') return { rows: [], measures: [] }
      if (block.type === 'hero') {
        const [value, delta, trendRows] = await Promise.all([
          resolveMeasure(spec, block.value, block.from, block.transform, ctx),
          block.delta ? resolveMeasure(spec, block.delta, block.from, block.transform, ctx) : Promise.resolve(null),
          block.trend ? resolveRows(spec, block.trend.from, undefined, ctx).catch(() => []) : Promise.resolve([] as Row[]),
        ])
        const trend = block.trend ? trendRows.map((r) => num(r[block.trend!.field])).filter((v): v is number => v != null) : undefined
        return { rows: [], measures: [{ value, delta }], trend }
      }
      if (block.type === 'meter') {
        const [value, max, mark] = await Promise.all([
          resolveMeasure(spec, block.value, block.from, block.transform, ctx),
          typeof block.max === 'number' ? Promise.resolve(block.max) : resolveMeasure(spec, block.max, block.from, block.transform, ctx),
          block.mark ? resolveMeasure(spec, block.mark, block.from, block.transform, ctx) : Promise.resolve(null),
        ])
        return { rows: [], measures: [{ value, delta: max }, { value: mark, delta: null }] }
      }
      if (block.type === 'kpis') {
        const measures = await Promise.all(
          block.items.map(async (it) => ({
            value: await resolveMeasure(spec, it.value, block.from, block.transform, ctx).catch(() => null),
            delta: it.delta ? await resolveMeasure(spec, it.delta, block.from, block.transform, ctx).catch(() => null) : null,
          })),
        )
        return { rows: [], measures }
      }
      return { rows: await resolveRows(spec, block.from, block.transform, ctx), measures: [] }
    }
    run()
      .then((d) => alive && setData(d))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : 'Could not load this block'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  const table = data && block.type !== 'table' ? tableFor(block, data.rows) : null

  let body: React.ReactNode
  if (error) body = <p className="py-6 text-center text-sm text-muted-foreground">{error}</p>
  else if (!data) body = <div className="animate-pulse rounded-lg bg-muted/60" style={{ height: env.compact ? Math.min(SKELETON_H[block.type], 280) : SKELETON_H[block.type] }} />
  else if (asTable && table) body = <DataTable columns={table.columns} rows={table.rows} empty={block.empty} />
  else {
    switch (block.type) {
      case 'hero':
        body = <Hero block={block} value={data.measures[0]?.value ?? null} delta={data.measures[0]?.delta ?? null} trend={data.trend} />
        break
      case 'meter':
        body = <Meter block={block} value={data.measures[0]?.value ?? null} max={data.measures[0]?.delta ?? null} mark={data.measures[1]?.value ?? null} />
        break
      case 'kpis':
        body = <Kpis block={block} values={data.measures} />
        break
      case 'treemap':
        body = <Treemap block={block} rows={data.rows} />
        break
      case 'allocation':
        body = <Allocation block={block} rows={data.rows} />
        break
      case 'bars':
        body = <Bars block={env.size ? { ...block, limit: Math.min(block.limit ?? 12, env.size === 'L' ? 8 : env.size === 'M' ? 4 : 3) } : block} rows={data.rows} />
        break
      case 'timeseries':
        body = <Timeseries block={block} rows={data.rows} />
        break
      case 'multiples':
        body = <Multiples block={block} rows={data.rows} />
        break
      case 'heatgrid':
        body = <Heatgrid block={block} rows={data.rows} />
        break
      case 'table':
        body = (
          <DataTable
            columns={env.size === 'S' ? block.columns.slice(0, 2) : env.size === 'M' ? block.columns.slice(0, 3) : block.columns}
            rows={data.rows}
            limit={env.size === 'L' ? 8 : env.size ? 4 : block.limit}
            empty={block.empty}
          />
        )
        break
      case 'note':
        body = <p className="text-sm leading-relaxed text-muted-foreground">{block.text}</p>
        break
    }
  }

  const bare = block.type === 'hero' || block.type === 'kpis' || block.type === 'note' || block.type === 'meter'
  const inner = <div className={cn('transition-opacity duration-200', loading && data && 'opacity-50')}>{body}</div>
  if (bare || !chrome) {
    return (
      <div className="viz-in">
        {!bare && (block.title || block.subtitle) ? <BlockHeader block={block} table={!!table} asTable={asTable} onToggle={() => setAsTable((v) => !v)} /> : null}
        {inner}
      </div>
    )
  }
  return (
    <section className="viz-in min-w-0 rounded-xl border border-border bg-card p-5">
      <BlockHeader block={block} table={!!table} asTable={asTable} onToggle={() => setAsTable((v) => !v)} />
      {inner}
    </section>
  )
}

function BlockHeader({ block, table, asTable, onToggle }: { block: Block; table: boolean; asTable: boolean; onToggle: () => void }) {
  if (!block.title && !block.subtitle && !table) return null
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        {block.title ? <h3 className="text-sm font-semibold tracking-tight">{block.title}</h3> : null}
        {block.subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{block.subtitle}</p> : null}
      </div>
      {table ? (
        <button
          type="button"
          onClick={onToggle}
          className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title={asTable ? 'Show chart' : 'Show as table'}
          aria-label={asTable ? 'Show chart' : 'Show as table'}
          aria-pressed={asTable}
        >
          {asTable ? <ChartNoAxesColumn className="size-4" /> : <Table2 className="size-4" />}
        </button>
      ) : null}
    </div>
  )
}
