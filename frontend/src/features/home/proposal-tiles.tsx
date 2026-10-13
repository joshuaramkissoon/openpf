import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  Ban,
  CalendarClock,
  Check,
  CheckCheck,
  Clock,
  Gauge,
  Hand,
  Hourglass,
  Info,
  Layers,
  Loader2,
  PauseCircle,
  TriangleAlert,
  X,
} from "lucide-react"

import { ArchieIcon, AssetIcon, COIN_NAMES, Money } from "@/components/kit"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button, buttonVariants } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useOpenInstrument } from "@/components/instrument/instrument-provider"
import { navigate } from "@/lib/router"
import { cn } from "@/lib/utils"
import { accountLabel, formatMoney, formatPercent, formatQuantity } from "@/utils/format"
import type { AppConfig, LimitBlock, PositionItem, TradeIntent } from "@/types"
import { PENDING_STATUSES, type DecisionsContextValue, type ExecAccount, type ProposalsState } from "@/app/hooks/use-proposals"

import { ago, railCheck } from "./decision-rows"

/**
 * Pending trade proposals as compact tiles: the asset, the side, the amount,
 * whose bet it is and whether it fits the rails, with the rest (fees,
 * quantity, timing) a tap away. Legs of one play tranche sit together under
 * a header with a total and one "approve both". Used by Home's Decisions,
 * Activity → Proposals and Archie's inline proposal cards.
 */

type Rail = { ok: boolean; label: string }

const isPending = (i: TradeIntent) => (PENDING_STATUSES as readonly string[]).includes(i.status)

function presetAccount(i: TradeIntent): ExecAccount | null {
  return i.account_kind === "stocks_isa" || i.account_kind === "invest" || i.account_kind === "crypto" ? i.account_kind : null
}

function accountShort(kind?: string | null) {
  return kind === "stocks_isa" ? "ISA" : kind === "invest" ? "Invest" : kind === "crypto" ? accountLabel("crypto") : null
}

function assetName(i: TradeIntent) {
  return i.instrument_name || (i.account_kind === "crypto" ? COIN_NAMES[i.symbol.toUpperCase()] : null) || null
}

/**
 * Older play tranches carried a "[play] Title — " prefix; the owner label says that now.
 * Rationale is written to the user: a stray third-person "the user's" reads as "your".
 */
export function cleanRationale(r: string) {
  return r
    .replace(/^\[play\][^—]*—\s*/, "")
    .replace(/\b([Tt])he user's\b/g, (_, t: string) => (t === "T" ? "Your" : "your"))
    .replace(/\b([Tt])he user\b/g, (_, t: string) => (t === "T" ? "You" : "you"))
    .trim()
}

/** "Place it myself": the stock sheet's Trade panel, prefilled with this ask. Advisory limits there. */
function usePlaceMyself(intent: TradeIntent) {
  const open = useOpenInstrument()
  if (!open) return null
  return () =>
    open(intent.symbol, {
      name: assetName(intent),
      trade: { side: intent.side, amount_gbp: intent.estimated_notional, account_kind: intent.account_kind ?? null },
    })
}

function ownerOf(i: TradeIntent): { kind: "play" | "core" | null; playId: string | null; label: string | null } {
  const kind = i.owner_kind ?? (i.play_id ? "play" : null)
  return { kind, playId: i.owner_play_id ?? i.play_id ?? null, label: i.owner_label ?? i.play_title ?? null }
}

/**
 * Rail checks for orders placed one after another: each order is checked with
 * the earlier ones already in the book, so "approve both" can't slip a pair
 * past a cap that each would pass alone.
 */
export function railChecksInSequence(
  intents: TradeIntent[],
  config: AppConfig | null,
  positions: PositionItem[],
  total: number,
): Map<string, Rail> {
  const out = new Map<string, Rail>()
  let book = positions
  for (const i of intents) {
    out.set(i.id, railCheck(i, config, book, total))
    if (i.side === "buy") {
      book = [
        ...book,
        { ticker: i.symbol, account_kind: i.account_kind ?? "invest", value: i.estimated_notional } as PositionItem,
      ]
    }
  }
  return out
}

export type Group = { key: string; title: string | null; playId: string | null; label: string | null; intents: TradeIntent[] }

/** Same play + same tranche → one group; everything else stands alone, in order. */
export function groupProposals(intents: TradeIntent[]): (Group | TradeIntent)[] {
  const keyOf = (i: TradeIntent) => {
    const { playId } = ownerOf(i)
    return playId && i.tranche_index != null ? `${playId}#${i.tranche_index}` : null
  }
  const counts = new Map<string, number>()
  for (const i of intents) {
    const k = keyOf(i)
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  const out: (Group | TradeIntent)[] = []
  const groups = new Map<string, Group>()
  for (const i of intents) {
    const k = keyOf(i)
    if (!k || (counts.get(k) ?? 0) < 2) {
      out.push(i)
      continue
    }
    let g = groups.get(k)
    if (!g) {
      const o = ownerOf(i)
      g = { key: k, title: o.label, playId: o.playId, label: i.tranche_label ?? null, intents: [] }
      groups.set(k, g)
      out.push(g)
    }
    g.intents.push(i)
  }
  // Largest leg first: the tranche reads BTC £189 then ETH £47.
  for (const g of groups.values()) g.intents.sort((a, b) => b.estimated_notional - a.estimated_notional)
  // Groups lead: a two-wide tranche packs with a single tile beside it.
  return [...out.filter((x) => "intents" in x), ...out.filter((x) => !("intents" in x))]
}

// ── Small parts ─────────────────────────────────────────────────────────

function SidePill({ side, small = false }: { side: TradeIntent["side"]; small?: boolean }) {
  const buy = side === "buy"
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full font-semibold uppercase tracking-[0.08em]",
        small ? "h-[18px] px-1.5 text-[9.5px]" : "h-5 px-2 text-[10px]",
        buy ? "bg-positive/12 text-positive" : "bg-negative/12 text-negative",
      )}
    >
      {buy ? "Buy" : "Sell"}
    </span>
  )
}

function RailMark({ rail, small = false }: { rail: Rail; small?: boolean }) {
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={120}
        aria-label={rail.label}
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-full transition-colors",
          small ? "size-5 -mr-0.5" : "size-6",
          rail.ok ? "text-positive hover:bg-positive/10" : "bg-warning/12 text-warning hover:bg-warning/20",
        )}
      >
        {rail.ok ? <Check className={small ? "size-3" : "size-3.5"} strokeWidth={2.5} /> : <TriangleAlert className={small ? "size-3" : "size-3.5"} />}
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-auto max-w-64 gap-1 px-3 py-2 text-xs">
        <p className="font-medium">{rail.ok ? "Within your rails" : "Outside your rails"}</p>
        {!rail.ok || rail.label !== "Within rails" ? <p className="text-muted-foreground">{rail.label}</p> : null}
      </PopoverContent>
    </Popover>
  )
}

function IconAction({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        delay={250}
        aria-label={label}
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "text-muted-foreground hover:text-foreground")}
        onClick={onClick}
        render={<button type="button" disabled={disabled} />}
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function PlayChip({ intent }: { intent: TradeIntent }) {
  const o = ownerOf(intent)
  if (o.kind === "play" && o.playId) {
    return (
      <button
        type="button"
        onClick={() => navigate(`/plays/${o.playId}`)}
        title={o.label ?? undefined}
        className="inline-flex h-5 min-w-0 max-w-full items-center gap-1 rounded-md bg-muted px-1.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <Layers className="size-3 shrink-0" />
        <span className="truncate">{o.label ?? "Play"}</span>
      </button>
    )
  }
  if (o.kind === "core") {
    return <span className="inline-flex h-5 items-center rounded-md bg-muted px-1.5 text-[11px] text-muted-foreground">Core</span>
  }
  return (
    <span
      className="inline-flex h-5 items-center rounded-md border border-dashed border-border px-1.5 text-[11px] italic text-muted-foreground"
      title="This proposal doesn't belong to a play or Core. Ask Archie which bet it serves before you approve."
    >
      No play
    </span>
  )
}

function DetailRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="text-right font-mono tabular-nums text-foreground/90">{children}</dd>
    </div>
  )
}

function DetailsPopover({ intent, rail }: { intent: TradeIntent; rail: Rail }) {
  const c = intent.crypto
  const crypto = intent.account_kind === "crypto"
  const pct = (v: number) => `${(v * 100).toFixed(v < 0.001 ? 3 : 2)}%`
  const o = ownerOf(intent)
  return (
    <Popover>
      <PopoverTrigger
        title="Order details"
        aria-label="Order details"
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "text-muted-foreground hover:text-foreground")}
      >
        <Info className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-72 gap-0 p-3 text-xs">
        <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Order details</p>
        <dl className="divide-y divide-border/60">
          <DetailRow k="Account">
            <span className="font-sans">{c?.label ?? accountShort(intent.account_kind) ?? "Choose on approve"}</span>
          </DetailRow>
          <DetailRow k="Order">
            <span className="font-sans">
              {(intent.order_type || "market").replace(/^./, (c) => c.toUpperCase())} {intent.side}
              {c?.market ? ` · ${c.market}` : ""}
            </span>
          </DetailRow>
          {intent.quantity > 0 ? (
            <DetailRow k="Quantity">
              {crypto ? `${formatQuantity(intent.quantity, "crypto")} ${intent.symbol}` : `${+intent.quantity.toFixed(4)} sh`}
            </DetailRow>
          ) : null}
          {c?.price_gbp ? <DetailRow k="Price">{formatMoney(c.price_gbp, "GBP", c.price_gbp < 10 ? 4 : 2)}</DetailRow> : null}
          {c ? (
            <>
              <DetailRow k="Est. fee">
                {formatMoney(c.fee_gbp ?? 0, "GBP", 2)} <span className="text-muted-foreground">({pct(c.fee_rate)})</span>
              </DetailRow>
              <DetailRow k="Slippage">
                ~{formatMoney(c.slippage_gbp ?? 0, "GBP", 2)} <span className="text-muted-foreground">({pct(c.slippage_pct)})</span>
              </DetailRow>
              {c.all_in_cost_gbp != null ? <DetailRow k="All-in cost">{formatMoney(c.all_in_cost_gbp, "GBP", 2)}</DetailRow> : null}
            </>
          ) : null}
          {intent.confidence > 0 ? <DetailRow k="Confidence">{formatPercent(intent.confidence, 0)}</DetailRow> : null}
          <DetailRow k="For">
            <span className="font-sans">
              {o.kind === "play" ? (o.label ?? "A play") : o.kind === "core" ? "Core" : "Not part of any play"}
              {intent.tranche_label ? ` · ${intent.tranche_label}` : ""}
            </span>
          </DetailRow>
          <DetailRow k="Rails">
            <span className={cn("font-sans", rail.ok ? "text-positive" : "text-warning")}>{rail.label}</span>
          </DetailRow>
          <DetailRow k="Proposed">{ago(intent.created_at)} ago</DetailRow>
        </dl>
      </PopoverContent>
    </Popover>
  )
}

export function LiveConfirm({
  open,
  onOpenChange,
  title,
  description,
  action,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  title: string
  description: string
  action: string
  onConfirm: () => void
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              onOpenChange(false)
              onConfirm()
            }}
          >
            {action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function liveDescription(intent: TradeIntent, account: ExecAccount | null) {
  return intent.account_kind === "crypto"
    ? `This sends a real market order to your ${accountLabel("crypto")} account.${intent.crypto?.fee_gbp != null ? ` Estimated fee ${formatMoney(intent.crypto.fee_gbp, "GBP", 2)}.` : ""} Crypto prices can move sharply; coins on an exchange are not FSCS-protected.`
    : `This sends a real order to your Trading 212 ${account === "stocks_isa" ? "Stocks ISA" : "Invest"} account.`
}

// ── Later-day proposals ────────────────────────────────────────────────

const utcDay = (offset = 0) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10)

/** The (UTC) day a staged proposal opens, when that's still ahead; else null. */
export function laterDay(i: TradeIntent): string | null {
  return i.not_before && i.not_before > utcDay() ? i.not_before : null
}

export function dayLabel(day: string) {
  if (day === utcDay(1)) return "Tomorrow"
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })
}

/** Can be placed right now with one tap: pending, account known, inside the rails, not staged for later. */
export function approvableNow(i: TradeIntent, rail: Rail | undefined) {
  return i.status !== "executing" && Boolean(presetAccount(i)) && Boolean(rail?.ok) && !laterDay(i)
}

function LaterBadge({ day }: { day: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        delay={200}
        render={<span />}
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-dashed border-primary/40 bg-primary/[0.06] px-2.5 text-xs font-medium text-primary"
      >
        <CalendarClock className="size-3.5" />
        {dayLabel(day)}
      </TooltipTrigger>
      <TooltipContent className="max-w-60">
        Staged for {day === utcDay(1) ? "tomorrow" : dayLabel(day)}'s limits. It counts against that day's daily limit and can be approved from then.
      </TooltipContent>
    </Tooltip>
  )
}

// ── The tile ────────────────────────────────────────────────────────────

export function ProposalTile({
  intent,
  rail,
  ctx,
  busy,
  onAsk,
  onViewTicker,
  askLabel = "Ask Archie",
  inGroup = false,
  compact = false,
  className,
}: {
  intent: TradeIntent
  rail: Rail
  ctx: DecisionsContextValue
  /** Another action (e.g. "approve both") is running over this tile. */
  busy?: boolean
  onAsk: (intent: TradeIntent) => void
  onViewTicker: (ticker: string) => void
  askLabel?: string
  /** Inside a tranche group: the header carries the play, so the chip is dropped. */
  inGroup?: boolean
  /** The carousel tile: amount beside the ticker, tighter rhythm (~150px tall). */
  compact?: boolean
  className?: string
}) {
  const preset = presetAccount(intent)
  const crypto = intent.account_kind === "crypto"
  const [account, setAccount] = useState<ExecAccount | null>(preset)
  const [confirm, setConfirm] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const executing = intent.status === "executing"
  const working = ctx.proposals.working === intent.id
  const placing = executing || working
  const disabled = working || executing || Boolean(busy)
  const name = assetName(intent)
  const rationale = intent.rationale ? cleanRationale(intent.rationale) : ""
  const later = laterDay(intent)
  const sub = [name, intent.crypto?.label ?? accountShort(intent.account_kind)].filter(Boolean).join(" · ") || "\u00a0"
  const placeMyself = usePlaceMyself(intent)

  function place() {
    if (!account) return
    if (ctx.brokerMode === "live") setConfirm(true)
    else void ctx.proposals.approveAndExecute(intent, account)
  }

  const identity = (
    <button
      type="button"
      onClick={() => onViewTicker(intent.symbol)}
      className={cn(
        "flex min-w-0 flex-1 items-center rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
        compact ? "gap-2" : "gap-2.5",
      )}
    >
      <AssetIcon symbol={intent.symbol} crypto={crypto} size={compact ? 30 : 34} />
      <span className="min-w-0">
        <span className={cn("flex items-center gap-1.5 font-semibold leading-tight tracking-tight", compact ? "text-[14px]" : "text-[15px]")}>
          {intent.symbol}
          {compact ? <SidePill side={intent.side} small /> : null}
        </span>
        <span className="block truncate text-[11.5px] leading-snug text-muted-foreground">{sub}</span>
      </span>
    </button>
  )

  return (
    <article
      data-state={placing ? "placing" : later ? "later" : "pending"}
      className={cn(
        "ptile relative flex min-w-0 flex-col overflow-hidden rounded-xl border border-border/80 bg-card transition-[border-color,box-shadow,opacity]",
        compact ? "p-3" : "p-3.5",
        "hover:border-border hover:shadow-[0_1px_0_0_color-mix(in_oklch,var(--foreground)_4%,transparent),0_8px_24px_-12px_color-mix(in_oklch,var(--foreground)_18%,transparent)]",
        !rail.ok && "border-warning/40 hover:border-warning/60",
        placing && "border-primary/40",
        className,
      )}
    >
      {placing ? <span className="ptile-bar" aria-hidden /> : null}

      {compact ? (
        <header className="flex items-start gap-2">
          {identity}
          <div className="flex shrink-0 flex-col items-end gap-0.5">
            <Money
              value={intent.estimated_notional}
              currency="GBP"
              decimals={0}
              className="font-sans text-[1.2rem] font-semibold leading-none tracking-[-0.02em]"
            />
            <RailMark rail={rail} small />
          </div>
        </header>
      ) : (
        <>
          <header className="flex items-start gap-2.5">
            {identity}
            <SidePill side={intent.side} />
          </header>
          <div className="mt-3.5 flex items-end justify-between gap-2">
            <Money
              value={intent.estimated_notional}
              currency="GBP"
              decimals={0}
              className="font-sans text-[1.65rem] font-semibold leading-none tracking-[-0.02em]"
            />
            <RailMark rail={rail} />
          </div>
        </>
      )}

      {!inGroup ? (
        <div className={cn("flex min-w-0", compact ? "mt-1.5" : "mt-2.5")}>
          <PlayChip intent={intent} />
        </div>
      ) : null}

      {rationale ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          title={expanded ? undefined : rationale}
          className={cn(
            "text-left text-xs leading-relaxed text-muted-foreground transition-colors hover:text-foreground/80",
            compact ? "mt-1.5" : "mt-2",
            expanded ? "" : "line-clamp-2",
          )}
        >
          {rationale}
        </button>
      ) : null}

      <footer className={cn("mt-auto flex items-center gap-0.5", compact ? "pt-2" : "pt-3")}>
        <DetailsPopover intent={intent} rail={rail} />
        <IconAction label={askLabel} onClick={() => onAsk(intent)}>
          <ArchieIcon className="size-3.5" />
        </IconAction>
        {placeMyself && (later || !rail.ok) ? (
          <IconAction label="Place it myself" onClick={placeMyself} disabled={disabled}>
            <Hand className="size-3.5" />
          </IconAction>
        ) : null}
        <IconAction label="Reject" onClick={() => void ctx.proposals.reject(intent.id)} disabled={disabled}>
          <X className="size-3.5" />
        </IconAction>
        <div className="ml-auto flex items-center gap-1.5">
          {later ? (
            <LaterBadge day={later} />
          ) : (
            <>
              {!preset ? (
                <Select value={account ?? undefined} onValueChange={(v) => setAccount(v as ExecAccount)}>
                  <SelectTrigger size="sm" className="h-7 w-[92px] text-xs">
                    <SelectValue placeholder="Account" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="invest">Invest</SelectItem>
                    <SelectItem value="stocks_isa">Stocks ISA</SelectItem>
                  </SelectContent>
                </Select>
              ) : null}
              <Button
                size="sm"
                variant={inGroup ? "outline" : "default"}
                className="h-7 gap-1.5 px-3 text-xs"
                onClick={place}
                disabled={disabled || !account}
              >
                {placing ? <Loader2 className="size-3.5 animate-spin" /> : null}
                {placing ? "Placing…" : "Approve"}
              </Button>
            </>
          )}
        </div>
      </footer>

      <LiveConfirm
        open={confirm}
        onOpenChange={setConfirm}
        title={`${intent.side === "buy" ? "Buy" : "Sell"} ${formatMoney(intent.estimated_notional, "GBP", 0)} of ${intent.symbol}?`}
        description={liveDescription(intent, account)}
        action="Place order"
        onConfirm={() => {
          if (account) void ctx.proposals.approveAndExecute(intent, account)
        }}
      />
    </article>
  )
}

// ── A held-back ask ─────────────────────────────────────────────────────

const RAIL_FIELD: Record<LimitBlock["rail"], string> = {
  max_single_order_notional: "max_single_order_notional",
  max_daily_notional: "max_daily_notional",
}

/** "over your £500 per-order limit" / "over today's £1,500 limit" / "daily limit used". */
export function blockPhrase(b: LimitBlock) {
  const limit = formatMoney(b.limit_gbp, "GBP", 0)
  if (b.rail === "max_single_order_notional") return `over your ${limit} per-order limit`
  const whose = !b.for_day || b.for_day <= utcDay() ? "today's" : b.for_day === utcDay(1) ? "tomorrow's" : `${dayLabel(b.for_day)}'s`
  return b.remaining_gbp < 1 ? (whose === "today's" ? "daily limit used" : `${whose} limit used`) : `over ${whose} ${limit} limit`
}

/**
 * The part of an ask a limit stopped, as a muted card with the honest options:
 * stage it for tomorrow's limit window, or go change the limit (the user's call;
 * Archie never touches it).
 */
export function HeldTile({
  intent,
  ctx,
  onViewTicker,
  compact = false,
  className,
}: {
  intent: TradeIntent
  ctx: DecisionsContextValue
  onViewTicker: (ticker: string) => void
  compact?: boolean
  className?: string
}) {
  const crypto = intent.account_kind === "crypto"
  const blocks = intent.held?.blocked ?? []
  const working = ctx.proposals.working === intent.id
  const risk = ctx.config?.risk
  const amount = intent.estimated_notional
  // Tomorrow's window only helps if it fits one order there.
  const fitsTomorrow = !risk || (amount <= risk.max_single_order_notional && amount <= risk.max_daily_notional)
  const fields = [...new Set(blocks.map((b) => RAIL_FIELD[b.rail]))]
  const reason = blocks.length ? blocks.map(blockPhrase).join(" and ") : (intent.held?.reason ?? "over your limits")
  const name = assetName(intent)
  const placeMyself = usePlaceMyself(intent)

  return (
    <article
      data-state="held"
      className={cn(
        "relative flex min-w-0 flex-col rounded-xl border border-dashed border-border bg-muted/30 dark:bg-muted/15",
        compact ? "p-3" : "p-3.5",
        className,
      )}
    >
      <header className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => onViewTicker(intent.symbol)}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <AssetIcon symbol={intent.symbol} crypto={crypto} size={compact ? 30 : 34} className="opacity-55 grayscale" />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-[14px] font-semibold leading-tight tracking-tight text-foreground/75">
              {intent.symbol}
              <span className="inline-flex h-[18px] items-center gap-1 rounded-full bg-foreground/[0.06] px-1.5 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                <PauseCircle className="size-2.5" />
                Held back
              </span>
            </span>
            <span className="block truncate text-[11.5px] leading-snug text-muted-foreground">
              {[name, accountShort(intent.account_kind)].filter(Boolean).join(" · ") || "\u00a0"}
            </span>
          </span>
        </button>
        <Money
          value={amount}
          currency="GBP"
          decimals={0}
          className="shrink-0 font-sans text-[1.2rem] font-semibold leading-none tracking-[-0.02em] text-muted-foreground"
        />
      </header>

      <p className="mt-2 flex items-start gap-1.5 text-xs leading-snug text-muted-foreground">
        <Gauge className="mt-px size-3.5 shrink-0 text-warning/80" />
        <span>
          <span className="text-foreground/80">{reason.replace(/^./, (c) => c.toUpperCase())}.</span>
          {!fitsTomorrow ? " Too big for one order under your limits." : ""}{" "}
          <button
            type="button"
            className="whitespace-nowrap underline decoration-muted-foreground/40 underline-offset-2 transition-colors hover:text-foreground"
            onClick={() => navigate(`/settings/risk?focus=${fields.join(",") || "max_daily_notional"}`)}
          >
            Change limits
          </button>
        </span>
      </p>

      <footer className={cn("mt-auto flex items-center gap-1", compact ? "pt-2.5" : "pt-3")}>
        {placeMyself ? (
          <Button size="sm" variant="outline" className="h-7 gap-1.5 bg-card px-2.5 text-xs" disabled={working} onClick={placeMyself}>
            <Hand className="size-3.5" />
            Place it myself
          </Button>
        ) : null}
        {fitsTomorrow ? (
          <Button
            size="sm"
            variant={placeMyself ? "ghost" : "outline"}
            className={cn("h-7 gap-1.5 px-2.5 text-xs", placeMyself ? "text-muted-foreground hover:text-foreground" : "bg-card")}
            disabled={working}
            title="Propose it under tomorrow's limits"
            onClick={() => void ctx.proposals.stageTomorrow(intent)}
          >
            {working ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {placeMyself ? "Tomorrow" : "Propose tomorrow"}
          </Button>
        ) : null}
        <span className="ml-auto">
          <IconAction label="Drop it" onClick={() => void ctx.proposals.dropHeld(intent.id)} disabled={working}>
            <X className="size-3.5" />
          </IconAction>
        </span>
      </footer>
    </article>
  )
}

// ── A tranche group ─────────────────────────────────────────────────────

export function TrancheGroup({
  group,
  ctx,
  onAsk,
  onViewTicker,
  askLabel,
  row = false,
}: {
  group: Group
  ctx: DecisionsContextValue
  onAsk: (intent: TradeIntent) => void
  onViewTicker: (ticker: string) => void
  askLabel?: string
  /** Carousel: one card holding its legs side by side (compact tiles), not a grid cell. */
  row?: boolean
}) {
  const [confirm, setConfirm] = useState(false)
  const [running, setRunning] = useState(false)
  const rails = railChecksInSequence(group.intents, ctx.config, ctx.positions, ctx.total)
  const total = group.intents.reduce((s, i) => s + i.estimated_notional, 0)
  const approvable = group.intents.filter((i) => approvableNow(i, rails.get(i.id)))
  const all = approvable.length === group.intents.length
  const anyWorking = group.intents.some((i) => ctx.proposals.working === i.id || i.status === "executing")
  const n = group.intents.length

  async function approveGroup() {
    setRunning(true)
    try {
      // Re-check each order against the book as it stands with the earlier ones placed.
      const checks = railChecksInSequence(approvable, ctx.config, ctx.positions, ctx.total)
      for (const i of approvable) {
        if (!checks.get(i.id)?.ok) continue
        const ok = await ctx.proposals.approveAndExecute(i, presetAccount(i)!)
        if (!ok) break
      }
    } finally {
      setRunning(false)
    }
  }

  const label = all ? (n === 2 ? "Approve both" : `Approve all ${n}`) : `Approve ${approvable.length} within rails`

  return (
    <section
      className={cn(
        "rounded-2xl border border-border/70 bg-muted/35 p-2 dark:bg-muted/20",
        row ? "flex shrink-0 snap-start flex-col" : "@container col-span-full",
        !row && n === 2 && "@4xl:col-span-2",
      )}
    >
      <div
        className={cn(
          "flex flex-wrap items-center gap-x-2 gap-y-1.5 px-1.5 pb-2 pt-0.5",
          // Wider than a phone: keep the header (and its "approve both") in view while the legs scroll.
          row && "sticky left-1 max-w-[calc(100vw-5.5rem)]",
        )}
      >
        <Layers className="size-3.5 shrink-0 text-muted-foreground" />
        <button
          type="button"
          onClick={() => group.playId && navigate(`/plays/${group.playId}`)}
          className={cn("min-w-0 truncate text-[13px] font-medium hover:underline", !row && "max-w-[60%]")}
          title={group.title ?? undefined}
        >
          {group.title ?? "Play"}
        </button>
        {group.label ? <span className="shrink-0 text-xs text-muted-foreground">{group.label}</span> : null}
        <span className="ml-auto flex items-center gap-2.5">
          <span className="text-xs text-muted-foreground">
            {n} orders · <Money value={total} currency="GBP" decimals={0} className="font-sans font-medium text-foreground" />
          </span>
          {approvable.length > 0 || running ? (
          <Button
            size="sm"
            className="h-7 gap-1.5 px-3 text-xs"
            disabled={approvable.length === 0 || running || anyWorking}
            onClick={() => (ctx.brokerMode === "live" ? setConfirm(true) : void approveGroup())}
          >
            {running ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCheck className="size-3.5" />}
            {running ? "Placing…" : label}
          </Button>
          ) : null}
        </span>
      </div>
      <div className={row ? "flex flex-1 gap-2" : cn("grid grid-cols-1 gap-2 @lg:grid-cols-2", n > 2 && "@4xl:grid-cols-3")}>
        {group.intents.map((i) => (
          <ProposalTile
            key={i.id}
            intent={i}
            rail={rails.get(i.id)!}
            ctx={ctx}
            busy={running}
            onAsk={onAsk}
            onViewTicker={onViewTicker}
            askLabel={askLabel}
            inGroup
            compact={row}
            className={row ? "w-[16.5rem] shrink-0 snap-start" : undefined}
          />
        ))}
      </div>
      <LiveConfirm
        open={confirm}
        onOpenChange={setConfirm}
        title={`Place ${approvable.length} orders (${formatMoney(approvable.reduce((s, i) => s + i.estimated_notional, 0), "GBP", 0)})?`}
        description={`These send real orders: ${approvable
          .map((i) => `${i.side.toUpperCase()} ${i.symbol} ${formatMoney(i.estimated_notional, "GBP", 0)} (${accountShort(i.account_kind)})`)
          .join(", ")}. Each is checked against your rails again as it goes; anything outside them is left for you.${approvable.some((i) => i.account_kind === "crypto") ? " Crypto prices can move sharply; coins on an exchange are not FSCS-protected." : ""}`}
        action="Place orders"
        onConfirm={() => void approveGroup()}
      />
    </section>
  )
}

// ── The grid ────────────────────────────────────────────────────────────

/** Pending proposals as a responsive grid of tiles, tranche legs grouped. */
export function ProposalTiles({
  intents,
  ctx,
  onAsk,
  onViewTicker,
  askLabel,
  className,
}: {
  intents: TradeIntent[]
  ctx: DecisionsContextValue
  onAsk: (intent: TradeIntent) => void
  onViewTicker: (ticker: string) => void
  askLabel?: string
  className?: string
}) {
  const items = groupProposals(intents)
  return (
    <div className={cn("@container", className)}>
      <div className="grid grid-flow-row-dense grid-cols-1 gap-2 @lg:grid-cols-2 @4xl:grid-cols-3">
        {items.map((x) =>
          "intents" in x ? (
            <TrancheGroup key={x.key} group={x} ctx={ctx} onAsk={onAsk} onViewTicker={onViewTicker} askLabel={askLabel} />
          ) : (
            <ProposalTile
              key={x.id}
              intent={x}
              rail={railCheck(x, ctx.config, ctx.positions, ctx.total)}
              ctx={ctx}
              onAsk={onAsk}
              onViewTicker={onViewTicker}
              askLabel={askLabel}
            />
          ),
        )}
      </div>
    </div>
  )
}

/** True when every pending proposal is one tranche (its header already offers "approve all"). */
export function isSingleTranche(intents: TradeIntent[]) {
  const items = groupProposals(intents)
  return items.length === 1 && "intents" in items[0]
}

// ── Receipts ────────────────────────────────────────────────────────────

const RECEIPT: Record<string, { label: (i: TradeIntent) => string; icon: typeof Check; tone: string }> = {
  executed: { label: (i) => (i.side === "buy" ? "Bought" : "Sold"), icon: CheckCheck, tone: "text-positive" },
  rejected: { label: (i) => (i.held && !i.held.staged ? "Dropped" : "Rejected"), icon: X, tone: "text-muted-foreground" },
  expired: { label: () => "Expired", icon: Hourglass, tone: "text-muted-foreground" },
  superseded: { label: () => "Superseded", icon: Clock, tone: "text-muted-foreground" },
  failed: { label: () => "Failed", icon: Ban, tone: "text-negative" },
}

/** A resolved proposal, collapsed to one quiet line. */
export function ProposalReceipt({
  intent,
  onViewTicker,
  onDetails,
  className,
}: {
  intent: TradeIntent
  onViewTicker: (ticker: string) => void
  onDetails?: () => void
  className?: string
}) {
  const meta = RECEIPT[intent.status] ?? { label: () => intent.status, icon: Clock, tone: "text-muted-foreground" }
  const Icon = meta.icon
  const executed = intent.status === "executed"
  return (
    <div className={cn("flex min-w-0 items-center gap-2 py-1.5 text-[13px]", className)}>
      <AssetIcon
        symbol={intent.symbol}
        crypto={intent.account_kind === "crypto"}
        size={20}
        className={cn(!executed && "opacity-60 grayscale")}
      />
      <Icon className={cn("size-3.5 shrink-0", meta.tone)} />
      <span className={cn("shrink-0 font-medium", executed ? "text-foreground" : "text-muted-foreground")}>
        {meta.label(intent)}
        {!executed ? <span className="ml-1 text-[10px] uppercase tracking-wider">{intent.side}</span> : null}
      </span>
      <button type="button" className="shrink-0 font-semibold hover:underline" onClick={() => onViewTicker(intent.symbol)}>
        {intent.symbol}
      </button>
      <Money value={intent.estimated_notional} currency="GBP" decimals={0} className="shrink-0 font-sans text-foreground/90" />
      {executed && intent.execution_price ? (
        <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
          at{" "}
          <span className="font-mono tabular-nums">
            {intent.crypto?.quote && ["GBP", "USD", "EUR"].includes(intent.crypto.quote)
              ? formatMoney(intent.execution_price, intent.crypto.quote, intent.execution_price < 10 ? 4 : 2)
              : intent.execution_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}
          </span>
        </span>
      ) : null}
      {intent.status === "failed" && intent.failure_reason ? (
        <span className="min-w-0 truncate text-xs text-muted-foreground" title={intent.failure_reason}>
          {intent.failure_reason}
        </span>
      ) : null}
      <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
        {ago(intent.executed_at || intent.approved_at || intent.created_at)} ago
      </span>
      {onDetails ? (
        <button type="button" className="shrink-0 text-[11px] text-muted-foreground hover:text-foreground" onClick={onDetails}>
          Details
        </button>
      ) : null}
    </div>
  )
}

/**
 * Proposals that were pending while this surface was open and have since
 * resolved: shown as receipts so an approve doesn't just make a tile vanish.
 */
export function useRecentlyResolved(proposals: ProposalsState): TradeIntent[] {
  const seen = useRef(new Set<string>())
  const [, bump] = useState(0)
  useEffect(() => {
    let grew = false
    for (const i of proposals.pending) {
      if (!seen.current.has(i.id)) {
        seen.current.add(i.id)
        grew = true
      }
    }
    if (grew) bump((n) => n + 1)
  }, [proposals.pending])
  return useMemo(
    () => proposals.intents.filter((i) => seen.current.has(i.id) && !isPending(i)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [proposals.intents, proposals.pending],
  )
}
