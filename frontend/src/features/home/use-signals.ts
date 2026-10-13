import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { getAttention, runWatches, setAlertStatus, type Alert } from "@/api/client"

const SEV_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 }

export type SignalGroup = {
  key: string
  /** The alert shown for the group (most severe, then newest). */
  lead: Alert
  /** Every open alert in the group, lead first. */
  alerts: Alert[]
}

function ts(a: Alert) {
  return a.created_at ? new Date(a.created_at).getTime() : 0
}

/**
 * Group open alerts by ticker. The backend already supersedes duplicates per
 * ticker+kind; this is the UI fallback so one name never fills the feed.
 */
export function groupSignals(alerts: Alert[]): SignalGroup[] {
  const groups = new Map<string, Alert[]>()
  for (const a of alerts) {
    const key = a.ticker ? `t:${a.ticker.toUpperCase()}` : `a:${a.id}`
    const list = groups.get(key)
    if (list) list.push(a)
    else groups.set(key, [a])
  }
  return Array.from(groups.entries())
    .map(([key, list]) => {
      const sorted = list.slice().sort((x, y) => (SEV_RANK[x.severity] ?? 3) - (SEV_RANK[y.severity] ?? 3) || ts(y) - ts(x))
      return { key, lead: sorted[0], alerts: sorted }
    })
    .sort((x, y) => (SEV_RANK[x.lead.severity] ?? 3) - (SEV_RANK[y.lead.severity] ?? 3) || ts(y.lead) - ts(x.lead))
}

export function useSignals() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)

  const reload = useCallback(async () => {
    try {
      const data = await getAttention()
      setAlerts(data.alerts)
    } catch {
      /* keep the last good feed */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const groups = useMemo(() => groupSignals(alerts), [alerts])

  /** Dismiss a whole group (every open alert for that ticker). */
  const dismiss = useCallback(
    async (group: SignalGroup) => {
      const ids = new Set(group.alerts.map((a) => a.id))
      setAlerts((prev) => prev.filter((a) => !ids.has(a.id)))
      try {
        await Promise.all(group.alerts.map((a) => setAlertStatus(a.id, "dismiss")))
      } catch {
        void reload()
      }
    },
    [reload],
  )

  const checkNow = useCallback(async () => {
    setChecking(true)
    try {
      const res = (await runWatches()) as { created?: number }
      await reload()
      toast(res?.created ? `${res.created} new signal${res.created === 1 ? "" : "s"}` : "Nothing new crossed a threshold")
    } catch {
      toast.error("Couldn't run the watches")
    } finally {
      setChecking(false)
    }
  }, [reload])

  return { alerts, groups, loading, checking, reload, dismiss, checkNow }
}

/** Prompt for "Ask Archie" on a signal — explain it and what it means for the book. */
export function signalPrompt(a: Alert): string {
  const lines = [`This came up in my signals:`, ``, `**${a.title}**`, a.detail]
  if (a.consider) lines.push(``, `Suggested: ${a.consider}`)
  lines.push(``, `What does this mean for my portfolio, and should I do anything? If a trade makes sense, propose it.`)
  return lines.join("\n")
}
