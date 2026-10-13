import { getViewData } from '@/api/views'
import { SCRAMBLE_MONEY_FACTOR, type PrivacyMode } from '@/lib/privacy'
import type { Account, Measure, Query, Row, Step, VizFormat, VizSpec } from './types'

/**
 * Data engine for viz specs: fetches whitelisted queries through the views data
 * API (read-only), resolves `from` paths, applies transforms and measures, and
 * formats numbers. No spec content is ever executed — only interpreted.
 */

export type EngineCtx = { currency: 'GBP' | 'USD'; account: Account }

const ACCOUNT_SOURCES = new Set(['portfolio', 'history', 'orders', 'dividends', 'cashflows'])
const DEFAULT_QUERIES: Record<string, Query> = { pf: { source: 'portfolio' } }
const TTL_MS = 30_000
const cache = new Map<string, { at: number; promise: Promise<unknown> }>()

/** Fetch one source (deduped + cached briefly so blocks and chat embeds share calls). */
export function fetchSource(source: string, params: Record<string, unknown>, currency: string): Promise<unknown> {
  const merged = { ...params, currency: params.currency ?? currency }
  const key = `${source}:${JSON.stringify(merged)}`
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < TTL_MS) return hit.promise
  const promise = getViewData(source, merged).catch((err) => {
    cache.delete(key)
    const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
    throw new Error(typeof detail === 'string' ? detail : `Could not load ${source}`)
  })
  cache.set(key, { at: Date.now(), promise })
  if (cache.size > 200) cache.delete(cache.keys().next().value as string)
  return promise
}

export function invalidateVizCache() {
  cache.clear()
}

function queryFor(spec: VizSpec, name: string, ctx: EngineCtx): Query {
  const q = spec.data?.[name] ?? DEFAULT_QUERIES[name]
  if (!q) throw new Error(`Query "${name}" is not defined`)
  const params = { ...(q.params ?? {}) }
  if (ACCOUNT_SOURCES.has(q.source) && spec.controls?.account && ctx.account !== 'all') params.account = ctx.account
  return { source: q.source, params }
}

function getPath(value: unknown, parts: string[]): unknown {
  let cur = value
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[p]
  }
  return cur
}

function asRows(value: unknown): Row[] {
  if (Array.isArray(value)) return value.map((v) => (v && typeof v === 'object' ? (v as Row) : { value: v }))
  if (value && typeof value === 'object') return [value as Row] // an object is one row
  if (value === undefined) return []
  return [{ value }]
}

export async function resolveRows(spec: VizSpec, from: string | undefined, steps: Step[] | undefined, ctx: EngineCtx): Promise<Row[]> {
  if (!from) return []
  const [name, ...parts] = from.split('.')
  const q = queryFor(spec, name, ctx)
  const data = await fetchSource(q.source, q.params ?? {}, ctx.currency)
  let rows = asRows(parts.length ? getPath(data, parts) : data).map((r) => ({ ...r }))
  for (const step of steps ?? []) rows = await applyStep(rows, step, ctx)
  return rows
}

// ── transforms ──────────────────────────────────────────────────────────
const SUM_FIELDS = ['value', 'cost', 'ppl', 'day_change', 'amount', 'weight', 'target_gbp', 'held_gbp', 'income']

export function num(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v)
  return null
}

/** Dynamic filter values: "$today" and "$tax_year_start" (UK tax year starts 6 April). */
function resolveToken(v: unknown): unknown {
  if (v === '$today') return new Date().toISOString().slice(0, 10)
  if (v === '$tax_year_start') {
    const d = new Date()
    const y = d.getMonth() > 3 || (d.getMonth() === 3 && d.getDate() >= 6) ? d.getFullYear() : d.getFullYear() - 1
    return `${y}-04-06`
  }
  return v
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/

function compare(a: unknown, op: string, b: unknown): boolean {
  if (typeof a === 'string' && typeof b === 'string' && ISO_DATE.test(a) && ISO_DATE.test(b) && ['>', '>=', '<', '<='].includes(op)) {
    const x = a.slice(0, 10)
    const y = b.slice(0, 10)
    return op === '>' ? x > y : op === '>=' ? x >= y : op === '<' ? x < y : x <= y
  }
  const na = num(a)
  const nb = num(b)
  switch (op) {
    case '!=':
      return String(a) !== String(b)
    case '>':
      return na != null && nb != null && na > nb
    case '>=':
      return na != null && nb != null && na >= nb
    case '<':
      return na != null && nb != null && na < nb
    case '<=':
      return na != null && nb != null && na <= nb
    case 'in':
      return Array.isArray(b) && b.map(String).includes(String(a))
    case 'notin':
      return Array.isArray(b) && !b.map(String).includes(String(a))
    case 'contains':
      return Array.isArray(a) ? a.map(String).includes(String(b)) : String(a ?? '').toLowerCase().includes(String(b ?? '').toLowerCase())
    default:
      return na != null && nb != null ? na === nb : String(a) === String(b)
  }
}

function recompute(row: Row) {
  const value = num(row.value)
  const dc = num(row.day_change)
  if (value != null && dc != null && value - dc !== 0) row.day_change_pct = dc / (value - dc)
  const ppl = num(row.ppl)
  const cost = num(row.cost)
  if (ppl != null && cost) row.ppl_pct = ppl / cost
}

function groupBy(rows: Row[], field: string): Row[] {
  const groups = new Map<string, Row[]>()
  for (const r of rows) {
    const key = r[field] == null || r[field] === '' ? 'Other' : String(r[field])
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(r)
  }
  return [...groups.entries()].map(([key, items]) => {
    const out: Row = { [field]: key, label: key, count: items.length, items }
    for (const f of SUM_FIELDS) {
      if (items.some((r) => num(r[f]) != null)) out[f] = items.reduce((a, r) => a + (num(r[f]) ?? 0), 0)
    }
    recompute(out)
    return out
  })
}

function topN(rows: Row[], n: number, other?: boolean): Row[] {
  if (rows.length <= n) return rows
  const head = rows.slice(0, n)
  if (!other) return head
  const tail = rows.slice(n)
  const fold: Row = { label: 'Other', ticker: 'Other', count: tail.length, items: tail, __other: true }
  const keys = new Set(tail.flatMap((r) => Object.keys(r)))
  for (const k of keys) {
    if (tail.some((r) => typeof r[k] === 'number')) fold[k] = tail.reduce((a, r) => a + (num(r[k]) ?? 0), 0)
    else if (typeof tail[0][k] === 'string' && !(k in fold)) fold[k] = 'Other'
  }
  recompute(fold)
  return [...head, fold]
}

function monthKey(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function months(rows: Row[], field: string, sum: string, last: number): Row[] {
  const byMonth = new Map<string, number>()
  for (const r of rows) {
    const raw = String(r[field] ?? '')
    const key = raw.slice(0, 7)
    if (!/^\d{4}-\d{2}$/.test(key)) continue
    byMonth.set(key, (byMonth.get(key) ?? 0) + (num(r[sum]) ?? 0))
  }
  const now = new Date()
  const out: Row[] = []
  for (let i = last - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const key = monthKey(d)
    out.push({
      month: key,
      label: d.toLocaleDateString('en-GB', { month: 'short', year: '2-digit' }),
      [sum]: byMonth.get(key) ?? 0,
    })
  }
  return out
}

async function enrich(rows: Row[], fields: string[], ctx: EngineCtx): Promise<Row[]> {
  const wantProfile = fields.some((f) => ['sector', 'industry', 'country', 'asset_type', 'theme', 'dividend_yield', 'ex_dividend_date'].includes(f))
  const wantPlays = fields.some((f) => ['play', 'play_id', 'theme'].includes(f))
  const symOf = (r: Row) => String(r.yf ?? r.ticker ?? '')
  const profiles = new Map<string, Row>()
  if (wantProfile) {
    const syms = [...new Set(rows.map(symOf).filter((s) => /^[A-Za-z0-9.\-^=_]{1,24}$/.test(s)))]
    for (let i = 0; i < syms.length; i += 40) {
      try {
        const res = (await fetchSource('profiles', { tickers: syms.slice(i, i + 40) }, ctx.currency)) as { profiles?: Row[] }
        for (const p of res.profiles ?? []) profiles.set(String(p.ticker), p)
      } catch {
        /* classification is best-effort */
      }
    }
  }
  const owner = new Map<string, { id: string; title: string }>()
  if (wantPlays) {
    try {
      const res = (await fetchSource('plays', {}, ctx.currency)) as { plays?: Row[]; owners?: Row[] }
      const titles = new Map((res.plays ?? []).map((p) => [String(p.id), String(p.title ?? 'Play')]))
      for (const o of res.owners ?? []) if (o.play_id) owner.set(String(o.ticker), { id: String(o.play_id), title: titles.get(String(o.play_id)) ?? 'Play' })
    } catch {
      /* no plays → Unsorted */
    }
  }
  return rows.map((r) => {
    const p = profiles.get(symOf(r)) ?? {}
    const qt = String(p.quote_type ?? '').toUpperCase()
    const assetType = qt === 'ETF' ? 'ETF' : qt === 'MUTUALFUND' ? 'Fund' : qt ? 'Stock' : null
    const own = owner.get(String(r.ticker ?? ''))
    const sector = (p.sector as string | undefined) || (assetType === 'ETF' || assetType === 'Fund' ? 'Funds' : 'Other')
    const out: Row = { ...r }
    for (const f of fields) {
      if (f === 'sector') out.sector = sector
      else if (f === 'industry') out.industry = p.industry || 'Other'
      else if (f === 'country') out.country = p.country || (assetType === 'ETF' || assetType === 'Fund' ? 'Funds' : 'Other')
      else if (f === 'asset_type') out.asset_type = assetType ?? 'Stock'
      else if (f === 'play') out.play = own?.title ?? 'Unsorted'
      else if (f === 'play_id') out.play_id = own?.id ?? null
      else if (f === 'theme') {
        out.theme = own?.title ?? sector
        out.__theme_play = !!own // false = fell back to the sector (keeps titles honest)
      }
      else if (f === 'dividend_yield') out.dividend_yield = num(p.dividend_yield)
      else if (f === 'ex_dividend_date') out.ex_dividend_date = p.ex_dividend_date ?? null
    }
    return out
  })
}

async function applyStep(rows: Row[], step: Step, ctx: EngineCtx): Promise<Row[]> {
  if ('filter' in step) {
    const { field, op = '=', value } = step.filter
    const target = Array.isArray(value) ? value.map(resolveToken) : resolveToken(value)
    return rows.filter((r) => compare(r[field], op, target))
  }
  if ('enrich' in step) return enrich(rows, step.enrich, ctx)
  if ('groupBy' in step) return groupBy(rows, step.groupBy)
  if ('sort' in step) {
    const { by, dir = 'desc' } = step.sort
    const s = dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const na = num(a[by])
      const nb = num(b[by])
      if (na != null && nb != null) return (na - nb) * s
      if (na == null && nb != null) return 1
      if (nb == null && na != null) return -1
      return String(a[by] ?? '').localeCompare(String(b[by] ?? '')) * s
    })
  }
  if ('top' in step) return topN(rows, step.top.n, step.top.other)
  if ('compute' in step) {
    const { as, op, field, a, b, by } = step.compute
    const total = op === 'share' && field ? rows.reduce((acc, r) => acc + (num(r[field]) ?? 0), 0) : 0
    return rows.map((r) => {
      const v = field ? num(r[field]) : null
      const va = a ? num(r[a]) : null
      const vb = b ? num(r[b]) : null
      let out: number | null = null
      if (op === 'share') out = v != null && total ? v / total : null
      else if (op === 'ratio') out = va != null && vb ? va / vb : null
      else if (op === 'diff') out = va != null && vb != null ? va - vb : null
      else if (op === 'product') out = va != null && vb != null ? va * vb : null
      else if (op === 'abs') out = v != null ? Math.abs(v) : null
      else if (op === 'neg') out = v != null ? -v : null
      else if (op === 'scale') out = v != null && by != null ? v * by : null
      return { ...r, [as]: out }
    })
  }
  if ('month' in step) return months(rows, step.month.field, step.month.sum, step.month.last ?? 12)
  return rows
}

// ── measures ────────────────────────────────────────────────────────────
export function measureOn(rows: Row[], m: Measure): number | null {
  let v: number | null
  if (!m.agg) v = m.field ? num(rows[0]?.[m.field]) : null
  else if (m.agg === 'count') v = rows.length
  else {
    const vals = rows.map((r) => num(r[m.field ?? ''])).filter((x): x is number => x != null)
    if (!vals.length) v = m.agg === 'sum' ? 0 : null
    else if (m.agg === 'sum') v = vals.reduce((a, b) => a + b, 0)
    else if (m.agg === 'avg') v = vals.reduce((a, b) => a + b, 0) / vals.length
    else if (m.agg === 'min') v = Math.min(...vals)
    else v = Math.max(...vals)
  }
  if (m.per && v != null) {
    const d = measureOn(rows, m.per)
    v = d ? v / d : null
  }
  return v
}

export async function resolveMeasure(spec: VizSpec, m: Measure, blockFrom: string | undefined, blockSteps: Step[] | undefined, ctx: EngineCtx): Promise<number | null> {
  const rows = await resolveRows(spec, m.from ?? blockFrom, m.from ? m.transform : (m.transform ?? blockSteps), ctx)
  // The denominator reads its own rows: its `from`, or the block's rows when the
  // numerator filtered its own (Σ filtered ÷ Σ the same filtered rows is always 1).
  if (m.per && (m.per.from || m.transform)) {
    const top = measureOn(rows, { ...m, per: undefined })
    const d = await resolveMeasure(spec, m.per, blockFrom, blockSteps, ctx)
    return top != null && d ? top / d : null
  }
  return measureOn(rows, m)
}

// ── formatting ──────────────────────────────────────────────────────────
export const MONEY_FORMATS = new Set<VizFormat>(['money', 'moneyCompact', 'signedMoney'])

export function formatValue(v: unknown, format: VizFormat | undefined, opts: { currency: string; privacy: PrivacyMode; compact?: boolean }): string {
  if (format === 'text' || format === undefined) {
    if (typeof v === 'number') return formatValue(v, 'num', opts)
    return v == null || v === '' ? '—' : String(v)
  }
  if (format === 'date') {
    const d = new Date(String(v).length === 10 ? `${v}T00:00:00` : String(v))
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' })
  }
  const n = num(v)
  if (n == null) return '—'
  if (MONEY_FORMATS.has(format)) {
    const val = opts.privacy === 'scramble' ? n * SCRAMBLE_MONEY_FACTOR : n
    const abs = Math.abs(val)
    const compact = format === 'moneyCompact' || (opts.compact && abs >= 10_000)
    let body: string
    try {
      body = new Intl.NumberFormat('en-GB', {
        style: 'currency',
        currency: opts.currency,
        notation: compact && abs >= 1000 ? 'compact' : 'standard',
        // Whole pounds from £100 up (pennies are noise on a headline amount);
        // pennies only where they matter — small sums like a £3.88 dividend.
        minimumFractionDigits: compact ? 0 : abs >= 100 ? 0 : 2,
        maximumFractionDigits: compact ? 1 : abs >= 100 ? 0 : 2,
      }).format(abs)
    } catch {
      body = abs.toFixed(2)
    }
    const sign = val < 0 ? '−' : format === 'signedMoney' && val > 0 ? '+' : ''
    return sign + body
  }
  if (format === 'pct' || format === 'signedPct') {
    // 0.17% · 6.6% · 172%: two decimals only under 1%, none from 100%.
    const a = Math.round(Math.abs(n) * 1e6) / 1e6
    const d = a < 0.01 ? 2 : a >= 1 ? 0 : 1
    const sign = n < 0 ? '−' : format === 'signedPct' && n > 0 ? '+' : ''
    return `${sign}${Math.abs(n * 100).toFixed(d)}%`
  }
  if (format === 'int') return new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(n)
  const abs = Math.abs(n)
  return new Intl.NumberFormat('en-GB', { notation: abs >= 100_000 ? 'compact' : 'standard', maximumFractionDigits: abs >= 100 ? 0 : 2 }).format(n)
}
