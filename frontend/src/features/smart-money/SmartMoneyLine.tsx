import { useEffect, useState } from 'react'
import { ExternalLink, Landmark } from 'lucide-react'
import { ArchieIcon } from '@/components/kit'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'

import { getInstrumentSmartMoney, type SmartMoneyEvidence, type SmartMoneySummary } from './api'

const SOURCE_LABEL: Record<SmartMoneyEvidence['source'], string> = {
  form4: 'Insider',
  '13f': 'Fund',
  congress: 'Congress',
}

const STRENGTH_TONE: Record<string, string> = {
  strong: 'text-foreground',
  moderate: 'text-foreground/80',
  weak: 'text-muted-foreground',
}

function delayText(e: SmartMoneyEvidence) {
  if (e.delay_days == null) return null
  if (e.source === '13f') return `filed ${e.delay_days}d after quarter end`
  return `disclosed ${e.delay_days}d later`
}

function shortDate(iso: string | null) {
  if (!iso) return ''
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** Ask Archie about the disclosures on one name — framed as research, not copying. */
export function smartMoneyPrompt(ticker: string, line: string) {
  return [
    `Smart money on **${ticker}**: ${line || 'no recent disclosures'}.`,
    ``,
    `What do these disclosures actually tell me, how stale are they, and does any of it change my view on ${ticker}? Don't suggest copying anyone.`,
  ].join('\n')
}

/**
 * One-line "smart money" read for a stock sheet: insider buyers (30d, plan
 * sales excluded), followed funds holding it, and Congress trades with their
 * disclosure delay. Opens a small popover with the evidence and caveats.
 * Renders nothing when there's nothing to say (unless `showEmpty`).
 */
export function SmartMoneyLine({
  ticker,
  className,
  showEmpty = false,
}: {
  ticker: string
  className?: string
  showEmpty?: boolean
}) {
  const { askArchie } = useAppActions()
  const [data, setData] = useState<SmartMoneySummary | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setData(null)
    setFailed(false)
    const load = (retry: boolean) =>
      getInstrumentSmartMoney(ticker)
        .then((d) => {
          if (!alive) return
          setData(d)
          // First look at this name: the backend is fetching Form 4s — check back once.
          if (d.refreshing && retry) timer = setTimeout(() => void load(false), 6000)
        })
        .catch(() => alive && setFailed(true))
    void load(true)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [ticker])

  if (failed || !data) return null
  if (!data.line && !showEmpty) return null

  const top = data.detections[0]
  return (
    <div className={cn('flex min-w-0 items-center gap-2 text-xs text-muted-foreground', className)}>
      <Landmark className="size-3.5 shrink-0" aria-hidden />
      <span className="min-w-0 truncate" title={data.line || undefined}>
        {data.line || 'No recent insider, fund or Congress activity'}
      </span>
      {data.line ? (
        <Popover>
          <PopoverTrigger className="shrink-0 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
            Details
          </PopoverTrigger>
          <PopoverContent align="start" className="w-80 gap-3 text-xs">
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-sm font-medium">Smart money · {data.ticker}</p>
              {data.sandbox ? <span className="text-[10px] uppercase tracking-wide text-muted-foreground">demo data</span> : null}
            </div>

            {top ? (
              <div className="space-y-0.5">
                <p className={cn('font-medium', STRENGTH_TONE[top.strength])}>{top.title}</p>
                <p className="text-muted-foreground">
                  Evidence: {top.strength} ({top.score}/100)
                </p>
              </div>
            ) : null}

            {data.recent.length ? (
              <ul className="space-y-1.5">
                {data.recent.slice(0, 6).map((e, i) => (
                  <li key={`${e.actor}-${e.trade_date}-${i}`} className="flex gap-2">
                    <span
                      className={cn(
                        'mt-[5px] size-1.5 shrink-0 rounded-full',
                        e.side === 'buy' ? 'bg-positive' : e.side === 'sell' ? 'bg-negative' : 'bg-muted-foreground/50',
                      )}
                    />
                    <span className="min-w-0">
                      <span className="text-foreground">{e.actor}</span>
                      <span className="text-muted-foreground">
                        {' '}
                        · {SOURCE_LABEL[e.source]}
                        {e.role && e.source !== '13f' ? `, ${e.role}` : ''} · {e.side === 'buy' ? 'bought' : e.side === 'sell' ? 'sold' : e.kind}
                        {e.size ? ` ${e.size}` : ''}
                        {e.trade_date ? ` · ${shortDate(e.trade_date)}` : ''}
                        {delayText(e) ? ` (${delayText(e)})` : ''}
                      </span>
                      {e.url ? (
                        <a href={e.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex align-middle text-muted-foreground hover:text-foreground">
                          <ExternalLink className="size-3" />
                        </a>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}

            {data.funds.holders.length ? (
              <p className="text-muted-foreground">
                Held by {data.funds.holders.map((h) => `${h.fund}${h.weight != null ? ` (${(h.weight * 100).toFixed(1)}%)` : ''}`).join(', ')}
                {data.funds.holders[0]?.period ? ` as of ${shortDate(data.funds.holders[0].period)}` : ''}.
              </p>
            ) : null}

            <p className="leading-relaxed text-muted-foreground">
              {top?.caveats[0] ??
                'Disclosures are lagging and partial. Treat them as leads to research, not trades to copy.'}
              {data.insiders.plan_sales_30d ? ` ${data.insiders.plan_sales_30d} pre-scheduled plan sale${data.insiders.plan_sales_30d === 1 ? '' : 's'} excluded.` : ''}
            </p>

            <div className="flex items-center justify-between gap-2 pt-0.5">
              <button
                type="button"
                onClick={() => askArchie(smartMoneyPrompt(data.ticker, data.line))}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              >
                <ArchieIcon className="size-3" />
                Ask Archie
              </button>
              {data.attribution ? (
                <a href={data.attribution.url} target="_blank" rel="noreferrer" className="text-[10px] text-muted-foreground hover:text-foreground">
                  {data.attribution.label}
                </a>
              ) : null}
            </div>
          </PopoverContent>
        </Popover>
      ) : null}
    </div>
  )
}

export default SmartMoneyLine
