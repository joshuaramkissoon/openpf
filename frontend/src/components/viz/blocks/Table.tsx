import { useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'

import { cn } from '@/lib/utils'
import { num } from '../engine'
import { EmptyNote, prettyLabel, useBlur, useFmt, useVizEnv } from '../shared'
import type { Row, VizFormat } from '../types'

export type TableColumn = { field: string; label: string; format?: VizFormat }

const SIGNED = new Set<VizFormat | undefined>(['signedMoney', 'signedPct'])

/** Sortable detail table — the drill-down / accessible twin of every chart. */
export function DataTable({ columns, rows, limit = 50, empty }: { columns: TableColumn[]; rows: Row[]; limit?: number; empty?: string }) {
  const fmt = useFmt()
  const blur = useBlur()
  const env = useVizEnv()
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null)
  if (!rows.length) return <EmptyNote>{empty ?? 'Nothing to show yet.'}</EmptyNote>
  const sorted = sort
    ? [...rows].sort((a, b) => {
        const na = num(a[sort.key])
        const nb = num(b[sort.key])
        if (na != null && nb != null) return (na - nb) * sort.dir
        return String(a[sort.key] ?? '').localeCompare(String(b[sort.key] ?? '')) * sort.dir
      })
    : rows
  const numeric = (c: TableColumn) => !!c.format && c.format !== 'text' && c.format !== 'date'
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.field}
                scope="col"
                aria-sort={sort?.key === c.field ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}
                className={cn('whitespace-nowrap border-b border-border px-2 py-2 text-xs font-medium text-muted-foreground', numeric(c) ? 'text-right' : 'text-left')}
              >
                <button
                  type="button"
                  className="inline-flex items-center gap-1 hover:text-foreground"
                  onClick={() => setSort((s) => ({ key: c.field, dir: s?.key === c.field && s.dir === -1 ? 1 : -1 }))}
                >
                  {c.label}
                  {sort?.key === c.field ? sort.dir === -1 ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" /> : null}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.slice(0, limit).map((r, i) => (
            <tr key={i} className="hover:bg-muted/40">
              {columns.map((c) => {
                const v = r[c.field]
                const n = num(v)
                const isTicker = c.field === 'ticker' && typeof v === 'string' && !r.__other
                return (
                  <td
                    key={c.field}
                    className={cn(
                      'border-b border-border/60 px-2 py-2 align-top',
                      numeric(c) || c.format === 'date' || isTicker ? 'whitespace-nowrap' : 'min-w-[7rem] max-w-[28rem]',
                      numeric(c) && 'text-right tabular-nums',
                      SIGNED.has(c.format) && n != null && (n > 0 ? 'text-positive' : n < 0 ? 'text-negative' : ''),
                      blur(c.format),
                    )}
                  >
                    {isTicker ? (
                      <button type="button" className="font-semibold hover:text-primary hover:underline hover:underline-offset-4" onClick={() => env.openTicker(v as string)}>
                        {v as string}
                      </button>
                    ) : Array.isArray(v) ? (
                      v.map((x) => (x === 'stocks_isa' ? 'ISA' : x === 'invest' ? 'Invest' : String(x))).join(', ')
                    ) : c.format === 'text' || !c.format ? (
                      prettyLabel(fmt(v, c.format))
                    ) : (
                      fmt(v, c.format)
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit ? <p className="px-2 pt-2 text-xs text-muted-foreground">Showing {limit} of {rows.length}</p> : null}
    </div>
  )
}
