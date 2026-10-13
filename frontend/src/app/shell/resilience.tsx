import { Component, useCallback, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from "react"
import { AlertTriangle, RefreshCw, WifiOff } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"

/**
 * Per-route error boundary: a render crash in one page shows a recoverable
 * card instead of blanking the whole app. Keyed by the caller on the route so
 * navigating away resets it.
 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page crashed", error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div role="alert" className="mx-auto mt-10 max-w-md rounded-xl border border-border bg-card p-6 text-sm">
        <div className="flex items-center gap-2 font-medium">
          <AlertTriangle className="size-4 text-warning" />
          This page hit a problem
        </div>
        <p className="mt-2 text-muted-foreground">
          The rest of OpenPF still works. Try again, or head to another page.
        </p>
        <pre className="mt-3 max-h-32 overflow-auto rounded-md bg-muted px-2.5 py-2 font-mono text-[11px] text-muted-foreground">
          {error.message || String(error)}
        </pre>
        <div className="mt-4 flex gap-2">
          <Button size="sm" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      </div>
    )
  }
}

const HEALTH_URL = `${import.meta.env.VITE_API_BASE ?? "/api"}/health`
const ONLINE_POLL_MS = 20_000
const OFFLINE_POLL_MS = [2_000, 4_000, 8_000, 15_000]

async function backendReachable(): Promise<boolean> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 5_000)
  try {
    const res = await fetch(HEALTH_URL, { cache: "no-store", signal: ctrl.signal })
    return res.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Thin strip shown while the backend is unreachable. Polls /api/health with
 * backoff, and on recovery dispatches `openpf:backend-online` (so data hooks
 * can refetch) and toasts once.
 */
export function BackendOfflineBanner() {
  const [offline, setOffline] = useState(false)
  const [checking, setChecking] = useState(false)
  const failures = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const offlineRef = useRef(false)

  const check = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current)
    setChecking(true)
    const ok = await backendReachable()
    setChecking(false)
    if (ok) {
      if (offlineRef.current) {
        toast.success("Reconnected to OpenPF")
        window.dispatchEvent(new Event("openpf:backend-online"))
      }
      failures.current = 0
      offlineRef.current = false
      setOffline(false)
    } else {
      failures.current += 1
      offlineRef.current = true
      setOffline(true)
    }
    const delay = ok ? ONLINE_POLL_MS : OFFLINE_POLL_MS[Math.min(failures.current - 1, OFFLINE_POLL_MS.length - 1)]
    timer.current = setTimeout(() => void check(), delay)
  }, [])

  useEffect(() => {
    void check()
    const onFocus = () => void check()
    window.addEventListener("online", onFocus)
    window.addEventListener("focus", onFocus)
    return () => {
      if (timer.current) clearTimeout(timer.current)
      window.removeEventListener("online", onFocus)
      window.removeEventListener("focus", onFocus)
    }
  }, [check])

  if (!offline) return null
  return (
    <div
      role="status"
      className="flex shrink-0 items-center justify-center gap-2 border-b border-negative/40 bg-negative/10 px-3 py-1.5 text-xs text-negative"
    >
      <WifiOff className="size-3.5 shrink-0" />
      <span className="truncate">Can't reach the OpenPF backend. Retrying automatically…</span>
      <button
        type="button"
        onClick={() => void check()}
        disabled={checking}
        className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium underline-offset-2 hover:underline disabled:opacity-60"
      >
        <RefreshCw className={checking ? "size-3 animate-spin" : "size-3"} />
        Retry now
      </button>
    </div>
  )
}
