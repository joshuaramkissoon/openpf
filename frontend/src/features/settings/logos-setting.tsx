import { useState } from "react"
import { toast } from "sonner"

import { Switch } from "@/components/ui/switch"
import { toastApiError } from "@/lib/api-error"
import { setLogosEnabled, useLogosEnabled } from "@/lib/logos"
import { cn } from "@/lib/utils"

/** Where logos come from (the free tier asks for a visible link back). */
export const LOGO_SOURCE = { label: "Elbstream", href: "https://elbstream.com" }

/**
 * "Company logos" toggle. Self-contained (loads and saves its own state) so the
 * Settings page can place it anywhere.
 */
export function LogosSetting() {
  const enabled = useLogosEnabled()
  const [saving, setSaving] = useState(false)

  async function toggle(next: boolean) {
    setSaving(true)
    try {
      await setLogosEnabled(next)
      toast.success(next ? "Company logos on" : "Company logos off")
    } catch (e) {
      toastApiError(e, "Couldn't update company logos")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-start justify-between gap-4 rounded-xl border border-border px-4 py-3">
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium">Company logos</p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Fetched once by your OpenPF server and cached; the logo service sees the ticker, nothing else.
        </p>
        <LogoCredit className="text-xs" />
      </div>
      <Switch
        aria-label="Company logos"
        checked={Boolean(enabled)}
        disabled={saving || enabled === null}
        onCheckedChange={(v) => void toggle(Boolean(v))}
      />
    </div>
  )
}

/** The attribution link the logo service's free tier requires. */
export function LogoCredit({ className }: { className?: string }) {
  return (
    <p className={cn("text-muted-foreground", className)}>
      Logos by{" "}
      <a href={LOGO_SOURCE.href} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-foreground">
        {LOGO_SOURCE.label}
      </a>
    </p>
  )
}
