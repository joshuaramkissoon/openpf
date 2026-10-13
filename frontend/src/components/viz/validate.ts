import type { Block, VizSpec } from './types'

/**
 * Client-side structural check for specs that arrive from chat or storage.
 * The backend (app/services/viz_spec.py) is the authority; this only makes sure
 * the renderer gets something it can draw, with a readable reason when not.
 */
const BLOCK_TYPES = new Set(['hero', 'meter', 'kpis', 'treemap', 'allocation', 'bars', 'timeseries', 'multiples', 'heatgrid', 'table', 'note'])
const SOURCES = new Set(['portfolio', 'history', 'orders', 'dividends', 'prices', 'quotes', 'fundamentals', 'watchlist', 'proposals', 'plays', 'profiles', 'fx', 'cashflows', 'news'])
const NEEDS_FROM = new Set(['treemap', 'allocation', 'bars', 'timeseries', 'multiples', 'heatgrid', 'table'])
const FROM_RE = /^[a-z][a-z0-9_]{0,23}(\.[A-Za-z_][A-Za-z0-9_]{0,39}){0,3}$/

const DEFAULTS: Record<string, Record<string, unknown>> = {
  hero: { format: 'money', deltaFormat: 'signedPct' },
  meter: { format: 'money' },
  treemap: { format: 'money', colorFormat: 'signedPct' },
  allocation: { style: 'bar', format: 'money' },
  bars: { format: 'money', orientation: 'horizontal', limit: 12, secondaryFormat: 'signedPct' },
  timeseries: { x: 'date', style: 'area', format: 'moneyCompact' },
  multiples: { ticker: 'yf', range: '3mo', valueFormat: 'signedPct' },
  heatgrid: { mode: 'pivot', ticker: 'yf', days: 10, range: '6mo', format: 'money' },
  table: { limit: 25 },
}

/** Fill the same defaults the backend schema applies. */
export function withDefaults(block: Block): Block {
  const base = { width: 'full', ...(DEFAULTS[block.type] ?? {}) }
  const out = { ...base, ...block } as Block
  if (out.type === 'kpis') out.items = out.items.map((it) => ({ format: 'money', deltaFormat: 'signedPct', ...it }))
  return out
}

export type ValidateResult = { ok: true; spec: VizSpec } | { ok: false; error: string }

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v)
}

export function validateVizSpec(input: unknown): ValidateResult {
  let raw = input
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw)
    } catch {
      return { ok: false, error: 'The chart data is not valid JSON.' }
    }
  }
  if (!isObj(raw)) return { ok: false, error: 'A chart spec must be an object.' }
  if (raw.version !== undefined && raw.version !== 1) return { ok: false, error: `Unsupported spec version ${String(raw.version)}.` }
  if (typeof raw.title !== 'string' || !raw.title.trim()) return { ok: false, error: 'The spec needs a title.' }
  if (!Array.isArray(raw.blocks) || raw.blocks.length === 0) return { ok: false, error: 'The spec has no blocks.' }
  if (raw.blocks.length > 12) return { ok: false, error: 'At most 12 blocks.' }

  const data = isObj(raw.data) ? raw.data : {}
  for (const [name, q] of Object.entries(data)) {
    if (!isObj(q) || typeof q.source !== 'string' || !SOURCES.has(q.source)) return { ok: false, error: `Query "${name}" uses an unknown source.` }
  }
  const names = new Set([...Object.keys(data), 'pf'])

  for (const [i, b] of (raw.blocks as unknown[]).entries()) {
    if (!isObj(b) || typeof b.type !== 'string' || !BLOCK_TYPES.has(b.type)) return { ok: false, error: `Block ${i + 1} has an unknown type.` }
    const from = b.from
    if (from !== undefined && (typeof from !== 'string' || !FROM_RE.test(from))) return { ok: false, error: `Block ${i + 1} has an invalid "from".` }
    if (NEEDS_FROM.has(b.type) && !from) return { ok: false, error: `Block ${i + 1} (${b.type}) needs "from".` }
    if (typeof from === 'string' && !names.has(from.split('.')[0])) return { ok: false, error: `Block ${i + 1} reads an undefined query "${from.split('.')[0]}".` }
    if (b.type === 'kpis' && (!Array.isArray(b.items) || !b.items.length)) return { ok: false, error: `Block ${i + 1} (kpis) has no items.` }
    if (b.type === 'timeseries' && (!Array.isArray(b.series) || !b.series.length)) return { ok: false, error: `Block ${i + 1} (timeseries) has no series.` }
    if (b.type === 'table' && (!Array.isArray(b.columns) || !b.columns.length)) return { ok: false, error: `Block ${i + 1} (table) has no columns.` }
    if (b.type === 'note' && typeof b.text !== 'string') return { ok: false, error: `Block ${i + 1} (note) has no text.` }
  }
  return { ok: true, spec: { ...(raw as unknown as VizSpec), version: 1, data: data as VizSpec['data'], blocks: (raw.blocks as Block[]).map(withDefaults) } }
}
