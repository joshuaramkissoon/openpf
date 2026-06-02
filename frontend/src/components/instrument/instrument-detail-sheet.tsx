import { useCallback, useEffect, useState } from "react"
import dayjs from "dayjs"
import { Loader2, MessageSquareText, Star } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Money, MoneyDelta, Pct, PctDelta, RiskBadge } from "@/components/kit"
import { StockChart } from "@/components/StockChart"
import { accountTag } from "@/utils/format"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { toastApiError } from "@/lib/api-error"
import { getInstrumentDetail, type InstrumentDetail } from "@/api/instruments"
import { createWatchlistItem, deleteWatchlistItem } from "@/api/client"
import { getOrderHistory, type OrderItem } from "@/api/orders"

/** A best-effort instant identity to paint the header before the fetch lands. */
export type InstrumentHint = {
  name?: string | null
  currency?: string | null
  price?: number | null
  change_pct?: number | null
}

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

const SEV_DOT: Record<string, string> = {
  critical: "bg-negative",
  warning: "bg-warning",
  info: "bg-muted-foreground",
}

function fmtSignal(v: number | null | undefined, kind: "pct" | "num" | "rsi"): string {
  if (v == null || !Number.isFinite(v)) return "—"
  if (kind === "pct") return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`
  if (kind === "rsi") return v.toFixed(0)
  return v.toFixed(2)
}

export function InstrumentDetailSheet({
  ticker,
  currency,
  open,
  onOpenChange,
  hint,
  onAskArchie,
  onWatchlistChanged,
}: {
  ticker: string | null
  currency: "GBP" | "USD"
  open: boolean
  onOpenChange: (open: boolean) => void
  hint?: InstrumentHint | null
  onAskArchie?: (ticker: string, name?: string | null) => void
  onWatchlistChanged?: () => void
}) {
  const [detail, setDetail] = useState<InstrumentDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [forecast, setForecast] = useState(false)
  const [watchBusy, setWatchBusy] = useState(false)
  const [orders, setOrders] = useState<OrderItem[] | null>(null)
  const [ordersLoading, setOrdersLoading] = useState(false)

  const loadDetail = useCallback(async () => {
    if (!ticker) return
    setLoading(true)
    try {
      const data = await getInstrumentDetail(ticker, currency)
      setDetail(data)
    } catch {
      setDetail(null)
    } finally {
      setLoading(false)
    }
  }, [ticker, currency])

  // Fetch on open / ticker change; reset the per-instrument lazy state.
  useEffect(() => {
    if (!open || !ticker) return
    setDetail(null)
    setForecast(false)
    setOrders(null)
    void loadDetail()
  }, [open, ticker, loadDetail])

  async function toggleWatch() {
    if (!detail || !ticker) return
    setWatchBusy(true)
    try {
      if (detail.watchlist) {
        await deleteWatchlistItem(detail.watchlist.id)
        toast.success(`Removed ${detail.ticker} from watchlist`)
      } else {
        await createWatchlistItem({ symbol: ticker })
        toast.success(`Added ${detail.ticker} to watchlist`)
      }
      await loadDetail()
      onWatchlistChanged?.()
    } catch (err) {
      toastApiError(err, "Watchlist update failed")
    } finally {
      setWatchBusy(false)
    }
  }

  async function loadOrders() {
    if (!ticker) return
    setOrdersLoading(true)
    try {
      const res = await getOrderHistory("all", ticker, 50)
      setOrders(res.orders)
    } catch (err) {
      toastApiError(err, "Couldn't load order history")
      setOrders([])
    } finally {
      setOrdersLoading(false)
    }
  }

  const name = detail?.name ?? hint?.name ?? null
  const headerCurrency = detail?.currency ?? hint?.currency ?? currency
  const price = detail?.price ?? hint?.price ?? null
  const changePct = detail?.change_pct ?? hint?.change_pct ?? null
  const riskKey = (detail?.signals.risk_flag || "ok").toLowerCase()
  const chartTicker = detail?.yfinance_ticker || detail?.instrument_code || ticker || ""

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-y-auto p-0 data-[side=right]:w-full sm:max-w-2xl sm:data-[side=right]:w-[42rem]"
      >
        {ticker ? (
          <>
            <SheetHeader className="border-b border-border/60 px-6 py-5">
              <div className="flex items-center gap-2.5">
                <SheetTitle className="text-2xl font-semibold tracking-tight">{detail?.ticker || ticker}</SheetTitle>
                {detail?.held && detail.position
                  ? detail.position.accounts.map((a) => (
                      <Badge key={a} variant="outline" className="text-muted-foreground">
                        {accountTag(a)}
                      </Badge>
                    ))
                  : null}
                {detail?.signals.risk_flag ? <RiskBadge flag={detail.signals.risk_flag} className="ml-auto" /> : null}
              </div>

              <SheetDescription className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
                {name ? <span className="font-medium text-foreground/90">{name}</span> : null}
                {detail?.instrument_code ? (
                  <span className="font-mono text-muted-foreground">{detail.instrument_code}</span>
                ) : null}
                {headerCurrency ? (
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                    {headerCurrency}
                  </span>
                ) : null}
              </SheetDescription>

              {/* Live price + day change */}
              <div className="mt-1 flex items-baseline gap-3">
                {price != null ? (
                  <span className="font-mono text-xl font-semibold tabular-nums">
                    <Money value={price} currency={headerCurrency} />
                  </span>
                ) : loading ? (
                  <Skeleton className="h-6 w-24" />
                ) : (
                  <span className="text-sm text-muted-foreground">Price unavailable</span>
                )}
                {changePct != null ? <PctDelta value={changePct} className="text-sm" /> : null}
              </div>

              {/* Folded context one-liners (watchlist + thesis) */}
              {(detail?.watchlist || (detail?.theses?.length ?? 0) > 0) && (
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  {detail?.watchlist ? (
                    <span className="inline-flex items-center gap-1.5">
                      <Star className="size-3 text-warning" />
                      Watchlist
                      {detail.watchlist.conviction ? <span className="capitalize">· {detail.watchlist.conviction} conviction</span> : null}
                      {detail.watchlist.note ? <span className="max-w-[18rem] truncate">· {detail.watchlist.note}</span> : null}
                    </span>
                  ) : null}
                  {detail?.theses?.length ? (
                    <span className="inline-flex items-center gap-1.5">
                      <span className="size-1.5 rounded-full bg-primary" />
                      Thesis: {detail.theses[0].title || "active"} · {(detail.theses[0].confidence * 100).toFixed(0)}%
                    </span>
                  ) : null}
                </div>
              )}

              {/* Quick actions */}
              <div className="mt-2.5 flex items-center gap-2">
                <Button
                  variant={detail?.watchlist ? "secondary" : "outline"}
                  size="sm"
                  onClick={() => void toggleWatch()}
                  disabled={watchBusy || !detail}
                >
                  {watchBusy ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Star className={cn("size-3.5", detail?.watchlist && "fill-warning text-warning")} />
                  )}
                  {detail?.watchlist ? "Watching" : "Watch"}
                </Button>
                {onAskArchie ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      onAskArchie(detail?.ticker || ticker, name)
                      onOpenChange(false)
                    }}
                  >
                    <MessageSquareText className="size-3.5" />
                    Ask Archie
                  </Button>
                ) : null}
              </div>
            </SheetHeader>

            <div className="flex flex-col gap-6 px-6 py-6">
              {/* Verdict line — only the parts we actually have. */}
              {detail && <VerdictLine detail={detail} />}

              {/* Position (only if held) */}
              {detail?.held && detail.position ? (
                <section className="flex flex-col gap-2.5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Your position</h3>
                  <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
                    <Stat label="Value"><Money value={detail.position.value} currency={currency} /></Stat>
                    <Stat label="P/L"><MoneyDelta value={detail.position.ppl} currency={currency} /></Stat>
                    <Stat label="Weight"><Pct value={detail.position.weight} /></Stat>
                    <Stat label="Quantity">{detail.position.quantity.toFixed(2)}</Stat>
                    <Stat label="Avg Price"><Money value={detail.position.average_price} currency={currency} /></Stat>
                    <Stat label="Last Price"><Money value={detail.position.current_price} currency={currency} /></Stat>
                  </div>
                </section>
              ) : null}

              {/* Chart + forecast toggle */}
              <section className="flex flex-col gap-3">
                <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/25 px-4 py-3">
                  <div className="min-w-0">
                    <Label htmlFor="spotlight-forecast" className="text-sm font-medium">Kronos forecast</Label>
                    <p className="text-xs text-muted-foreground">Probabilistic 30-day price cone (p10–p50–p90)</p>
                  </div>
                  <Switch id="spotlight-forecast" checked={forecast} onCheckedChange={setForecast} />
                </div>
                <div className="rounded-xl border border-border/60 bg-card/40 p-4">
                  {chartTicker ? (
                    <StockChart
                      key={chartTicker}
                      ticker={chartTicker}
                      period="6mo"
                      interval="1d"
                      chartType="line"
                      indicators={["sma20", "sma50"]}
                      height={320}
                      forecast={forecast}
                      forecastHorizon={30}
                      forecastSamples={12}
                      forecastLookback={180}
                    />
                  ) : null}
                </div>
              </section>

              {/* Signals & risk */}
              {detail ? (
                <section className="flex flex-col gap-2.5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Signals &amp; risk</h3>
                  <div className="grid grid-cols-3 gap-2.5">
                    <Stat label="3M Momentum">{fmtSignal(detail.signals.momentum_63d, "pct")}</Stat>
                    <Stat label="RSI (14)">{fmtSignal(detail.signals.rsi_14, "rsi")}</Stat>
                    <Stat label="Trend">
                      {detail.signals.trend_score != null
                        ? fmtSignal(detail.signals.trend_score, "num")
                        : detail.signals.trend_direction
                          ? <span className="capitalize">{detail.signals.trend_direction}</span>
                          : "—"}
                    </Stat>
                  </div>
                  {detail.signals.risk_flag ? (
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      <span className="font-medium capitalize text-foreground">{riskKey}</span>
                      {" — "}
                      {RISK_NOTE[riskKey] ?? RISK_NOTE.ok}
                    </p>
                  ) : null}
                </section>
              ) : null}

              {/* Attention flags — only when there are open ones. */}
              {detail?.alerts?.length ? (
                <section className="flex flex-col gap-2.5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Needs attention
                  </h3>
                  <div className="flex flex-col gap-2">
                    {detail.alerts.map((a) => (
                      <div key={a.id} className="rounded-lg border border-border/60 bg-muted/20 px-3.5 py-2.5">
                        <div className="flex items-center gap-2">
                          <span className={cn("size-1.5 shrink-0 rounded-full", SEV_DOT[a.severity] ?? SEV_DOT.info)} />
                          <span className="text-sm font-medium">{a.title}</span>
                          <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">{a.category.replace(/_/g, " ")}</span>
                        </div>
                        {a.detail ? <p className="mt-1 pl-3.5 text-xs text-muted-foreground">{a.detail}</p> : null}
                        {a.consider ? (
                          <p className="mt-1 pl-3.5 text-xs text-foreground/80">
                            <span className="font-medium">Consider:</span> {a.consider}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              {/* Order history — lazy, the one rate-limited call. */}
              <section className="flex flex-col gap-2.5">
                {orders === null ? (
                  <Button variant="ghost" size="sm" className="self-start text-muted-foreground" onClick={() => void loadOrders()} disabled={ordersLoading}>
                    {ordersLoading ? <Loader2 className="size-3.5 animate-spin" /> : null}
                    {ordersLoading ? "Loading order history…" : "Load order history"}
                  </Button>
                ) : orders.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No orders found for {detail?.ticker || ticker}.</p>
                ) : (
                  <>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Order history ({orders.length})
                    </h3>
                    <div className="flex flex-col divide-y divide-border/50 overflow-hidden rounded-lg border border-border/60">
                      {orders.slice(0, 12).map((o, i) => (
                        <div key={`${o.order_id}-${i}`} className="flex items-center gap-3 px-3.5 py-2 text-xs">
                          <span className="font-mono tabular-nums text-muted-foreground">
                            {o.created_at ? dayjs(o.created_at).format("DD MMM YY") : "—"}
                          </span>
                          <span className={cn("font-medium uppercase", o.side === "buy" ? "text-positive" : o.side === "sell" ? "text-negative" : "")}>
                            {o.side ?? "—"}
                          </span>
                          <span className="font-mono tabular-nums">
                            {o.filled_quantity ?? o.quantity ?? "—"}
                            {o.fill_price != null ? ` @ ${o.fill_price}` : ""}
                          </span>
                          <span className="ml-auto truncate text-[10px] uppercase tracking-wide text-muted-foreground">{o.status ?? ""}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </section>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}

/** Decision-grade one-liner: distance to watchlist target and move vs. your average. */
function VerdictLine({ detail }: { detail: InstrumentDetail }) {
  const bits: React.ReactNode[] = []

  if (detail.target_distance_pct != null && detail.target_price != null) {
    const below = detail.target_distance_pct > 0 // live price sits below the target
    bits.push(
      <span key="target">
        <Pct value={Math.abs(detail.target_distance_pct)} className="font-semibold text-foreground" />{" "}
        {below ? "below" : "above"} target {detail.target_price}
      </span>
    )
  }

  if (detail.held && detail.position?.ppl_pct != null) {
    bits.push(
      <span key="avg">
        <PctDelta value={detail.position.ppl_pct} className="font-semibold" /> vs your avg
      </span>
    )
  }

  if (bits.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border/60 bg-muted/15 px-3.5 py-2.5 text-sm text-muted-foreground">
      {bits.map((b, i) => (
        <span key={i} className="inline-flex items-center gap-2">
          {i > 0 ? <span className="text-border">·</span> : null}
          {b}
        </span>
      ))}
    </div>
  )
}
