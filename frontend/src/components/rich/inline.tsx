import { createContext, useContext, useMemo, type ReactNode } from 'react'

import { useHeldPositions } from '@/components/instrument/instrument-provider'
import { useAppActions } from '@/lib/app-actions'
import { privacyBlurClass, usePrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import { ProposalChip } from './proposal'

/* ── Context: which tickers are "known" (held) and how to open them ──────── */

type RichCtx = {
  /** Upper-cased display symbol → the ticker string the detail sheet expects. */
  tickers: Map<string, string>
  tickerRe: RegExp | null
  openTicker: (t: string) => void
  navigate: (path: string) => void
  blurMoney: string
}

const Ctx = createContext<RichCtx | null>(null)

// Short all-caps words that are real English/finance words, never chipped
// even if a holding happens to use them as a symbol.
const STOP = new Set(['A', 'I', 'AI', 'ALL', 'ARE', 'FOR', 'IT', 'ON', 'OR', 'NOW', 'US', 'UK', 'EU', 'ETF', 'ISA', 'GBP', 'USD', 'EUR', 'CEO', 'CFO', 'IPO', 'EPS', 'YTD', 'PE', 'TLDR', 'NOTE', 'RSI', 'SMA', 'ATH'])

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function RichProvider({ children, extra }: { children: ReactNode; extra?: string[] }) {
  const positions = useHeldPositions()
  const { openTicker, navigate } = useAppActions()
  const mode = usePrivacyMode()
  const value = useMemo<RichCtx>(() => {
    const tickers = new Map<string, string>()
    for (const p of positions) {
      const base = p.ticker.split('_')[0]
      const forms = [p.display_ticker, base, base.replace(/l$/, '')]
      for (const f of forms) {
        if (!f || f.length < 2) continue
        const key = f.toUpperCase()
        if (!STOP.has(key) && !tickers.has(key)) tickers.set(key, p.ticker)
      }
    }
    for (const sym of extra ?? []) {
      const full = sym.toUpperCase()
      // Market symbols too: "SMSN.L" and its bare "SMSN" both chip.
      for (const f of [full, full.replace(/\.[A-Z]{1,2}$/, '')]) {
        if (f.length >= 2 && !STOP.has(f) && !tickers.has(f)) tickers.set(f, full)
      }
    }
    const syms = [...tickers.keys()].sort((a, b) => b.length - a.length).map(escapeRe)
    const tickerRe = syms.length ? new RegExp(`(?<![A-Za-z0-9$£€.])(${syms.join('|')})(?![A-Za-z0-9])`, 'g') : null
    return { tickers, tickerRe, openTicker, navigate, blurMoney: privacyBlurClass(mode) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, openTicker, navigate, mode, (extra ?? []).join(',')])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useRich(): RichCtx {
  const ctx = useContext(Ctx)
  if (ctx) return ctx
  return { tickers: new Map(), tickerRe: null, openTicker: () => {}, navigate: () => {}, blurMoney: '' }
}

/* ── Atoms ─────────────────────────────────────────────────────────────── */

export function TickerChip({ symbol, ticker }: { symbol: string; ticker?: string }) {
  const { openTicker } = useRich()
  return (
    <button type="button" className="rc-ticker" onClick={() => openTicker(ticker ?? symbol)} title={`Open ${symbol}`}>
      {symbol}
    </button>
  )
}

export type Tone = 'pos' | 'neg' | null

/** "+1234.5" → "+1,234.5"; "-" → "−". Keeps the author's precision. */
export function normaliseFigure(raw: string): { text: string; sign: -1 | 0 | 1 } {
  let s = raw.replace(/\s+/g, ' ').trim()
  let sign: -1 | 0 | 1 = 0
  if (/^[-−–]/.test(s)) {
    sign = -1
    s = s.replace(/^[-−–]\s?/, '')
  } else if (s.startsWith('+')) {
    sign = 1
    s = s.replace(/^\+\s?/, '')
  }
  s = s.replace(/^([£$€]?)\s?/, '$1')
  s = s.replace(/(\d+)(?=(\.\d+)?)/, (int) => (int.length > 3 && !int.includes(',') ? Number(int).toLocaleString('en-GB') : int))
  return { text: `${sign === -1 ? '−' : sign === 1 ? '+' : ''}${s}`, sign }
}

/** Parse a figure-ish string to a number (for sign tests); null if not numeric. */
export function figureValue(raw: string): number | null {
  const t = raw.replace(/[*_`]/g, '').trim()
  const m = t.match(/^([+\-−–]?)\s?[£$€]?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|pp|bps|x|k|m|bn|b)?$/i)
  if (!m) return null
  const n = Number(m[2].replace(/,/g, ''))
  if (!Number.isFinite(n)) return null
  return m[1] && m[1] !== '+' ? -n : n
}

function Figure({ raw, money, tone }: { raw: string; money: boolean; tone?: Tone }) {
  const { blurMoney } = useRich()
  const { text, sign } = normaliseFigure(raw)
  const t: Tone = tone !== undefined && tone !== null ? tone : sign > 0 ? 'pos' : sign < 0 ? 'neg' : null
  return <span className={cn('rc-fig', t === 'pos' && 'rc-pos', t === 'neg' && 'rc-neg', money && blurMoney)}>{text}</span>
}

/* ── Inline renderer ───────────────────────────────────────────────────── */

const MONEY = String.raw`(?:[+\-−]\s?)?[£$€]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:bn|[kKmMbB])(?![A-Za-z]))?`
const PCT = String.raw`[+\-−]?\d[\d,]*(?:\.\d+)?\s?%`
const PP = String.raw`[+\-−]\d+(?:\.\d+)?\s?(?:pp|bps?)(?![A-Za-z])`
const CASHTAG = String.raw`\$[A-Z]{1,6}(?![A-Za-z])`
const LEAF = new RegExp(`(?<![\\w.])(${MONEY})|(?<![\\w.])(${PCT})|(?<![\\w.])(${PP})|(${CASHTAG})`, 'g')

function renderLeaf(text: string, ctx: RichCtx, key: string, tone?: Tone): ReactNode[] {
  const out: ReactNode[] = []
  let cursor = 0
  let n = 0
  const pushText = (s: string) => {
    if (!s) return
    if (!ctx.tickerRe) {
      out.push(s)
      return
    }
    let c = 0
    ctx.tickerRe.lastIndex = 0
    for (const m of s.matchAll(ctx.tickerRe)) {
      const at = m.index ?? 0
      if (at > c) out.push(s.slice(c, at))
      const sym = m[1]
      out.push(<TickerChip key={`${key}-t${n++}`} symbol={sym} ticker={ctx.tickers.get(sym.toUpperCase())} />)
      c = at + sym.length
    }
    if (c < s.length) out.push(s.slice(c))
  }
  for (const m of text.matchAll(LEAF)) {
    const at = m.index ?? 0
    if (at > cursor) pushText(text.slice(cursor, at))
    const [whole, money, pct, pp, cash] = m
    if (cash) {
      const sym = cash.slice(1)
      out.push(<TickerChip key={`${key}-c${n++}`} symbol={sym} ticker={ctx.tickers.get(sym)} />)
    } else {
      out.push(<Figure key={`${key}-f${n++}`} raw={money || pct || pp} money={Boolean(money)} tone={tone} />)
    }
    cursor = at + whole.length
  }
  if (cursor < text.length) pushText(text.slice(cursor))
  return out
}

const INLINE = /(`[^`\n]+`)|(\[[^\]]+\]\([^)\s]+\))|(\*\*[^*]+?\*\*|__[^_]+?__)|((?<![\w*])\*(?!\s)[^*\n]+?\*(?![\w*])|(?<![\w_])_(?!\s)[^_\n]+?_(?![\w_]))|(\n)/g

export function renderInline(text: string, ctx: RichCtx, key = 'i', tone?: Tone): ReactNode[] {
  const out: ReactNode[] = []
  let cursor = 0
  let n = 0
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0
    if (at > cursor) out.push(...renderLeaf(text.slice(cursor, at), ctx, `${key}-${n++}`, tone))
    const [whole, code, link, strong, em, br] = m
    const k = `${key}-${n++}`
    if (code) {
      out.push(<code key={k} className="rc-code">{code.slice(1, -1)}</code>)
    } else if (link) {
      const split = link.indexOf('](')
      const label = link.slice(1, split)
      const href = link.slice(split + 2, -1)
      const proposal = href.match(/^(?:proposal:|#?\/activity\/proposals\?id=)([\w-]+)$/)
      const ticker = href.match(/^ticker:([\w.\-^=]+)$/)
      if (proposal) out.push(<ProposalChip key={k} id={proposal[1]} label={label} />)
      else if (ticker) out.push(<TickerChip key={k} symbol={label.replace(/^\$/, '')} ticker={ticker[1]} />)
      else if (href.startsWith('#/')) {
        out.push(
          <a key={k} href={href} className="rc-link" onClick={(e) => { e.preventDefault(); ctx.navigate(href.slice(1)) }}>
            {renderInline(label, ctx, k, tone)}
          </a>,
        )
      } else {
        out.push(
          <a key={k} href={href} target="_blank" rel="noreferrer" className="rc-link">
            {renderInline(label, ctx, k, tone)}
          </a>,
        )
      }
    } else if (strong) {
      out.push(<strong key={k}>{renderInline(strong.slice(2, -2), ctx, k, tone)}</strong>)
    } else if (em) {
      out.push(<em key={k}>{renderInline(em.slice(1, -1), ctx, k, tone)}</em>)
    } else if (br) {
      out.push(' ')
    }
    cursor = at + whole.length
  }
  if (cursor < text.length) out.push(...renderLeaf(text.slice(cursor), ctx, `${key}-${n++}`, tone))
  return out
}

/** Hook form for components. */
export function Inline({ text, tone }: { text: string; tone?: Tone }) {
  const ctx = useRich()
  return <>{renderInline(text, ctx, 'in', tone)}</>
}
