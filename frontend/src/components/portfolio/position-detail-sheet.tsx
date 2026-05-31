import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { Money, MoneyDelta, Pct, RiskBadge } from "@/components/kit"
import { StockChart } from "@/components/StockChart"
import { accountTag } from "@/utils/format"
import type { PositionItem } from "@/types"

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border/50 bg-muted/20 px-3 py-2.5">
      <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="whitespace-nowrap font-mono text-sm font-semibold tabular-nums">{children}</span>
    </div>
  )
}

const RISK_NOTE: Record<string, string> = {
  ok: "No elevated technical risk flags.",
  oversold: "RSI in oversold territory — momentum is weak, but a mean-reversion bounce is possible.",
  overbought: "RSI in overbought territory — extended; watch for a pullback.",
  warning: "Elevated risk — review momentum and concentration.",
  critical: "High risk — large drawdown or heavy concentration. Review position sizing.",
}

function fmtSignal(v: number | null | undefined, kind: "pct" | "num" | "rsi"): string {
  if (v == null || !Number.isFinite(v)) return "—"
  if (kind === "pct") return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`
  if (kind === "rsi") return v.toFixed(0)
  return v.toFixed(2)
}

export function PositionDetailSheet({
  position,
  currency,
  open,
  onOpenChange,
}: {
  position: PositionItem | null
  currency: "GBP" | "USD"
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [forecast, setForecast] = useState(false)

  // Reset the forecast toggle whenever a different position is opened.
  useEffect(() => {
    setForecast(false)
  }, [position?.ticker])

  const riskKey = (position?.risk_flag || "ok").toLowerCase()

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-y-auto p-0 data-[side=right]:w-full sm:max-w-4xl sm:data-[side=right]:w-3/4"
      >
        {position ? (
          <>
            <SheetHeader className="border-b border-border/60 px-6 py-5">
              <div className="flex items-center gap-2.5">
                <SheetTitle className="text-2xl font-semibold tracking-tight">{position.ticker}</SheetTitle>
                <Badge variant="outline" className="text-muted-foreground">{accountTag(position.account_kind)}</Badge>
                <RiskBadge flag={position.risk_flag} className="ml-auto" />
              </div>
              <SheetDescription className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
                {position.name ? <span className="font-medium text-foreground/90">{position.name}</span> : null}
                <span className="font-mono text-muted-foreground">{position.instrument_code}</span>
                {position.yfinance_ticker && position.yfinance_ticker !== position.ticker ? (
                  <span className="font-mono text-muted-foreground/70">· {position.yfinance_ticker}</span>
                ) : null}
                {position.instrument_currency ? (
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                    {position.instrument_currency}
                  </span>
                ) : null}
              </SheetDescription>
            </SheetHeader>

            <div className="flex flex-col gap-6 px-6 py-6">
              <section className="flex flex-col gap-2.5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Financials</h3>
                <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
                  <Stat label="Value"><Money value={position.value} currency={currency} /></Stat>
                  <Stat label="P/L"><MoneyDelta value={position.ppl} currency={currency} /></Stat>
                  <Stat label="Weight"><Pct value={position.weight} /></Stat>
                  <Stat label="Quantity">{position.quantity.toFixed(2)}</Stat>
                  <Stat label="Avg Price"><Money value={position.average_price} currency={currency} /></Stat>
                  <Stat label="Last Price"><Money value={position.current_price} currency={currency} /></Stat>
                </div>
              </section>

              <section className="flex flex-col gap-2.5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Signals &amp; risk</h3>
                <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-3">
                  <Stat label="3M Momentum">{fmtSignal(position.momentum_63d, "pct")}</Stat>
                  <Stat label="RSI (14)">{fmtSignal(position.rsi_14, "rsi")}</Stat>
                  <Stat label="Trend">{fmtSignal(position.trend_score, "num")}</Stat>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium capitalize text-foreground">{riskKey}</span>
                  {" — "}
                  {RISK_NOTE[riskKey] ?? RISK_NOTE.ok}
                </p>
              </section>

              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/25 px-4 py-3">
                <div className="min-w-0">
                  <Label htmlFor="forecast-toggle" className="text-sm font-medium">Kronos forecast</Label>
                  <p className="text-xs text-muted-foreground">Probabilistic 30-day price cone (p10–p50–p90)</p>
                </div>
                <Switch id="forecast-toggle" checked={forecast} onCheckedChange={setForecast} />
              </div>

              <div className="rounded-xl border border-border/60 bg-card/40 p-4">
                <StockChart
                  key={position.yfinance_ticker || position.instrument_code || position.ticker}
                  ticker={position.yfinance_ticker || position.instrument_code || position.ticker}
                  period="6mo"
                  interval="1d"
                  chartType="candlestick"
                  indicators={["sma20", "sma50"]}
                  height={340}
                  forecast={forecast}
                  forecastHorizon={30}
                  forecastSamples={12}
                  forecastLookback={180}
                />
              </div>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
