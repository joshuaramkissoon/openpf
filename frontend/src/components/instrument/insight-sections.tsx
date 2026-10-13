import { useState } from "react"
import { ChevronDown, ExternalLink } from "lucide-react"

import { cn } from "@/lib/utils"
import { navigate } from "@/lib/router"
import type {
  InsightEvents,
  InsightFundamentals,
  InsightNews,
  InsightPlay,
  InsightRisk,
  InsightThesis,
} from "@/api/instruments"
import { fmtBig, fmtDate, fmtPct, num, relDays, timeAgo } from "./insight-format"

export function Section({
  title,
  children,
  aside,
  className,
}: {
  title: string
  children: React.ReactNode
  aside?: React.ReactNode
  className?: string
}) {
  return (
    <section className={cn("flex flex-col gap-3", className)}>
      <div className="flex items-baseline gap-2">
        <h3 className="text-[13px] font-semibold tracking-tight text-foreground">{title}</h3>
        {aside ? <div className="ml-auto text-xs text-muted-foreground">{aside}</div> : null}
      </div>
      {children}
    </section>
  )
}

/** Bullet meter: a track to the cap, a fill for the current weight, a tick for
 *  the target, and an optional ghost for the post-trade weight. */
export function WeightMeter({
  weight,
  target,
  cap,
  next,
}: {
  weight: number
  target?: number | null
  cap?: number | null
  next?: number | null
}) {
  const max = Math.max(cap ?? 0, weight, next ?? 0, target ?? 0) * 1.08 || 0.1
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`
  const over = cap != null && Math.max(weight, next ?? 0) > cap
  return (
    <div className="relative h-2 w-full rounded-full bg-muted" aria-hidden>
      {next != null && next > weight ? (
        <div className="absolute inset-y-0 left-0 rounded-full bg-chart-2/35" style={{ width: pct(next) }} />
      ) : null}
      <div
        className={cn("absolute inset-y-0 left-0 rounded-full", over ? "bg-negative" : "bg-chart-2")}
        style={{ width: pct(next != null && next < weight ? next : weight) }}
      />
      {next != null && next < weight ? (
        <div className="absolute inset-y-0 rounded-r-full bg-chart-2/35" style={{ left: pct(next), width: `calc(${pct(weight)} - ${pct(next)})` }} />
      ) : null}
      {target != null ? <div className="absolute -inset-y-1 w-0.5 rounded bg-foreground/80" style={{ left: pct(target) }} /> : null}
      {cap != null ? <div className="absolute -inset-y-1 w-px bg-negative/70" style={{ left: pct(cap) }} /> : null}
    </div>
  )
}

// ── Should I worry? ─────────────────────────────────────────────────────────

function volWords(r: InsightRisk): string {
  const daily = r.typical_daily_move * 100
  const rel = r.vol_vs_market
  let tail = ""
  if (rel != null) {
    if (rel >= 1.25) tail = ` — about ${rel.toFixed(1)}× as jumpy as the S&P 500`
    else if (rel <= 0.8) tail = " — calmer than the S&P 500"
    else tail = " — about as jumpy as the S&P 500"
  }
  return `Moves about ±${daily.toFixed(1)}% on a typical day${tail}.`
}

export function RiskSection({ risk, held }: { risk: InsightRisk; held: boolean }) {
  const value = risk.value_share
  const share = risk.risk_share
  const heavy = value != null && share != null && share > value * 1.25
  return (
    <Section title="Should I worry?">
      <div className="flex flex-col gap-3 text-sm leading-relaxed">
        {held && value != null && share != null ? (
          <div className="flex flex-col gap-2">
            <p>
              <span className={cn("font-medium", num)}>{fmtPct(value)}</span> of your money but{" "}
              <span className={cn("font-medium", num, heavy && "text-warning")}>{fmtPct(share)}</span> of your portfolio's risk
              {heavy ? " — it pulls more than its weight." : share < value * 0.8 ? " — it steadies the book a little." : "."}
            </p>
            <div className="grid grid-cols-[4.5rem_1fr_3rem] items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
              <span>Value</span>
              <div className="h-1.5 rounded-full bg-muted">
                <div className="h-full rounded-full bg-muted-foreground/60" style={{ width: `${Math.min(100, (value / Math.max(value, share)) * 100)}%` }} />
              </div>
              <span className={cn("text-right", num)}>{fmtPct(value)}</span>
              <span>Risk</span>
              <div className="h-1.5 rounded-full bg-muted">
                <div className={cn("h-full rounded-full", heavy ? "bg-warning" : "bg-chart-2")} style={{ width: `${Math.min(100, (share / Math.max(value, share)) * 100)}%` }} />
              </div>
              <span className={cn("text-right", num)}>{fmtPct(share)}</span>
            </div>
          </div>
        ) : null}
        {risk.moves_with.length ? (
          <p>
            <span className="text-muted-foreground">{held ? "Moves with" : "Would move with your"} </span>
            {risk.moves_with.map((m, i) => (
              <span key={m.ticker}>
                {i > 0 ? (i === risk.moves_with.length - 1 ? " and " : ", ") : ""}
                <span className="font-medium">{m.label}</span>
                <span className={cn("text-xs text-muted-foreground", num)}> {m.corr.toFixed(2)}</span>
              </span>
            ))}
            <span className="text-muted-foreground">
              {risk.moves_with[0].corr >= 0.6 ? " — closely, so they tend to fall together." : "."}
            </span>
          </p>
        ) : null}
        <p>
          {volWords(risk)}
          {risk.max_drawdown_1y != null && risk.max_drawdown_1y < -0.05 ? (
            <span className="text-muted-foreground">
              {" "}
              Its worst fall in the past year was <span className={num}>{fmtPct(-risk.max_drawdown_1y, 0)}</span> from a peak.
            </span>
          ) : null}
        </p>
      </div>
    </Section>
  )
}

// ── What's coming? ──────────────────────────────────────────────────────────

export function EventsSection({ events }: { events: InsightEvents }) {
  const rows: { label: string; date: string; days: number; note?: string }[] = []
  if (events.next_earnings) rows.push({ label: "Earnings", date: events.next_earnings.date, days: events.next_earnings.days_away })
  if (events.ex_dividend)
    rows.push({
      label: "Ex-dividend",
      date: events.ex_dividend.date,
      days: events.ex_dividend.days_away,
      note: events.ex_dividend.amount ? `${events.ex_dividend.amount} per share` : undefined,
    })
  if (!rows.length) return null
  return (
    <Section title="Coming up">
      <div className="flex flex-col divide-y divide-border/60">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline gap-3 py-2 text-sm first:pt-0 last:pb-0">
            <span className="w-24 shrink-0 text-muted-foreground">{r.label}</span>
            <span className={cn("font-medium", num)}>{fmtDate(r.date, true)}</span>
            <span className={cn("text-xs", r.days <= 7 ? "text-warning" : "text-muted-foreground")}>{relDays(r.days)}</span>
            {r.note ? <span className={cn("ml-auto text-xs text-muted-foreground", num)}>{r.note}</span> : null}
          </div>
        ))}
      </div>
    </Section>
  )
}

// ── Why do I own it? ────────────────────────────────────────────────────────

const STATUS: Record<string, { label: string; dot: string; text: string }> = {
  fine: { label: "Fine", dot: "bg-positive", text: "text-positive" },
  watch: { label: "Watch", dot: "bg-warning", text: "text-warning" },
  triggered: { label: "Triggered", dot: "bg-negative", text: "text-negative" },
  judgement: { label: "Judgement", dot: "bg-muted-foreground", text: "text-muted-foreground" },
  unknown: { label: "Not checked", dot: "bg-muted-foreground/50", text: "text-muted-foreground" },
}

const VERDICT: Record<string, string> = { on_track: "On track", watch: "Watch", broken: "Broken" }

export function ThesisSection({ play, theses, held }: { play: InsightPlay | null; theses: InsightThesis[]; held: boolean }) {
  if (!play && !theses.length) return null
  return (
    <Section
      title={held ? "Why you own it" : "Why it's on your radar"}
      aside={
        play?.verdict ? (
          <span className={cn(play.verdict === "broken" ? "text-negative" : play.verdict === "watch" ? "text-warning" : "text-positive")}>
            {VERDICT[play.verdict] ?? play.verdict}
          </span>
        ) : null
      }
    >
      {play ? (
        <div className="flex flex-col gap-3 text-sm leading-relaxed">
          {play.rationale ? <p>{play.rationale}</p> : null}
          {play.thesis ? (
            <p className="text-muted-foreground">
              <button className="font-medium text-foreground underline-offset-2 hover:underline" onClick={() => navigate(`/plays/${play.id}`)}>
                {play.title}
              </button>
              : {firstSentences(play.thesis, 2)}
            </p>
          ) : null}
          {play.rules.length ? (
            <div className="flex flex-col gap-1.5">
              <span className="text-xs text-muted-foreground">Exit rules</span>
              <ul className="flex flex-col gap-1.5">
                {play.rules.map((r, i) => {
                  const s = STATUS[r.status ?? "unknown"] ?? STATUS.unknown
                  return (
                    <li key={i} className="flex items-start gap-2.5">
                      <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", s.dot)} />
                      <span className="min-w-0 flex-1">
                        {r.text}
                        {r.detail && r.status !== "judgement" ? (
                          <span className={cn("block text-xs text-muted-foreground", num)}>{r.detail}</span>
                        ) : null}
                      </span>
                      <span className={cn("shrink-0 text-xs font-medium", s.text)}>{s.label}</span>
                    </li>
                  )
                })}
              </ul>
            </div>
          ) : null}
          {play.what_would_change_my_mind ? (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground/80">What would change my mind:</span> {play.what_would_change_my_mind}
            </p>
          ) : null}
        </div>
      ) : null}
      {theses.map((t) => (
        <div key={t.id} className="flex flex-col gap-1 text-sm leading-relaxed">
          <p>
            <span className="font-medium">{t.title || "Thesis"}</span>
            <span className={cn("text-xs text-muted-foreground", num)}> · {Math.round(t.confidence * 100)}% confidence</span>
          </p>
          {t.thesis ? <p className="text-muted-foreground">{firstSentences(t.thesis, 2)}</p> : null}
          {t.invalidation ? (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground/80">Wrong if:</span> {t.invalidation}
            </p>
          ) : null}
        </div>
      ))}
    </Section>
  )
}

function firstSentences(text: string, n: number): string {
  const clean = text.replace(/[#*_>`]/g, "").replace(/\s+/g, " ").trim()
  const parts = clean.match(/[^.!?]+[.!?]+/g)
  if (!parts) return clean
  return parts.slice(0, n).join(" ").trim()
}

// ── News ────────────────────────────────────────────────────────────────────

export function NewsSection({ news }: { news: InsightNews[] }) {
  const [all, setAll] = useState(false)
  if (!news.length) return null
  const shown = all ? news : news.slice(0, 3)
  return (
    <Section
      title="News"
      aside={
        news.length > 3 ? (
          <button className="hover:text-foreground" onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `Show ${news.length - 3} more`}
          </button>
        ) : null
      }
    >
      <ul className="flex flex-col gap-3">
        {shown.map((n, i) => (
          <li key={i}>
            <a
              href={n.url || undefined}
              target="_blank"
              rel="noreferrer noopener"
              className="group flex flex-col gap-0.5 text-sm leading-snug"
            >
              <span className="group-hover:underline group-hover:underline-offset-2">
                {n.headline}
                {n.url ? <ExternalLink className="ml-1 inline size-3 text-muted-foreground opacity-0 group-hover:opacity-100" /> : null}
              </span>
              <span className="text-xs text-muted-foreground">
                {n.source}
                {n.published_at ? ` · ${timeAgo(n.published_at)}` : ""}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </Section>
  )
}

// ── Fundamentals ────────────────────────────────────────────────────────────

export function FundamentalsSection({ f, currency }: { f: InsightFundamentals; currency: string }) {
  const [open, setOpen] = useState(false)
  const pe = f.pe_history
  const summary = [
    pe ? `P/E ${pe.now.toFixed(0)} vs ${pe.median.toFixed(0)} typical` : f.pe_trailing ? `P/E ${f.pe_trailing.toFixed(0)}` : null,
    f.revenue_growth != null ? `revenue ${fmtPct(f.revenue_growth, 0, { signed: true })}` : null,
    f.operating_margin != null ? `${fmtPct(f.operating_margin, 0)} op. margin` : null,
  ]
    .filter(Boolean)
    .join(" · ")
  return (
    <section className="flex flex-col gap-3">
      <button className="flex items-baseline gap-2 text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <h3 className="text-[13px] font-semibold tracking-tight">Fundamentals</h3>
        {!open ? <span className={cn("min-w-0 truncate text-xs text-muted-foreground", num)}>{summary}</span> : null}
        <ChevronDown className={cn("ml-auto size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div className="flex flex-col gap-4 text-sm">
          {pe ? (
            <div className="flex flex-col gap-2">
              <p>
                Valuation: <span className={cn("font-medium", num)}>{pe.now.toFixed(1)}×</span> earnings, against{" "}
                <span className={num}>
                  {pe.min.toFixed(0)}–{pe.max.toFixed(0)}×
                </span>{" "}
                since {fmtDate(pe.since, true).replace(/^\d+ /, "")} (typically <span className={num}>{pe.median.toFixed(0)}×</span>).{" "}
                <span className="text-muted-foreground">{peWords(pe)}</span>
              </p>
              <RangeStrip min={pe.min} max={pe.max} median={pe.median} now={pe.now} />
              <span className="text-[11px] text-muted-foreground">Trailing P/E, {pe.basis}.</span>
            </div>
          ) : null}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
            <Fact label="Forward P/E" value={f.pe_forward != null ? `${f.pe_forward.toFixed(1)}×` : null} />
            <Fact label="Revenue growth" value={f.revenue_growth != null ? fmtPct(f.revenue_growth, 0, { signed: true }) : null} />
            <Fact label="Earnings growth" value={f.earnings_growth != null ? fmtPct(f.earnings_growth, 0, { signed: true }) : null} />
            <Fact label="Gross margin" value={f.gross_margin != null ? fmtPct(f.gross_margin, 0) : null} />
            <Fact label="Operating margin" value={f.operating_margin != null ? fmtPct(f.operating_margin, 0) : null} />
            <Fact label="Net margin" value={f.profit_margin != null ? fmtPct(f.profit_margin, 0) : null} />
            <Fact label="Market cap" value={f.market_cap != null ? fmtBig(f.market_cap, currency) : null} />
            <Fact label="Dividend yield" value={f.dividend_yield ? `${(f.dividend_yield > 1 ? f.dividend_yield : f.dividend_yield * 100).toFixed(2)}%` : null} />
            <Fact label="Sector" value={f.industry || f.sector} text />
          </dl>
        </div>
      ) : null}
    </section>
  )
}

function peWords(pe: NonNullable<InsightFundamentals["pe_history"]>): string {
  const pos = (pe.now - pe.min) / Math.max(1e-9, pe.max - pe.min)
  if (pos <= 0.2) return "Near the cheap end of its own range."
  if (pos >= 0.8) return "Near the expensive end of its own range."
  return pe.now < pe.median ? "Below its usual multiple." : "Above its usual multiple."
}

function RangeStrip({ min, max, median: med, now }: { min: number; max: number; median: number; now: number }) {
  const lo = Math.min(min, now)
  const hi = Math.max(max, now)
  const p = (v: number) => `${((v - lo) / Math.max(1e-9, hi - lo)) * 100}%`
  return (
    <div className="relative mx-1 h-5" aria-hidden>
      <div className="absolute top-2 h-1 rounded-full bg-foreground/15" style={{ left: p(min), width: `calc(${p(max)} - ${p(min)})` }} />
      <div className="absolute top-1 h-3 w-px bg-muted-foreground/70" style={{ left: p(med) }} />
      <div className="absolute top-[3px] size-2.5 -translate-x-1/2 rounded-full border-2 border-popover bg-chart-2" style={{ left: p(now) }} />
    </div>
  )
}

function Fact({ label, value, text }: { label: string; value: string | null | undefined; text?: boolean }) {
  if (!value) return null
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("truncate font-medium", !text && num)}>{value}</dd>
    </div>
  )
}
