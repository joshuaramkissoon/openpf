import { Money, MoneyDelta, Pct, StatCard } from "@/components/kit"
import { useInstrument } from "@/components/instrument/instrument-provider"
import { accountLabel, formatNumber, formatPercent } from "@/utils/format"
import type { PortfolioSnapshot, PositionItem } from "@/types"

import { AllocationCard } from "./allocation-card"
import { PortfolioHistoryCard } from "./portfolio-history-card"
import { PositionsTable } from "./positions-table"
import { RebalanceCard } from "./rebalance-card"

export function PortfolioOverview({
  snapshot,
  positions,
  accountView,
  displayCurrency,
}: {
  snapshot: PortfolioSnapshot
  positions: PositionItem[]
  accountView: "all" | "invest" | "stocks_isa"
  displayCurrency: "GBP" | "USD"
}) {
  const { openInstrument } = useInstrument()

  const { account, metrics, accounts } = snapshot
  const topWeight = positions.length ? Math.max(...positions.map((p) => p.weight)) : 0
  const concentration = positions.reduce((sum, p) => sum + p.weight * p.weight, 0)
  const accountRows = accountView === "all" ? accounts : accounts.filter((a) => a.account_kind === accountView)

  function handleSelect(p: PositionItem) {
    openInstrument(p.ticker, { name: p.name, price: p.current_price })
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total Equity"
          value={<Money value={account.total} currency={account.currency} />}
          footer={
            accountRows.length > 1
              ? accountRows.map((r) => (
                  <span key={r.account_kind} className="flex justify-between gap-3">
                    <span>{accountLabel(r.account_kind)}</span>
                    <Money value={r.total} currency={r.currency} className="text-foreground" />
                  </span>
                ))
              : undefined
          }
        />
        <StatCard
          label="Free Cash"
          value={<Money value={account.free_cash} currency={account.currency} />}
          hint={`Cash ratio ${formatPercent(metrics.cash_ratio)}`}
        />
        <StatCard label="Unrealised P/L" value={<MoneyDelta value={account.ppl} currency={account.currency} />} />
        <StatCard
          label="Positions"
          value={positions.length}
          hint={`Top weight ${formatPercent(topWeight)}`}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Concentration (HHI)" value={formatNumber(concentration, 3)} />
        <StatCard label="Top Position" value={<Pct value={topWeight} />} />
        <StatCard label="Est. Beta" value={formatNumber(metrics.estimated_beta, 2)} />
        <StatCard label="Est. Volatility" value={<Pct value={metrics.estimated_volatility} />} />
      </div>

      <PortfolioHistoryCard accountView={accountView} displayCurrency={displayCurrency} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-1">
          <AllocationCard positions={positions} />
        </div>
        <div className="min-w-0 lg:col-span-2">
          <PositionsTable
            positions={positions}
            accountView={accountView}
            displayCurrency={displayCurrency}
            onSelect={handleSelect}
          />
        </div>
      </div>

      <RebalanceCard accountView={accountView} />
    </div>
  )
}
