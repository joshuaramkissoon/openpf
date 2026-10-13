import { useEffect, useState } from "react"
import dayjs from "dayjs"
import { RefreshCw } from "lucide-react"

import { getPortfolioHistory, getTodayMove, type PortfolioHistory, type TodayMove } from "@/api/client"
import { Money, MoneyDelta, PctDelta, Segmented } from "@/components/kit"
import { Skeleton } from "@/components/ui/skeleton"
import { scrambleMoney, usePrivacyMode } from "@/lib/privacy"
import { cn } from "@/lib/utils"
import type { PortfolioState } from "@/app/hooks/use-portfolio"

import { EquityChart, RANGES } from "./equity-chart"

const RANGE_KEY = "openpf:home-range"

function loadRange(): number {
  try {
    const v = Number(localStorage.getItem(RANGE_KEY))
    return RANGES.some((r) => r.days === v) ? v : 365
  } catch {
    return 365
  }
}

function useHistory(account: string, currency: "GBP" | "USD", days: number, bump: string | null) {
  const [data, setData] = useState<PortfolioHistory | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    setLoading(true)
    getPortfolioHistory(account, currency, days)
      .then((d) => active && setData(d))
      .catch(() => active && setData(null))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [account, currency, days, bump])
  return { data, loading }
}

function useTodayMove(account: string, currency: "GBP" | "USD", bump: string | null) {
  const [data, setData] = useState<TodayMove | null>(null)
  useEffect(() => {
    let active = true
    getTodayMove(account, currency)
      .then((d) => active && setData(d))
      .catch(() => active && setData(null))
    return () => {
      active = false
    }
  }, [account, currency, bump])
  return data
}

/**
 * The one big number: total value, today's move and the period move, over a
 * compact equity curve. Account + currency are quiet controls on the right.
 */
export function HomeHero({ portfolio }: { portfolio: PortfolioState }) {
  const { snapshot, accountView, setAccountView, currency, setCurrency, busy, reload, lastUpdate } = portfolio
  const [days, setDays] = useState(loadRange)
  const privacy = usePrivacyMode()
  const scramble = (v: number) => (privacy === "scramble" ? scrambleMoney(v) : v)

  // Bucket lastUpdate to the minute so a poll refetches history at most once a minute.
  const bump = lastUpdate ? lastUpdate.slice(0, 16) : null
  const history = useHistory(accountView, currency, days, bump)

  // Today's move: per-holding moves since the previous close, summed on the
  // server by the same function the Views data source uses.
  const today = useTodayMove(accountView, currency, bump)
  const dayMove = today?.day_change ?? null
  const dayPct = today?.day_change_pct ?? null
  const dayLabel = !today?.session ? "" : today.is_today ? "today" : `${dayjs(today.session).format("ddd")} session`

  const hp = history.data?.points ?? []
  const periodGain = hp.length >= 2 ? hp[hp.length - 1].gain - hp[0].gain : null
  const rangeLabel = RANGES.find((r) => r.days === days)?.label ?? ""

  function pickRange(v: string) {
    const d = Number(v)
    setDays(d)
    try {
      localStorage.setItem(RANGE_KEY, String(d))
    } catch {
      /* best-effort */
    }
  }

  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0 space-y-1.5">
          <p className="text-xs text-muted-foreground">
            Total value
            {accountView !== "all" ? ` · ${accountView === "stocks_isa" ? "Stocks ISA" : "Invest"}` : ""}
          </p>
          {snapshot ? (
            <Money
              value={snapshot.account.total}
              currency={snapshot.account.currency}
              className="block font-sans text-4xl font-semibold leading-none tracking-tight sm:text-5xl"
            />
          ) : (
            <Skeleton className="h-12 w-64" />
          )}
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 pt-1 text-sm">
            {dayMove !== null ? (
              <span className="flex items-baseline gap-1.5">
                <MoneyDelta value={scramble(dayMove)} currency={currency} />
                {dayPct !== null ? <PctDelta value={dayPct} decimals={2} className="text-xs" /> : null}
                <span className="text-xs text-muted-foreground">{dayLabel}</span>
              </span>
            ) : null}
            {periodGain !== null ? (
              <span className="flex items-baseline gap-1.5">
                <MoneyDelta value={scramble(periodGain)} currency={currency} />
                {history.data ? <PctDelta value={history.data.return_pct} className="text-xs" /> : null}
                <span className="text-xs text-muted-foreground">{rangeLabel === "All" ? "all time" : `past ${rangeLabel}`}</span>
              </span>
            ) : null}
            {snapshot ? (
              <span className="flex items-baseline gap-1.5">
                <Money value={snapshot.account.free_cash} currency={snapshot.account.currency} decimals={0} className="text-foreground" />
                <span className="text-xs text-muted-foreground">cash</span>
              </span>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            ariaLabel="Account"
            value={accountView}
            onChange={setAccountView}
            options={[
              { value: "all", label: "All" },
              { value: "invest", label: "Invest" },
              { value: "stocks_isa", label: "ISA" },
            ]}
          />
          <Segmented
            ariaLabel="Currency"
            value={currency}
            onChange={setCurrency}
            options={[
              { value: "GBP", label: "£" },
              { value: "USD", label: "$" },
            ]}
          />
          <button
            type="button"
            onClick={() => void reload({ live: true, force: true })}
            disabled={busy}
            title={lastUpdate ? `Updated ${dayjs(lastUpdate).format("HH:mm:ss")} — refresh` : "Refresh"}
            aria-label="Refresh live data"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />
          </button>
        </div>
      </div>

      <div className="space-y-2">
        <EquityChart history={history.data} currency={currency} loading={history.loading} />
        <div className="flex justify-end">
          <Segmented
            ariaLabel="Chart range"
            value={String(days)}
            onChange={pickRange}
            options={RANGES.map((r) => ({ value: String(r.days), label: r.label }))}
          />
        </div>
      </div>
    </section>
  )
}
