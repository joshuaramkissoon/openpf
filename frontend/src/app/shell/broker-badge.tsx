import { cn } from "@/lib/utils"

import type { BrokerMode } from "../hooks/use-app-config"

const META: Record<BrokerMode, { label: string; title: string; dot: string; text: string }> = {
  sandbox: {
    label: "Sandbox",
    title: "Sandbox broker — simulated portfolio, no real money",
    dot: "bg-chart-2",
    text: "text-chart-2",
  },
  paper: {
    label: "Paper",
    title: "Paper trading — orders are simulated, no real money",
    dot: "bg-chart-2",
    text: "text-chart-2",
  },
  demo: {
    label: "Practice",
    title: "Trading 212 practice account — no real money",
    dot: "bg-chart-2",
    text: "text-chart-2",
  },
  live: {
    label: "Live",
    title: "Live — approved orders go to your real Trading 212 account",
    dot: "bg-positive",
    text: "text-foreground",
  },
}

/**
 * Persistent "what money is this" badge. Simulated modes read as clearly not
 * real (cool blue + an explicit note); LIVE is calm and deliberate — a steady
 * green dot, no alarm colours.
 */
export function BrokerBadge({
  mode,
  collapsed = false,
  className,
}: {
  mode: BrokerMode | null
  collapsed?: boolean
  className?: string
}) {
  if (!mode) return null
  const m = META[mode]
  const simulated = mode !== "live"
  if (collapsed) {
    return (
      <span title={m.title} className={cn("flex justify-center py-1", className)}>
        <span className={cn("size-2 rounded-full", m.dot)} />
      </span>
    )
  }
  return (
    <div
      title={m.title}
      className={cn(
        "flex items-center gap-2 rounded-lg border border-border/70 px-2.5 py-1.5 text-[11px]",
        className,
      )}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", m.dot)} />
      <span className={cn("font-mono font-semibold uppercase tracking-wider", m.text)}>{m.label}</span>
      <span className="truncate text-muted-foreground">{simulated ? "not real money" : "real account"}</span>
    </div>
  )
}
