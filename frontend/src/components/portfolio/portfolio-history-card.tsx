import { useEffect, useMemo, useState } from "react"
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { getPortfolioHistory, type PortfolioHistory } from "@/api/client"
import { SectionCard } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { usePrivacyMode } from "@/lib/privacy"

type Mode = "value" | "return"

const RANGES: { label: string; days: number }[] = [
  { label: "1M", days: 31 },
  { label: "3M", days: 93 },
  { label: "6M", days: 186 },
  { label: "1Y", days: 365 },
  { label: "All", days: 1825 },
]

function fmtMoney(v: number, currency: "GBP" | "USD"): string {
  const sym = currency === "USD" ? "$" : "£"
  const sign = v < 0 ? "-" : ""
  const a = Math.abs(v)
  if (a >= 1000) return `${sign}${sym}${(a / 1000).toFixed(a >= 100000 ? 0 : 1)}k`
  return `${sign}${sym}${a.toFixed(0)}`
}

function fmtSignedMoney(v: number, currency: "GBP" | "USD"): string {
  return `${v >= 0 ? "+" : ""}${fmtMoney(v, currency)}`
}

function HistoryTooltip({ active, payload, currency, mode, redact }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  const val = mode === "value" ? p.total : p.gain
  const label = mode === "value" ? "Value" : "Gain"
  return (
    <div className="rounded-lg border border-border/60 bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-medium">{p.date}</div>
      <div className="mt-0.5 text-muted-foreground">
        {label}:{" "}
        <span className={cn("font-semibold text-foreground", redact && "blur-[5px] select-none")}>
          {mode === "value" ? fmtMoney(val, currency) : fmtSignedMoney(val, currency)}
        </span>
      </div>
    </div>
  )
}

export function PortfolioHistoryCard({
  accountView,
  displayCurrency,
}: {
  accountView: "all" | "invest" | "stocks_isa"
  displayCurrency: "GBP" | "USD"
}) {
  const [data, setData] = useState<PortfolioHistory | null>(null)
  const [days, setDays] = useState(365)
  const [mode, setMode] = useState<Mode>("value")
  const privacy = usePrivacyMode()
  // Both modes hide the £ axis values; only full-privacy "blur" obscures the curve
  // itself. Scramble keeps a real-looking line (shape leaks no absolute amount).
  const hideValues = privacy !== "off"
  const blurChart = privacy === "blur"

  useEffect(() => {
    let active = true
    getPortfolioHistory(accountView, displayCurrency, days)
      .then((d) => active && setData(d))
      .catch(() => active && setData(null))
    return () => {
      active = false
    }
  }, [accountView, displayCurrency, days])

  const points = data?.points ?? []
  const rangeLabel = RANGES.find((r) => r.days === days)?.label ?? "period"

  const valueChange = useMemo(() => {
    if (points.length < 2) return 0
    return points[points.length - 1].total - points[0].total
  }, [points])

  // value mode coloured by the value delta; return mode by the (contribution-
  // adjusted) Dietz return.
  const positive = mode === "value" ? valueChange >= 0 : (data?.return_pct ?? 0) >= 0
  const stroke = positive ? "var(--positive, #10b981)" : "var(--destructive, #ef4444)"
  const dataKey = mode === "value" ? "total" : "gain"
  const finalGain = points.length ? points[points.length - 1].gain : 0

  return (
    <SectionCard
      title="Portfolio value"
      description={
        points.length >= 2
          ? mode === "value"
            ? "Total equity over time"
            : "Return — value change net of deposits & withdrawals"
          : "Builds up as snapshots are recorded"
      }
      action={
        <div className="flex gap-0.5">
          {RANGES.map((r) => (
            <Button
              key={r.label}
              variant="ghost"
              size="sm"
              onClick={() => setDays(r.days)}
              className={cn("h-7 px-2 text-xs", days === r.days ? "bg-muted text-foreground" : "text-muted-foreground")}
            >
              {r.label}
            </Button>
          ))}
        </div>
      }
    >
      {points.length < 2 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          Not enough history yet — the equity curve fills in as the dashboard records daily snapshots.
        </p>
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between gap-3">
            {/* Value ⇄ Return toggle */}
            <div className="inline-flex rounded-md border border-border/60 p-0.5">
              {(["value", "return"] as Mode[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={cn(
                    "h-6 rounded px-2.5 text-xs font-medium capitalize transition-colors",
                    mode === m ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {m}
                </button>
              ))}
            </div>

            {/* Headline metric */}
            <div className="flex items-baseline gap-2 text-right">
              {mode === "value" ? (
                <>
                  <span className={cn("text-sm font-medium", positive ? "text-positive" : "text-destructive", hideValues && "blur-[5px] select-none")}>
                    {fmtSignedMoney(valueChange, displayCurrency)}
                  </span>
                  <span className="text-xs text-muted-foreground">over {rangeLabel}</span>
                </>
              ) : (
                <>
                  <span className={cn("text-sm font-semibold", positive ? "text-positive" : "text-destructive")}>
                    {(data?.return_pct ?? 0) >= 0 ? "+" : ""}
                    {(((data?.return_pct ?? 0)) * 100).toFixed(1)}%
                  </span>
                  <span className={cn("text-xs text-muted-foreground", hideValues && "blur-[5px] select-none")}>
                    {fmtSignedMoney(finalGain, displayCurrency)} · {rangeLabel}
                  </span>
                </>
              )}
            </div>
          </div>

          <div className={cn(blurChart && "blur-[6px] select-none")} aria-hidden={blurChart}>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={points} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={stroke} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={stroke} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={40}
                />
                <YAxis
                  width={48}
                  tick={hideValues ? false : { fontSize: 10, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => fmtMoney(v, displayCurrency)}
                  domain={mode === "return" ? ([(min: number) => Math.min(0, min), "auto"] as any) : ["auto", "auto"]}
                />
                {mode === "return" ? <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" /> : null}
                <Tooltip
                  cursor={{ stroke: "var(--border)" }}
                  content={<HistoryTooltip currency={displayCurrency} mode={mode} redact={hideValues} />}
                />
                <Area type="monotone" dataKey={dataKey} stroke={stroke} strokeWidth={2} fill="url(#equityFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </SectionCard>
  )
}
