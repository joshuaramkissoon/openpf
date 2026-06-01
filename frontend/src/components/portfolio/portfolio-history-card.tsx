import { useEffect, useMemo, useState } from "react"
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { getPortfolioHistory, type PortfolioHistory, type PortfolioHistoryPoint } from "@/api/client"
import { Money, MoneyDelta, PctDelta, SectionCard } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import { usePrivacyMode, scrambleMoney } from "@/lib/privacy"
import { formatCompactMoney } from "@/utils/format"

type Mode = "value" | "return"

const RANGES: { label: string; days: number }[] = [
  { label: "1M", days: 31 },
  { label: "3M", days: 93 },
  { label: "6M", days: 186 },
  { label: "1Y", days: 365 },
  { label: "All", days: 1825 },
]

function HistoryTooltip({ active, payload, currency, mode }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as PortfolioHistoryPoint
  return (
    <div className="rounded-lg border border-border/60 bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-medium">{p.date}</div>
      <div className="mt-0.5 flex items-center gap-1 text-muted-foreground">
        {mode === "value" ? "Value:" : "Gain:"}
        {mode === "value" ? (
          <Money value={p.total} currency={currency} className="text-foreground" />
        ) : (
          <MoneyDelta value={p.gain} currency={currency} />
        )}
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
  // Scramble scales the figures (kit components then render them plainly, matching
  // the rest of the dashboard); blur obscures the curve itself + the kit figures
  // blur themselves via privacy context. So we only special-case the SVG here.
  const scramble = privacy === "scramble"
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

  // In scramble mode, scale the plotted series so the axis/curve look real without
  // leaking absolute amounts (same deterministic factor the stat cards use).
  const chartData = useMemo(
    () => (scramble ? points.map((p) => ({ ...p, total: scrambleMoney(p.total), gain: scrambleMoney(p.gain) })) : points),
    [points, scramble],
  )

  const valueChange = points.length >= 2 ? points[points.length - 1].total - points[0].total : 0
  const finalGain = points.length ? points[points.length - 1].gain : 0
  const returnPct = data?.return_pct ?? 0

  const positive = mode === "value" ? valueChange >= 0 : returnPct >= 0
  const stroke = positive ? "var(--positive, #10b981)" : "var(--destructive, #ef4444)"
  const dataKey = mode === "value" ? "total" : "gain"

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
            <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
              <TabsList className="h-7">
                <TabsTrigger value="value" className="px-2.5 text-xs">
                  Value
                </TabsTrigger>
                <TabsTrigger value="return" className="px-2.5 text-xs">
                  Return
                </TabsTrigger>
              </TabsList>
            </Tabs>

            <div className="flex items-baseline gap-2 text-right text-sm">
              {mode === "value" ? (
                <>
                  <MoneyDelta value={scramble ? scrambleMoney(valueChange) : valueChange} currency={displayCurrency} />
                  <span className="text-xs text-muted-foreground">over {rangeLabel}</span>
                </>
              ) : (
                <>
                  <PctDelta value={returnPct} className="font-semibold" />
                  <span className="flex items-baseline gap-1 text-xs text-muted-foreground">
                    <MoneyDelta
                      value={scramble ? scrambleMoney(finalGain) : finalGain}
                      currency={displayCurrency}
                      className="text-xs"
                    />
                    · {rangeLabel}
                  </span>
                </>
              )}
            </div>
          </div>

          <div className={cn(blurChart && "blur-[6px] select-none")} aria-hidden={blurChart}>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={chartData} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
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
                  tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => formatCompactMoney(v, displayCurrency)}
                  domain={mode === "return" ? ([(min: number) => Math.min(0, min), "auto"] as any) : ["auto", "auto"]}
                />
                {mode === "return" ? <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" /> : null}
                <Tooltip
                  cursor={{ stroke: "var(--border)" }}
                  content={<HistoryTooltip currency={displayCurrency} mode={mode} />}
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
