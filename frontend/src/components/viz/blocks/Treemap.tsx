import { useMemo, useState } from 'react'

import { cn } from '@/lib/utils'
import { num } from '../engine'
import { Breadcrumb, colorCap, divergingFill, EmptyNote, fieldLabel, inkOn, Tooltip, useBlur, useFmt, useTooltip, useVizEnv, useWidth } from '../shared'
import { detailRows } from './Bars'
import type { Row, TreemapBlock } from '../types'

type Rect = { x: number; y: number; w: number; h: number }
type Item<T> = { value: number; data: T }

/** Squarified treemap layout (Bruls et al.). Items must be sorted desc. */
export function squarify<T>(items: Item<T>[], box: Rect): (Rect & { data: T; value: number })[] {
  const out: (Rect & { data: T; value: number })[] = []
  const total = items.reduce((a, i) => a + i.value, 0)
  if (!total || box.w <= 0 || box.h <= 0) return out
  const scale = (box.w * box.h) / total
  let rest = items.map((i) => ({ ...i, area: i.value * scale }))
  let { x, y, w, h } = box

  const worst = (row: { area: number }[], side: number) => {
    const s = row.reduce((a, r) => a + r.area, 0)
    const max = Math.max(...row.map((r) => r.area))
    const min = Math.min(...row.map((r) => r.area))
    return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min))
  }

  while (rest.length) {
    const side = Math.min(w, h)
    const row = [rest[0]]
    let i = 1
    while (i < rest.length && worst([...row, rest[i]], side) <= worst(row, side)) {
      row.push(rest[i])
      i++
    }
    rest = rest.slice(i)
    const s = row.reduce((a, r) => a + r.area, 0)
    if (w >= h) {
      const rw = s / h
      let yy = y
      for (const r of row) {
        const rh = r.area / rw
        out.push({ x, y: yy, w: rw, h: rh, data: r.data, value: r.value })
        yy += rh
      }
      x += rw
      w -= rw
    } else {
      const rh = s / w
      let xx = x
      for (const r of row) {
        const rw = r.area / rh
        out.push({ x: xx, y, w: rw, h: rh, data: r.data, value: r.value })
        xx += rw
      }
      y += rh
      h -= rh
    }
  }
  return out
}

const GAP = 2
const HEADER = 22

export function Treemap({ block, rows }: { block: TreemapBlock; rows: Row[] }) {
  const env = useVizEnv()
  const fmt = useFmt()
  const blur = useBlur()
  const [ref, width] = useWidth<HTMLDivElement>()
  const { tip, show, hide, hostRef } = useTooltip()
  const [focus, setFocus] = useState<string | null>(null)
  const height = env.size === 'L' ? 286 : env.size ? 112 : env.compact ? 280 : width < 520 ? 320 : 420

  const sizeOf = (r: Row) => Math.max(0, num(r[block.size]) ?? 0)
  const colorOf = (r: Row) => (block.color ? num(r[block.color]) : null)
  const leaves = rows.filter((r) => sizeOf(r) > 0)
  const cap = useMemo(() => colorCap(leaves.map(colorOf)), [leaves.length, block.color]) // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    if (!block.group || env.size === 'S' || env.size === 'M') return null
    const m = new Map<string, Row[]>()
    for (const r of leaves) {
      const k = r[block.group] == null || r[block.group] === '' ? 'Other' : String(r[block.group])
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(r)
    }
    return [...m.entries()]
      .map(([key, items]) => {
        const value = items.reduce((a, r) => a + sizeOf(r), 0)
        const dc = items.reduce((a, r) => a + (num(r.day_change) ?? 0), 0)
        const hasDc = items.some((r) => num(r.day_change) != null)
        const colorVal = block.color === 'day_change_pct' && hasDc ? (value - dc ? dc / (value - dc) : null) : avgWeighted(items, sizeOf, colorOf)
        return { key, items: items.sort((a, b) => sizeOf(b) - sizeOf(a)), value, colorVal }
      })
      .sort((a, b) => b.value - a.value)
  }, [leaves, block.group, block.color]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!leaves.length) return <EmptyNote>{block.empty ?? 'Nothing to show yet.'}</EmptyNote>
  const total = leaves.reduce((a, r) => a + sizeOf(r), 0)

  const tipFor = (r: Row, groupTotal?: number) => ({
    title: String(r.name ?? r[block.label] ?? ''),
    rows: [
      { label: 'Value', value: fmt(sizeOf(r), block.format), money: true },
      { label: groupTotal != null ? '% of group' : '% of total', value: fmt(sizeOf(r) / (groupTotal ?? total), 'pct') },
      ...(block.color ? [{ label: fieldLabel(block.color), value: fmt(colorOf(r), block.colorFormat) }] : []),
      ...(num(r.day_change) != null && block.color === 'day_change_pct' ? [{ label: 'Today', value: fmt(r.day_change, 'signedMoney'), money: true }] : []),
      ...detailRows(block.details, r, fmt),
    ].filter((x, i, arr) => arr.findIndex((y) => y.label === x.label) === i),
  })

  const renderLeaves = (items: Row[], box: Rect, groupTotal?: number, interactive = true) =>
    squarify(
      items.map((r) => ({ value: sizeOf(r), data: r })),
      box,
    ).map((t) => {
      const c = divergingFill(colorOf(t.data), cap)
      const fill = block.color ? c.fill : 'color-mix(in oklab, var(--viz-neutral) 45%, var(--viz-mid))'
      const ink = block.color ? inkOn(c.strength) : 'var(--foreground)'
      const w = t.w - GAP
      const h = t.h - GAP
      const label = String(t.data[block.label] ?? '')
      // Only where the label fits inside its padding (p-2 + one line); otherwise the tooltip carries it.
      const showLabel = w > 34 && h >= 32
      const showSub = w > 46 && h >= 46
      const big = w > 120 && h > 80
      const ticker = typeof t.data.ticker === 'string' && !t.data.__other ? t.data.ticker : null
      return (
        <button
          key={`${label}-${t.x}-${t.y}`}
          type="button"
          tabIndex={interactive ? 0 : -1}
          disabled={!interactive}
          aria-label={`${label}: ${fmt(t.value, block.format)}${block.color ? `, ${fmt(colorOf(t.data), block.colorFormat)}` : ''}`}
          onPointerMove={(e) => show(e, tipFor(t.data, groupTotal))}
          onPointerLeave={hide}
          onFocus={(e) => {
            const r = (e.target as HTMLElement).getBoundingClientRect()
            show({ clientX: r.left + r.width / 2, clientY: r.top + 8 }, tipFor(t.data, groupTotal))
          }}
          onBlur={hide}
          onClick={() => ticker && interactive && env.openTicker(ticker)}
          className={cn(
            'absolute overflow-hidden rounded-[4px] text-left outline-none transition-[filter] duration-150',
            interactive && 'hover:brightness-110 focus-visible:ring-2 focus-visible:ring-ring',
            ticker && interactive ? 'cursor-pointer' : 'cursor-default',
          )}
          style={{ left: t.x + GAP / 2, top: t.y + GAP / 2, width: Math.max(0, w), height: Math.max(0, h), background: fill, color: ink }}
        >
          {showLabel ? (
            <span className="viz-tile-text absolute inset-0 flex flex-col p-2 leading-tight">
              <span className={cn('truncate font-semibold tracking-tight', big ? 'text-[15px]' : 'text-xs')}>{label}</span>
              {showSub && block.color ? <span className={cn('tabular-nums opacity-90', big ? 'text-[13px]' : 'text-[11px]')}>{fmt(colorOf(t.data), block.colorFormat)}</span> : null}
              {big ? <span className={cn('mt-auto text-[11px] tabular-nums opacity-75', blur(block.format))}>{fmt(t.value, block.format)}</span> : null}
            </span>
          ) : null}
        </button>
      )
    })

  const box: Rect = { x: 0, y: 0, w: width, h: height }
  let content
  if (groups && !focus) {
    const groupRects = squarify(
      groups.map((g) => ({ value: g.value, data: g })),
      box,
    )
    content = groupRects.map((g) => {
      const inner: Rect = { x: g.x, y: g.y + HEADER, w: g.w, h: Math.max(0, g.h - HEADER) }
      const roomy = g.w > 70 && g.h > HEADER + 24
      return (
        <div key={g.data.key}>
          {roomy ? (
            <button
              type="button"
              onClick={() => setFocus(g.data.key)}
              className="absolute flex items-center gap-1.5 truncate rounded px-1 text-left text-[11px] font-medium text-foreground/90 hover:text-foreground"
              style={{ left: g.x + 1, top: g.y, width: g.w - 2, height: HEADER }}
              title={`Drill into ${g.data.key}`}
            >
              <span className="truncate">{g.data.key}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">{fmt(g.data.value / total, 'pct')}</span>
              {block.color && g.data.colorVal != null ? (
                <span className={cn('shrink-0 tabular-nums', g.data.colorVal >= 0 ? 'text-positive' : 'text-negative')}>{fmt(g.data.colorVal, block.colorFormat)}</span>
              ) : null}
            </button>
          ) : null}
          {roomy ? renderLeaves(g.data.items, inner, g.data.value, false) : renderLeaves(g.data.items, g, g.data.value, false)}
          <button
            type="button"
            aria-label={`Drill into ${g.data.key}`}
            className="absolute rounded-[4px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={{ left: g.x, top: roomy ? g.y + HEADER : g.y, width: g.w, height: roomy ? g.h - HEADER : g.h, cursor: 'zoom-in' }}
            onClick={() => setFocus(g.data.key)}
            onPointerMove={(e) =>
              show(e, {
                title: g.data.key,
                rows: [
                  { label: 'Value', value: fmt(g.data.value, block.format), money: true },
                  { label: 'Share', value: fmt(g.data.value / total, 'pct') },
                  ...(block.color ? [{ label: 'Today', value: fmt(g.data.colorVal, block.colorFormat) }] : []),
                  { label: 'Holdings', value: String(g.data.items.length) },
                ],
                note: 'Click to drill in',
              })
            }
            onPointerLeave={hide}
          />
        </div>
      )
    })
  } else {
    const items = groups && focus ? (groups.find((g) => g.key === focus)?.items ?? []) : [...leaves].sort((a, b) => sizeOf(b) - sizeOf(a))
    const gTotal = items.reduce((a, r) => a + sizeOf(r), 0)
    content = renderLeaves(items, box, gTotal)
  }

  return (
    <div>
      {groups && focus ? <Breadcrumb trail={['All', focus]} onBack={() => setFocus(null)} /> : null}
      <div ref={hostRef} className="relative">
        <div ref={ref} className="relative w-full" style={{ height }} onPointerLeave={hide}>
          {width > 0 ? content : null}
        </div>
        <Tooltip tip={tip} hostWidth={width} />
      </div>
      {block.color && !env.size ? <ScaleKey cap={cap} format={block.colorFormat} /> : null}
    </div>
  )
}

function avgWeighted(items: Row[], size: (r: Row) => number, color: (r: Row) => number | null) {
  let s = 0
  let w = 0
  for (const r of items) {
    const c = color(r)
    if (c == null) continue
    s += c * size(r)
    w += size(r)
  }
  return w ? s / w : null
}

/** Diverging scale key: loss pole → grey → gain pole, labelled at the cap. */
export function ScaleKey({ cap, format }: { cap: number; format?: TreemapBlock['colorFormat'] }) {
  const fmt = useFmt()
  return (
    <div className="mt-3 flex items-center justify-end gap-2 text-[11px] text-muted-foreground tabular-nums">
      <span>{fmt(-cap, format)}</span>
      <span
        className="h-1.5 w-28 rounded-full"
        style={{ background: 'linear-gradient(90deg, var(--viz-loss), var(--viz-mid), var(--viz-gain))' }}
        aria-hidden
      />
      <span>{fmt(cap, format)}</span>
    </div>
  )
}
