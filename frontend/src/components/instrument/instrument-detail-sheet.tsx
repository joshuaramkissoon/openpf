import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowLeftRight, Loader2, MessageSquareText, Sparkles, Star, Telescope } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { privacyBlurClass, usePrivacyMode } from "@/lib/privacy"
import { toastApiError } from "@/lib/api-error"
import { getInstrumentInsight, type InstrumentInsight, type InsightPlay } from "@/api/instruments"
import { createWatchlistItem, deleteWatchlistItem } from "@/api/client"
import { InsightChartView } from "./insight-chart"
import { EventsSection, FundamentalsSection, NewsSection, RiskSection, ThesisSection, WeightMeter } from "./insight-sections"
import { TradeSizer } from "./trade-sizer"
import { fmtMoney, fmtPct, fmtPrice, num } from "./insight-format"

/** A best-effort instant identity to paint the header before the fetch lands. */
export type InstrumentHint = {
  name?: string | null
  currency?: string | null
  price?: number | null
  change_pct?: number | null
}

const POLL_MS = 2500
const MAX_POLLS = 4

/**
 * The stock sheet. One hero (what it is, what it's doing, why), then one line on
 * what it means for *you*, then the chart, then the questions an investor asks
 * in order: should I worry, what's coming, why do I own it, news, fundamentals.
 * Every section is optional — a missing source hides its section.
 */
export function InstrumentDetailSheet({
  ticker,
  open,
  onOpenChange,
  hint,
  onAskArchie,
  onAskArchieSend,
  onDeepAnalysis,
  onWatchlistChanged,
}: {
  ticker: string | null
  currency?: "GBP" | "USD"
  open: boolean
  onOpenChange: (open: boolean) => void
  hint?: InstrumentHint | null
  onAskArchie?: (ticker: string, name?: string | null) => void
  /** Start a chat with this prompt and send it. */
  onAskArchieSend?: (prompt: string) => void
  onDeepAnalysis?: (ticker: string, name?: string | null) => void
  onWatchlistChanged?: () => void
}) {
  const [data, setData] = useState<InstrumentInsight | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [watchBusy, setWatchBusy] = useState(false)
  const [sizerOpen, setSizerOpen] = useState(false)
  const reqRef = useRef(0)

  const load = useCallback(
    async (t: string, attempt = 0) => {
      const req = ++reqRef.current
      try {
        const res = await getInstrumentInsight(t)
        if (req !== reqRef.current) return
        setData(res)
        setError(null)
        // Slow sections keep computing server-side; pick them up shortly.
        if (res.pending.length && attempt < MAX_POLLS) {
          window.setTimeout(() => {
            if (req === reqRef.current) void load(t, attempt + 1)
          }, POLL_MS)
        }
      } catch (err: unknown) {
        if (req !== reqRef.current) return
        const status = (err as { response?: { status?: number } })?.response?.status
        setError(status === 404 ? "We couldn't find that instrument." : "Couldn't load this instrument right now.")
      }
    },
    [],
  )

  useEffect(() => {
    if (!open || !ticker) return
    setData(null)
    setError(null)
    setSizerOpen(false)
    void load(ticker)
    return () => {
      reqRef.current++
    }
  }, [open, ticker, load])

  async function toggleWatch() {
    if (!data || !ticker) return
    setWatchBusy(true)
    try {
      if (data.watchlist) {
        await deleteWatchlistItem(data.watchlist.id)
        toast.success(`Stopped watching ${data.display_ticker}`)
      } else {
        await createWatchlistItem({ symbol: ticker })
        toast.success(`Watching ${data.display_ticker}`)
      }
      reqRef.current++
      await load(ticker, MAX_POLLS)
      onWatchlistChanged?.()
    } catch (err) {
      toastApiError(err, "Watchlist update failed")
    } finally {
      setWatchBusy(false)
    }
  }

  const name = data?.name ?? hint?.name ?? null
  const display = data?.display_ticker ?? ticker ?? ""

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-y-auto p-0 data-[side=right]:w-full data-[side=right]:max-w-none sm:data-[side=right]:w-[44rem] sm:data-[side=right]:max-w-[44rem]"
      >
        {ticker ? (
          <div className="flex flex-col">
            <Hero
              data={data}
              display={display}
              name={name}
              hint={hint}
              error={error}
              watchBusy={watchBusy}
              onToggleWatch={() => void toggleWatch()}
            />

            {data ? (
              <div className="flex flex-col gap-8 px-5 pb-10 sm:px-7">
                <ForMe data={data} />
                <Actions
                  data={data}
                  name={name}
                  sizerOpen={sizerOpen}
                  onToggleSizer={() => setSizerOpen((v) => !v)}
                  onAsk={(prompt) => {
                    if (onAskArchieSend) onAskArchieSend(prompt)
                    else onAskArchie?.(display, name)
                    onOpenChange(false)
                  }}
                  onDeepAnalysis={
                    onDeepAnalysis
                      ? () => {
                          onDeepAnalysis(display, name)
                          onOpenChange(false)
                        }
                      : undefined
                  }
                />
                {sizerOpen ? <TradeSizer insight={data} onDone={() => setSizerOpen(false)} /> : null}

                {data.chart ? (
                  <InsightChartView
                    chart={data.chart}
                    forecastTicker={data.yfinance_ticker}
                    minorMultiplier={data.chart.unit_multiplier ?? 1}
                  />
                ) : data.pending.includes("chart") ? (
                  <Skeleton className="h-[300px] w-full rounded-xl" />
                ) : null}

                {data.alerts.length ? <AlertsList alerts={data.alerts} /> : null}
                {data.risk ? <RiskSection risk={data.risk} held={data.held} /> : data.pending.includes("risk") ? <SectionSkeleton /> : null}
                {data.events ? <EventsSection events={data.events} /> : null}
                <ThesisSection play={primaryPlay(data.plays)} theses={data.theses} held={data.held} />
                <NewsSection news={data.news} />
                {data.fundamentals ? <FundamentalsSection f={data.fundamentals} currency={data.quote?.currency ?? "USD"} /> : null}
              </div>
            ) : !error ? (
              <div className="flex flex-col gap-4 px-5 sm:px-7">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-9 w-64" />
                <Skeleton className="h-[300px] w-full rounded-xl" />
              </div>
            ) : null}
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}

function primaryPlay(plays: InsightPlay[]): InsightPlay | null {
  return plays.find((p) => p.relation === "owner") ?? plays.find((p) => p.relation === "bench") ?? null
}

// ── 1. What is it doing, and why? ───────────────────────────────────────────

function Hero({
  data,
  display,
  name,
  hint,
  error,
  watchBusy,
  onToggleWatch,
}: {
  data: InstrumentInsight | null
  display: string
  name: string | null
  hint?: InstrumentHint | null
  error: string | null
  watchBusy: boolean
  onToggleWatch: () => void
}) {
  const q = data?.quote
  const up = (q?.change_pct ?? hint?.change_pct ?? 0) >= 0
  const watching = Boolean(data?.watchlist)
  return (
    <header className="flex flex-col gap-3 px-5 pt-6 pb-6 sm:px-7">
      <div className="flex items-start gap-3 pr-10">
        <div className="min-w-0 flex-1">
          <SheetTitle className="truncate text-xl font-semibold tracking-tight">{name || display}</SheetTitle>
          <SheetDescription className="text-sm text-muted-foreground">
            {display}
            {data?.fundamentals?.industry ? ` · ${data.fundamentals.industry}` : ""}
          </SheetDescription>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className={cn("h-8 gap-1.5 text-muted-foreground", watching && "text-foreground")}
          onClick={onToggleWatch}
          disabled={watchBusy || !data}
          aria-pressed={watching}
          title={watching ? "Stop watching" : "Watch for big moves, earnings and news"}
        >
          {watchBusy ? <Loader2 className="size-4 animate-spin" /> : <Star className={cn("size-4", watching && "fill-warning text-warning")} />}
          {watching ? "Watching" : "Watch"}
        </Button>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : q ? (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className={cn("text-4xl font-semibold tracking-tight", num)}>{fmtPrice(q.price, q.currency)}</span>
            {q.change_pct != null ? (
              <span className={cn("text-base font-medium", num, up ? "text-positive" : "text-negative")}>
                {q.change != null ? `${q.change >= 0 ? "+" : "−"}${fmtPrice(Math.abs(q.change), q.currency)} ` : ""}(
                {fmtPct(q.change_pct, 2, { signed: true })})
                <span className="ml-1.5 font-normal text-muted-foreground">today</span>
              </span>
            ) : null}
          </div>
          {q.currency !== "GBP" && q.price_gbp != null ? (
            <span className={cn("text-xs text-muted-foreground", num)}>
              {fmtPrice(q.price_gbp, "GBP")} in pounds · priced in {q.currency}
            </span>
          ) : null}
        </div>
      ) : data && !data.pending.includes("chart") ? (
        <span className="text-sm text-muted-foreground">No live price</span>
      ) : (
        <Skeleton className="h-10 w-48" />
      )}

      {data?.why_moved?.text ? (
        <p className="flex items-start gap-2 text-sm leading-snug">
          <Sparkles className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <span>{data.why_moved.text}.</span>
        </p>
      ) : null}
    </header>
  )
}

// ── 2. What does it mean for me? ────────────────────────────────────────────

function ForMe({ data }: { data: InstrumentInsight }) {
  const blur = privacyBlurClass(usePrivacyMode())
  const pos = data.position
  const book = data.book
  const plays = data.plays
  const owner = primaryPlay(plays)
  const others = plays.filter((p) => p !== owner)
  const cap = book?.position_cap ?? null
  const target = owner?.target_weight_of_book ?? null

  if (!pos) {
    const probe = Math.max(50, Math.round(Math.min(book?.max_order_gbp ?? 500, (book?.total_gbp ?? 0) * 0.01 || 500) / 50) * 50)
    return (
      <div className="flex flex-col gap-2 text-sm">
        <p className={cn("text-[15px] leading-relaxed", num)}>
          <span className="font-medium">Not in your book</span>
          {book?.total_gbp ? (
            <span className="text-muted-foreground">
              {" "}
              · <span className={blur}>{fmtMoney(probe)}</span> would be {fmtPct(probe / book.total_gbp)} of it
            </span>
          ) : null}
          {data.watchlist ? <span className="text-muted-foreground"> · on your watchlist</span> : null}
        </p>
        <PlayLine owner={owner} others={others} />
      </div>
    )
  }

  const pplUp = pos.ppl_gbp >= 0
  return (
    <div className="flex flex-col gap-3">
      <p className={cn("text-[15px] leading-relaxed", num)}>
        <span className={cn("font-semibold", blur)}>{fmtMoney(pos.value_gbp)}</span>
        <span className="text-muted-foreground"> · </span>
        <span className="font-medium">{fmtPct(pos.weight)}</span>
        <span className="text-muted-foreground"> of your money · </span>
        <span className={cn("font-medium", pplUp ? "text-positive" : "text-negative", blur)}>
          {fmtMoney(pos.ppl_gbp, "GBP", { signed: true })}
          {pos.ppl_pct != null ? ` (${fmtPct(pos.ppl_pct, 1, { signed: true })})` : ""}
        </span>
        <span className="text-muted-foreground"> since you bought</span>
      </p>
      <PlayLine owner={owner} others={others} />
      {target != null || cap != null ? (
        <div className="flex flex-col gap-1.5">
          <WeightMeter weight={pos.weight} target={target} cap={cap} />
          <div className={cn("flex flex-wrap gap-x-4 text-xs text-muted-foreground", num)}>
            <span>
              <span className="text-foreground">{fmtPct(pos.weight)}</span> now
            </span>
            {target != null ? (
              <span>
                <i className="mr-1 inline-block h-2.5 w-0.5 translate-y-0.5 rounded bg-foreground/80" />
                {fmtPct(target)} play target
              </span>
            ) : null}
            {cap != null ? (
              <span>
                <i className="mr-1 inline-block h-2.5 w-px translate-y-0.5 bg-negative/70" />
                {fmtPct(cap, 0)} position cap
              </span>
            ) : null}
            {cap != null && pos.weight > cap ? <span className="text-negative">Over your cap</span> : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function PlayLine({ owner, others }: { owner: InsightPlay | null; others: InsightPlay[] }) {
  if (!owner && !others.length) return null
  const role = owner?.role ? `${owner.role[0].toUpperCase()}${owner.role.slice(1)}` : null
  return (
    <p className="text-sm leading-relaxed text-muted-foreground">
      {owner ? (
        <>
          {owner.relation === "bench" ? "On the bench for " : role ? `${role} holding in ` : "Part of "}
          <a href={`#/plays/${owner.id}`} className="font-medium text-foreground underline-offset-2 hover:underline">
            {owner.title}
          </a>
          {owner.status !== "active" ? ` (${owner.status})` : ""}
          {others.length ? " · " : ""}
        </>
      ) : null}
      {others.length ? (
        <>
          Also relevant to{" "}
          {others.map((p, i) => (
            <span key={p.id}>
              {i ? ", " : ""}
              <a href={`#/plays/${p.id}`} className="text-foreground underline-offset-2 hover:underline">
                {p.title}
              </a>
            </span>
          ))}
        </>
      ) : null}
    </p>
  )
}

// ── 9. Actions ──────────────────────────────────────────────────────────────

function contextLine(d: InstrumentInsight): string {
  const bits: string[] = []
  if (d.quote) bits.push(`trading at ${fmtPrice(d.quote.price, d.quote.currency)} (${fmtPct(d.quote.change_pct ?? 0, 1, { signed: true })} today)`)
  if (d.position)
    bits.push(
      `I hold ${fmtMoney(d.position.value_gbp)} (${fmtPct(d.position.weight)} of my book, ${fmtPct(d.position.ppl_pct ?? 0, 1, { signed: true })} vs my cost)`,
    )
  else bits.push("I don't hold it")
  const owner = primaryPlay(d.plays)
  if (owner) bits.push(`it's ${owner.role ? `a ${owner.role} leg` : "part"} of my "${owner.title}" play`)
  return bits.join("; ")
}

function Actions({
  data,
  name,
  sizerOpen,
  onToggleSizer,
  onAsk,
  onDeepAnalysis,
}: {
  data: InstrumentInsight
  name: string | null
  sizerOpen: boolean
  onToggleSizer: () => void
  onAsk: (prompt: string) => void
  onDeepAnalysis?: () => void
}) {
  const who = `${data.display_ticker}${name ? ` (${name})` : ""}`
  const ctx = contextLine(data)
  const chips: { label: string; prompt: string }[] = [
    data.held
      ? { label: "Should I trim?", prompt: `Should I trim ${who}? Context: ${ctx}. Weigh concentration, risk contribution and the play's target, and give a clear answer with a size if yes.` }
      : { label: "Should I buy?", prompt: `Should I buy ${who}? Context: ${ctx}. Consider how it would fit my book (overlap, correlation, sizing) and give a clear answer with a size if yes.` },
    { label: "What's priced in?", prompt: `What's priced in for ${who}? Context: ${ctx}. What does the current valuation assume about growth and margins, and how does that compare with consensus?` },
    { label: "Bull vs bear case", prompt: `Give me the bull and bear case for ${who} in a few lines each. Context: ${ctx}. End with what would decide it.` },
  ]
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => onAsk(`What's your read on ${who}? Context: ${ctx}. Anything I should act on?`)}>
          <MessageSquareText className="size-3.5" />
          Ask Archie
        </Button>
        <Button size="sm" variant={sizerOpen ? "secondary" : "outline"} onClick={onToggleSizer} aria-expanded={sizerOpen}>
          <ArrowLeftRight className="size-3.5" />
          Propose a trade
        </Button>
        {onDeepAnalysis ? (
          <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={onDeepAnalysis} title="Archie runs a full workup and finishes with a verdict">
            <Telescope className="size-3.5" />
            Deep analysis
          </Button>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((c) => (
          <button
            key={c.label}
            onClick={() => onAsk(c.prompt)}
            className="rounded-full border border-border/80 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function AlertsList({ alerts }: { alerts: InstrumentInsight["alerts"] }) {
  return (
    <div className="flex flex-col gap-2">
      {alerts.map((a) => (
        <div
          key={a.id}
          className={cn(
            "rounded-lg border-l-2 bg-muted/20 px-3.5 py-2.5 text-sm",
            a.severity === "critical" ? "border-negative" : a.severity === "warning" ? "border-warning" : "border-border",
          )}
        >
          <p className="font-medium">{a.title}</p>
          {a.detail ? <p className="text-xs text-muted-foreground">{a.detail}</p> : null}
          {a.consider ? <p className="mt-0.5 text-xs">Consider: {a.consider}</p> : null}
        </div>
      ))}
    </div>
  )
}

function SectionSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  )
}
