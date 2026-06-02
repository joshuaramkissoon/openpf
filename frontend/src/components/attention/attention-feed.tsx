import { useCallback, useEffect, useState } from "react"
import {
  AlertTriangle,
  Bell,
  CalendarClock,
  Check,
  ExternalLink,
  LineChart,
  Newspaper,
  RefreshCw,
  Scale,
  Sparkles,
  TrendingUp,
  Wand2,
  X,
  type LucideIcon,
} from "lucide-react"

import { getAttention, markAllAlertsSeen, runWatches, setAlertStatus, type Alert, type AttentionResponse } from "@/api/client"
import { SectionCard } from "@/components/kit"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const CATEGORY_ICON: Record<string, LucideIcon> = {
  thesis_invalidation: AlertTriangle,
  concentration: Scale,
  earnings: CalendarClock,
  big_move: TrendingUp,
  news: Newspaper,
  rebalance: Scale,
}

const SEVERITY_STYLE: Record<Alert["severity"], { ring: string; text: string; label: string }> = {
  critical: { ring: "border-rose-500/40 bg-rose-500/5", text: "text-rose-500", label: "Critical" },
  warning: { ring: "border-amber-500/40 bg-amber-500/5", text: "text-amber-500", label: "Warning" },
  info: { ring: "border-border/60 bg-muted/15", text: "text-muted-foreground", label: "Info" },
}

function timeAgo(iso: string | null): string {
  if (!iso) return ""
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 60) return `${mins}m ago`
  const h = Math.floor(mins / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

interface AlertCardActions {
  onAskArchie: (alert: Alert) => void
  onActOnConsider: (alert: Alert) => void
  onViewTicker: (ticker: string) => void
  onDismiss: (id: string) => void
}

function AlertCard({ alert, onAskArchie, onActOnConsider, onViewTicker, onDismiss }: { alert: Alert } & AlertCardActions) {
  // News watches carry a source URL + attribution in meta; other watches don't.
  const url = typeof alert.meta?.url === "string" ? alert.meta.url : null
  const source = typeof alert.meta?.source === "string" ? alert.meta.source : null
  const Icon = CATEGORY_ICON[alert.category] ?? (url ? Newspaper : Bell)
  const sev = SEVERITY_STYLE[alert.severity]
  return (
    <div className={cn("flex gap-3 rounded-lg border p-3.5", sev.ring)}>
      <div className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg bg-background/60 ring-1 ring-border/60", sev.text)}>
        <Icon className="size-4" strokeWidth={2} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">{alert.title}</span>
          {alert.ticker ? <Badge variant="outline" className="text-[10px]">{alert.ticker}</Badge> : null}
          <Badge variant="outline" className={cn("text-[10px]", sev.text)}>{sev.label}</Badge>
          <span className="ml-auto text-[11px] text-muted-foreground">{timeAgo(alert.created_at)}</span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{alert.detail}</p>
        {alert.consider ? (
          <p className="mt-1.5 text-xs leading-relaxed">
            <span className="font-medium text-foreground/80">Consider: </span>
            <span className="text-muted-foreground">{alert.consider}</span>
          </p>
        ) : null}
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }), "gap-1")}
            >
              <ExternalLink className="size-3.5" />
              Read{source ? ` · ${source}` : ""}
            </a>
          ) : null}
          {alert.ticker ? (
            <Button variant="outline" size="sm" className="gap-1" onClick={() => onViewTicker(alert.ticker!)}>
              <LineChart className="size-3.5" />
              View {alert.ticker}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" className="gap-1" onClick={() => onAskArchie(alert)}>
            <Sparkles className="size-3.5" />
            Ask Archie
          </Button>
          {alert.consider ? (
            <Button variant="secondary" size="sm" className="gap-1" onClick={() => onActOnConsider(alert)}>
              <Wand2 className="size-3.5" />
              Act on this
            </Button>
          ) : null}
        </div>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        className="shrink-0 text-muted-foreground hover:text-foreground"
        title="Dismiss"
        onClick={() => onDismiss(alert.id)}
      >
        <X className="size-3.5" />
      </Button>
    </div>
  )
}

/** Compact "N need a look" chip for the Overview header — clickable, fetches its own count. */
export function AttentionChip({ onOpen }: { onOpen: () => void }) {
  const [counts, setCounts] = useState<AttentionResponse["counts"] | null>(null)
  useEffect(() => {
    let active = true
    getAttention().then((d) => active && setCounts(d.counts)).catch(() => {})
    return () => { active = false }
  }, [])
  if (!counts || counts.alerts_open + counts.pending_intents === 0) return null
  const critical = counts.critical > 0
  return (
    <button
      onClick={onOpen}
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors hover:bg-muted/40",
        critical ? "border-rose-500/40 bg-rose-500/5" : "border-amber-500/40 bg-amber-500/5",
      )}
    >
      <Bell className={cn("size-4", critical ? "text-rose-500" : "text-amber-500")} />
      <span className="font-medium">
        {counts.alerts_open > 0 ? `${counts.alerts_open} alert${counts.alerts_open === 1 ? "" : "s"}` : ""}
        {counts.alerts_open > 0 && counts.pending_intents > 0 ? " · " : ""}
        {counts.pending_intents > 0 ? `${counts.pending_intents} proposal${counts.pending_intents === 1 ? "" : "s"}` : ""}
        {" need a look"}
      </span>
      <span className="text-xs text-muted-foreground">View →</span>
    </button>
  )
}

export function AttentionFeed({
  onError,
  onSeedChat,
  onViewTicker,
}: {
  onError: (m: string | null) => void
  /** Jump to Archie chat with the composer pre-filled (not auto-sent). */
  onSeedChat: (prompt: string) => void
  /** Jump to the Research Desk pre-seeded with a ticker. */
  onViewTicker: (ticker: string) => void
}) {
  const [data, setData] = useState<AttentionResponse | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await getAttention())
      onError(null)
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to load attention feed")
    }
  }, [onError])

  useEffect(() => {
    void load()
    // Mark new alerts as seen shortly after the user opens the tab.
    const t = window.setTimeout(() => void markAllAlertsSeen().catch(() => {}), 2500)
    return () => window.clearTimeout(t)
  }, [load])

  async function refresh() {
    setBusy(true)
    try {
      await runWatches()
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to run watches")
    } finally {
      setBusy(false)
    }
  }

  async function dismiss(id: string) {
    setData((d) => (d ? { ...d, alerts: d.alerts.filter((a) => a.id !== id) } : d))
    try {
      await setAlertStatus(id, "dismiss")
    } catch {
      void load()
    }
  }

  // "Ask Archie" — discuss framing: explain it and what it means for the book.
  function askArchie(alert: Alert) {
    const lines = [`This came up in my Attention feed:`, ``, `**${alert.title}**`, alert.detail]
    if (alert.ticker) lines.push(``, `Ticker: ${alert.ticker}`)
    lines.push(``, `What does this mean for my portfolio, and should I do anything?`)
    onSeedChat(lines.join("\n"))
  }

  // "Act on this" — action framing: turn the Consider suggestion into a concrete next step.
  function actOnConsider(alert: Alert) {
    const lines = [
      `Attention alert — **${alert.title}**${alert.ticker ? ` (${alert.ticker})` : ""}`,
      alert.detail,
      ``,
      `Suggested action: ${alert.consider}`,
      ``,
      `Help me action this: propose the specific trade(s) or next step, and draft a trade intent if it makes sense.`,
    ]
    onSeedChat(lines.join("\n"))
  }

  const alerts = data?.alerts ?? []
  const counts = data?.counts

  return (
    <div className="space-y-6">
      <SectionCard
        title="Attention"
        description="What Archie thinks needs a look — ranked, portfolio-scoped, deduped. Not a news feed."
        action={
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy}>
            <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />
            <span className="hidden sm:inline">Check now</span>
          </Button>
        }
      >
        {counts ? (
          <div className="mb-4 flex flex-wrap gap-2">
            <Badge variant="outline" className={counts.critical ? "text-rose-500" : "text-muted-foreground"}>
              {counts.critical} critical
            </Badge>
            <Badge variant="outline" className="text-muted-foreground">{counts.alerts_open} open</Badge>
            {counts.pending_intents ? (
              <Badge variant="outline" className="text-primary">{counts.pending_intents} proposal(s) awaiting approval</Badge>
            ) : null}
          </div>
        ) : null}

        {alerts.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-12 text-center">
            <div className="flex size-10 items-center justify-center rounded-full bg-positive/10 text-positive ring-1 ring-positive/20">
              <Check className="size-5" />
            </div>
            <p className="text-sm font-medium">All clear</p>
            <p className="text-xs text-muted-foreground">Nothing's crossed a threshold. Archie's still watching — hit "Check now" to re-scan.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {alerts.map((a) => (
              <AlertCard
                key={a.id}
                alert={a}
                onAskArchie={askArchie}
                onActOnConsider={actOnConsider}
                onViewTicker={onViewTicker}
                onDismiss={dismiss}
              />
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  )
}
