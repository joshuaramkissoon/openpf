import { Check } from "lucide-react"

import { SectionLink } from "@/components/kit"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppActions } from "@/lib/app-actions"
import type { AppConfig, PositionItem } from "@/types"
import type { ProposalsState } from "@/app/hooks/use-proposals"
import type { BrokerMode } from "@/app/hooks/use-app-config"

import { PermissionSuggestionRow, usePermissionSuggestions } from "@/features/autonomy/home"

import { SignalRow } from "./decision-rows"
import { ProposalCarousel, useApproveAll } from "./proposal-carousel"
import { ProposalReceipt, useRecentlyResolved } from "./proposal-tiles"
import { signalPrompt, type useSignals } from "./use-signals"

// Proposals sit in one sideways row, so more of them fit without the card
// growing; signals keep a few rows beneath.
const MAX_PROPOSALS = 12
const MAX_SIGNALS = 3

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
 * The heart of Home: what's waiting on the owner. Pending proposals first, as
 * one carousel row (approve & place / reject / ask, or all within limits from
 * the header), then the top deduped signals. The rest live behind "View all".
 */
export function DecisionsCard({
  proposals,
  signals,
  config,
  brokerMode,
  positions,
  total,
  hideWhenEmpty = false,
}: {
  proposals: ProposalsState
  signals: ReturnType<typeof useSignals>
  config: AppConfig | null
  brokerMode: BrokerMode | null
  positions: PositionItem[]
  total: number
  /** Home pins Decisions but hides it while nothing needs the owner. */
  hideWhenEmpty?: boolean
}) {
  const { askArchie, openTicker, navigate } = useAppActions()
  const { suggestions, dismiss } = usePermissionSuggestions()
  const shownSuggestions = suggestions.slice(0, 1)
  const pending = proposals.pending
  const shownProposals = pending.slice(0, MAX_PROPOSALS)
  const resolved = useRecentlyResolved(proposals).slice(0, 3)
  const shownSignals = signals.groups.slice(0, shownProposals.length ? MAX_SIGNALS - 1 : MAX_SIGNALS)
  const hiddenSignals = signals.groups.length - shownSignals.length
  const hiddenProposals = pending.length - shownProposals.length
  const loading = !proposals.loaded || signals.loading
  const empty = !loading && pending.length === 0 && signals.groups.length === 0 && shownSuggestions.length === 0 && resolved.length === 0
  const count = pending.length + signals.groups.length + shownSuggestions.length
  const ctx = { proposals, config, brokerMode, positions, total }
  const approveAll = useApproveAll(shownProposals, ctx)
  if (hideWhenEmpty && (empty || loading)) return null

  return (
    <div className="rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-5 pb-3 pt-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold tracking-tight">Decisions</h2>
          {count > 0 ? <span className="font-mono text-xs tabular-nums text-muted-foreground">{count}</span> : null}
        </div>
        <div className="flex items-center gap-3">
          {!loading ? approveAll.button : null}
          {signals.groups.length > 0 || hiddenProposals > 0 ? (
            <SectionLink onClick={() => navigate(hiddenProposals > 0 ? "/activity/proposals" : "/home/signals")}>
              View all →
            </SectionLink>
          ) : null}
        </div>
      </div>
      {approveAll.dialog}

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
          <div className="space-y-4">
            {shownSuggestions.length ? (
              <div className="divide-y divide-border/70">
                {shownSuggestions.map((s) => (
                  <PermissionSuggestionRow key={s.id} s={s} onDismiss={() => void dismiss(s.id)} />
                ))}
              </div>
            ) : null}
            {shownProposals.length ? (
              <ProposalCarousel
                intents={shownProposals}
                ctx={ctx}
                onAsk={(i) => askArchie(proposalPrompt(i))}
                onViewTicker={openTicker}
              />
            ) : null}
            {resolved.length ? (
              <div className="divide-y divide-border/50 rounded-lg border border-border/60 px-3 py-0.5">
                {resolved.map((i) => (
                  <ProposalReceipt key={i.id} intent={i} onViewTicker={openTicker} onDetails={() => navigate("/activity/proposals")} />
                ))}
              </div>
            ) : null}
            {shownSignals.length ? (
              <div className={cn("divide-y divide-border/70", (shownProposals.length > 0 || resolved.length > 0) && "border-t border-border/70 pt-4")}>
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
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
