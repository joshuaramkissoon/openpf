import { ArrowUpRight, Eye } from "lucide-react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { accountInfo, useAccounts } from "@/lib/accounts"
import { cn } from "@/lib/utils"
import { accountTag } from "@/utils/format"

/**
 * The source-account chip on a holding row ("ISA", "KRAKEN", "IBKR").
 * Accounts OpenPF can only read carry an eye mark and say so on hover —
 * trades there are placed in the broker, not here.
 */
export function AccountChip({ kind, className }: { kind: string; className?: string }) {
  useAccounts()
  const info = accountInfo(kind)
  const chip = (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-px text-[10px] font-medium leading-4 tracking-wide",
        info?.read_only
          ? "border-dashed border-border text-muted-foreground"
          : "border-border text-muted-foreground",
        className,
      )}
    >
      {info?.read_only ? <Eye className="size-2.5" aria-hidden /> : null}
      {accountTag(kind)}
      {info?.read_only ? <span className="sr-only"> (read-only)</span> : null}
    </span>
  )
  if (!info?.read_only) return chip
  return (
    <Tooltip>
      <TooltipTrigger delay={250} render={chip} />
      <TooltipContent className="max-w-56 text-xs">
        {info.label} · read-only. Archie can analyse it; trades are placed in {info.broker}.
      </TooltipContent>
    </Tooltip>
  )
}

/** "Place it in <broker>" — shown on a proposal for a read-only account instead of Approve. */
export function readOnlyPlacement(intent: { read_only?: boolean; place_in?: string | null; account_kind?: string | null }):
  | string
  | null {
  if (!intent.read_only && !accountInfo(intent.account_kind ?? "")?.read_only) return null
  return intent.place_in || accountInfo(intent.account_kind ?? "")?.broker || "your broker"
}

/** The exchange behind a read-only crypto account ("crypto:kraken" → "kraken"), or null. */
function cryptoExchange(accountKind: string | null | undefined, broker: string): string | null {
  const k = (accountKind ?? "").toLowerCase()
  if (k.startsWith("crypto:")) return k.slice("crypto:".length)
  if (k === "crypto") return broker.toLowerCase().split(/\s+/)[0] || null
  return null
}

/**
 * The footer note a read-only suggestion gets instead of Approve. On a crypto
 * exchange (where OpenPF can trade with a trade key) it also offers the fix.
 */
export function ReadOnlyNote({ broker, accountKind, className }: { broker: string; accountKind?: string | null; className?: string }) {
  const exchange = cryptoExchange(accountKind, broker)
  return (
    <span className={cn("ml-auto inline-flex flex-wrap items-center justify-end gap-2", className)}>
      {exchange ? (
        <a
          href={`#/settings/accounts?add=${encodeURIComponent(exchange)}`}
          className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          title={`${broker} is connected read-only. Add a trade key to let Archie place the orders you approve there.`}
        >
          Enable trading
        </a>
      ) : null}
      <span
        className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-border px-2.5 py-1 text-xs text-muted-foreground"
        title="This account is connected read-only. OpenPF can't place the order — you do, in your broker."
      >
        <Eye className="size-3" />
        Place it in <span className="font-medium text-foreground">{broker}</span>
        <ArrowUpRight className="size-3" />
      </span>
    </span>
  )
}
