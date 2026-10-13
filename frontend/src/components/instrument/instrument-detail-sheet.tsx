import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowLeftRight, ChevronDown, Loader2, MessageSquareText, Sparkles, Star, Telescope } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { AssetIcon } from "@/components/kit"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { privacyBlurClass, usePrivacyMode } from "@/lib/privacy"
import { toastApiError } from "@/lib/api-error"
import { getInstrumentInsight, type InstrumentInsight, type InsightPlay } from "@/api/instruments"
import { createWatchlistItem, deleteWatchlistItem } from "@/api/client"
import { InsightChartView } from "./insight-chart"
import { CapMeter, EventsSection, FundamentalsSection, NewsSection, RiskSection, ThesisSection } from "./insight-sections"
import { TradeSizer } from "./trade-sizer"
import { SmartMoneySection } from "@/features/smart-money"
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

  const ask = useCallback(
    (prompt: string) => {
      if (onAskArchieSend) onAskArchieSend(prompt)
      else onAskArchie?.(display, name)
      onOpenChange(false)
    },
    [onAskArchieSend, onAskArchie, onOpenChange, display, name],
  )

  // Sheet shortcuts: A asks Archie, T opens the trade panel. Bare keys only, never
  // while typing or with a menu/dialog open (the shell owns P, / and ⌘K).
  useEffect(() => {
    if (!open || !data) return
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return
      const el = document.activeElement as HTMLElement | null
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return
      if (document.querySelector('[role="menu"], [role="alertdialog"]')) return
      const k = e.key.toLowerCase()
      if (k === "a") {
        e.preventDefault()
        ask(readPrompt(data!, name))
      } else if (k === "t") {
        e.preventDefault()
        setSizerOpen(true)
        window.setTimeout(() => {
          const input = document.getElementById("sizer-amount") as HTMLInputElement | null
          input?.scrollIntoView({ block: "center", behavior: "smooth" })
          input?.focus()
          input?.select()
        }, 60)
      }
    }
    // Capture phase: the sheet's focus trap stops bubbling key events from reaching window.
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [open, data, name, ask])

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
              <div className="flex flex-col gap-6 px-5 pb-10 sm:px-7">
                <ForMe data={data} />
                <Actions
                  data={data}
                  name={name}
                  sizerOpen={sizerOpen}
                  onToggleSizer={() => setSizerOpen((v) => !v)}
                  onAsk={ask}
                  onDeepAnalysis={
                    onDeepAnalysis
                      ? () => {
                          onDeepAnalysis(display, name)
                          onOpenChange(false)
                        }
                      : undefined
                  }
                />
                {sizerOpen ? (
                  <TradeSizer
                    insight={data}
                    onDone={(placed) => {
                      setSizerOpen(false)
                      // A placed trade changes the position, weight and cap meter: refresh them.
                      if (placed && ticker) {
                        window.dispatchEvent(new Event("openpf:portfolio-changed"))
                        // Once now, and again after the portfolio refresh has landed.
                        for (const ms of [0, 3500]) {
                          window.setTimeout(() => {
                            reqRef.current++
                            void load(ticker, MAX_POLLS)
                          }, ms)
                        }
                      }
                    }}
                  />
                ) : null}

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
                <SmartMoneySection ticker={data.display_ticker} />
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
    <header className="flex flex-col gap-2.5 px-5 pt-6 pb-5 sm:px-7">
      <div className="flex items-start gap-3 pr-10">
        <AssetIcon symbol={display} crypto={Boolean(data?.position?.accounts?.includes("crypto") || data?.instrument_code?.includes("/"))} size={44} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <SheetTitle className="truncate text-xl font-semibold tracking-tight">{name || display}</SheetTitle>
          <SheetDescription className="truncate text-sm text-muted-foreground">
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
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className={cn("text-[2rem] leading-none font-semibold tracking-tight", num)}>{fmtPrice(q.price, q.currency)}</span>
          {q.change_pct != null ? (
            <span className={cn("text-sm font-medium", num, up ? "text-positive" : "text-negative")}>
              {fmtPct(q.change_pct, 2, { signed: true })}
              {q.change != null ? <span className="font-normal opacity-80"> ({`${q.change >= 0 ? "+" : "−"}${fmtPrice(Math.abs(q.change), q.currency)}`})</span> : null}
              <span className="ml-1 font-normal text-muted-foreground">today</span>
            </span>
          ) : null}
          {q.currency !== "GBP" && q.price_gbp != null ? (
            // The pounds price is secondary: a quiet figure with the currency story in a tooltip.
            <Tooltip>
              <TooltipTrigger
                className={cn("cursor-help text-sm text-muted-foreground underline decoration-dotted decoration-muted-foreground/40 underline-offset-4", num)}
                aria-label={`${fmtPrice(q.price_gbp, "GBP")} in pounds; priced in ${q.currency}`}
              >
                ≈ {fmtPrice(q.price_gbp, "GBP")}
              </TooltipTrigger>
              <TooltipContent>Priced in {q.currency}. In pounds at today’s rate.</TooltipContent>
            </Tooltip>
          ) : null}
        </div>
      ) : data && !data.pending.includes("chart") ? (
        <span className="text-sm text-muted-foreground">No live price</span>
      ) : (
        <Skeleton className="h-9 w-48" />
      )}

      {data?.why_moved?.text ? (
        <p className="flex items-start gap-2 text-sm leading-snug text-muted-foreground">
          <Sparkles className="mt-0.5 size-3.5 shrink-0" />
          <span className="text-foreground/90">{data.why_moved.text}.</span>
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
      <div className="flex flex-col gap-1 text-sm">
        <p className={cn("leading-relaxed", num)}>
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
  // One line for the money (weight lives on the meter), one for the play, then the meter.
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <p className={cn("text-[15px] leading-relaxed", num)}>
          <span className={cn("font-semibold", blur)}>{fmtMoney(pos.value_gbp)}</span>
          <span className="text-muted-foreground"> held · </span>
          <span className={cn("font-medium", pplUp ? "text-positive" : "text-negative", blur)}>
            {fmtMoney(pos.ppl_gbp, "GBP", { signed: true })}
            {pos.ppl_pct != null ? ` (${fmtPct(pos.ppl_pct, 1, { signed: true })})` : ""}
          </span>
          <span className="text-muted-foreground"> since you bought</span>
        </p>
        <PlayLine owner={owner} others={others} />
      </div>
      <CapMeter weight={pos.weight} cap={cap} target={target} />
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

function whoOf(data: InstrumentInsight, name: string | null) {
  return `${data.display_ticker}${name ? ` (${name})` : ""}`
}

/** The general question Ask Archie (and its A shortcut) sends. */
function readPrompt(data: InstrumentInsight, name: string | null) {
  return `What's your read on ${whoOf(data, name)}? Context: ${contextLine(data)}. Anything I should act on?`
}

/** A key hint inside a button; hidden on touch screens, where there's no keyboard. */
function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="ml-0.5 hidden rounded border border-current/25 px-1 font-mono text-[10px] leading-4 font-normal opacity-70 [@media(hover:hover)]:inline">
      {children}
    </kbd>
  )
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
  const who = whoOf(data, name)
  const ctx = contextLine(data)
  const prompts: { label: string; prompt: string }[] = [
    data.held
      ? { label: "Should I trim?", prompt: `Should I trim ${who}? Context: ${ctx}. Weigh concentration, risk contribution and the play's target, and give a clear answer with a size if yes.` }
      : { label: "Should I buy?", prompt: `Should I buy ${who}? Context: ${ctx}. Consider how it would fit my book (overlap, correlation, sizing) and give a clear answer with a size if yes.` },
    { label: "What's priced in?", prompt: `What's priced in for ${who}? Context: ${ctx}. What does the current valuation assume about growth and margins, and how does that compare with consensus?` },
    { label: "Bull vs bear case", prompt: `Give me the bull and bear case for ${who} in a few lines each. Context: ${ctx}. End with what would decide it.` },
  ]
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex">
        <Button size="sm" className="rounded-r-none" onClick={() => onAsk(readPrompt(data, name))} title="Ask Archie (A)">
          <MessageSquareText className="size-3.5" />
          Ask Archie
          <Kbd>A</Kbd>
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            className="inline-flex h-7 items-center rounded-r-[min(var(--radius-md),12px)] border-l border-primary-foreground/25 bg-primary px-1.5 text-primary-foreground hover:bg-primary/90 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            aria-label="More questions for Archie"
          >
            <ChevronDown className="size-3.5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-52">
            {prompts.map((c) => (
              <DropdownMenuItem key={c.label} onClick={() => onAsk(c.prompt)}>
                {c.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <Button size="sm" variant={sizerOpen ? "secondary" : "outline"} onClick={onToggleSizer} aria-expanded={sizerOpen} title="Trade (T)">
        <ArrowLeftRight className="size-3.5" />
        Trade
        <Kbd>T</Kbd>
      </Button>
      {onDeepAnalysis ? (
        <Button
          size="sm"
          variant="ghost"
          className="text-muted-foreground"
          onClick={onDeepAnalysis}
          title="Archie runs a full workup and finishes with a verdict"
          aria-label="Deep analysis"
        >
          <Telescope className="size-3.5" />
          <span className="hidden min-[400px]:inline">Deep analysis</span>
        </Button>
      ) : null}
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
