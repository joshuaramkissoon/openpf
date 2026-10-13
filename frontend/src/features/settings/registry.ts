import {
  Bell,
  Link2,
  type LucideIcon,
  Palette,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react"

/** The Settings pages, in nav order. Each is one user intent. */
export type SettingsPageId = "accounts" | "safety" | "archie" | "notifications" | "appearance" | "advanced" | "help"

export const SETTINGS_PAGES: { id: SettingsPageId; label: string; blurb: string; icon: LucideIcon }[] = [
  { id: "accounts", label: "Accounts", blurb: "Brokers and exchanges, read-only or trading.", icon: Link2 },
  { id: "safety", label: "Safety", blurb: "Order limits and what Archie may do on his own.", icon: ShieldCheck },
  { id: "archie", label: "Archie", blurb: "Models, data sources and what he costs.", icon: Sparkles },
  { id: "notifications", label: "Notifications", blurb: "Telegram alerts and how chatty they are.", icon: Bell },
  { id: "appearance", label: "Appearance & privacy", blurb: "Theme, privacy mode and logos.", icon: Palette },
  { id: "advanced", label: "Advanced", blurb: "Background jobs, diagnostics and Labs.", icon: SlidersHorizontal },
]

/**
 * Old `/settings/<tab>` routes and where they live now. Query params on the old
 * link (e.g. `?focus=max_daily_notional`, `?suggestion=…`) are carried over.
 */
export const LEGACY_ROUTES: Record<string, { page: SettingsPageId; params?: Record<string, string> }> = {
  general: { page: "appearance" },
  connections: { page: "accounts" },
  broker: { page: "accounts", params: { manage: "t212" } },
  crypto: { page: "accounts" },
  providers: { page: "archie", params: { section: "providers" } },
  models: { page: "archie" },
  usage: { page: "archie", params: { section: "usage" } },
  risk: { page: "safety" },
  permissions: { page: "safety", params: { section: "permissions" } },
  diagnostics: { page: "advanced", params: { section: "diagnostics" } },
  labs: { page: "advanced", params: { section: "labs" } },
}

/** Where an old or current settings link should land (null = already canonical). */
export function canonicalSettingsPath(segment: string | undefined, query: URLSearchParams): string | null {
  const legacy = segment ? LEGACY_ROUTES[segment] : undefined
  if (!legacy) return null
  const q = new URLSearchParams(query)
  for (const [k, v] of Object.entries(legacy.params ?? {})) if (!q.has(k)) q.set(k, v)
  const qs = q.toString()
  return `/settings/${legacy.page}${qs ? `?${qs}` : ""}`
}

/** Individual settings, registered in ⌘K so "telegram" or "daily limit" jumps straight there. */
export const SETTINGS_ENTRIES: { id: string; label: string; path: string; keywords: string }[] = [
  { id: "accounts", label: "Accounts & connections", path: "/settings/accounts", keywords: "connect broker exchange link add" },
  { id: "t212-keys", label: "Trading 212 keys", path: "/settings/accounts?manage=t212", keywords: "api key secret invest isa execution demo live environment" },
  { id: "add-kraken", label: "Connect Kraken", path: "/settings/accounts?add=kraken", keywords: "crypto exchange key trade buy" },
  { id: "add-binance", label: "Connect Binance", path: "/settings/accounts?add=binance", keywords: "crypto exchange key trade buy" },
  { id: "add-coinbase", label: "Connect Coinbase", path: "/settings/accounts?add=coinbase", keywords: "crypto exchange key trade" },
  { id: "add-ibkr", label: "Connect Interactive Brokers", path: "/settings/accounts?add=ibkr", keywords: "ibkr flex broker" },
  { id: "order-limit", label: "Order size limit", path: "/settings/safety?focus=max_single_order_notional", keywords: "risk rails max single order cap" },
  { id: "daily-limit", label: "Daily trading limit", path: "/settings/safety?focus=max_daily_notional", keywords: "risk rails max daily notional cap" },
  { id: "position-limit", label: "Largest position", path: "/settings/safety?focus=max_position_weight", keywords: "risk weight concentration" },
  { id: "crypto-cap", label: "Crypto cap", path: "/settings/safety?focus=max_crypto_pct", keywords: "crypto share book percent" },
  { id: "order-mode", label: "Paper or live orders", path: "/settings/safety?focus=broker_mode", keywords: "paper live trading mode real money" },
  { id: "permissions", label: "Standing permissions", path: "/settings/safety?section=permissions", keywords: "autonomy auto approve kill switch pause" },
  { id: "models", label: "Models", path: "/settings/archie", keywords: "claude opus sonnet haiku llm routing" },
  { id: "providers", label: "Data providers", path: "/settings/archie?section=providers", keywords: "api keys news macro fred finnhub gemini together" },
  { id: "usage", label: "Usage & cost", path: "/settings/archie?section=usage", keywords: "tokens spend cost billing" },
  { id: "telegram", label: "Telegram", path: "/settings/notifications", keywords: "alerts notifications bot chat" },
  { id: "theme", label: "Theme", path: "/settings/appearance", keywords: "dark light system appearance" },
  { id: "privacy", label: "Privacy mode", path: "/settings/appearance?focus=privacy", keywords: "blur scramble hide numbers screen share" },
  { id: "logos", label: "Company logos", path: "/settings/appearance?focus=logos", keywords: "icons images logos" },
  { id: "scheduler", label: "Background jobs", path: "/settings/advanced", keywords: "scheduler automation engine cron" },
  { id: "diagnostics", label: "Diagnostics", path: "/settings/advanced?section=diagnostics", keywords: "runtime mcp health debug" },
  { id: "labs", label: "Labs", path: "/settings/advanced?section=labs", keywords: "leveraged experimental" },
]
