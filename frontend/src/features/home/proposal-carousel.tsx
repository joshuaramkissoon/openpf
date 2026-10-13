import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react"
import { CheckCheck, ChevronLeft, ChevronRight, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { formatMoney } from "@/utils/format"
import type { TradeIntent } from "@/types"
import type { DecisionsContextValue, ExecAccount } from "@/app/hooks/use-proposals"

import { railCheck } from "./decision-rows"
import {
  HeldTile,
  LiveConfirm,
  ProposalTile,
  TrancheGroup,
  approvableNow,
  groupProposals,
  railChecksInSequence,
} from "./proposal-tiles"

/**
 * Pending proposals in one row: compact tiles that scroll sideways with snap,
 * edge fades where there's more, prev/next on a mouse and plain swipe on
 * touch. Tranche legs stay together in one group card. Held-back asks (the
 * part a limit stopped) trail the proposals as muted cards. Home's Decisions
 * and Archie's replies with more than two cards use it.
 */

type Shared = {
  ctx: DecisionsContextValue
  onAsk: (intent: TradeIntent) => void
  onViewTicker: (ticker: string) => void
  askLabel?: string
}

const TILE = "w-[16.5rem] shrink-0 snap-start"

export function ProposalCarousel({
  intents,
  held = [],
  className,
  ...shared
}: Shared & { intents: TradeIntent[]; held?: TradeIntent[]; className?: string }) {
  const track = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ prev: false, next: false })

  const measure = useCallback(() => {
    const el = track.current
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    setEdges({ prev: el.scrollLeft > 4, next: el.scrollLeft < max - 4 })
  }, [])

  useEffect(() => {
    const el = track.current
    if (!el) return
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    for (const child of Array.from(el.children)) ro.observe(child)
    return () => ro.disconnect()
  }, [measure, intents.length, held.length])

  const step = (dir: -1 | 1) => {
    const el = track.current
    if (!el) return
    el.scrollBy({ left: dir * Math.max(240, el.clientWidth * 0.8), behavior: "smooth" })
  }

  const items = groupProposals(intents)
  const fade = { "--fl": edges.prev ? "2rem" : "0px", "--fr": edges.next ? "3rem" : "0px" } as CSSProperties

  return (
    <div className={cn("relative", className)}>
      <div
        ref={track}
        onScroll={measure}
        style={fade}
        className="pcar -mx-1 flex snap-x snap-mandatory items-stretch gap-2 overflow-x-auto overscroll-x-contain scroll-px-1 px-1 pb-1"
        role="group"
        aria-label="Proposals"
      >
        {items.map((x) =>
          "intents" in x ? (
            <TrancheGroup key={x.key} group={x} row {...shared} />
          ) : (
            <ProposalTile
              key={x.id}
              intent={x}
              rail={railCheck(x, shared.ctx.config, shared.ctx.positions, shared.ctx.total)}
              compact
              className={TILE}
              {...shared}
            />
          ),
        )}
        {held.map((i) => (
          <HeldTile key={i.id} intent={i} ctx={shared.ctx} onViewTicker={shared.onViewTicker} compact className={TILE} />
        ))}
      </div>
      <CarouselNav side="prev" show={edges.prev} onClick={() => step(-1)} />
      <CarouselNav side="next" show={edges.next} onClick={() => step(1)} />
    </div>
  )
}

function CarouselNav({ side, show, onClick }: { side: "prev" | "next"; show: boolean; onClick: () => void }) {
  const Icon = side === "prev" ? ChevronLeft : ChevronRight
  return (
    <button
      type="button"
      aria-label={side === "prev" ? "Previous proposals" : "More proposals"}
      tabIndex={show ? 0 : -1}
      onClick={onClick}
      className={cn(
        "absolute top-1/2 z-10 hidden size-8 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-card/95 text-muted-foreground shadow-[0_4px_14px_-6px_color-mix(in_oklch,var(--foreground)_35%,transparent)] backdrop-blur transition-[opacity,color,transform] hover:text-foreground pointer-fine:flex",
        side === "prev" ? "-left-2" : "-right-2",
        show ? "opacity-100" : "pointer-events-none scale-90 opacity-0",
      )}
    >
      <Icon className="size-4" />
    </button>
  )
}

/** One or two cards: side by side, compact, no scrolling. */
export function ProposalPair({
  intents,
  held = [],
  className,
  ...shared
}: Shared & { intents: TradeIntent[]; held?: TradeIntent[]; className?: string }) {
  const items = groupProposals(intents)
  return (
    <div className={cn("@container", className)}>
      <div className="grid grid-cols-1 gap-2 @md:grid-cols-2">
        {items.map((x) =>
          "intents" in x ? (
            <TrancheGroup key={x.key} group={x} {...shared} />
          ) : (
            <ProposalTile
              key={x.id}
              intent={x}
              rail={railCheck(x, shared.ctx.config, shared.ctx.positions, shared.ctx.total)}
              compact
              {...shared}
            />
          ),
        )}
        {held.map((i) => (
          <HeldTile key={i.id} intent={i} ctx={shared.ctx} onViewTicker={shared.onViewTicker} compact />
        ))}
      </div>
    </div>
  )
}

/**
 * "Approve all within limits": every pending proposal that fits the rails (each
 * checked with the earlier ones already in the book), has its account and isn't
 * staged for a later day. Placed one after another; stops at the first failure.
 */
export function useApproveAll(intents: TradeIntent[], ctx: DecisionsContextValue | null) {
  const [running, setRunning] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const rails = ctx ? railChecksInSequence(intents, ctx.config, ctx.positions, ctx.total) : new Map()
  const approvable = ctx ? intents.filter((i) => approvableNow(i, rails.get(i.id))) : []
  const total = approvable.reduce((s, i) => s + i.estimated_notional, 0)

  async function run() {
    if (!ctx) return
    setRunning(true)
    try {
      const checks = railChecksInSequence(approvable, ctx.config, ctx.positions, ctx.total)
      for (const i of approvable) {
        if (!checks.get(i.id)?.ok) continue
        const ok = await ctx.proposals.approveAndExecute(i, i.account_kind as ExecAccount)
        if (!ok) break
      }
    } finally {
      setRunning(false)
    }
  }

  const button =
    ctx && approvable.length > 1 ? (
      <Button
        size="sm"
        variant="outline"
        className="h-7 gap-1.5 px-2.5 text-xs"
        disabled={running || ctx.proposals.working !== null}
        onClick={() => (ctx.brokerMode === "live" ? setConfirm(true) : void run())}
      >
        {running ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCheck className="size-3.5" />}
        {running ? "Placing…" : (
          <>
            <span>
              Approve all<span className="hidden sm:inline"> within limits</span>
            </span>
            <span className="font-mono tabular-nums text-muted-foreground">{approvable.length}</span>
          </>
        )}
      </Button>
    ) : null

  const dialog = (
    <LiveConfirm
      open={confirm}
      onOpenChange={setConfirm}
      title={`Place ${approvable.length} orders (${formatMoney(total, "GBP", 0)})?`}
      description={`These send real orders: ${approvable
        .map((i) => `${i.side.toUpperCase()} ${i.symbol} ${formatMoney(i.estimated_notional, "GBP", 0)}`)
        .join(", ")}. Each is checked against your limits again as it goes; anything outside them is left for you.`}
      action="Place orders"
      onConfirm={() => void run()}
    />
  )

  return { approvable, button, dialog }
}
