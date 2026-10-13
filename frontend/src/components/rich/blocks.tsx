import { useState, type ReactNode } from 'react'
import { ChevronDown, Lightbulb, TriangleAlert, Info } from 'lucide-react'

import { ChartBlock } from '@/components/chat/chart-block'
import { cn } from '@/lib/utils'
import { Inline, TickerChip, figureValue, normaliseFigure, useRich, type Tone } from './inline'
import { blockWeight, plainText, type Block, type CalloutTone, type ListItem } from './parse'
import { ProposalBlock } from './proposal'
import { VizFence } from './viz-fence'
import { CodeBlock } from './code-block'

/* ── Callouts ──────────────────────────────────────────────────────────── */

const INSIGHT_RE = /^(what stands out|stand[- ]?outs?|key takeaways?|takeaways?|highlights?|the big picture|what matters|why it matters|key insights?|insights?)\b/i
const RISK_RE = /^(risks?|key risks?|risk notes?|risk check|watch(ing)?(\s?(out|outs|list|points))?|what to watch|things to watch|caveats?|heads[- ]up|red flags?|concerns?|downside|warnings?)\b/i
const LABEL_RE = /^\*\*(risk|risks|note|watch|watch out|caveat|heads up|warning|bottom line|takeaway|key takeaway|what stands out)\s*:?\*\*\s*:?\s*/i

export function calloutToneFor(heading: string): CalloutTone | null {
  const t = plainText(heading)
  if (INSIGHT_RE.test(t)) return 'insight'
  if (RISK_RE.test(t)) return 'risk'
  return null
}

const TONE_ICON = { insight: Lightbulb, risk: TriangleAlert, note: Info }

export function Callout({ tone, title, children }: { tone: CalloutTone; title?: ReactNode; children: ReactNode }) {
  const Icon = TONE_ICON[tone]
  return (
    <aside className={cn('rc-callout', `is-${tone}`)}>
      <Icon className="rc-callout-icon" aria-hidden />
      <div className="min-w-0 flex-1">
        {title ? <p className="rc-callout-title">{title}</p> : null}
        <div className="rc-callout-body">{children}</div>
      </div>
    </aside>
  )
}

/* ── Tables ────────────────────────────────────────────────────────────── */

const SIGNED_HEADER = /(chg|change|move|today|day|daily|p&l|p\/l|pnl|return|gain|perf|delta|Δ|\b1d\b|\b5d\b|\b1w\b|\b1m\b|ytd|drift|vs\b)/i
const TICKER_HEADER = /^(ticker|symbol|instrument|holding|name|asset|etp)$/i
const EMPTY_CELL = /^(—|–|-|n\/a|na|none|)$/i

function isNumericColumn(cells: string[]): boolean {
  const filled = cells.map((c) => plainText(c)).filter((c) => !EMPTY_CELL.test(c))
  if (!filled.length) return false
  const nums = filled.filter((c) => figureValue(c) !== null || /^[+\-−]?\d[\d,]*(\.\d+)?\s?(x|×|sh|shares)?$/i.test(c))
  return nums.length / filled.length >= 0.7
}

function NumberCell({ raw, signed }: { raw: string; signed: boolean }) {
  const { blurMoney } = useRich()
  const text = plainText(raw)
  const value = figureValue(text)
  if (value === null) return <Inline text={raw} />
  const { text: shown } = normaliseFigure(text)
  const explicit = /^[+\-−–]/.test(text)
  const tone: Tone = signed || explicit ? (value > 0 ? 'pos' : value < 0 ? 'neg' : null) : null
  const money = /[£$€]/.test(text)
  const bold = /^\*\*.*\*\*$/.test(raw.trim())
  return <span className={cn('rc-fig', tone === 'pos' && 'rc-pos', tone === 'neg' && 'rc-neg', money && blurMoney, bold && 'font-semibold')}>{shown}</span>
}

function DataTable({ block }: { block: Extract<Block, { kind: 'table' }> }) {
  const { header, rows, align, pending } = block
  const cols = header.map((h, c) => {
    const cells = rows.map((r) => r[c] ?? '')
    const numeric = rows.length > 0 && isNumericColumn(cells)
    return {
      numeric,
      signed: SIGNED_HEADER.test(plainText(h)),
      ticker: TICKER_HEADER.test(plainText(h)),
      align: align[c] ?? (numeric ? 'right' : null),
    }
  })
  return (
    <div className={cn('rc-table-wrap', rows.length > 10 && 'is-tall')}>
      <table className="rc-table">
        <thead>
          <tr>
            {header.map((h, c) => (
              <th key={c} className={cn(cols[c].align === 'right' && 'is-right', cols[c].align === 'center' && 'is-center')}>
                <Inline text={h} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pending ? (
            <tr>
              <td colSpan={header.length}>
                <span className="rc-skel h-3 w-1/2" />
              </td>
            </tr>
          ) : null}
          {rows.map((r, ri) => (
            <tr key={ri}>
              {header.map((_, c) => {
                const raw = r[c] ?? ''
                const col = cols[c]
                const sym = plainText(raw)
                return (
                  <td key={c} className={cn(col.align === 'right' && 'is-right', col.align === 'center' && 'is-center', col.numeric && 'is-num')}>
                    {col.numeric ? (
                      <NumberCell raw={raw} signed={col.signed} />
                    ) : col.ticker && /^[A-Z0-9][A-Z0-9.]{0,9}[a-z]?$/.test(sym) ? (
                      <TickerChip symbol={sym} ticker={undefined} />
                    ) : (
                      <Inline text={raw} />
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ── Lists ─────────────────────────────────────────────────────────────── */

function List({ ordered, start, items }: { ordered: boolean; start?: number; items: ListItem[] }) {
  const Tag = ordered ? 'ol' : 'ul'
  return (
    <Tag className={ordered ? 'rc-ol' : 'rc-ul'} start={ordered && start && start !== 1 ? start : undefined}>
      {items.map((it, i) => (
        <li key={i}>
          <Inline text={it.text} />
          {it.children.length ? <List ordered={it.children[0].ordered} items={it.children} /> : null}
        </li>
      ))}
    </Tag>
  )
}

/* ── Single block ──────────────────────────────────────────────────────── */

export function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case 'heading': {
      const level = Math.min(block.level, 4)
      const cls = `rc-h rc-h${level}`
      const content = <Inline text={block.text} />
      if (level === 1) return <h2 id={block.id} className={cls}>{content}</h2>
      if (level === 2) return <h3 id={block.id} className={cls}>{content}</h3>
      if (level === 3) return <h4 id={block.id} className={cls}>{content}</h4>
      return <h5 id={block.id} className={cls}>{content}</h5>
    }
    case 'paragraph': {
      const label = block.text.match(LABEL_RE)
      if (label) {
        const word = label[1].toLowerCase()
        const tone: CalloutTone = /risk|watch|caveat|warning|heads/.test(word) ? 'risk' : /note/.test(word) ? 'note' : 'insight'
        return (
          <Callout tone={tone} title={label[1].replace(/^\w/, (c) => c.toUpperCase())}>
            <p>
              <Inline text={block.text.slice(label[0].length).replace(/^[a-z]/, (c) => c.toUpperCase())} />
            </p>
          </Callout>
        )
      }
      return (
        <p className="rc-p">
          <Inline text={block.text} />
        </p>
      )
    }
    case 'list':
      return <List ordered={block.ordered} start={block.start} items={block.items} />
    case 'table':
      return <DataTable block={block} />
    case 'quote':
      return (
        <Callout tone={block.tone ?? 'note'}>
          {block.text.split(/\n{2,}/).map((p, i) => (
            <p key={i}>
              <Inline text={p} />
            </p>
          ))}
        </Callout>
      )
    case 'code':
      if (block.lang === 'viz') return <VizFence body={block.body} closed={block.closed} />
      if (block.lang === 'chart') {
        if (!block.closed) return <div className="rc-skel-card" aria-label="Drawing chart…" />
        return <ChartBlock spec={block.body} />
      }
      return <CodeBlock lang={block.lang} body={block.body} />
    case 'proposal':
      return <ProposalBlock id={block.id} />
    case 'hr':
      return <hr className="rc-hr" />
  }
}

/* ── Block lists with callout grouping + collapsing ────────────────────── */

type Item =
  | { kind: 'block'; block: Block; key: string }
  | { kind: 'callout'; tone: CalloutTone; heading: Extract<Block, { kind: 'heading' }>; blocks: Block[]; key: string }

function groupCallouts(blocks: Block[]): Item[] {
  const out: Item[] = []
  let i = 0
  while (i < blocks.length) {
    const b = blocks[i]
    const tone = b.kind === 'heading' ? calloutToneFor(b.text) : null
    if (b.kind === 'heading' && tone) {
      // In a reply, a "What stands out" heading owns only the run right after
      // it (one list, or a few paragraphs) — not the rest of the message.
      const body: Block[] = []
      let j = i + 1
      while (j < blocks.length) {
        const nb = blocks[j]
        if (nb.kind === 'heading' || nb.kind === 'proposal' || nb.kind === 'hr') break
        if (body.length && (body[0].kind !== 'paragraph' || nb.kind !== 'paragraph' || body.length >= 3)) break
        body.push(nb)
        j++
        if (nb.kind === 'list' || nb.kind === 'table') break
      }
      // A callout heading with no body yet (streaming) stays a plain heading.
      if (body.length) {
        out.push({ kind: 'callout', tone, heading: b, blocks: body, key: `c${i}` })
        i = j
        continue
      }
    }
    out.push({ kind: 'block', block: b, key: `b${i}` })
    i++
  }
  return out
}

export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {groupCallouts(blocks).map((item) =>
        item.kind === 'block' ? (
          <BlockView key={item.key} block={item.block} />
        ) : (
          <Callout key={item.key} tone={item.tone} title={<Inline text={item.heading.text} />}>
            <Blocks blocks={item.blocks} />
          </Callout>
        ),
      )}
    </>
  )
}

/** Clamp a long section with a fade and a "show more" toggle. */
export function Clamp({ children, active, label = 'Show full section' }: { children: ReactNode; active: boolean; label?: string }) {
  const [open, setOpen] = useState(false)
  // Always the same wrapper, so toggling `active` (e.g. when a stream ends)
  // never remounts the content (charts, viz) inside it.
  return (
    <div className={cn('rc-clamp', !active ? 'is-off' : open ? 'is-open' : 'is-closed')}>
      <div className="rc-clamp-body">{children}</div>
      {active ? (
        <button type="button" className="rc-clamp-toggle" onClick={() => setOpen((v) => !v)}>
          <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
          {open ? 'Show less' : label}
        </button>
      ) : null}
    </div>
  )
}

export const CLAMP_WEIGHT = 1800

/** Chat: blocks grouped into heading-led sections; long ones clamp once complete. */
export function ChatBlocks({ blocks, streaming }: { blocks: Block[]; streaming: boolean }) {
  const sections: { key: string; blocks: Block[] }[] = []
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i]
    if (!sections.length || (b.kind === 'heading' && b.level <= 3)) sections.push({ key: `s${i}`, blocks: [b] })
    else sections[sections.length - 1].blocks.push(b)
  }
  return (
    <>
      {sections.map((s) => (
        <Clamp key={s.key} active={!streaming && sections.length > 1 && blockWeight(s.blocks) > CLAMP_WEIGHT}>
          <Blocks blocks={s.blocks} />
        </Clamp>
      ))}
    </>
  )
}
