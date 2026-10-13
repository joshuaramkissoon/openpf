import { useState } from "react"
import dayjs from "dayjs"
import { ChevronRight, Inbox } from "lucide-react"

import { ArtifactsWorkspace } from "@/components/ArtifactsWorkspace"
import { EventsFeed } from "@/components/EventsFeed"
import { OrdersWorkspace } from "@/components/OrdersWorkspace"
import { TodayTimeline } from "@/components/scheduled-jobs/TodayTimeline"
import { Money, PageHeader, TabNav } from "@/components/kit"
import { useAppActions } from "@/lib/app-actions"
import type { Route } from "@/lib/router"
import { cn } from "@/lib/utils"
import type { AppConfig, PositionItem, TradeIntent } from "@/types"
import type { BrokerMode } from "@/app/hooks/use-app-config"
import type { ProposalsState } from "@/app/hooks/use-proposals"
import { ProposalReceipt, ProposalTiles, useRecentlyResolved } from "@/features/home/proposal-tiles"
import { proposalPrompt } from "@/features/home/decisions-card"
import { TrackRecordTab } from "@/features/autonomy/track-record"

type Tab = "proposals" | "track-record" | "orders" | "automations" | "audit"
const TABS: Tab[] = ["proposals", "track-record", "orders", "automations", "audit"]

/** `/activity/:tab` — proposals, broker orders, automations and the audit trail. */
export function ActivityPage({
  route,
  proposals,
  config,
  brokerMode,
  positions,
  total,
  onError,
}: {
  route: Route
  proposals: ProposalsState
  config: AppConfig | null
  brokerMode: BrokerMode | null
  positions: PositionItem[]
  total: number
  onError: (m: string | null) => void
}) {
  const { navigate } = useAppActions()
  const tab: Tab = TABS.includes(route.segments[1] as Tab) ? (route.segments[1] as Tab) : "proposals"

  return (
    <div className="space-y-6">
      <PageHeader title="Activity" description="Everything Archie proposed, what went to the broker, and what ran on a schedule." />
      <TabNav
        value={tab}
        onChange={(t) => navigate(`/activity/${t}`)}
        tabs={[
          { value: "proposals", label: "Proposals", count: proposals.pending.length },
          { value: "track-record", label: "Track record" },
          { value: "orders", label: "Orders" },
          { value: "automations", label: "Runs" },
          { value: "audit", label: "Audit" },
        ]}
      />
      {tab === "proposals" ? (
        <ProposalsTab proposals={proposals} config={config} brokerMode={brokerMode} positions={positions} total={total} />
      ) : null}
      {tab === "track-record" ? <TrackRecordTab /> : null}
      {tab === "orders" ? <OrdersWorkspace onError={onError} /> : null}
      {tab === "automations" ? (
        <div className="space-y-8">
          <section>
            <div className="mb-3 flex items-end justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold tracking-tight">Today</h2>
                <p className="text-xs text-muted-foreground">What ran and what's next. Set up and change routines in Recipes.</p>
              </div>
              <button type="button" onClick={() => navigate("/recipes")} className="rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground">
                Manage recipes →
              </button>
            </div>
            <TodayTimeline onError={onError} onViewTask={() => navigate("/recipes")} />
          </section>
          <ArtifactsWorkspace onError={onError} />
        </div>
      ) : null}
      {tab === "audit" ? <EventsFeed events={proposals.events} /> : null}
    </div>
  )
}

function ProposalsTab({
  proposals,
  config,
  brokerMode,
  positions,
  total,
}: {
  proposals: ProposalsState
  config: AppConfig | null
  brokerMode: BrokerMode | null
  positions: PositionItem[]
  total: number
}) {
  const { askArchie, openTicker } = useAppActions()
  const [historyOpen, setHistoryOpen] = useState(false)
  const { pending, history } = proposals
  const resolved = useRecentlyResolved(proposals)

  return (
    <div className="max-w-3xl space-y-8">
      <section>
        <h2 className="mb-3 text-sm font-semibold tracking-tight">Waiting for you</h2>
        {pending.length === 0 ? (
          <div className="flex items-center gap-3 rounded-xl border border-dashed border-border px-5 py-6 text-sm text-muted-foreground">
            <Inbox className="size-4" />
            No open proposals. Ask Archie for ideas, or wait for the next scheduled review.
          </div>
        ) : (
          <ProposalTiles
            intents={pending}
            ctx={{ proposals, config, brokerMode, positions, total }}
            onAsk={(i) => askArchie(proposalPrompt(i))}
            onViewTicker={openTicker}
          />
        )}
        {resolved.length ? (
          <div className="mt-3 divide-y divide-border/50 rounded-lg border border-border/60 px-3 py-0.5">
            {resolved.map((i) => (
              <ProposalReceipt key={i.id} intent={i} onViewTicker={openTicker} />
            ))}
          </div>
        ) : null}
      </section>

      {history.length > 0 ? (
        <section>
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            className="flex items-center gap-1.5 text-sm font-semibold tracking-tight"
            aria-expanded={historyOpen}
          >
            <ChevronRight className={cn("size-4 text-muted-foreground transition-transform", historyOpen && "rotate-90")} />
            History
            <span className="font-mono text-xs font-normal tabular-nums text-muted-foreground">{history.length}</span>
          </button>
          {historyOpen ? (
            <ul className="mt-3 divide-y divide-border/60">
              {history.map((i) => (
                <HistoryRow key={i.id} intent={i} />
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}

const STATUS_TONE: Record<string, string> = {
  executed: "text-positive",
  failed: "text-negative",
  rejected: "text-muted-foreground",
  expired: "text-muted-foreground",
}

function HistoryRow({ intent }: { intent: TradeIntent }) {
  const auto = (intent.approved_by ?? "").startsWith("permission:")
  // Placed by hand from a stock sheet: no proposal, the user decided and approved it.
  const manual = intent.approved_by === "user"
  return (
    <li className="flex items-baseline gap-3 py-2 text-sm">
      <span className="w-24 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
        {dayjs(intent.created_at).format("MMM D HH:mm")}
      </span>
      <span className={cn("w-9 shrink-0 text-xs font-semibold uppercase", intent.side === "buy" ? "text-positive" : "text-negative")}>
        {intent.side}
      </span>
      <span className="w-16 shrink-0 font-medium">{intent.symbol}</span>
      <Money value={intent.estimated_notional} currency="GBP" decimals={0} className="w-20 shrink-0 text-right text-xs" />
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={intent.failure_reason || intent.rationale}>
        {auto ? (
          <span
            className="mr-1.5 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-foreground"
            title="Placed under a standing permission you granted"
          >
            auto · {intent.permission_label ?? "permission"}
          </span>
        ) : manual ? (
          <span className="mr-1.5 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-foreground" title="You placed this trade yourself from the stock sheet">
            You · manual
          </span>
        ) : null}
        {intent.failure_reason || intent.rationale}
      </span>
      <span className={cn("shrink-0 text-xs capitalize", STATUS_TONE[intent.status] ?? "text-muted-foreground")}>
        {intent.status}
      </span>
    </li>
  )
}
