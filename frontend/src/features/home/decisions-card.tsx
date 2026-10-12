import { Check } from "lucide-react"

import { SectionLink } from "@/components/kit"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppActions } from "@/lib/app-actions"
import type { AppConfig, PositionItem } from "@/types"
import type { ProposalsState } from "@/app/hooks/use-proposals"
import type { BrokerMode } from "@/app/hooks/use-app-config"

import { ProposalRow, SignalRow, railCheck } from "./decision-rows"
import { signalPrompt, type useSignals } from "./use-signals"

const MAX_ITEMS = 5

export function proposalPrompt(i: { side: string; symbol: string; estimated_notional: number; rationale: string }) {
  return [
    `You proposed: **${i.side.toUpperCase()} ${i.symbol}** (~£${Math.round(i.estimated_notional).toLocaleString()}).`,
    i.rationale ? `Rationale: ${i.rationale}` : "",
    ``,
    `Walk me through the case and the risks. Is the size right for my book, and is now the right time?`,
  ]
    .filter((l) => l !== "")
    .join("\n")
}

/**
 * The heart of Home: what's waiting on the owner. Pending proposals first
 * (approve & place / reject / ask), then the top deduped signals. Capped at
 * five items; the rest live behind "View all".
 */
export function DecisionsCard({
  proposals,
  signals,
  config,
  brokerMode,
  positions,
  total,
}: {
  proposals: ProposalsState
  signals: ReturnType<typeof useSignals>
  config: AppConfig | null
  brokerMode: BrokerMode | null
  positions: PositionItem[]
  total: number
}) {
  const { askArchie, openTicker, navigate } = useAppActions()
  const pending = proposals.pending
  const shownProposals = pending.slice(0, MAX_ITEMS)
  const signalSlots = Math.max(0, Math.min(3, MAX_ITEMS - shownProposals.length))
  const shownSignals = signals.groups.slice(0, signalSlots)
  const hiddenSignals = signals.groups.length - shownSignals.length
  const hiddenProposals = pending.length - shownProposals.length
  const loading = !proposals.loaded || signals.loading
  const empty = !loading && pending.length === 0 && signals.groups.length === 0
  const count = pending.length + signals.groups.length

  return (
    <div className="rounded-xl border border-border bg-card">
      <div className="flex items-baseline justify-between gap-3 px-5 pb-3 pt-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold tracking-tight">Decisions</h2>
          {count > 0 ? <span className="font-mono text-xs tabular-nums text-muted-foreground">{count}</span> : null}
        </div>
        {signals.groups.length > 0 || hiddenProposals > 0 ? (
          <SectionLink onClick={() => navigate(hiddenProposals > 0 ? "/activity/proposals" : "/home/signals")}>
            View all →
          </SectionLink>
        ) : null}
      </div>

      <div className="px-5 pb-5">
        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : empty ? (
          <div className="flex flex-col items-start gap-1.5 py-6">
            <span className="flex size-8 items-center justify-center rounded-full bg-positive/10 text-positive">
              <Check className="size-4" />
            </span>
            <p className="pt-1 text-sm font-medium">Nothing needs you today</p>
            <p className="text-xs text-muted-foreground">
              Archie is watching your holdings. Proposals and signals land here when something crosses a line.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border/70">
            {shownProposals.map((intent) => (
              <ProposalRow
                key={intent.id}
                intent={intent}
                rail={railCheck(intent, config, positions, total)}
                brokerMode={brokerMode}
                working={proposals.working === intent.id}
                onPlace={(acct) => void proposals.approveAndExecute(intent, acct)}
                onReject={() => void proposals.reject(intent.id)}
                onAsk={() => askArchie(proposalPrompt(intent))}
                onViewTicker={openTicker}
              />
            ))}
            {shownSignals.map((g) => (
              <SignalRow
                key={g.key}
                group={g}
                onAsk={(a) => askArchie(signalPrompt(a))}
                onViewTicker={openTicker}
                onDismiss={() => void signals.dismiss(g)}
              />
            ))}
            {hiddenSignals > 0 && shownSignals.length > 0 ? (
              <button
                type="button"
                onClick={() => navigate("/home/signals")}
                className="w-full pt-3 text-left text-xs text-muted-foreground hover:text-foreground"
              >
                {hiddenSignals} more signal{hiddenSignals === 1 ? "" : "s"} →
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
