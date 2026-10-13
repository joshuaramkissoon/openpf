import { navigate } from "@/lib/router"
import { useState } from "react"
import dayjs from "dayjs"
import relativeTime from "dayjs/plugin/relativeTime"
import { Check, ExternalLink, Loader2, Sparkles, TriangleAlert, X } from "lucide-react"

import type { Alert } from "@/api/client"
import { Money } from "@/components/kit"
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
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { formatMoney, formatPercent } from "@/utils/format"
import type { AppConfig, PositionItem, TradeIntent } from "@/types"
import type { ExecAccount } from "@/app/hooks/use-proposals"
import type { BrokerMode } from "@/app/hooks/use-app-config"

import type { SignalGroup } from "./use-signals"

dayjs.extend(relativeTime)

/** The API stores naive UTC timestamps; read a zone-less ISO string as UTC. */
function utc(iso: string) {
  return /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
}

export function ago(iso: string | null | undefined): string {
  return iso ? dayjs(utc(iso)).fromNow(true) : ""
}

/** Plain-English check of a proposal against the configured risk rails. */
export function railCheck(
  intent: TradeIntent,
  config: AppConfig | null,
  positions: PositionItem[],
  total: number,
): { ok: boolean; label: string } {
  if (!config) return { ok: true, label: "Rails not loaded" }
  const { max_single_order_notional: cap, max_position_weight: maxW } = config.risk
  if (cap > 0 && intent.estimated_notional > cap) {
    return { ok: false, label: `Over the ${formatMoney(cap, "GBP", 0)} order cap` }
  }
  if (intent.side === "buy" && total > 0 && maxW > 0) {
    const held = positions.find((p) => p.ticker.toUpperCase() === intent.symbol.toUpperCase())?.value ?? 0
    const after = (held + intent.estimated_notional) / total
    if (after > maxW) return { ok: false, label: `Would be ${formatPercent(after, 0)} of book (cap ${formatPercent(maxW, 0)})` }
  }
  return { ok: true, label: "Within rails" }
}

/** "For which play?" — the play (linked), Core, or a gentle "not part of any play". */
export function ProposalOwner({ intent }: { intent: TradeIntent }) {
  const kind = intent.owner_kind ?? (intent.play_id ? "play" : null)
  const playId = intent.owner_play_id ?? intent.play_id
  if (kind === "play" && playId) {
    return (
      <button
        type="button"
        onClick={() => navigate(`/plays/${playId}`)}
        title={intent.owner_label ?? undefined}
        className="max-w-[16rem] truncate text-xs text-muted-foreground hover:text-foreground hover:underline"
      >
        · for {intent.owner_label ?? intent.play_title ?? "a play"}
      </button>
    )
  }
  if (kind === "core") return <span className="text-xs text-muted-foreground">· Core</span>
  return (
    <span
      className="text-xs text-muted-foreground/80 italic"
      title="This proposal doesn't belong to a play or Core. Ask Archie which bet it serves before you approve."
    >
      · not part of any play
    </span>
  )
}

function accountShort(kind?: string | null) {
  return kind === "stocks_isa" ? "ISA" : kind === "invest" ? "Invest" : null
}

export function ProposalRow({
  intent,
  rail,
  brokerMode,
  working,
  onPlace,
  onReject,
  onAsk,
  onViewTicker,
  askLabel = "Ask Archie",
}: {
  intent: TradeIntent
  rail: { ok: boolean; label: string }
  brokerMode: BrokerMode | null
  working: boolean
  onPlace: (account: ExecAccount) => void
  onReject: () => void
  onAsk: () => void
  onViewTicker: (ticker: string) => void
  /** Label for the ask button (Archie's inline card says "Ask why"). */
  askLabel?: string
}) {
  const preset = intent.account_kind === "stocks_isa" || intent.account_kind === "invest" ? intent.account_kind : null
  const [account, setAccount] = useState<ExecAccount | null>(preset)
  const [confirm, setConfirm] = useState(false)
  const executing = intent.status === "executing"
  const buy = intent.side === "buy"

  function place() {
    if (!account) return
    if (brokerMode === "live") setConfirm(true)
    else onPlace(account)
  }

  return (
    <div className="space-y-2.5 py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className={cn("text-xs font-semibold uppercase tracking-wide", buy ? "text-positive" : "text-negative")}>
          {intent.side}
        </span>
        <button type="button" onClick={() => onViewTicker(intent.symbol)} className="text-sm font-semibold hover:underline">
          {intent.symbol}
        </button>
        <Money value={intent.estimated_notional} currency="GBP" decimals={0} className="text-sm" />
        {preset ? <span className="text-xs text-muted-foreground">· {accountShort(preset)}</span> : null}
        <ProposalOwner intent={intent} />
        {intent.status === "approved" ? <span className="text-xs text-muted-foreground">· approved</span> : null}
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1 text-[11px]",
            rail.ok ? "text-muted-foreground" : "text-warning",
          )}
        >
          {rail.ok ? <Check className="size-3 text-positive" /> : <TriangleAlert className="size-3" />}
          {rail.label}
        </span>
      </div>

      {intent.rationale ? (
        <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground">
          {/* Older play tranches carried a "[play] Title — " prefix; the owner label says that now. */}
          {intent.rationale.replace(/^\[play\][^—]*—\s*/, "")}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <span className="w-full font-mono text-[11px] tabular-nums text-muted-foreground sm:mr-auto sm:w-auto">
          {intent.quantity > 0 ? `${+intent.quantity.toFixed(4)} sh · ` : ""}{intent.confidence > 0 ? `conf ${formatPercent(intent.confidence, 0)} · ` : ""}{ago(intent.created_at)} ago
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {!preset ? (
            <Select value={account ?? undefined} onValueChange={(v) => setAccount(v as ExecAccount)}>
              <SelectTrigger size="sm" className="h-7 w-[112px] text-xs">
                <SelectValue placeholder="Account…" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="invest">Invest</SelectItem>
                <SelectItem value="stocks_isa">Stocks ISA</SelectItem>
              </SelectContent>
            </Select>
          ) : null}
          <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs text-muted-foreground" onClick={onAsk}>
            <Sparkles className="size-3.5" />
            <span className="hidden sm:inline">{askLabel}</span>
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onReject} disabled={working || executing}>
            Reject
          </Button>
          <Button size="sm" className="h-7 px-3 text-xs" onClick={place} disabled={working || executing || !account}>
            {working || executing ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {executing ? "Placing…" : "Approve & place"}
          </Button>
        </div>
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {buy ? "Buy" : "Sell"} {formatMoney(intent.estimated_notional, "GBP", 0)} of {intent.symbol}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This sends a real order to your Trading 212 {account === "stocks_isa" ? "Stocks ISA" : "Invest"} account.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirm(false)
                if (account) onPlace(account)
              }}
            >
              Place order
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

const SEV_DOT: Record<Alert["severity"], string> = {
  critical: "bg-negative",
  warning: "bg-warning",
  info: "bg-muted-foreground/50",
}

export function SignalRow({
  group,
  onAsk,
  onViewTicker,
  onDismiss,
  expanded = false,
}: {
  group: SignalGroup
  onAsk: (a: Alert) => void
  onViewTicker: (ticker: string) => void
  onDismiss: () => void
  /** Show full detail + the "consider" line (the signals page). */
  expanded?: boolean
}) {
  const a = group.lead
  const url = typeof a.meta?.url === "string" ? a.meta.url : null
  const more = group.alerts.length - 1
  return (
    <div className="group/sig flex gap-3 py-3 first:pt-0 last:pb-0">
      <span className={cn("mt-[7px] size-1.5 shrink-0 rounded-full", SEV_DOT[a.severity])} aria-label={a.severity} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-baseline gap-2">
          <p className={cn("min-w-0 text-sm font-medium", !expanded && "truncate")}>{a.title}</p>
          <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">{ago(a.created_at)}</span>
        </div>
        {a.detail ? (
          <p className={cn("text-xs leading-relaxed text-muted-foreground", !expanded && "line-clamp-1")}>{a.detail}</p>
        ) : null}
        {expanded && a.consider ? (
          <p className="text-xs leading-relaxed">
            <span className="text-foreground/80">Consider: </span>
            <span className="text-muted-foreground">{a.consider}</span>
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-0.5 text-xs">
          {a.ticker ? (
            <button type="button" onClick={() => onViewTicker(a.ticker!)} className="font-mono text-muted-foreground hover:text-foreground">
              {a.ticker}
            </button>
          ) : null}
          {more > 0 ? <span className="text-muted-foreground">+{more} more</span> : null}
          <button type="button" onClick={() => onAsk(a)} className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
            <Sparkles className="size-3" />
            Ask Archie
          </button>
          {url ? (
            <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
              <ExternalLink className="size-3" />
              Read
            </a>
          ) : null}
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            title="Dismiss"
          >
            <X className="size-3" />
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}
