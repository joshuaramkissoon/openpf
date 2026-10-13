import { useCallback, useEffect, useMemo, useState } from "react"
import dayjs from "dayjs"
import { Info, RefreshCw } from "lucide-react"

import { MoneyDelta, Section, StatCard } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toastApiError } from "@/lib/api-error"
import { cn } from "@/lib/utils"
import { formatSignedPercent } from "@/utils/format"

import { getTrackRecord, refreshTrackRecord, type Horizon, type OutcomeItem, type Pnl, type TrackRecord } from "./api"

const FATE_LABEL: Record<OutcomeItem["fate"], string> = {
  taken: "Taken",
  rejected: "Rejected",
  expired: "Expired",
  superseded: "Superseded",
  failed: "Failed",
  open: "Open",
}

/** Percentage points, signed: 0.012 → "+1.2 pts". */
function pts(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—"
  const s = (v * 100).toFixed(digits)
  return `${v > 0 ? "+" : ""}${s} pts`
}

function useTrackRecord() {
  const [data, setData] = useState<TrackRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const load = useCallback(async () => {
    try {
      setData(await getTrackRecord())
    } catch (err) {
      toastApiError(err, "Couldn't load the track record")
    } finally {
      setLoading(false)
    }
  }, [])
  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      setData(await refreshTrackRecord())
    } catch (err) {
      toastApiError(err, "Couldn't refresh the track record")
    } finally {
      setRefreshing(false)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  return { data, loading, refresh, refreshing }
}

/** `/activity/track-record` — how Archie's proposals actually did. */
export function TrackRecordTab() {
  const { data, loading, refresh, refreshing } = useTrackRecord()

  if (loading) {
    return (
      <div className="max-w-4xl space-y-4">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    )
  }
  if (!data) return null

  const h = data.headline
  const bench = data.benchmark
  const nothing = data.counts.proposals === 0

  return (
    <div className="max-w-4xl space-y-10">
      <div className="flex items-start justify-between gap-4">
        <p className="max-w-2xl text-sm text-muted-foreground">
          Every proposal is tracked forward from its price when proposed — taken, rejected or expired — and
          compared with {bench} over the same window.
        </p>
        <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={refreshing}>
          <RefreshCw className={cn("size-3.5", refreshing && "animate-spin")} />
          Mark now
        </Button>
      </div>

      {nothing ? (
        <p className="rounded-xl border border-dashed border-border px-5 py-8 text-sm text-muted-foreground">
          Nothing to track yet. Each proposal Archie makes is marked at 1 day, 1 week, 1 month and 3 months.
        </p>
      ) : (
        <>
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <StatCard
                label={`Beat ${bench}`}
                value={h.n_vs_benchmark ? `${h.hits} of ${h.n_vs_benchmark}` : "—"}
                hint={`at ${h.horizon_label}`}
              />
              <StatCard
                label={`Average vs ${bench}`}
                value={
                  <span className={cn(h.avg_excess == null ? "" : h.avg_excess >= 0 ? "text-positive" : "text-negative")}>
                    {pts(h.avg_excess)}
                  </span>
                }
                hint={`per proposal, at ${h.horizon_label}`}
              />
              <StatCard
                label="Tracked"
                value={data.counts.proposals}
                hint={`${data.counts.taken} taken · ${data.counts.passed} passed on · ${data.counts.open} open`}
              />
            </div>
            <Caveat n={h.n_vs_benchmark} text={h.caveat} />
          </div>

          <Section
            title="Following Archie vs what you did"
            description={`£ move on each proposal's size at its latest mark. Passed-on proposals are what you'd have made had you taken them.`}
          >
            <Comparison data={data} />
          </Section>

          <Section title="By category" description={`Hit rate and average outcome vs ${bench}, at ${h.horizon_label}.`}>
            <CategoryTable data={data} />
          </Section>

          <Section title="Every proposal" description="Directional return since proposed at 1d · 1w · 1m · 3m. A sell scores the drop it avoided.">
            <OutcomeList items={data.items} bench={bench} />
          </Section>

          <p className="flex gap-2 text-xs text-muted-foreground">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            {data.method} Past calls don't predict the next one.
          </p>
        </>
      )}
    </div>
  )
}

function Caveat({ n, text }: { n: number; text: string }) {
  const early = n < 10
  return (
    <p className={cn("text-xs", early ? "text-foreground" : "text-muted-foreground")}>
      {early ? <span className="mr-1.5 rounded bg-muted px-1.5 py-0.5 font-medium">Small sample</span> : null}
      {text}
    </p>
  )
}

// ── Following Archie vs what you did ────────────────────────────────────────

function Comparison({ data }: { data: TrackRecord }) {
  const rows: { key: string; label: string; hint: string; p: Pnl }[] = [
    { key: "all", label: "Following Archie", hint: "if you'd taken every proposal", p: data.comparison.following_archie },
    { key: "you", label: "What you did", hint: "the proposals you took", p: data.comparison.what_you_did },
    { key: "passed", label: "What you passed on", hint: "rejected or let expire", p: data.comparison.passed_on },
  ]
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.p.gain_gbp)))
  return (
    <div className="divide-y divide-border/70 rounded-xl border border-border">
      {rows.map((r) => (
        <div key={r.key} className="grid grid-cols-[minmax(0,15rem)_1fr_auto] items-center gap-4 px-4 py-3 max-sm:grid-cols-[1fr_auto]">
          <div className="min-w-0">
            <p className="text-sm font-medium">{r.label}</p>
            <p className="truncate text-xs text-muted-foreground">
              {r.hint} · n={r.p.n}
            </p>
          </div>
          <DivergingBar value={r.p.gain_gbp} max={max} className="max-sm:hidden" title={`${r.label}: £${r.p.gain_gbp.toFixed(0)} on £${r.p.notional_gbp.toFixed(0)}`} />
          <div className="text-right">
            {r.p.n ? <MoneyDelta value={r.p.gain_gbp} decimals={0} className="text-sm" /> : <span className="text-sm text-muted-foreground">—</span>}
            <p className="text-xs text-muted-foreground">
              {r.p.n ? (
                <>
                  <MoneyDelta value={r.p.vs_benchmark_gbp} decimals={0} className="text-xs text-muted-foreground" /> vs {data.benchmark}
                </>
              ) : (
                "nothing marked"
              )}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

/** One value on a zero-centred track: gains grow right, losses grow left. */
function DivergingBar({ value, max, className, title }: { value: number; max: number; className?: string; title: string }) {
  const frac = Math.min(1, Math.abs(value) / max) * 50
  return (
    <div className={cn("relative h-5", className)} title={title} role="img" aria-label={title}>
      <div className="absolute inset-y-[9px] left-0 right-0 rounded-full bg-muted" />
      <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
      {value !== 0 ? (
        <div
          className={cn("absolute inset-y-[6px] rounded", value > 0 ? "bg-positive" : "bg-negative")}
          style={value > 0 ? { left: "50%", width: `${frac}%` } : { right: "50%", width: `${frac}%` }}
        />
      ) : null}
    </div>
  )
}

// ── per category ────────────────────────────────────────────────────────────

function CategoryTable({ data }: { data: TrackRecord }) {
  if (!data.categories.length) return <p className="text-sm text-muted-foreground">Nothing resolved yet.</p>
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2 font-medium">Category</th>
            <th className="px-3 py-2 text-right font-medium">Proposals</th>
            <th className="px-3 py-2 font-medium">Beat {data.benchmark}</th>
            <th className="px-3 py-2 text-right font-medium">Avg vs {data.benchmark}</th>
            <th className="px-4 py-2 font-medium">Sample</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {data.categories.map((c) => (
            <tr key={c.category}>
              <td className="px-4 py-2.5 font-medium">{c.label}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular-nums text-muted-foreground">
                {c.proposals}
                <span className="text-xs"> · {c.taken} taken</span>
              </td>
              <td className="px-3 py-2.5">
                {c.n_vs_benchmark ? (
                  <div className="flex items-center gap-2" title={`${c.hits} of ${c.n_vs_benchmark} beat ${data.benchmark}`}>
                    <HitMeter hits={c.hits} n={c.n_vs_benchmark} />
                    <span className="font-mono text-xs tabular-nums">
                      {c.hits}/{c.n_vs_benchmark}
                    </span>
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">not marked yet</span>
                )}
              </td>
              <td className={cn("px-3 py-2.5 text-right font-mono tabular-nums", c.avg_excess == null ? "text-muted-foreground" : c.avg_excess >= 0 ? "text-positive" : "text-negative")}>
                {pts(c.avg_excess)}
              </td>
              <td className="px-4 py-2.5 text-xs text-muted-foreground">{c.n_vs_benchmark < 10 ? "too early to tell" : c.n_vs_benchmark < 30 ? "early read" : "meaningful"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HitMeter({ hits, n }: { hits: number; n: number }) {
  return (
    <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-muted">
      <div className="absolute inset-y-0 left-0 rounded-full bg-foreground/70" style={{ width: `${(hits / Math.max(1, n)) * 100}%` }} />
    </div>
  )
}

// ── list with sparklines ────────────────────────────────────────────────────

const SPARK_KEYS: Horizon[] = ["1d", "1w", "1m", "3m"]

function OutcomeList({ items, bench }: { items: OutcomeItem[]; bench: string }) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? items : items.slice(0, 25)
  // One y-scale for every sparkline so shapes are comparable down the list.
  const scale = useMemo(() => {
    const vals = items.flatMap((i) => SPARK_KEYS.map((k) => i.marks[k]?.dir_ret).filter((v): v is number => v != null))
    return Math.max(0.02, ...vals.map(Math.abs))
  }, [items])
  return (
    <div>
      <ul className="divide-y divide-border/60">
        {shown.map((i) => (
          <OutcomeRow key={i.intent_id} item={i} scale={scale} bench={bench} />
        ))}
      </ul>
      {items.length > shown.length ? (
        <button type="button" onClick={() => setShowAll(true)} className="pt-3 text-xs text-muted-foreground hover:text-foreground">
          Show all {items.length} →
        </button>
      ) : null}
    </div>
  )
}

function OutcomeRow({ item, scale, bench }: { item: OutcomeItem; scale: number; bench: string }) {
  const auto = (item.approved_by ?? "").startsWith("permission:")
  const latest = item.latest
  return (
    <li className="grid grid-cols-[4.5rem_2.25rem_4.5rem_minmax(0,1fr)_7rem_5.5rem] items-center gap-3 py-2 text-sm max-md:grid-cols-[4.5rem_2.25rem_4.5rem_minmax(0,1fr)_5.5rem]">
      <span className="font-mono text-xs tabular-nums text-muted-foreground">{dayjs(item.proposed_at).format("MMM D")}</span>
      <span className={cn("text-xs font-semibold uppercase", item.side === "buy" ? "text-positive" : "text-negative")}>{item.side}</span>
      <span className="truncate font-medium">{item.symbol}</span>
      <span className="min-w-0 truncate text-xs text-muted-foreground">
        {item.category_label} · {FATE_LABEL[item.fate]}
        {auto ? <span className="ml-1.5 rounded bg-muted px-1 py-0.5 text-[10px] font-medium text-foreground">auto · {item.permission_label ?? "permission"}</span> : null}
      </span>
      <span className="max-md:hidden">
        {item.state === "unpriced" ? (
          <span className="text-xs text-muted-foreground" title={item.note ?? undefined}>no price data</span>
        ) : (
          <Sparkline item={item} scale={scale} />
        )}
      </span>
      <span
        className={cn("text-right font-mono text-xs tabular-nums", latest?.excess == null ? "text-muted-foreground" : latest.excess >= 0 ? "text-positive" : "text-negative")}
        title={latest ? `${formatSignedPercent(latest.dir_ret)} at ${latest.horizon}, ${pts(latest.excess)} vs ${bench}` : "not marked yet"}
      >
        {latest ? pts(latest.excess) : item.fate === "open" ? "open" : "pending"}
      </span>
    </li>
  )
}

/**
 * Directional return at proposal (0) → 1d → 1w → 1m → 3m, on a shared scale.
 * A dashed zero line; the last mark is dotted in the sign of its excess vs the
 * benchmark. Each point carries a hover title.
 */
function Sparkline({ item, scale }: { item: OutcomeItem; scale: number }) {
  const W = 104
  const H = 24
  const pad = 3
  const step = (W - pad * 2) / SPARK_KEYS.length
  const y = (v: number) => H / 2 - (v / scale) * (H / 2 - pad)
  const pts_: { x: number; y: number; k: string; v: number; excess?: number }[] = [{ x: pad, y: y(0), k: "proposed", v: 0 }]
  SPARK_KEYS.forEach((k, idx) => {
    const m = item.marks[k]
    if (m) pts_.push({ x: pad + step * (idx + 1), y: y(m.dir_ret), k, v: m.dir_ret, excess: m.excess })
  })
  const last = pts_[pts_.length - 1]
  const d = pts_.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="overflow-visible text-muted-foreground" role="img" aria-label={`${item.symbol} outcome sparkline`}>
      <line x1={pad} x2={W - pad} y1={y(0)} y2={y(0)} stroke="currentColor" strokeOpacity={0.35} strokeDasharray="2 2" strokeWidth={1} />
      {pts_.length > 1 ? <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" /> : null}
      {pts_.length > 1 ? (
        <circle
          cx={last.x}
          cy={last.y}
          r={2.75}
          className={cn((last.excess ?? last.v) >= 0 ? "fill-positive" : "fill-negative")}
          stroke="var(--background)"
          strokeWidth={1.5}
        />
      ) : null}
      {pts_.slice(1).map((p) => (
        <circle key={p.k} cx={p.x} cy={p.y} r={7} fill="transparent" className="cursor-default">
          <title>
            {`${p.k}: ${formatSignedPercent(p.v)}${p.excess != null ? ` (${pts(p.excess)} vs benchmark)` : ""}`}
          </title>
        </circle>
      ))}
    </svg>
  )
}
