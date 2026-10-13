import { useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { ArrowRight, ArrowUpRight, Check, Copy, Download, Info, Share2 } from 'lucide-react'

import { VizBlock } from '@/components/viz'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useAppActions } from '@/lib/app-actions'
import { privacyBlurClass, usePrivacyMode } from '@/lib/privacy'
import { cn } from '@/lib/utils'
import type { ReportHolding, ReportStat, ReportSummary } from '@/types'
import { fmtDate, fmtMoney, fmtPct, statText, statTone } from './card-model'
import { Inline, RichProvider, TickerChip } from './inline'
import { cardFileName, renderCardPng } from './share-image'
import './result-card.css'

/* ── the action button ──────────────────────────────────────────────────── */

export type SummaryAction = { label: string; run: () => void; external?: boolean }

/** A summary's `action` → one button: a proposal / play / page link, or "ask Archie". */
export function useSummaryAction(summary: ReportSummary | null | undefined, kind: string): SummaryAction | null {
  const { navigate, askArchie } = useAppActions()
  return useMemo(() => {
    const action = summary?.action?.trim()
    if (!action) return null
    const link = action.match(/\[([^\]]+)\]\(([^)\s]+)\)/)
    if (link) {
      const [, label, href] = link
      if (href.startsWith('proposal:')) return { label, run: () => navigate('/activity/proposals') }
      if (href.startsWith('#/')) return { label, run: () => navigate(href.slice(1)) }
      if (href.startsWith('/')) return { label, run: () => navigate(href) }
      if (/^https?:/.test(href)) return { label, run: () => window.open(href, '_blank', 'noreferrer'), external: true }
    }
    const plain = action.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[*_`]/g, '').replace(/^consider:\s*/i, '')
    const label = plain.charAt(0).toUpperCase() + plain.slice(1)
    return { label, run: () => askArchie(`From the ${kind.toLowerCase()}: "${plain}". Help me do this.`) }
  }, [summary, kind, navigate, askArchie])
}

/* ── stat tiles ─────────────────────────────────────────────────────────── */

function Spark({ values, tone }: { values: number[]; tone: 'pos' | 'neg' | null }) {
  const w = 120
  const h = 28
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)] as const)
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('')
  const last = pts[pts.length - 1]
  return (
    <svg className={cn('rcard-spark', tone === 'pos' && 'is-pos', tone === 'neg' && 'is-neg')} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      <path d={`${d}L${w},${h}L0,${h}Z`} className="rcard-spark-area" />
      <path d={d} className="rcard-spark-line" vectorEffect="non-scaling-stroke" />
      <circle cx={last[0]} cy={last[1]} r="2.5" className="rcard-spark-dot" />
    </svg>
  )
}

function Meter({ value, max, label }: { value: number; max: number; label?: string }) {
  const f = Math.max(0, Math.min(1, value / max))
  return (
    <div className="rcard-meter" role="img" aria-label={label ?? `${Math.round(f * 100)}%`}>
      <span style={{ width: `${Math.max(f * 100, 1.5)}%` }} />
    </div>
  )
}

/** Report dates on a small line from today: one dot per name. */
function DateLine({ marks }: { marks: { date: string; label: string }[] }) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const times = marks.map((m) => new Date(`${m.date}T00:00:00`).getTime())
  const start = Math.min(today.getTime(), ...times)
  const end = Math.max(...times, start + 86400000) + 86400000
  const at = (t: number) => ((t - start) / (end - start)) * 100
  return (
    <div className="rcard-dateline" aria-label={marks.map((m) => `${m.label} ${fmtDate(m.date)}`).join(', ')}>
      <span className="rcard-dateline-rule" />
      <span className="rcard-dateline-today" style={{ left: `${at(today.getTime())}%` }} title="Today" />
      {marks.map((m, i) => (
        <span key={`${m.label}-${i}`} className="rcard-dateline-mark" style={{ left: `${at(times[i])}%` }} title={`${m.label} · ${fmtDate(m.date)}`}>
          <i />
          <b>{m.label}</b>
        </span>
      ))}
    </div>
  )
}

export function StatTile({ stat, compact = false }: { stat: ReportStat; compact?: boolean }) {
  const mode = usePrivacyMode()
  const tone = statTone(stat)
  const text = statText(stat)
  const visual = compact ? null : stat.spark && stat.spark.length >= 2 ? (
      <Spark values={stat.spark} tone={tone} />
    ) : stat.bar && stat.bar.max > 0 ? (
      <Meter value={stat.bar.value} max={stat.bar.max} />
    ) : stat.kind === 'date' && stat.timeline?.length ? (
      <DateLine marks={stat.timeline} />
    ) : stat.tickers?.length ? (
      <span className="rcard-chips">
        {stat.tickers.slice(0, compact ? 3 : 5).map((t) => (
          <TickerChip key={t} symbol={t} />
        ))}
      </span>
    ) : null
  return (
    <div className={cn('rcard-stat', compact && 'is-compact')}>
      <dt className="rcard-stat-label">{stat.label}</dt>
      <dd className="rcard-stat-body">
        <span className={cn('rcard-stat-value', tone === 'pos' && 'is-pos', tone === 'neg' && 'is-neg', stat.kind === 'text' && 'is-text', stat.kind === 'date' && 'is-date', stat.kind === 'money' && privacyBlurClass(mode))}>
          {text}
        </span>
        {stat.sub && !compact ? (
          <span className="rcard-stat-sub">
            <Inline text={stat.sub} />
          </span>
        ) : null}
        {visual ? <span className="rcard-stat-visual">{visual}</span> : null}
      </dd>
    </div>
  )
}

export function StatGrid({ stats, compact = false }: { stats: ReportStat[]; compact?: boolean }) {
  if (!stats.length) return null
  // Compact (Home, run list): one calm row of up to three numbers.
  const shown = stats.slice(0, compact ? 3 : 4)
  const n = shown.length
  return (
    <dl className={cn('rcard-stats', compact && 'is-compact')} data-n={n} style={{ ['--n' as string]: n }}>
      {shown.map((s, i) => (
        <StatTile key={`${s.label}-${i}`} stat={s} compact={compact} />
      ))}
    </dl>
  )
}

/* ── holding cards ──────────────────────────────────────────────────────── */

const VERDICT: Record<NonNullable<ReportHolding['verdict']>, { label: string; cls: string }> = {
  hold: { label: 'Hold through', cls: 'is-hold' },
  trim: { label: 'Consider trimming', cls: 'is-trim' },
  opportunity: { label: 'Opportunity', cls: 'is-opp' },
}

export function HoldingCard({ h }: { h: ReportHolding }) {
  const mode = usePrivacyMode()
  const verdict = h.verdict ? VERDICT[h.verdict] : null
  const when = h.date ? `${fmtDate(h.date)}${h.timing ? ` · ${h.timing}` : ''}` : h.when
  const facts: { label: string; value: ReactNode; tone?: 'pos' | 'neg' | null }[] = []
  if (h.value != null || h.weight != null)
    facts.push({
      label: 'Exposure',
      value: (
        <>
          {h.value != null ? <span className={privacyBlurClass(mode)}>{fmtMoney(h.value)}</span> : null}
          {h.weight != null ? <span className="rcard-fact-aside">{fmtPct(h.weight)} of book</span> : null}
        </>
      ),
    })
  if (h.gain_pct != null) facts.push({ label: 'Gain on cost', value: fmtPct(h.gain_pct, true), tone: h.gain_pct > 0 ? 'pos' : h.gain_pct < 0 ? 'neg' : null })
  if (h.forward_pe != null) facts.push({ label: 'Fwd P/E', value: `${h.forward_pe.toFixed(1)}×` })
  if (h.usual_move != null) facts.push({ label: 'Usual move', value: `±${(h.usual_move * 100).toFixed(1)}%` })
  return (
    <article className="rcard-holding">
      <header className="rcard-holding-head">
        <TickerChip symbol={h.ticker} />
        {h.name ? <span className="rcard-holding-name">{h.name}</span> : null}
        {verdict ? <span className={cn('rcard-verdict', verdict.cls)}>{verdict.label}</span> : null}
      </header>
      {when ? <p className="rcard-holding-when">{when}</p> : null}
      {facts.length ? (
        <dl className="rcard-facts">
          {facts.map((f) => (
            <div key={f.label}>
              <dt>{f.label}</dt>
              <dd className={cn(f.tone === 'pos' && 'is-pos', f.tone === 'neg' && 'is-neg')}>{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {h.watch?.length ? (
        <div className="rcard-watch">
          <span className="rcard-watch-label">Watch</span>
          <ul>
            {h.watch.map((w, i) => (
              <li key={i}>
                <Inline text={w} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {h.note || h.extra?.length ? (
        <p className="rcard-holding-note">
          <Inline text={[h.note, ...(h.extra ?? [])].filter(Boolean).join(' · ')} />
        </p>
      ) : null}
    </article>
  )
}

export function HoldingCards({ holdings }: { holdings: ReportHolding[] }) {
  if (!holdings.length) return null
  return (
    <section className="rcard-holdings" aria-label="Names in this report">
      {holdings.map((h) => (
        <HoldingCard key={h.ticker} h={h} />
      ))}
    </section>
  )
}

export function GapsLine({ gaps }: { gaps: string[] }) {
  return (
    <p className="rcard-gaps">
      <Info className="size-3.5 shrink-0" />
      <span>{gaps.length ? `Some data wasn’t available: ${gaps.join(', ')}.` : 'Some data wasn’t available.'}</span>
    </p>
  )
}

/* ── share ──────────────────────────────────────────────────────────────── */

function ShareMenu({ summary, stats, title, when }: { summary: ReportSummary; stats: ReportStat[]; title: string; when?: string | null }) {
  const mode = usePrivacyMode()
  const [copied, setCopied] = useState(false)
  const make = () =>
    renderCardPng({
      summary,
      stats,
      title,
      when,
      theme: document.documentElement.classList.contains('light') ? 'light' : 'dark',
      blurMoney: mode !== 'off',
    })
  const copy = async () => {
    try {
      if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) throw new Error('Copying images isn’t supported in this browser')
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': make() })])
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
      toast.success('Card copied', { description: mode !== 'off' ? 'Money is blurred while privacy is on.' : undefined })
    } catch (err) {
      toast.error('Couldn’t copy the image', { description: err instanceof Error ? err.message : undefined })
    }
  }
  const download = async () => {
    try {
      const blob = await make()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = cardFileName(title, when)
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (err) {
      toast.error('Couldn’t make the image', { description: err instanceof Error ? err.message : undefined })
    }
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<button type="button" className="rcard-iconbtn" aria-label="Share this card" title="Share" />}>
        {copied ? <Check className="size-3.5" /> : <Share2 className="size-3.5" />}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuItem onClick={copy}>
          <Copy /> Copy image
        </DropdownMenuItem>
        <DropdownMenuItem onClick={download}>
          <Download /> Download PNG
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/* ── the card ───────────────────────────────────────────────────────────── */

export type ResultCardProps = {
  summary: ReportSummary
  /** Stat tiles to draw (the summary's own, or derived from key numbers). */
  stats: ReportStat[]
  /** What produced it: "Earnings week prep", "Morning brief". */
  title: string
  /** e.g. "Tue 13 Oct · 09:12" */
  when?: string | null
  /** Why it ran, one line (from the trigger), or null. */
  why?: string | null
  /** Opens the full report. */
  onOpen?: () => void
  /** `full`: the recipe's latest result. `compact`: Home / run list. `reader`: top of the full report. */
  variant?: 'full' | 'compact' | 'reader'
  /** Extra line under the headline (e.g. "Running a fresh one…"). */
  status?: ReactNode
  className?: string
  shareable?: boolean
  /** Hide the title row (when the page already names the recipe). */
  bare?: boolean
}

/**
 * The result of a run as an OG-card: a bold headline, 2–4 visual stat tiles,
 * the points that change what you'd do, one action, and a quiet footer.
 */
export function ResultCard({ summary, stats, title, when, why, onOpen, variant = 'full', status, className, shareable = variant === 'full', bare = false }: ResultCardProps) {
  const action = useSummaryAction(summary, title)
  const compact = variant === 'compact'
  const quiet = summary.signal === 'none'
  const viz = summary.viz && variant !== 'compact' ? summary.viz : null
  return (
    <RichProvider>
      <article className={cn('rcard', `is-${variant}`, summary.signal === 'high' && 'is-high', quiet && 'is-quiet', className)}>
        {variant !== 'reader' && !bare ? (
          <header className="rcard-eyebrow">
            <span className="rcard-mark" aria-hidden />
            <span className="rcard-title">{title}</span>
            {summary.signal === 'high' ? <span className="rcard-signal">Worth a look</span> : quiet ? <span className="rcard-signal is-quiet">Nothing needs you</span> : null}
            {when ? <span className="rcard-when">{when}</span> : null}
          </header>
        ) : null}

        <h3 className={cn('rcard-headline', compact && 'is-compact')}>
          {onOpen && variant !== 'reader' ? (
            <button type="button" onClick={onOpen}>
              <Inline text={summary.headline} />
            </button>
          ) : (
            <Inline text={summary.headline} />
          )}
        </h3>
        {status ? <div className="rcard-status">{status}</div> : null}

        <StatGrid stats={stats} compact={compact} />

        {viz ? (
          <div className="rcard-viz">
            <VizBlock spec={viz} size="M" fluid pinnable={false} />
          </div>
        ) : null}

        {!compact && summary.points.length ? (
          <ul className="rcard-points">
            {summary.points.map((p, i) => (
              <li key={i}>
                <Inline text={p} />
              </li>
            ))}
          </ul>
        ) : null}

        {!compact && summary.gaps?.length ? <GapsLine gaps={summary.gaps} /> : null}

        {action || (compact && onOpen) ? (
          <div className="rcard-actions">
            {action ? (
              <button type="button" className="rcard-action" onClick={action.run}>
                <span className="truncate">{action.label}</span>
                {action.external ? <ArrowUpRight className="size-3.5 shrink-0" /> : <ArrowRight className="size-3.5 shrink-0" />}
              </button>
            ) : null}
            {compact && onOpen ? (
              <button type="button" className="rcard-link" onClick={onOpen}>
                Full report <ArrowRight className="size-3.5" />
              </button>
            ) : null}
          </div>
        ) : null}

        {variant === 'full' ? (
          <footer className="rcard-foot">
            <span className="rcard-why">{why ? <Inline text={why} /> : null}</span>
            <span className="rcard-foot-actions">
              {shareable ? <ShareMenu summary={summary} stats={stats} title={title} when={when} /> : null}
              {onOpen ? (
                <button type="button" className="rcard-link" onClick={onOpen}>
                  Full report <ArrowRight className="size-3.5" />
                </button>
              ) : null}
            </span>
          </footer>
        ) : null}
      </article>
    </RichProvider>
  )
}

/** "Holdings reporting in the next 7 days:\n- ASML reports …" → "ASML reports … · TSM reports …" */
export function whyLine(triggerContext: string | null | undefined, manual?: boolean): string | null {
  const t = (triggerContext ?? '').trim()
  if (t) {
    const lines = t.split('\n').map((l) => l.replace(/^[-*]\s*/, '').trim()).filter(Boolean)
    const body = lines.length > 1 ? lines.slice(1) : lines
    return body
      .map((l) => l.replace(/,\s*[£$€][\d,.]+ held$/, '').replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (d) => fmtDate(d)))
      .join(' · ')
  }
  return manual ? 'You ran it' : null
}
