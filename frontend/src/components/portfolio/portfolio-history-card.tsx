import { useEffect, useMemo, useState } from "react"
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { getPortfolioHistory, type PortfolioHistory } from "@/api/client"
import { SectionCard } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { usePrivacyMode } from "@/lib/privacy"

const RANGES: { label: string; days: number }[] = [
  { label: "1M", days: 31 },
  { label: "3M", days: 93 },
  { label: "6M", days: 186 },
  { label: "1Y", days: 365 },
  { label: "All", days: 1825 },
]

function fmtMoney(v: number, currency: "GBP" | "USD"): string {
  const sym = currency === "USD" ? "$" : "£"
  if (Math.abs(v) >= 1000) return `${sym}${(v / 1000).toFixed(v >= 100000 ? 0 : 1)}k`
  return `${sym}${v.toFixed(0)}`
}

function HistoryTooltip({ active, payload, currency, redact }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="rounded-lg border border-border/60 bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-medium">{p.date}</div>
      <div className="mt-0.5 text-muted-foreground">
        Total: <span className={cn("font-semibold text-foreground", redact && "blur-[5px] select-none")}>{fmtMoney(p.total, currency)}</span>
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
  const { change, changePct } = useMemo(() => {
    if (points.length < 2) return { change: 0, changePct: 0 }
    const first = points[0].total
    const last = points[points.length - 1].total
    return { change: last - first, changePct: first ? (last - first) / first : 0 }
  }, [points])

  const up = change >= 0
  const stroke = up ? "var(--positive, #10b981)" : "var(--destructive, #ef4444)"

  return (
    <SectionCard
      title="Portfolio value"
      description={points.length >= 2 ? "Total equity over time" : "Builds up as snapshots are recorded"}
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
          <div className="mb-2 flex items-baseline gap-2">
            <span className={cn("text-sm font-medium", up ? "text-positive" : "text-destructive")}>
              {up ? "+" : ""}{(changePct * 100).toFixed(1)}%
            </span>
            <span className="text-xs text-muted-foreground">over {RANGES.find((r) => r.days === days)?.label ?? "period"}</span>
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
                  domain={["auto", "auto"]}
                />
                <Tooltip cursor={{ stroke: "var(--border)" }} content={<HistoryTooltip currency={displayCurrency} redact={hideValues} />} />
                <Area type="monotone" dataKey="total" stroke={stroke} strokeWidth={2} fill="url(#equityFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </SectionCard>
  )
}
