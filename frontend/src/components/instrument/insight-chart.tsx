import { useEffect, useMemo, useRef, useState } from "react"
import { Check, Loader2, SlidersHorizontal } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { privacyBlurClass, usePrivacyMode } from "@/lib/privacy"
import { fetchForecast, type ForecastData } from "@/api/charts"
import type { InsightChart, InsightTrade } from "@/api/instruments"
import { fmtDate, fmtPct, fmtPrice, num } from "./insight-format"

/**
 * The personal price chart: one clean daily close line in the instrument's own
 * currency, your average cost as a reference line, and your actual buys/sells as
 * markers. Indicators (SMA, candles, Kronos forecast) live behind one menu and
 * are off by default. Hand-built SVG so every mark follows the theme tokens.
 */

export const RANGES = ["1W", "1M", "6M", "1Y", "5Y"] as const
export type Range = (typeof RANGES)[number]

const RANGE_DAYS: Record<Range, number> = { "1W": 7, "1M": 31, "6M": 183, "1Y": 366, "5Y": 365 * 5 + 2 }

type Pt = { d: string; o: number; h: number; l: number; c: number }

function sliceRange(points: InsightChart["points"], range: Range): Pt[] {
  if (!points.length) return []
  const last = new Date(`${points[points.length - 1][0]}T00:00:00`)
  const cutoff = new Date(last.getTime() - RANGE_DAYS[range] * 86400000).toISOString().slice(0, 10)
  const out: Pt[] = []
  for (const [d, o, h, l, c] of points) if (d > cutoff) out.push({ d, o, h, l, c })
  return out
}

/** Weekly bars for dense candle views (a 5Y daily candle is sub-pixel). */
function toWeekly(pts: Pt[]): Pt[] {
  const out: Pt[] = []
  let cur: Pt | null = null
  let wk = ""
  for (const p of pts) {
    const dt = new Date(`${p.d}T00:00:00`)
    const monday = new Date(dt.getTime() - ((dt.getDay() + 6) % 7) * 86400000).toISOString().slice(0, 10)
    if (monday !== wk) {
      if (cur) out.push(cur)
      wk = monday
      cur = { ...p }
    } else if (cur) {
      cur = { d: p.d, o: cur.o, h: Math.max(cur.h, p.h), l: Math.min(cur.l, p.l), c: p.c }
    }
  }
  if (cur) out.push(cur)
  return out
}

function sma(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= n) sum -= values[i - n]
    out.push(i >= n - 1 ? sum / n : null)
  }
  return out
}

function niceTicks(lo: number, hi: number, count = 4): number[] {
  const span = hi - lo || Math.abs(hi) || 1
  const raw = span / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? 10 * mag
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10))
  return out
}

function useWidth<T extends HTMLElement>(): [React.MutableRefObject<T | null>, number] {
  const ref = useRef<T | null>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver((e) => setW(e[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

export type Indicators = { sma20: boolean; sma50: boolean; candles: boolean; forecast: boolean }

export function InsightChartView({
  chart,
  forecastTicker,
  minorMultiplier = 1,
  height = 260,
}: {
  chart: InsightChart
  /** The yfinance ticker the Kronos forecast endpoint understands. */
  forecastTicker: string
  /** Multiplier from the forecast's quoted unit (pence) to the chart's major unit. */
  minorMultiplier?: number
  height?: number
}) {
  const [range, setRange] = useState<Range>("6M")
  const [ind, setInd] = useState<Indicators>({ sma20: false, sma50: false, candles: false, forecast: false })
  const [forecast, setForecast] = useState<ForecastData | null>(null)
  const [forecastState, setForecastState] = useState<"idle" | "loading" | "error">("idle")
  const [hover, setHover] = useState<number | null>(null)
  const [wrapRef, width] = useWidth<HTMLDivElement>()
  const privacy = usePrivacyMode()
  const blur = privacyBlurClass(privacy)
  const ccy = chart.currency

  useEffect(() => {
    if (!ind.forecast || forecast) return
    let cancelled = false
    setForecastState("loading")
    fetchForecast({ ticker: forecastTicker, horizon: 30, samples: 12, lookback: 256 })
      .then((f) => {
        if (!cancelled) {
          setForecast(f)
          setForecastState("idle")
        }
      })
      .catch(() => !cancelled && setForecastState("error"))
    return () => {
      cancelled = true
    }
  }, [ind.forecast, forecast, forecastTicker])

  const allCloses = useMemo(() => chart.points.map((p) => p[4]), [chart.points])
  const sma20All = useMemo(() => sma(allCloses, 20), [allCloses])
  const sma50All = useMemo(() => sma(allCloses, 50), [allCloses])

  const model = useMemo(() => {
    const daily = sliceRange(chart.points, range)
    const offset = chart.points.length - daily.length
    const candleMode = ind.candles
    const pts = candleMode && daily.length > 260 ? toWeekly(daily) : daily
    const showForecast = ind.forecast && forecast && forecast.forecast.length > 0
    const fc = showForecast
      ? forecast!.forecast.map((f) => ({ d: f.date, p10: f.p10 * minorMultiplier, p50: f.p50 * minorMultiplier, p90: f.p90 * minorMultiplier }))
      : []
    const smaOf = (all: (number | null)[]) =>
      pts === daily ? daily.map((_, i) => all[offset + i]) : []
    const s20 = ind.sma20 ? smaOf(sma20All) : []
    const s50 = ind.sma50 ? smaOf(sma50All) : []
    const first = daily[0]
    const trades = chart.trades.filter((t) => first && t.date >= first.d)
    return { pts, daily, fc, s20, s50, trades }
  }, [chart, range, ind, forecast, minorMultiplier, sma20All, sma50All])

  const { pts, fc, s20, s50, trades } = model
  const avg = chart.avg_cost

  // Geometry
  const padL = 8
  const padR = 52
  const padT = 14
  const padB = 22
  const W = Math.max(width, 200)
  const H = height
  const n = pts.length + fc.length
  const x = (i: number) => padL + (n <= 1 ? 0 : (i / (n - 1)) * (W - padL - padR))

  const lows = pts.map((p) => (ind.candles ? p.l : p.c))
  const highs = pts.map((p) => (ind.candles ? p.h : p.c))
  for (const f of fc) {
    lows.push(f.p10)
    highs.push(f.p90)
  }
  for (const t of trades) {
    lows.push(t.price)
    highs.push(t.price)
  }
  let lo = Math.min(...lows)
  let hi = Math.max(...highs)
  const span = hi - lo || hi * 0.02 || 1
  // Show the cost line in-frame when it's reasonably near; otherwise pin a tag.
  let avgPinned: "above" | "below" | null = null
  if (avg != null) {
    if (avg < lo && lo - avg <= span * 0.6) lo = avg
    else if (avg > hi && avg - hi <= span * 0.6) hi = avg
    else if (avg < lo) avgPinned = "below"
    else if (avg > hi) avgPinned = "above"
  }
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad
  hi += pad
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB)
  const ticks = niceTicks(lo, hi, H < 220 ? 3 : 5).filter((t) => t > lo && t < hi)

  const linePath = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.c).toFixed(1)}`).join("")
  const seriesPath = (vals: (number | null)[]) => {
    let d = ""
    let pen = false
    vals.forEach((v, i) => {
      if (v == null) {
        pen = false
        return
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`
      pen = true
    })
    return d
  }
  const lastIdx = pts.length - 1
  const fcBand =
    fc.length > 0
      ? `M${x(lastIdx)},${y(pts[lastIdx].c)}` +
        fc.map((f, i) => `L${x(lastIdx + 1 + i)},${y(f.p90)}`).join("") +
        fc
          .slice()
          .reverse()
          .map((f, i) => `L${x(lastIdx + fc.length - i)},${y(f.p10)}`)
          .join("") +
        "Z"
      : ""
  const fcMid = fc.length > 0 ? `M${x(lastIdx)},${y(pts[lastIdx].c)}` + fc.map((f, i) => `L${x(lastIdx + 1 + i)},${y(f.p50)}`).join("") : ""

  // Date → index for trade markers (nearest bar on or after the trade date).
  const indexOf = (d: string) => {
    let lo2 = 0
    let hi2 = pts.length - 1
    while (lo2 < hi2) {
      const mid = (lo2 + hi2) >> 1
      if (pts[mid].d < d) lo2 = mid + 1
      else hi2 = mid
    }
    return lo2
  }

  const xTicks = useMemo(() => {
    if (pts.length < 2) return [] as number[]
    const k = W < 420 ? 3 : 4
    return Array.from({ length: k }, (_, i) => Math.round((i * (pts.length - 1)) / (k - 1)))
  }, [pts.length, W])

  const first = pts[0]
  const last = pts[lastIdx]
  const rangeChange = first && last ? last.c / first.c - 1 : null
  const hovered = hover != null ? pts[hover] : null
  const hoverTrades = hovered ? trades.filter((t) => indexOf(t.date) === hover) : []

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()
    const px = e.clientX - rect.left
    const i = Math.round(((px - padL) / (W - padL - padR)) * (n - 1))
    setHover(Math.max(0, Math.min(pts.length - 1, i)))
  }

  const activeCount = Object.values(ind).filter(Boolean).length
  const tradeLabel = (t: InsightTrade) =>
    `${t.side === "buy" ? "Bought" : "Sold"} ${t.quantity.toLocaleString("en-GB", { maximumFractionDigits: 4 })} @ ${fmtPrice(t.price, ccy)}`

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div role="tablist" aria-label="Chart range" className="flex items-center rounded-lg bg-muted/50 p-0.5">
          {RANGES.map((r) => (
            <button
              key={r}
              role="tab"
              aria-selected={range === r}
              onClick={() => {
                setRange(r)
                setHover(null)
              }}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                range === r ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {r}
            </button>
          ))}
        </div>
        {rangeChange != null ? (
          <span className={cn("text-xs", num, rangeChange >= 0 ? "text-positive" : "text-negative")}>
            {fmtPct(rangeChange, 1, { signed: true })} <span className="text-muted-foreground">over {range}</span>
          </span>
        ) : null}
        <IndicatorMenu ind={ind} setInd={setInd} activeCount={activeCount} />
      </div>

      <div ref={wrapRef} className="relative w-full select-none" style={{ height: H }}>
        {width > 0 && pts.length > 1 ? (
          <svg width={W} height={H} className="block overflow-visible" role="img" aria-label={`Price chart, ${range}`}>
            {/* Recessive grid + right-hand axis */}
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} opacity={0.6} />
                <text x={W - padR + 8} y={y(t) + 3.5} className={cn("fill-muted-foreground text-[10px]", num, blur)}>
                  {fmtPrice(t, ccy).replace(/\.00$/, "")}
                </text>
              </g>
            ))}
            {xTicks.map((i) => (
              <text
                key={i}
                x={x(i)}
                y={H - 6}
                textAnchor={i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"}
                className="fill-muted-foreground text-[10px]"
              >
                {fmtDate(pts[i].d, range === "5Y" || range === "1Y")}
              </text>
            ))}

            {/* Forecast cone */}
            {fcBand ? (
              <g>
                <path d={fcBand} fill="var(--chart-3)" opacity={0.16} />
                <path d={fcMid} fill="none" stroke="var(--chart-3)" strokeWidth={1.5} strokeDasharray="4 3" />
              </g>
            ) : null}

            {/* Indicators */}
            {s20.length ? <path d={seriesPath(s20)} fill="none" stroke="var(--chart-5)" strokeWidth={1.25} opacity={0.9} /> : null}
            {s50.length ? <path d={seriesPath(s50)} fill="none" stroke="var(--chart-8)" strokeWidth={1.25} opacity={0.9} /> : null}

            {/* Price */}
            {ind.candles ? (
              <g>
                {pts.map((p, i) => {
                  const bw = Math.max(1, Math.min(8, ((W - padL - padR) / n) * 0.6))
                  const up = p.c >= p.o
                  const color = up ? "var(--positive)" : "var(--negative)"
                  return (
                    <g key={p.d}>
                      <line x1={x(i)} x2={x(i)} y1={y(p.h)} y2={y(p.l)} stroke={color} strokeWidth={1} />
                      <rect
                        x={x(i) - bw / 2}
                        y={y(Math.max(p.o, p.c))}
                        width={bw}
                        height={Math.max(1, Math.abs(y(p.o) - y(p.c)))}
                        fill={color}
                      />
                    </g>
                  )
                })}
              </g>
            ) : (
              <path d={linePath} fill="none" stroke="var(--chart-2)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            )}

            {/* Your average cost */}
            {avg != null && !avgPinned ? (
              <g>
                <line x1={padL} x2={W - padR} y1={y(avg)} y2={y(avg)} stroke="var(--muted-foreground)" strokeWidth={1} strokeDasharray="3 3" />
                <text
                  x={padL + 2}
                  y={y(avg) - 5}
                  stroke="var(--popover)"
                  strokeWidth={4}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                  className={cn("fill-muted-foreground text-[10px] font-medium", num, blur)}
                >
                  Your avg {fmtPrice(avg, ccy)}
                </text>
              </g>
            ) : null}

            {/* Your trades */}
            {trades.map((t, k) => {
              const i = indexOf(t.date)
              const cx = x(i)
              const cy = y(t.price)
              const buy = t.side === "buy"
              const s = 5
              const d = buy
                ? `M${cx},${cy - s}L${cx + s},${cy + s * 0.8}L${cx - s},${cy + s * 0.8}Z`
                : `M${cx},${cy + s}L${cx + s},${cy - s * 0.8}L${cx - s},${cy - s * 0.8}Z`
              return (
                <path
                  key={`${t.date}-${k}`}
                  d={d}
                  fill={buy ? "var(--positive)" : "var(--negative)"}
                  stroke="var(--popover)"
                  strokeWidth={2}
                  paintOrder="stroke"
                >
                  <title>{`${fmtDate(t.date, true)} · ${tradeLabel(t)}`}</title>
                </path>
              )
            })}

            {/* Crosshair */}
            {hovered ? (
              <g pointerEvents="none">
                <line x1={x(hover!)} x2={x(hover!)} y1={padT} y2={H - padB} stroke="var(--muted-foreground)" strokeWidth={1} opacity={0.5} />
                <circle cx={x(hover!)} cy={y(hovered.c)} r={4} fill="var(--chart-2)" stroke="var(--popover)" strokeWidth={2} />
              </g>
            ) : null}

            <rect
              x={padL}
              y={0}
              width={Math.max(0, W - padL - padR)}
              height={H}
              fill="transparent"
              onPointerMove={onMove}
              onPointerDown={onMove}
              onPointerLeave={() => setHover(null)}
            />
          </svg>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">No price history</div>
        )}

        {avgPinned && avg != null ? (
          <span
            className={cn(
              "absolute left-2 rounded bg-muted/70 px-1.5 py-0.5 text-[10px] text-muted-foreground",
              num,
              avgPinned === "above" ? "top-1" : "bottom-6",
            )}
          >
            <span className={blur}>Your avg {fmtPrice(avg, ccy)}</span> {avgPinned === "above" ? "↑" : "↓"} off chart
          </span>
        ) : null}

        {hovered ? (
          <div
            className="pointer-events-none absolute top-1 z-10 min-w-36 rounded-lg border border-border/70 bg-popover/95 px-2.5 py-1.5 text-xs shadow-md backdrop-blur"
            style={x(hover!) > W / 2 ? { left: Math.max(4, x(hover!) - 172) } : { left: x(hover!) + 12 }}
          >
            <div className={cn("text-sm font-semibold", num, blur)}>{fmtPrice(hovered.c, ccy)}</div>
            <div className="text-muted-foreground">{fmtDate(hovered.d, true)}</div>
            {first ? (
              <div className={cn(num, hovered.c >= first.c ? "text-positive" : "text-negative")}>
                {fmtPct(hovered.c / first.c - 1, 1, { signed: true })} <span className="text-muted-foreground">since {fmtDate(first.d)}</span>
              </div>
            ) : null}
            {avg != null ? (
              <div className={cn("text-muted-foreground", num)}>{fmtPct(hovered.c / avg - 1, 1, { signed: true })} vs your avg</div>
            ) : null}
            {hoverTrades.map((t, k) => (
              <div key={k} className={cn("mt-0.5 font-medium", t.side === "buy" ? "text-positive" : "text-negative", blur)}>
                {tradeLabel(t)}
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {avg != null ? (
          <span className="inline-flex items-center gap-1.5">
            <i className="inline-block w-3.5 border-t border-dashed border-muted-foreground" /> Your average cost
          </span>
        ) : null}
        {trades.some((t) => t.side === "buy") ? (
          <span className="inline-flex items-center gap-1.5">
            <svg width="10" height="10" aria-hidden><path d="M5,0L10,9L0,9Z" fill="var(--positive)" /></svg> Your buys
          </span>
        ) : null}
        {trades.some((t) => t.side === "sell") ? (
          <span className="inline-flex items-center gap-1.5">
            <svg width="10" height="10" aria-hidden><path d="M5,10L10,1L0,1Z" fill="var(--negative)" /></svg> Your sells
          </span>
        ) : null}
        {ind.sma20 ? <Key color="var(--chart-5)">20-day avg</Key> : null}
        {ind.sma50 ? <Key color="var(--chart-8)">50-day avg</Key> : null}
        {ind.forecast ? (
          forecastState === "loading" ? (
            <span className="inline-flex items-center gap-1.5"><Loader2 className="size-3 animate-spin" /> Forecasting…</span>
          ) : forecastState === "error" ? (
            <span className="text-negative">Forecast unavailable</span>
          ) : forecast ? (
            <span className="inline-flex items-center gap-1.5">
              <Key color="var(--chart-3)" dashed>
                Kronos median, 10–90% band
              </Key>
              <span className={num}>
                · 30d {fmtPct(forecast.summary.expected_return_pct / 100, 1, { signed: true })} · P(up){" "}
                {Math.round(forecast.summary.prob_up * 100)}%
              </span>
            </span>
          ) : null
        ) : null}
        <span className="ml-auto inline-flex items-center gap-1">
          <Check className="size-3" /> Daily closes in {ccy}
        </span>
      </div>
    </div>
  )
}

/** Inline popover (not a portal): portalled menus stack under the modal sheet. */
function IndicatorMenu({
  ind,
  setInd,
  activeCount,
}: {
  ind: Indicators
  setInd: React.Dispatch<React.SetStateAction<Indicators>>
  activeCount: number
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener("pointerdown", onDown)
    document.addEventListener("keydown", onKey, true)
    return () => {
      document.removeEventListener("pointerdown", onDown)
      document.removeEventListener("keydown", onKey, true)
    }
  }, [open])
  const rows: { key: keyof Indicators; label: string; group: string }[] = [
    { key: "sma20", label: "20-day average", group: "Overlays" },
    { key: "sma50", label: "50-day average", group: "Overlays" },
    { key: "candles", label: "Candles", group: "Overlays" },
    { key: "forecast", label: "Kronos 30-day forecast", group: "Model" },
  ]
  return (
    <div ref={ref} className="relative ml-auto">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <SlidersHorizontal className="size-3.5" />
        Indicators{activeCount ? ` · ${activeCount}` : ""}
      </Button>
      {open ? (
        <div role="menu" className="absolute top-8 right-0 z-30 w-56 rounded-lg bg-popover p-1 text-sm shadow-md ring-1 ring-foreground/10">
          {rows.map((r, i) => (
            <div key={r.key}>
              {i === 0 || rows[i - 1].group !== r.group ? (
                <div className={cn("px-2 pt-1.5 pb-1 text-xs text-muted-foreground", i > 0 && "mt-1 border-t border-border/60")}>{r.group}</div>
              ) : null}
              <button
                role="menuitemcheckbox"
                aria-checked={ind[r.key]}
                onClick={() => setInd((s) => ({ ...s, [r.key]: !s[r.key] }))}
                className="flex w-full items-center rounded-md px-2 py-1.5 text-left hover:bg-accent"
              >
                {r.label}
                {ind[r.key] ? <Check className="ml-auto size-4" /> : null}
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function Key({ color, dashed, children }: { color: string; dashed?: boolean; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i className={cn("inline-block w-3.5 border-t-2", dashed && "border-dashed")} style={{ borderColor: color }} />
      {children}
    </span>
  )
}
