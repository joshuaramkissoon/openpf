import { useEffect, useState } from "react"
import { ChevronLeft, ChevronRight, HelpCircle, Search } from "lucide-react"

import { HelpGuide } from "@/components/help/help-guide"
import { useInstrument } from "@/components/instrument/instrument-provider"
import { PageHeader } from "@/components/kit"
import type { PrivacyMode } from "@/lib/privacy"
import { navigate, type Route } from "@/lib/router"
import { cn } from "@/lib/utils"
import type { AppConfig } from "@/types"
import { AdvancedSettings } from "./advanced"
import { AppearanceSettings } from "./appearance"
import { ArchieSettings } from "./archie"
import { AccountsSettings } from "./connections"
import { NotificationsSettings } from "./notifications"
import { canonicalSettingsPath, SETTINGS_PAGES, type SettingsPageId } from "./registry"
import { SafetySettings } from "./safety"

const PAGE_IDS = new Set<string>([...SETTINGS_PAGES.map((p) => p.id), "help"])

function useIsDesktop() {
  const query = "(min-width: 768px)"
  const [desktop, setDesktop] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setDesktop(mq.matches)
    mq.addEventListener("change", on)
    return () => mq.removeEventListener("change", on)
  }, [])
  return desktop
}

/**
 * `/settings/:page` — a left section nav on desktop; a list → detail flow on
 * mobile (`/settings` is the list). Old tab routes redirect to their new home.
 */
export function SettingsPage({
  route,
  config,
  onConfigChanged,
  onError,
  privacyMode,
  onPrivacyModeChange,
}: {
  route: Route
  config: AppConfig | null
  onConfigChanged: () => void
  onError: (m: string | null) => void
  privacyMode: PrivacyMode
  onPrivacyModeChange: (m: PrivacyMode) => void
}) {
  const desktop = useIsDesktop()
  const segment = route.segments[1]
  const redirect = canonicalSettingsPath(segment, route.query)
  useEffect(() => {
    if (redirect) navigate(redirect, { replace: true })
    else if (segment && !PAGE_IDS.has(segment)) navigate("/settings", { replace: true })
  }, [redirect, segment])

  const page: SettingsPageId | null = segment && PAGE_IDS.has(segment) ? (segment as SettingsPageId) : desktop ? "accounts" : null
  const q = (k: string) => route.query.get(k)

  if (!page) return <MobileIndex />

  const meta =
    page === "help"
      ? { label: "Help", blurb: "What OpenPF does and how to use it." }
      : SETTINGS_PAGES.find((p) => p.id === page)!

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" className="hidden md:flex" />
      <div className="flex gap-10">
        <SettingsNav current={page} />

        <div className="min-w-0 max-w-3xl flex-1 space-y-8 pb-16">
          <div className="space-y-3">
            <button
              type="button"
              onClick={() => navigate("/settings")}
              className="-ml-1 inline-flex items-center gap-0.5 text-sm text-muted-foreground transition-colors hover:text-foreground md:hidden"
            >
              <ChevronLeft className="size-4" />
              Settings
            </button>
            <div>
              <h2 className="text-xl font-semibold tracking-tight md:text-base">{meta.label}</h2>
              <p className="text-sm text-muted-foreground">{meta.blurb}</p>
            </div>
          </div>

          <div key={page} className="animate-in fade-in-0 duration-300">
            {page === "accounts" ? (
              <AccountsSettings config={config} onConfigChanged={onConfigChanged} manage={q("manage")} add={q("add")} />
            ) : null}
            {page === "safety" ? (
              <SafetySettings
                config={config}
                onConfigChanged={onConfigChanged}
                focus={q("focus")}
                section={q("section")}
                suggestionId={q("suggestion")}
              />
            ) : null}
            {page === "archie" ? <ArchieSettings section={q("section")} onError={onError} /> : null}
            {page === "notifications" ? <NotificationsSettings config={config} onConfigChanged={onConfigChanged} /> : null}
            {page === "appearance" ? (
              <AppearanceSettings privacyMode={privacyMode} onPrivacyModeChange={onPrivacyModeChange} focus={q("focus")} />
            ) : null}
            {page === "advanced" ? (
              <AdvancedSettings config={config} onConfigChanged={onConfigChanged} section={q("section")} onError={onError} />
            ) : null}
            {page === "help" ? <HelpGuide /> : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function SearchButton({ className }: { className?: string }) {
  const { openPalette } = useInstrument()
  return (
    <button
      type="button"
      onClick={openPalette}
      className={cn(
        "flex w-full items-center gap-2 rounded-lg border border-border/80 px-2.5 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground",
        className,
      )}
    >
      <Search className="size-3.5" />
      <span className="flex-1">Find a setting</span>
      <kbd className="rounded border border-border/70 px-1 font-mono text-[10px]">⌘K</kbd>
    </button>
  )
}

function SettingsNav({ current }: { current: SettingsPageId }) {
  return (
    <nav className="hidden w-52 shrink-0 flex-col gap-4 md:flex" aria-label="Settings sections">
      <SearchButton />
      <ul className="flex flex-col gap-0.5">
        {SETTINGS_PAGES.map((p) => {
          const on = current === p.id
          return (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => navigate(`/settings/${p.id}`)}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors",
                  on ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <p.icon className={cn("size-4 shrink-0", on ? "text-foreground" : "text-muted-foreground/80")} />
                {p.label}
              </button>
            </li>
          )
        })}
      </ul>
      <div className="border-t border-border/60 pt-3">
        <button
          type="button"
          onClick={() => navigate("/settings/help")}
          aria-current={current === "help" ? "page" : undefined}
          className={cn(
            "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors",
            current === "help" ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
          )}
        >
          <HelpCircle className="size-4" />
          Help & guide
        </button>
      </div>
    </nav>
  )
}

/** Mobile `/settings`: the list half of list → detail. */
function MobileIndex() {
  return (
    <div className="space-y-5">
      <PageHeader title="Settings" />
      <SearchButton className="py-2" />
      <ul className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/30">
        {SETTINGS_PAGES.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              onClick={() => navigate(`/settings/${p.id}`)}
              className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors active:bg-muted/60"
            >
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted">
                <p.icon className="size-4 text-muted-foreground" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{p.label}</span>
                <span className="block truncate text-xs text-muted-foreground">{p.blurb}</span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground/60" />
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => navigate("/settings/help")}
        className="flex w-full items-center gap-2 px-1 text-sm text-muted-foreground"
      >
        <HelpCircle className="size-4" />
        Help & guide
      </button>
    </div>
  )
}
