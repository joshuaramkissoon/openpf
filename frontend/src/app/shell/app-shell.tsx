import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Activity,
  Bell,
  CalendarClock,
  Eye,
  HelpCircle,
  Home,
  LayoutGrid,
  ListOrdered,
  Menu,
  MessageSquare,
  Moon,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Target,
  Wallet,
  X,
} from "lucide-react"

import { LeveragedWorkspace } from "@/components/LeveragedWorkspace"
import { TokenGate } from "@/components/auth/token-gate"
import { InstrumentProvider, useInstrument } from "@/components/instrument/instrument-provider"
import type { SpotlightCommand } from "@/components/instrument/instrument-spotlight"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { ActivityPage } from "@/features/activity"
import { ArchiePage } from "@/features/archie"
import { HomePage } from "@/features/home"
import { PlaysPage } from "@/features/plays"
import { SettingsPage } from "@/features/settings"
import { ViewsPage } from "@/features/views"
import { AppActionsProvider, type AppActions } from "@/lib/app-actions"
import { DisplayPrefsProvider } from "@/lib/display-prefs"
import { PrivacyProvider, loadPrivacyMode, nextPrivacyMode, savePrivacyMode, type PrivacyMode } from "@/lib/privacy"
import { navigate, useRoute, type Route } from "@/lib/router"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

import { brokerModeOf, useAppConfig } from "../hooks/use-app-config"
import { useChatSessions } from "../hooks/use-chat-sessions"
import { usePortfolio } from "../hooks/use-portfolio"
import { useProposals } from "../hooks/use-proposals"
import { BrokerBadge } from "./broker-badge"
import { AppSidebar, SidebarBody } from "./sidebar"

const KNOWN = new Set(["home", "archie", "plays", "activity", "views", "settings", "labs"])

const TITLES: Record<string, string> = {
  home: "Home",
  archie: "Archie",
  plays: "Plays",
  activity: "Activity",
  views: "Views",
  settings: "Settings",
  labs: "Labs",
}

function isTypingTarget(): boolean {
  const el = document.activeElement as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable === true
}

function useNavCollapsed() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("openpf:nav-collapsed") === "1"
    } catch {
      return false
    }
  })
  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem("openpf:nav-collapsed", next ? "1" : "0")
      } catch {
        /* ignore */
      }
      return next
    })
  }, [])
  return [collapsed, toggle] as const
}

export function AppShell() {
  const route = useRoute()
  const section = route.segments[0]

  // Normalise unknown / bare routes once so deep links stay canonical.
  useEffect(() => {
    if (!KNOWN.has(section)) navigate("/home", { replace: true })
    else if (section === "settings" && !route.segments[1]) navigate("/settings/general", { replace: true })
  }, [section, route.segments])

  const [privacyMode, setPrivacyMode] = useState<PrivacyMode>(() => loadPrivacyMode())
  useEffect(() => savePrivacyMode(privacyMode), [privacyMode])

  const { config, reload: reloadConfig } = useAppConfig()
  const brokerMode = brokerModeOf(config)
  const portfolio = usePortfolio({ privacyMode, pollActive: section === "home" })
  const proposals = useProposals({
    paper: brokerMode === "paper",
    onExecuted: () => void portfolio.reload({ live: true }),
  })
  const chat = useChatSessions()

  const [pendingPrompt, setPendingPrompt] = useState<{ text: string; send: boolean } | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  useEffect(() => setPageError(null), [route.path])

  const actions = useMemo<AppActions>(
    () => ({
      askArchie: (prompt, opts) => {
        setPendingPrompt({ text: prompt, send: Boolean(opts?.send) })
        navigate("/archie")
      },
      openTicker: () => {}, // replaced inside InstrumentProvider (see ShellActions)
      navigate: (p) => navigate(p),
      refresh: () => {
        void portfolio.reload()
        void proposals.reload()
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [portfolio.reload, proposals.reload],
  )

  const cyclePrivacy = useCallback(() => setPrivacyMode((m) => nextPrivacyMode(m)), [])
  const displayPrefs = useMemo(
    () => ({ currency: portfolio.currency, accountView: portfolio.accountView }),
    [portfolio.currency, portfolio.accountView],
  )
  const commands = useCommands(actions, cyclePrivacy, portfolio.reload)

  return (
    <PrivacyProvider mode={privacyMode}>
      <DisplayPrefsProvider value={displayPrefs}>
      <InstrumentProvider
        positions={portfolio.positions}
        currency={portfolio.currency}
        onAskArchie={(ticker, name) =>
          actions.askArchie(`What's your current read on ${ticker}${name ? ` (${name})` : ""}? Anything I should act on?`)
        }
        onDeepAnalysis={(ticker, name) =>
          actions.askArchie(
            `Run a deep analysis on ${ticker}${name ? ` (${name})` : ""}: live price action and technicals, fundamentals and valuation, recent news, a forecast, and how it fits my current book. Finish with a clear verdict (buy / hold / trim / avoid) and a suggested size if it's a buy.`,
            { send: true },
          )
        }
        onAskArchiePrompt={(p) => actions.askArchie(p)}
        commands={commands}
      >
        <ShellActions base={actions}>
          <ShellLayout
            route={route}
            section={section}
            privacyMode={privacyMode}
            setPrivacyMode={setPrivacyMode}
            config={config}
            reloadConfig={reloadConfig}
            brokerMode={brokerMode}
            portfolio={portfolio}
            proposals={proposals}
            chat={chat}
            pendingPrompt={pendingPrompt}
            clearPendingPrompt={() => setPendingPrompt(null)}
            pageError={pageError}
            setPageError={setPageError}
          />
        </ShellActions>
      </InstrumentProvider>
      <TokenGate />
      </DisplayPrefsProvider>
    </PrivacyProvider>
  )
}

/** Completes the app actions with `openTicker`, which needs the instrument context. */
function ShellActions({ base, children }: { base: AppActions; children: React.ReactNode }) {
  const { openInstrument } = useInstrument()
  const value = useMemo<AppActions>(() => ({ ...base, openTicker: (t) => openInstrument(t) }), [base, openInstrument])
  return <AppActionsProvider value={value}>{children}</AppActionsProvider>
}

function useCommands(actions: AppActions, cyclePrivacy: () => void, reload: (o?: { live?: boolean; force?: boolean }) => void) {
  const { resolved, setPreference } = useTheme()
  return useMemo<SpotlightCommand[]>(() => {
    const go = (id: string, label: string, path: string, icon: SpotlightCommand["icon"], keywords?: string): SpotlightCommand => ({
      id,
      label,
      group: "Go to",
      icon,
      keywords,
      onSelect: () => navigate(path),
    })
    return [
      { id: "ask", label: "Ask Archie…", group: "Actions", icon: Sparkles, keywords: "chat question", onSelect: () => actions.askArchie("") },
      { id: "new-play", label: "New play", group: "Actions", icon: Plus, keywords: "theme bet basket", onSelect: () => navigate("/plays?new=1") },
      { id: "new-view", label: "New view", group: "Actions", icon: LayoutGrid, keywords: "page build", onSelect: () => navigate("/views/new") },
      { id: "refresh", label: "Refresh portfolio", group: "Actions", icon: RefreshCw, keywords: "sync reload", onSelect: () => reload({ live: true, force: true }) },
      { id: "privacy", label: "Cycle privacy mode", group: "Actions", icon: Eye, shortcut: "P", keywords: "blur scramble hide", onSelect: cyclePrivacy },
      {
        id: "theme",
        label: resolved === "dark" ? "Switch to light theme" : "Switch to dark theme",
        group: "Actions",
        icon: Moon,
        keywords: "theme appearance dark light",
        onSelect: () => setPreference(resolved === "dark" ? "light" : "dark"),
      },
      go("home", "Home", "/home", Home),
      go("archie", "Archie", "/archie", MessageSquare, "chat"),
      go("plays", "Plays", "/plays", Target, "themes watchlist"),
      go("holdings", "Holdings", "/home/holdings", Wallet, "positions allocation"),
      go("signals", "Signals", "/home/signals", Bell, "alerts attention"),
      go("proposals", "Proposals", "/activity/proposals", Activity, "intents approve"),
      go("orders", "Orders", "/activity/orders", ListOrdered, "broker"),
      go("automations", "Automations", "/activity/automations", CalendarClock, "scheduled jobs briefing reports"),
      go("settings", "Settings", "/settings/general", Settings, "keys broker risk"),
      go("help", "Help", "/settings/help", HelpCircle, "guide"),
    ]
  }, [actions, cyclePrivacy, reload, resolved, setPreference])
}

type LayoutProps = {
  route: Route
  section: string
  privacyMode: PrivacyMode
  setPrivacyMode: React.Dispatch<React.SetStateAction<PrivacyMode>>
  config: ReturnType<typeof useAppConfig>["config"]
  reloadConfig: () => Promise<void>
  brokerMode: ReturnType<typeof brokerModeOf>
  portfolio: ReturnType<typeof usePortfolio>
  proposals: ReturnType<typeof useProposals>
  chat: ReturnType<typeof useChatSessions>
  pendingPrompt: { text: string; send: boolean } | null
  clearPendingPrompt: () => void
  pageError: string | null
  setPageError: (m: string | null) => void
}

function ShellLayout(props: LayoutProps) {
  const { route, section, privacyMode, setPrivacyMode, config, brokerMode, portfolio, proposals, chat } = props
  const { openPalette } = useInstrument()
  const [collapsed, toggleCollapsed] = useNavCollapsed()
  const [mobileNav, setMobileNav] = useState(false)

  useEffect(() => {
    function onKeydown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault()
        navigate(currentSection() === "archie" ? "/home" : "/archie")
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget()) return
      if (e.key === "p" || e.key === "P") {
        e.preventDefault()
        setPrivacyMode((m) => nextPrivacyMode(m))
      } else if (e.key === "/") {
        e.preventDefault()
        toggleCollapsed()
      }
    }
    window.addEventListener("keydown", onKeydown)
    return () => window.removeEventListener("keydown", onKeydown)
  }, [setPrivacyMode, toggleCollapsed])

  const sidebarProps = {
    section,
    path: route.path,
    onNavigate: (p: string) => navigate(p),
    onOpenPalette: openPalette,
    pendingProposals: proposals.pending.length,
    leveragedEnabled: Boolean(config?.leveraged?.enabled),
    brokerMode,
    privacyMode,
    onCyclePrivacyMode: () => setPrivacyMode((m) => nextPrivacyMode(m)),
  }
  const isArchie = section === "archie"

  return (
    <div className="flex h-dvh overflow-hidden bg-background text-foreground">
      <AppSidebar {...sidebarProps} collapsed={collapsed} onToggleCollapsed={toggleCollapsed} />

      <Sheet open={mobileNav} onOpenChange={setMobileNav}>
        <SheetContent side="left" showCloseButton={false} className="flex w-64 max-w-[80vw] flex-col gap-4 bg-sidebar px-2.5 py-4">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarBody {...sidebarProps} onAfterNavigate={() => setMobileNav(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border px-3 py-2 md:hidden">
          <Button variant="ghost" size="icon" className="size-9" aria-label="Open navigation" onClick={() => setMobileNav(true)}>
            <Menu className="size-5" />
          </Button>
          <span className="text-sm font-semibold tracking-tight">{TITLES[section] ?? "OpenPF"}</span>
          <BrokerBadge mode={brokerMode} collapsed className="ml-1" />
          <Button variant="ghost" size="icon" className="ml-auto size-9" aria-label="Search" onClick={openPalette}>
            <Search className="size-4" />
          </Button>
        </header>

        <main className="relative min-h-0 flex-1">
          {/* Archie stays MOUNTED across navigation (just hidden) so an
              in-flight stream keeps rendering and isn't lost. */}
          <div className={cn("absolute inset-0", isArchie ? "block" : "hidden")}>
            <ArchiePage
              chat={chat}
              accountView={portfolio.accountView}
              currency={portfolio.currency}
              presentationMask={privacyMode !== "off"}
              pendingPrompt={props.pendingPrompt?.text || null}
              pendingPromptSend={props.pendingPrompt?.send ?? false}
              onPendingPromptConsumed={props.clearPendingPrompt}
              onError={props.setPageError}
            />
          </div>

          {!isArchie ? (
            <div id="page-scroll" className="h-full overflow-y-auto overscroll-contain">
              <div className="mx-auto w-full max-w-[1200px] px-4 pb-16 pt-5 sm:px-8 sm:pt-8">
                {props.pageError ? (
                  <div className="mb-6 flex items-start gap-3 rounded-lg border border-negative/30 bg-negative/5 px-3.5 py-2.5 text-sm">
                    <span className="min-w-0 flex-1 text-negative">{props.pageError}</span>
                    <button type="button" aria-label="Dismiss" onClick={() => props.setPageError(null)} className="text-muted-foreground hover:text-foreground">
                      <X className="size-4" />
                    </button>
                  </div>
                ) : null}
                <Page {...props} />
              </div>
            </div>
          ) : null}
        </main>
      </div>
    </div>
  )
}

function currentSection() {
  return window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0] || "home"
}

function Page(props: LayoutProps) {
  const { route, section, config, brokerMode, portfolio, proposals } = props
  const total = portfolio.snapshot?.account.total ?? 0
  switch (section) {
    case "home":
      return <HomePage route={route} portfolio={portfolio} proposals={proposals} config={config} brokerMode={brokerMode} />
    case "plays":
      return <PlaysPage route={route} />
    case "views":
      return <ViewsPage route={route} />
    case "activity":
      return (
        <ActivityPage
          route={route}
          proposals={proposals}
          config={config}
          brokerMode={brokerMode}
          positions={portfolio.positions}
          total={total}
          onError={props.setPageError}
        />
      )
    case "settings":
      return (
        <SettingsPage
          route={route}
          config={config}
          onConfigChanged={() => void props.reloadConfig()}
          onError={props.setPageError}
          privacyMode={props.privacyMode}
          onPrivacyModeChange={props.setPrivacyMode}
        />
      )
    case "labs":
      return <LeveragedWorkspace onError={props.setPageError} />
    default:
      return null
  }
}
