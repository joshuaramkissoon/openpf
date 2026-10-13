/**
 * The result card's model: format stats, turn an older report's key-numbers
 * bullets into stat tiles (short value + sublabel, never truncated), and read
 * per-holding sections ("Exposure: … · Verdict: …") into holding cards.
 * Pure functions — no React.
 */
import dayjs from 'dayjs'

import type { ReportHolding, ReportStat, ReportSummary } from '@/types'
import { plainText, type Block, type KeyNumber, type Section } from './parse'

/* ── formatting ─────────────────────────────────────────────────────────── */

const CCY_SYMBOL: Record<string, string> = { GBP: '£', USD: '$', EUR: '€' }

export function fmtMoney(v: number, ccy = 'GBP'): string {
  const sym = CCY_SYMBOL[ccy] ?? ''
  const abs = Math.abs(v)
  const body = abs >= 1_000_000 ? `${(abs / 1_000_000).toFixed(2)}m` : abs.toLocaleString('en-GB', { maximumFractionDigits: abs >= 100 ? 0 : 2 })
  return `${v < 0 ? '−' : ''}${sym}${body}`
}

export function fmtPct(v: number, signed = false): string {
  const p = v * 100
  const digits = Math.abs(p) >= 100 ? 0 : 1
  const body = `${Math.abs(p).toFixed(digits)}%`
  if (!signed) return `${p < 0 ? '−' : ''}${body}`
  return `${p > 0.0001 ? '+' : p < -0.0001 ? '−' : ''}${body}`
}

export function fmtDate(iso: string, withDay = true): string {
  const d = dayjs(iso)
  return d.isValid() ? d.format(withDay ? 'ddd D MMM' : 'D MMM') : iso
}

/** The big number on a tile, as text. */
export function statText(s: ReportStat, ccy = 'GBP'): string {
  const n = typeof s.value === 'number' ? s.value : Number(s.value)
  switch (s.kind) {
    case 'money':
      return Number.isFinite(n) ? fmtMoney(n, ccy) : String(s.value)
    case 'pct':
      return Number.isFinite(n) ? fmtPct(n) : String(s.value)
    case 'signedPct':
      return Number.isFinite(n) ? fmtPct(n, true) : String(s.value)
    case 'count':
      return Number.isFinite(n) ? n.toLocaleString('en-GB', { maximumFractionDigits: 1 }) : String(s.value)
    case 'date':
      return fmtDate(String(s.value))
    default:
      return String(s.value)
  }
}

export function statTone(s: ReportStat): 'pos' | 'neg' | null {
  if (s.tone === 'positive') return 'pos'
  if (s.tone === 'negative') return 'neg'
  if (s.tone === 'neutral') return null
  if (s.kind === 'signedPct' && typeof s.value === 'number') return s.value > 0 ? 'pos' : s.value < 0 ? 'neg' : null
  return null
}

/* ── key numbers → stat tiles (older reports) ───────────────────────────── */

const MAX_VALUE = 18
const TICKER_RUN = /^(?:[A-Z][A-Z0-9.]{0,6})(?:\s*(?:,|and|&|\+)\s*[A-Z][A-Z0-9.]{0,6})*$/

/** "£11,323 — 13.6% of the £83,261 book" → value "£11,323", sub "13.6% of the £83,261 book". */
export function splitValue(raw: string): { value: string; sub?: string } {
  const t = raw.trim()
  if (t.length <= MAX_VALUE) return { value: t }
  // A leading figure ("£11,323", "+2.4%", "3", "13.6%") is the value; the rest is context.
  const fig = t.match(/^([+\-−–]?\s?[£$€]?\d[\d,]*(?:\.\d+)?\s?(?:%|pp|k|m|bn|x)?)(?:\s*(?:[—–:;,(]|-\s|\bat\b|\bof\b|\bacross\b)\s*)(.+)$/i)
  if (fig) return { value: fig[1].trim(), sub: fig[2].replace(/\)\s*$/, '').trim() }
  // "ASML (Wed 14 Oct) and TSM (Thu 15 Oct)" → tickers as the value, the dates as the sub.
  const tickers = [...t.matchAll(/\b([A-Z][A-Z0-9.]{1,6})\b/g)].map((m) => m[1])
  const stripped = t.replace(/\([^)]*\)/g, '').replace(/\s+/g, ' ').trim()
  if (tickers.length && TICKER_RUN.test(stripped)) {
    const dates = [...t.matchAll(/\(([^)]+)\)/g)].map((m) => m[1])
    return { value: stripped, sub: dates.length ? dates.join(' · ') : undefined }
  }
  // "ASML at £5,951 (7.1% of book)" → "ASML", "£5,951 (7.1% of book)"
  const lead = t.match(/^(.{1,18}?)\s+(?:at|—|–|-|:|,)\s+(.+)$/)
  if (lead) return { value: lead[1].trim(), sub: lead[2].trim() }
  const cut = t.lastIndexOf(' ', MAX_VALUE)
  return cut > 6 ? { value: t.slice(0, cut), sub: t.slice(cut + 1) } : { value: t }
}

function kindOf(value: string): { kind: ReportStat['kind']; num?: number } {
  const m = value.replace(/[*_`]/g, '').trim().match(/^([+\-−–]?)\s?([£$€]?)\s?(\d[\d,]*(?:\.\d+)?)\s?(%)?$/)
  if (!m) return { kind: 'text' }
  const n = Number(m[3].replace(/,/g, '')) * (m[1] && m[1] !== '+' ? -1 : 1)
  if (!Number.isFinite(n)) return { kind: 'text' }
  if (m[4]) return { kind: m[1] ? 'signedPct' : 'pct', num: n / 100 }
  if (m[2]) return { kind: 'money', num: n }
  return { kind: 'count', num: n }
}

/** Key-number bullets → up to 4 stat tiles with short values. */
export function statsFromKeyNumbers(items: KeyNumber[]): ReportStat[] {
  return items.slice(0, 4).map((k) => {
    const { value, sub } = splitValue(k.value)
    const { kind, num } = kindOf(value)
    const tickers = kind === 'text' && TICKER_RUN.test(value) ? [...value.matchAll(/\b([A-Z][A-Z0-9.]{1,6})\b/g)].map((m) => m[1]) : []
    // Several names → "2" with ticker chips; one name stays the value.
    const stat: ReportStat =
      tickers.length >= 2
        ? { label: k.label, value: tickers.length, kind: 'count', tickers }
        : { label: k.label, value: kind === 'text' ? value : (num as number), kind }
    if (sub) stat.sub = sub
    return stat
  })
}

/** The tiles for a summary: its own stats, else the report's key numbers. */
export function cardStats(summary: ReportSummary | null | undefined, keyNumbers: KeyNumber[] = []): ReportStat[] {
  if (summary?.stats?.length) return summary.stats.slice(0, 4)
  return statsFromKeyNumbers(keyNumbers)
}

/* ── per-holding sections → holding cards ───────────────────────────────── */

const SECTION_TICKER = /^\$?([A-Z][A-Z0-9.]{0,7})\b(?:\s*[—–:(-]\s*(.*?)\)?)?$/
const FIELD = /^(exposure|position|timing|date|when|valuation|what to watch|watch|verdict|decision|call|setup|usual move|typical (?:move|reaction)|expectations?)\s*[:—–-]\s*(.+)$/i
const VERDICTS: [RegExp, ReportHolding['verdict']][] = [
  [/\btrim/i, 'trim'],
  [/\bopportunit/i, 'opportunity'],
  [/\bhold/i, 'hold'],
]

export function verdictOf(text: string): ReportHolding['verdict'] | undefined {
  for (const [re, v] of VERDICTS) if (re.test(text)) return v
  return undefined
}

function itemsOf(blocks: Block[]): string[] {
  const out: string[] = []
  for (const b of blocks) {
    if (b.kind === 'list') for (const it of b.items) out.push(plainText(it.text))
    else if (b.kind === 'paragraph') out.push(...plainText(b.text).split(/\s+·\s+/))
  }
  return out
}

/** A "## ASML" section of field bullets → a holding card; null if it isn't one. */
export function holdingFromSection(section: Section): ReportHolding | null {
  const head = plainText(section.heading.text).trim()
  const hm = head.match(SECTION_TICKER)
  if (!hm) return null
  const items = itemsOf(section.blocks)
  const fields: Record<string, string> = {}
  const extra: string[] = []
  for (const it of items) {
    const m = it.match(FIELD)
    if (m) fields[m[1].toLowerCase()] = m[2].trim()
    else if (it.trim()) extra.push(it.trim())
  }
  const known = Object.keys(fields).length
  if (known < 2 || !(fields.verdict || fields.decision || fields.call || fields.exposure || fields.position)) return null
  const h: ReportHolding = { ticker: hm[1] }
  const exposure = fields.exposure || fields.position
  if (exposure) {
    const money = exposure.match(/[£$€]\s?\d[\d,]*(?:\.\d+)?/)
    const pct = exposure.match(/(\d+(?:\.\d+)?)%/)
    if (money) h.value = Number(money[0].replace(/[£$€,\s]/g, ''))
    if (pct) h.weight = Number(pct[1]) / 100
  }
  const when = fields.timing || fields.date || fields.when || hm[2]
  if (when) h.when = when.replace(/,?\s*per the trigger context/i, '').trim()
  const val = fields.valuation
  if (val) {
    const pe = val.match(/(?:forward|fwd)\s*p\/?e\D{0,4}(\d+(?:\.\d+)?)/i) || val.match(/p\/?e\D{0,4}(\d+(?:\.\d+)?)/i)
    if (pe) h.forward_pe = Number(pe[1])
  }
  const watch = fields['what to watch'] || fields.watch
  if (watch) h.watch = watch.split(/\s*;\s*|\s*,\s*(?=[A-Z0-9])/).map((s) => s.trim()).filter(Boolean).slice(0, 3)
  const verdict = fields.verdict || fields.decision || fields.call
  if (verdict) h.verdict = verdictOf(verdict)
  if (extra.length) h.extra = extra.slice(0, 3)
  return h
}

/**
 * Pull the per-name sections out of a report: the holdings to draw as cards
 * (summary.holdings win, enriched by what the section said), the remaining
 * sections, and where the cards go.
 */
export function splitHoldingSections(
  sections: Section[],
  summary: ReportSummary | null | undefined,
): { holdings: ReportHolding[]; rest: Section[]; at: number } {
  const parsed = new Map<string, { h: ReportHolding; idx: number }>()
  sections.forEach((s, idx) => {
    const h = holdingFromSection(s)
    if (h) parsed.set(h.ticker, { h, idx })
  })
  const given = summary?.holdings ?? []
  let holdings: ReportHolding[]
  if (given.length) {
    holdings = given.map((g) => {
      const p = parsed.get(g.ticker)?.h
      return p ? { ...p, ...stripUndef(g), watch: g.watch?.length ? g.watch : p.watch } : g
    })
  } else {
    holdings = parsed.size >= 1 ? [...parsed.values()].map((v) => v.h) : []
  }
  if (!holdings.length) return { holdings: [], rest: sections, at: -1 }
  const tickers = new Set(holdings.map((h) => h.ticker))
  const firstIdx = sections.findIndex((s) => {
    const h = parsed.get(plainText(s.heading.text).match(SECTION_TICKER)?.[1] ?? '')
    return h && tickers.has(h.h.ticker)
  })
  const rest = sections.filter((s) => {
    const t = plainText(s.heading.text).match(SECTION_TICKER)?.[1]
    return !(t && parsed.has(t) && tickers.has(t))
  })
  const removedBefore = firstIdx >= 0 ? sections.slice(0, firstIdx).filter((s) => !rest.includes(s)).length : 0
  return { holdings, rest, at: firstIdx >= 0 ? firstIdx - removedBefore : rest.length }
}

function stripUndef<T extends object>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null)) as Partial<T>
}

/* ── tidy the report body ───────────────────────────────────────────────── */

const NOTES_RE = /^(notes?|data notes?|caveats?)$/i

/** A "Notes" section about unavailable data → one muted line of gap names. */
export function gapsFromNotes(section: Section): string[] | null {
  if (!NOTES_RE.test(plainText(section.heading.text).trim())) return null
  const text = itemsOf(section.blocks).join(' ')
  if (!/unavailable|not available|missing|couldn.t|no data|trigger context/i.test(text)) return null
  const m = text.match(/(?:unavailable|not available|missing)[^:]*:\s*([^.]+)/i)
  if (!m) return []
  return m[1].split(/\s*,\s*|\s+and\s+/).map((s) => s.trim().replace(/\.$/, '')).filter((s) => s && s.length < 40).slice(0, 5)
}

function words(s: string): Set<string> {
  return new Set(plainText(s).toLowerCase().replace(/[^a-z0-9%£.\s]/g, ' ').split(/\s+/).filter((w) => w.length > 2))
}

/** True when `a` mostly restates `b` (a callout bullet repeating the headline). */
export function restates(a: string, b: string): boolean {
  const wa = words(a)
  const wb = words(b)
  if (!wa.size || !wb.size) return false
  let shared = 0
  for (const w of wa) if (wb.has(w)) shared++
  return shared / wa.size >= 0.6
}
