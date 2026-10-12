import { useMemo } from "react"
import dayjs from "dayjs"
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import type { PortfolioHistory, PortfolioHistoryPoint } from "@/api/client"
import { Money } from "@/components/kit"
import { Skeleton } from "@/components/ui/skeleton"
import { scrambleMoney, usePrivacyMode } from "@/lib/privacy"
import { cn } from "@/lib/utils"

export const RANGES: { label: string; days: number }[] = [
  { label: "1M", days: 31 },
  { label: "3M", days: 93 },
  { label: "6M", days: 186 },
  { label: "1Y", days: 365 },
  { label: "All", days: 8000 },
]

function ChartTooltip({ active, payload, currency }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as PortfolioHistoryPoint
  return (
    <div className="rounded-lg border border-border bg-popover px-2.5 py-1.5 text-xs shadow-sm">
      <div className="text-muted-foreground">
        {p.date}
        {p.source === "reconstructed" ? " · estimated" : ""}
      </div>
      <Money value={p.total} currency={currency} className="font-medium text-foreground" />
    </div>
  )
}

/**
 * Compact equity curve for the Home hero. Axis-light: no Y axis, a sparse X
 * axis, the reconstructed (pre-recording) span faded.
 */
export function EquityChart({
  history,
  currency,
  loading,
}: {
  history: PortfolioHistory | null
  currency: "GBP" | "USD"
  loading: boolean
}) {
  const privacy = usePrivacyMode()
  const points = history?.points ?? []
  const data = useMemo(
    () => (privacy === "scramble" ? points.map((p) => ({ ...p, total: scrambleMoney(p.total) })) : points),
    [points, privacy],
  )

  if (loading && points.length === 0) return <Skeleton className="h-40 w-full rounded-lg" />
  if (points.length < 2) {
    return (
      <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground">
        The equity curve fills in as daily snapshots are recorded.
      </div>
    )
  }

  const up = points[points.length - 1].total >= points[0].total
  const spanDays = dayjs(points[points.length - 1].date).diff(points[0].date, "day")
  const stroke = up ? "var(--positive)" : "var(--negative)"
  const firstRecordedIdx = points.findIndex((p) => p.source === "recorded")
  const hasReconstructed = firstRecordedIdx > 0 && points.some((p) => p.source === "reconstructed")
  const seamFrac = hasReconstructed ? firstRecordedIdx / (points.length - 1) : 0

  return (
    <div className={cn("h-40 w-full", privacy === "blur" && "select-none blur-[6px]")} aria-hidden={privacy === "blur"}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="homeEquityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.18} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
            <linearGradient id="homeEquityStroke" x1="0" y1="0" x2="1" y2="0">
              <stop offset={0} stopColor={stroke} stopOpacity={0.35} />
              <stop offset={seamFrac} stopColor={stroke} stopOpacity={0.35} />
              <stop offset={seamFrac} stopColor={stroke} stopOpacity={1} />
              <stop offset={1} stopColor={stroke} stopOpacity={1} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
            tickLine={false}
            axisLine={false}
            minTickGap={64}
            tickMargin={6}
            tickFormatter={(d: string) => dayjs(d).format(spanDays > 400 ? "MMM YYYY" : spanDays > 60 ? "MMM D" : "D MMM")}
          />
          <YAxis hide domain={["auto", "auto"]} />
          {hasReconstructed ? (
            <ReferenceLine x={points[firstRecordedIdx].date} stroke="var(--border)" strokeDasharray="3 3" />
          ) : null}
          <Tooltip cursor={{ stroke: "var(--border)" }} content={<ChartTooltip currency={currency} />} />
          <Area
            type="monotone"
            dataKey="total"
            stroke={hasReconstructed ? "url(#homeEquityStroke)" : stroke}
            strokeWidth={1.75}
            fill="url(#homeEquityFill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
