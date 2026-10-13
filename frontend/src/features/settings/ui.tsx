import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { Check, ChevronDown, Loader2 } from "lucide-react"

import { Input } from "@/components/ui/input"
import { parseApiError } from "@/lib/api-error"
import { cn } from "@/lib/utils"

/* Shared building blocks for the Settings pages: a titled group of rows, one
 * row per setting, an inline save state, and a disclosure for expert knobs. */

export type SaveState = { state: "idle" | "saving" | "saved" | "error"; message?: string }

/** Run a save and expose its state for an inline indicator ("Saving…" → "Saved"). */
export function useSaver() {
  const [status, setStatus] = useState<SaveState>({ state: "idle" })
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const run = useCallback(async (fn: () => Promise<unknown>): Promise<boolean> => {
    window.clearTimeout(timer.current)
    setStatus({ state: "saving" })
    try {
      await fn()
      setStatus({ state: "saved" })
      timer.current = window.setTimeout(() => setStatus({ state: "idle" }), 2200)
      return true
    } catch (e) {
      setStatus({ state: "error", message: parseApiError(e).message || "Couldn't save" })
      return false
    }
  }, [])
  const fail = useCallback((message: string) => {
    window.clearTimeout(timer.current)
    setStatus({ state: "error", message })
  }, [])
  return { status, run, fail }
}

export function SaveIndicator({ status, className }: { status: SaveState; className?: string }) {
  if (status.state === "idle") return null
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1 text-xs animate-in fade-in-0 duration-200",
        status.state === "error" ? "text-negative" : "text-muted-foreground",
        status.state === "saved" && "text-positive",
        className,
      )}
    >
      {status.state === "saving" ? <Loader2 className="size-3 animate-spin" /> : null}
      {status.state === "saved" ? <Check className="size-3" /> : null}
      {status.state === "saving" ? "Saving…" : status.state === "saved" ? "Saved" : status.message}
    </span>
  )
}

/** A titled group: heading + one bordered list of rows. */
export function SettingsGroup({
  id,
  title,
  description,
  action,
  children,
  className,
}: {
  id?: string
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section id={id} className={cn("scroll-mt-20 space-y-3", className)}>
      {title || action ? (
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            {title ? <h3 className="text-sm font-semibold tracking-tight">{title}</h3> : null}
            {description ? <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{description}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </div>
      ) : null}
      <div className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/30">{children}</div>
    </section>
  )
}

/** One setting: label + plain-English help on the left, the control on the right. */
export function SettingRow({
  label,
  htmlFor,
  help,
  control,
  status,
  children,
  stack = false,
  highlight = false,
  className,
}: {
  label: ReactNode
  htmlFor?: string
  help?: ReactNode
  control?: ReactNode
  status?: SaveState
  children?: ReactNode
  /** Put the control under the label (wide inputs, segmented choices on mobile). */
  stack?: boolean
  /** Spotlight a row a deep link pointed at (e.g. "Change limits"). */
  highlight?: boolean
  className?: string
}) {
  return (
    <div className={cn("px-4 py-3.5", highlight && "field-spotlight", className)}>
      <div className={cn("flex gap-x-6 gap-y-2.5", stack ? "flex-col" : "flex-col sm:flex-row sm:items-center sm:justify-between")}>
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex flex-wrap items-center gap-x-2">
            {htmlFor ? (
              <label htmlFor={htmlFor} className="text-sm font-medium">
                {label}
              </label>
            ) : (
              <p className="text-sm font-medium">{label}</p>
            )}
            {status ? <SaveIndicator status={status} /> : null}
          </div>
          {help ? <div className="text-xs leading-relaxed text-muted-foreground">{help}</div> : null}
        </div>
        {control ? <div className="flex shrink-0 items-center gap-2">{control}</div> : null}
      </div>
      {children}
    </div>
  )
}

/** Progressive disclosure for expert knobs: closed by default. */
export function Disclosure({
  label,
  hint,
  defaultOpen = false,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  useEffect(() => {
    if (defaultOpen) setOpen(true)
  }, [defaultOpen])
  return (
    <div className="space-y-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="group inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown className={cn("size-3.5 transition-transform duration-200", !open && "-rotate-90")} />
        {label}
        {hint && !open ? <span className="font-normal text-muted-foreground/70">· {hint}</span> : null}
      </button>
      {open ? <div className="animate-in fade-in-0 slide-in-from-top-1 duration-200">{children}</div> : null}
    </div>
  )
}

/**
 * A number field that saves itself when you leave it or press Enter — no form,
 * no Save button. `parse` returns the value or an error string.
 */
export function AutoNumberField({
  id,
  value,
  onCommit,
  parse,
  prefix,
  suffix,
  className,
  inputRef,
}: {
  id: string
  value: number
  onCommit: (v: number) => void
  parse: (raw: string) => number | string
  prefix?: string
  suffix?: string
  className?: string
  inputRef?: React.Ref<HTMLInputElement>
}) {
  const [draft, setDraft] = useState(String(value))
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setDraft(String(value)), [value])

  function commit() {
    if (draft.trim() === String(value)) {
      setError(null)
      return
    }
    const parsed = parse(draft)
    if (typeof parsed === "string") {
      setError(parsed)
      return
    }
    setError(null)
    if (parsed !== value) onCommit(parsed)
  }

  return (
    <div className="space-y-1">
      <div className={cn("relative flex items-center", className)}>
        {prefix ? (
          <span className="pointer-events-none absolute left-2.5 font-mono text-sm text-muted-foreground">{prefix}</span>
        ) : null}
        <Input
          id={id}
          ref={inputRef}
          inputMode="decimal"
          autoComplete="off"
          value={draft}
          aria-invalid={error ? true : undefined}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              commit()
            }
            if (e.key === "Escape") {
              setDraft(String(value))
              setError(null)
            }
          }}
          className={cn("h-8 w-full text-right font-mono text-sm tabular-nums", prefix && "pl-6", suffix && "pr-9")}
        />
        {suffix ? (
          <span className="pointer-events-none absolute right-2.5 font-mono text-xs text-muted-foreground">{suffix}</span>
        ) : null}
      </div>
      {error ? <p className="max-w-56 text-right text-[11px] leading-snug text-negative">{error}</p> : null}
    </div>
  )
}
