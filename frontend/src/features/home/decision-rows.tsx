import dayjs from "dayjs"
import relativeTime from "dayjs/plugin/relativeTime"
import { ExternalLink, X } from "lucide-react"
import { ArchieIcon } from "@/components/kit"

import type { Alert } from "@/api/client"
import { cn } from "@/lib/utils"
import { formatMoney, formatPercent } from "@/utils/format"
import type { AppConfig, PositionItem, TradeIntent } from "@/types"

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
  const cryptoCap = config.risk.max_crypto_pct_of_book
  if (intent.account_kind === "crypto" && intent.side === "buy" && total > 0 && cryptoCap != null) {
    const crypto = positions.filter((p) => p.account_kind === "crypto").reduce((s, p) => s + p.value, 0)
    const after = (crypto + intent.estimated_notional) / total
    if (after > cryptoCap) return { ok: false, label: `Crypto would be ${formatPercent(after, 1)} of book (cap ${formatPercent(cryptoCap, 0)})` }
  }
  if (intent.side === "buy" && total > 0 && maxW > 0) {
    const held = positions.find((p) => p.ticker.toUpperCase() === intent.symbol.toUpperCase())?.value ?? 0
    const after = (held + intent.estimated_notional) / total
    if (after > maxW) return { ok: false, label: `Would be ${formatPercent(after, 0)} of book (cap ${formatPercent(maxW, 0)})` }
  }
  return { ok: true, label: "Within rails" }
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
            <ArchieIcon className="size-3" />
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
