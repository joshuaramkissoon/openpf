import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, Landmark, MessageSquareText } from 'lucide-react'

import { Section } from '@/components/instrument/insight-sections'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppActions } from '@/lib/app-actions'
import { cn } from '@/lib/utils'

import { getInstrumentSmartMoney, type SmartMoneyEvidence, type SmartMoneySummary } from './api'
import { smartMoneyPrompt } from './SmartMoneyLine'

const SOURCE_LABEL: Record<SmartMoneyEvidence['source'], string> = {
  form4: 'Insider',
  '13f': 'Fund',
  congress: 'Congress',
}

const SHOWN = 3

function shortDate(iso: string | null) {
  if (!iso) return ''
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function action(e: SmartMoneyEvidence) {
  if (e.side === 'buy') return e.source === '13f' ? 'added' : 'bought'
  if (e.side === 'sell') return e.source === '13f' ? 'cut' : 'sold'
  return e.kind.replace(/_/g, ' ')
}

/**
 * Smart money on a stock sheet: who's buying vs selling (insiders, followed funds,
 * Congress), how strong the evidence is, the most relevant disclosures with their
 * delay, and the caveats. Research leads, never trades to copy.
 */
export function SmartMoneySection({ ticker }: { ticker: string }) {
  const { askArchie } = useAppActions()
  const [data, setData] = useState<SmartMoneySummary | null>(null)
  const [failed, setFailed] = useState(false)
  const [all, setAll] = useState(false)

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setData(null)
    setFailed(false)
    setAll(false)
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

  const evidence = useMemo(() => {
    if (!data) return []
    const seen = new Set<string>()
    const out: SmartMoneyEvidence[] = []
    for (const e of [...data.recent, ...data.funds.changes]) {
      const k = `${e.source}|${e.actor}|${e.trade_date}|${e.side}`
      if (seen.has(k)) continue
      seen.add(k)
      out.push(e)
    }
    return out
  }, [data])

  if (failed) return null
  if (!data) {
    return (
      <Section title="Smart money" icon={Landmark}>
        <div className="space-y-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </Section>
    )
  }

  const empty = !data.line && !evidence.length
  const top = data.detections[0]
  const fundBuys = data.funds.changes.filter((c) => c.side === 'buy').length
  const fundSells = data.funds.changes.filter((c) => c.side === 'sell').length
  const flows = [
    {
      label: 'Insiders',
      window: '30 days',
      buys: data.insiders.buyers_30d,
      sells: data.insiders.sellers_30d,
      note: data.insiders.plan_sales_30d ? `${data.insiders.plan_sales_30d} plan sale${data.insiders.plan_sales_30d === 1 ? '' : 's'} excluded` : null,
    },
    {
      label: 'Funds you follow',
      window: 'last 13F',
      buys: fundBuys,
      sells: fundSells,
      note: data.funds.holders.length ? `held by ${data.funds.holders.length}` : null,
    },
    {
      label: 'Congress',
      window: '90 days',
      buys: data.congress.buys_90d,
      sells: data.congress.sells_90d,
      note: data.congress.median_delay_days != null ? `~${data.congress.median_delay_days}d to disclose` : null,
    },
  ]
  const maxCount = Math.max(1, ...flows.map((f) => Math.max(f.buys, f.sells)))
  const shown = all ? evidence : evidence.slice(0, SHOWN)

  return (
    <Section
      title="Smart money"
      icon={Landmark}
      aside={data.sandbox ? <span className="text-[10px] tracking-wider uppercase">demo data</span> : null}
    >
      {empty ? (
        <p className="text-sm text-muted-foreground">No recent insider, fund or Congress activity on {data.ticker}.</p>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="grid gap-5 sm:grid-cols-[minmax(0,1fr)_11rem]">
            {/* Who's buying vs selling */}
            <div className="flex flex-col gap-2.5" role="table" aria-label="Buying versus selling">
              <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] items-center gap-3 text-[11px] text-muted-foreground" role="row">
                <span role="columnheader" />
                <span className="flex justify-between" role="columnheader">
                  <span>Selling</span>
                  <span>Buying</span>
                </span>
              </div>
              {flows.map((f) => (
                <div key={f.label} className="grid grid-cols-[7.5rem_minmax(0,1fr)] items-center gap-3" role="row">
                  <div className="min-w-0" role="rowheader">
                    <p className="truncate text-sm">{f.label}</p>
                    <p className="truncate text-[11px] text-muted-foreground">{f.note ?? f.window}</p>
                  </div>
                  <div className="flex items-center gap-2" role="cell" aria-label={`${f.sells} selling, ${f.buys} buying, ${f.window}`}>
                    <span className="w-4 text-right font-mono text-xs text-muted-foreground tabular-nums">{f.sells || ''}</span>
                    <div className="flex h-2 flex-1 items-center">
                      <div className="flex h-full flex-1 justify-end rounded-l-full bg-muted/60">
                        {f.sells ? <div className="h-full rounded-l-full bg-negative/80" style={{ width: `${(f.sells / maxCount) * 100}%` }} /> : null}
                      </div>
                      <div className="h-3 w-px bg-border" />
                      <div className="flex h-full flex-1 rounded-r-full bg-muted/60">
                        {f.buys ? <div className="h-full rounded-r-full bg-positive/80" style={{ width: `${(f.buys / maxCount) * 100}%` }} /> : null}
                      </div>
                    </div>
                    <span className="w-4 font-mono text-xs text-muted-foreground tabular-nums">{f.buys || ''}</span>
                  </div>
                </div>
              ))}
            </div>

            {/* How much it means */}
            <div className="flex flex-col gap-2 rounded-lg border border-border/70 p-3">
              <p className="text-[11px] text-muted-foreground">Evidence strength</p>
              {top ? (
                <>
                  <p className="flex items-baseline gap-1.5">
                    <span className="font-mono text-xl font-semibold tabular-nums">{top.score}</span>
                    <span className="text-xs text-muted-foreground">/100 · {top.strength}</span>
                  </p>
                  <div className="flex gap-0.5" aria-hidden>
                    {Array.from({ length: 10 }, (_, i) => (
                      <span key={i} className={cn('h-1.5 flex-1 rounded-sm', i < Math.round(top.score / 10) ? 'bg-foreground/70' : 'bg-muted')} />
                    ))}
                  </div>
                  <p className="line-clamp-2 text-xs leading-snug text-foreground/85">{top.title}</p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">No pattern worth flagging — individual filings only.</p>
              )}
            </div>
          </div>

          {/* The filings themselves */}
          {evidence.length ? (
            <div className="flex flex-col gap-1">
              <ul className="flex flex-col divide-y divide-border/60">
                {shown.map((e, i) => (
                  <li
                    key={`${e.source}-${e.actor}-${e.trade_date}-${i}`}
                    className={cn('flex items-start gap-3 py-2.5 text-sm first:pt-0', i >= SHOWN && 'animate-in fade-in duration-300')}
                  >
                    <span
                      className={cn(
                        'mt-1.5 size-2 shrink-0 rounded-full',
                        e.side === 'buy' ? 'bg-positive' : e.side === 'sell' ? 'bg-negative' : 'bg-muted-foreground/50',
                      )}
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="leading-snug">
                        <span className="font-medium">{e.actor}</span> <span className="text-muted-foreground">{action(e)}</span>
                        {e.size ? <span className="font-mono text-xs text-foreground/85 tabular-nums"> {e.size}</span> : null}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {SOURCE_LABEL[e.source]}
                        {e.role && e.source !== '13f' ? ` · ${e.role}` : ''}
                        {e.trade_date ? ` · ${e.source === '13f' ? 'as of' : 'traded'} ${shortDate(e.trade_date)}` : ''}
                        {e.url ? (
                          <a href={e.url} target="_blank" rel="noreferrer" className="ml-1.5 inline-flex align-[-2px] hover:text-foreground" aria-label="Source filing">
                            <ExternalLink className="size-3" />
                          </a>
                        ) : null}
                      </p>
                    </div>
                    {e.delay_days != null ? (
                      <span
                        className={cn(
                          'shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] tabular-nums',
                          e.delay_days > 30 ? 'bg-warning/10 text-warning' : 'bg-muted text-muted-foreground',
                        )}
                        title={e.source === '13f' ? 'Days from quarter end to filing' : 'Days from trade to disclosure'}
                      >
                        {e.delay_days}d late
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
              {evidence.length > SHOWN ? (
                <button type="button" onClick={() => setAll((v) => !v)} className="self-start text-xs text-muted-foreground hover:text-foreground">
                  {all ? 'Show fewer' : `Show all ${evidence.length}`}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {top?.caveats[0] ?? 'Disclosures are lagging and partial. Treat them as leads to research, not trades to copy.'}
      </p>
      <div className="flex items-center justify-between gap-3">
        <Button variant="outline" size="sm" onClick={() => askArchie(smartMoneyPrompt(data.ticker, data.line))}>
          <MessageSquareText className="size-3.5" />
          Ask Archie what it means
        </Button>
        {data.attribution ? (
          <a href={data.attribution.url} target="_blank" rel="noreferrer" className="text-[10px] text-muted-foreground hover:text-foreground">
            {data.attribution.label}
          </a>
        ) : null}
      </div>
    </Section>
  )
}

export default SmartMoneySection
