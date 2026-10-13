import { useEffect, useMemo, useState } from "react"
import { ArrowLeft, Lock, RefreshCw } from "lucide-react"

import { markAllAlertsSeen } from "@/api/client"
import { PageHeader } from "@/components/kit"
import { PositionsTable } from "@/components/portfolio/positions-table"
import { AllocationCard } from "@/components/portfolio/allocation-card"
import { RebalanceCard } from "@/components/portfolio/rebalance-card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { OwnerMenu, holdingKey, useBook } from "@/features/plays"
import { useAppActions } from "@/lib/app-actions"
import type { Route } from "@/lib/router"
import { cn } from "@/lib/utils"
import type { AppConfig } from "@/types"
import type { BrokerMode } from "@/app/hooks/use-app-config"
import type { PortfolioState } from "@/app/hooks/use-portfolio"
import type { ProposalsState } from "@/app/hooks/use-proposals"

import { DecisionsCard } from "./decisions-card"
import { SignalRow } from "./decision-rows"
import { HomeHero } from "./hero"
import { signalPrompt, useSignals } from "./use-signals"
import { HomeWidgets } from "./widgets/home-widgets"

export type HomeProps = {
  route: Route
  portfolio: PortfolioState
  proposals: ProposalsState
  config: AppConfig | null
  brokerMode: BrokerMode | null
}

/** `/home`, `/home/signals`, `/home/holdings`. */
export function HomePage(props: HomeProps) {
  const sub = props.route.segments[1]
  if (sub === "signals") return <SignalsPage />
  if (sub === "holdings") return <HoldingsPage portfolio={props.portfolio} />
  return <HomeOverview {...props} />
}

function HomeOverview({ portfolio, proposals, config, brokerMode }: HomeProps) {
  const signals = useSignals()
  const { snapshot, positions } = portfolio
  const [editing, setEditing] = useState(false)
  const ctx = useMemo(() => ({ portfolio, config }), [portfolio, config])

  return (
    <div className="space-y-10">
      <HomeHero portfolio={portfolio} />

      <HomeWidgets
        ctx={ctx}
        editing={editing}
        onEditingChange={setEditing}
        beforeGrid={
          // Decisions is the safety surface: pinned above the widgets, never removable,
          // and out of the way while nothing needs the owner.
          <>
            <DecisionsCard
              proposals={proposals}
              signals={signals}
              config={config}
              brokerMode={brokerMode}
              positions={positions}
              total={snapshot?.account.total ?? 0}
              hideWhenEmpty
            />
            {editing ? (
              <p className="flex items-center gap-2 rounded-xl border border-dashed border-border/80 px-3.5 py-2.5 text-xs text-muted-foreground">
                <Lock className="size-3.5 shrink-0" aria-hidden />
                Decisions are pinned here whenever something needs you, and can’t be removed.
              </p>
            ) : null}
          </>
        }
      />
    </div>
  )
}

function BackLink() {
  const { navigate } = useAppActions()
  return (
    <button
      type="button"
      onClick={() => navigate("/home")}
      className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" />
      Home
    </button>
  )
}

function SignalsPage() {
  const signals = useSignals()
  const { askArchie, openTicker } = useAppActions()

  // Opening the full list counts as having seen what's new.
  useEffect(() => {
    const t = window.setTimeout(() => void markAllAlertsSeen().catch(() => {}), 2000)
    return () => window.clearTimeout(t)
  }, [])

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <BackLink />
        <PageHeader
          title="Signals"
          description="What Archie's watches flagged across your holdings and watchlist — one line per name."
          actions={
            <Button variant="outline" size="sm" onClick={() => void signals.checkNow()} disabled={signals.checking}>
              <RefreshCw className={cn("size-3.5", signals.checking && "animate-spin")} />
              Check now
            </Button>
          }
        />
      </div>
      {signals.loading ? (
        <Skeleton className="h-40 w-full rounded-xl" />
      ) : signals.groups.length === 0 ? (
        <p className="py-16 text-center text-sm text-muted-foreground">All clear — nothing has crossed a threshold.</p>
      ) : (
        <div className="divide-y divide-border/70">
          {signals.groups.map((g) => (
            <SignalRow
              key={g.key}
              group={g}
              expanded
              onAsk={(a) => askArchie(signalPrompt(a))}
              onViewTicker={openTicker}
              onDismiss={() => void signals.dismiss(g)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function HoldingsPage({ portfolio }: { portfolio: PortfolioState }) {
  const { openTicker } = useAppActions()
  const { snapshot, positions, accountView, currency } = portfolio
  const { book } = useBook()
  const byKey = new Map((book?.holdings ?? []).map((h) => [h.key, h]))
  return (
    <div className="space-y-6">
      <div>
        <BackLink />
        <PageHeader title="Holdings" description="Every position, aggregated across accounts unless you pick one on Home." />
      </div>
      {!snapshot ? (
        <Skeleton className="h-96 w-full rounded-xl" />
      ) : (
        <>
          <PositionsTable
            positions={positions}
            accountView={accountView}
            displayCurrency={currency}
            onSelect={(p) => openTicker(p.account_kind === "crypto" && p.yfinance_ticker ? p.yfinance_ticker : p.ticker)}
            renderOwner={(p) => {
              const h = byKey.get(holdingKey(p.account_kind, p.display_ticker || p.ticker))
              return h ? (
                <OwnerMenu
                  ticker={h.ticker}
                  owner={h.owner}
                  relatedTo={h.related_to}
                  accountKind={h.account_kind}
                  className="max-w-[11rem]"
                />
              ) : null
            }}
          />
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="min-w-0">
              <AllocationCard positions={positions} />
            </div>
            <div className="min-w-0 lg:col-span-2">
              <RebalanceCard accountView={accountView} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
