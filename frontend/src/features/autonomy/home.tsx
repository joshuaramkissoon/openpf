import { useCallback, useEffect, useState, type ReactNode } from "react"
import { ShieldCheck, Sparkles, X } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { toastApiError } from "@/lib/api-error"
import { navigate } from "@/lib/router"

import {
  dismissPermissionSuggestion,
  getDigest,
  listPermissionSuggestions,
  revokePermission,
  type Digest,
  type PermissionSuggestion,
} from "./api"

function usePoll<T>(fn: () => Promise<T>, ms = 60_000) {
  const [data, setData] = useState<T | null>(null)
  const load = useCallback(async () => {
    try {
      setData(await fn())
    } catch {
      /* quiet: these are optional surfaces */
    }
  }, [fn])
  useEffect(() => {
    void load()
    const id = window.setInterval(() => document.visibilityState === "visible" && void load(), ms)
    return () => window.clearInterval(id)
  }, [load, ms])
  return { data, reload: load }
}

/**
 * Home: one line, only on days Archie placed something under a standing
 * permission — with a one-tap revoke per permission.
 */
export function AutonomyDigest({ fallback = null }: { fallback?: ReactNode } = {}) {
  const { data, reload } = usePoll<Digest>(getDigest)
  if (!data || !data.count) return <>{fallback}</>

  async function revoke(id: string, label: string) {
    try {
      await revokePermission(id)
      toast.success(`Revoked “${label}”`)
    } catch (err) {
      toastApiError(err, "Couldn't revoke")
    } finally {
      void reload()
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border px-4 py-2.5 text-sm">
      <ShieldCheck className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0">{data.line}</span>
      <span className="text-xs text-muted-foreground">{data.groups.flatMap((g) => g.symbols).join(", ")}</span>
      <span className="ml-auto flex flex-wrap items-center gap-1">
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => navigate("/activity/proposals")}>
          See trades
        </Button>
        {data.groups.map((g) => (
          <Button
            key={g.permission_id}
            size="sm"
            variant="ghost"
            className="h-7 text-xs text-negative hover:text-negative"
            onClick={() => void revoke(g.permission_id, g.label)}
            title={`Revoke “${g.label}”`}
          >
            Revoke{data.groups.length > 1 ? ` ${g.label}` : ""}
          </Button>
        ))}
      </span>
    </div>
  )
}

export function usePermissionSuggestions() {
  const { data, reload } = usePoll<PermissionSuggestion[]>(listPermissionSuggestions)
  const dismiss = useCallback(
    async (id: string) => {
      try {
        await dismissPermissionSuggestion(id)
      } catch (err) {
        toastApiError(err, "Couldn't dismiss")
      } finally {
        void reload()
      }
    },
    [reload],
  )
  return { suggestions: data ?? [], dismiss }
}

/** A Decisions card: Archie asking for a standing permission. Grants nothing. */
export function PermissionSuggestionRow({ s, onDismiss }: { s: PermissionSuggestion; onDismiss: () => void }) {
  const caveat = typeof s.evidence?.caveat === "string" ? s.evidence.caveat : null
  return (
    <div className="flex gap-3 py-3.5">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Sparkles className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="text-sm leading-snug">{s.message}</p>
        {s.note ? <p className="text-xs text-muted-foreground">“{s.note}”</p> : null}
        <p className="text-xs text-muted-foreground">
          {caveat ? `${caveat} ` : ""}Daily cap £{Math.round(s.daily_cap_gbp).toLocaleString()}; you can change or revoke it any time.
        </p>
        <div className="flex gap-2 pt-0.5">
          <Button size="sm" className="h-7 text-xs" onClick={() => navigate(`/settings/permissions?suggestion=${s.id}`)}>
            Review &amp; grant
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onDismiss}>
            Not now
          </Button>
        </div>
      </div>
      <button type="button" onClick={onDismiss} aria-label="Dismiss" className="self-start text-muted-foreground hover:text-foreground">
        <X className="size-3.5" />
      </button>
    </div>
  )
}
