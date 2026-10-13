import { useState } from "react"
import { FlaskConical } from "lucide-react"
import { toast } from "sonner"

import { patchLeveragedPolicy } from "@/api/client"
import { CostsWorkspace } from "@/components/CostsWorkspace"
import { RuntimeDiagnosticsPanel } from "@/components/RuntimeDiagnosticsPanel"
import { SettingsPanel, type SettingsSection } from "@/components/SettingsPanel"
import { HelpGuide } from "@/components/help/help-guide"
import { PermissionsSettings } from "@/features/autonomy/permissions"
import { CryptoSettings } from "./crypto"
import { ConnectionsSettings } from "./connections"
import { PageHeader, Section, Segmented, TabNav } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { useAppActions } from "@/lib/app-actions"
import type { PrivacyMode } from "@/lib/privacy"
import type { Route } from "@/lib/router"
import { useTheme, type ThemePreference } from "@/lib/theme"
import { cn } from "@/lib/utils"
import type { AppConfig } from "@/types"
import { DataProvidersSection, ModelsSection } from "./models"
import { LogosSetting } from "./logos-setting"

type Tab = "general" | "connections" | "broker" | "crypto" | "providers" | "models" | "risk" | "permissions" | "notifications" | "usage" | "diagnostics" | "labs" | "help"

const TABS: { value: Tab; label: string; description: string }[] = [
  { value: "general", label: "General", description: "Theme, logos and privacy." },
  { value: "connections", label: "Connections", description: "Brokers, crypto exchanges and aggregators — read-only first, trading only where you add a trade key." },
  { value: "broker", label: "Broker & keys", description: "Execution mode, Trading 212 environment and API keys." },
  { value: "crypto", label: "Crypto", description: "The crypto cap, fees and UK notes. Exchanges are connected under Connections." },
  { value: "providers", label: "Data providers", description: "API keys for news, macro data and the fast model. Saved keys are never shown again." },
  { value: "models", label: "Models", description: "Which model does which job. Defaults are tuned for cost and speed." },
  { value: "risk", label: "Risk rails", description: "Hard limits checked on every order." },
  { value: "permissions", label: "Permissions", description: "Where Archie may act on his own, and how far." },
  { value: "notifications", label: "Notifications", description: "Telegram alerts and commands." },
  { value: "usage", label: "Usage", description: "Estimated model usage on your Claude account." },
  { value: "diagnostics", label: "Diagnostics", description: "Runtime, MCP servers and capabilities." },
  { value: "labs", label: "Labs", description: "Experimental features. Off by default." },
  { value: "help", label: "Help", description: "What OpenPF does and how to use it." },
]

const PANEL_SECTIONS: Partial<Record<Tab, SettingsSection[]>> = {
  general: ["privacy"],
  broker: ["broker", "credentials"],
  risk: ["risk"],
  notifications: ["telegram"],
}

/** `/settings/:tab` — a full page with a left tab list. */
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
  const { navigate } = useAppActions()
  const tab: Tab = TABS.some((t) => t.value === route.segments[1]) ? (route.segments[1] as Tab) : "general"
  const meta = TABS.find((t) => t.value === tab)!
  const sections = PANEL_SECTIONS[tab]

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" />
      <TabNav className="md:hidden" value={tab} onChange={(t) => navigate(`/settings/${t}`)} tabs={TABS} />
      <div className="flex gap-10">
        <nav className="hidden w-44 shrink-0 flex-col gap-0.5 md:flex" aria-label="Settings sections">
          {TABS.map((t) => (
            <button
              key={t.value}
              type="button"
              onClick={() => navigate(`/settings/${t.value}`)}
              aria-current={tab === t.value ? "page" : undefined}
              className={cn(
                "rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors",
                tab === t.value
                  ? "bg-muted font-medium text-foreground"
                  : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="min-w-0 max-w-3xl flex-1 space-y-6">
          <div>
            <h2 className="text-base font-semibold tracking-tight">{meta.label}</h2>
            <p className="text-sm text-muted-foreground">{meta.description}</p>
          </div>

          {tab === "general" ? <AppearanceSection /> : null}
          {tab === "general" ? (
            <Section title="Logos">
              <LogosSetting />
            </Section>
          ) : null}
          {tab === "connections" ? <ConnectionsSettings /> : null}
          {tab === "crypto" ? <CryptoSettings /> : null}
          {sections ? (
            <SettingsPanel
              config={config}
              onReload={onConfigChanged}
              onError={(m) => onError(m)}
              hideHeader
              sections={sections}
              focus={tab === "risk" ? route.query.get("focus") : null}
              privacyMode={privacyMode}
              onPrivacyModeChange={onPrivacyModeChange}
            />
          ) : null}
          {tab === "permissions" ? <PermissionsSettings suggestionId={route.query.get("suggestion")} /> : null}
          {tab === "providers" ? <DataProvidersSection /> : null}
          {tab === "models" ? <ModelsSection /> : null}
          {tab === "usage" ? <CostsWorkspace onError={onError} /> : null}
          {tab === "diagnostics" ? <RuntimeDiagnosticsPanel onError={onError} /> : null}
          {tab === "labs" ? <LabsSection config={config} onConfigChanged={onConfigChanged} /> : null}
          {tab === "help" ? <HelpGuide /> : null}
        </div>
      </div>
    </div>
  )
}

function AppearanceSection() {
  const { preference, setPreference } = useTheme()
  return (
    <Section title="Theme">
      <div className="flex items-center justify-between gap-4 rounded-xl border border-border px-4 py-3">
        <p className="text-sm text-muted-foreground">System follows your OS setting.</p>
        <Segmented<ThemePreference>
          ariaLabel="Theme"
          value={preference}
          onChange={setPreference}
          options={[
            { value: "system", label: "System" },
            { value: "dark", label: "Dark" },
            { value: "light", label: "Light" },
          ]}
        />
      </div>
    </Section>
  )
}

function LabsSection({ config, onConfigChanged }: { config: AppConfig | null; onConfigChanged: () => void }) {
  const { navigate } = useAppActions()
  const [saving, setSaving] = useState(false)
  const enabled = Boolean(config?.leveraged?.enabled)

  async function toggle(next: boolean) {
    setSaving(true)
    try {
      await patchLeveragedPolicy({ enabled: next })
      toast.success(next ? "Leveraged desk enabled" : "Leveraged desk disabled")
      onConfigChanged()
    } catch {
      toast.error("Couldn't update the leveraged desk")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl border border-border">
      <div className="flex items-start justify-between gap-4 p-4">
        <div className="flex min-w-0 gap-3">
          <FlaskConical className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-medium">Leveraged desk</p>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Regime-aware short-term trades in 3x ISA ETPs, under hard daily rails (size, exposure, take-profit,
              stop-loss, close time). High risk — leveraged products can lose value fast. Adds a Labs entry to the
              sidebar.
            </p>
          </div>
        </div>
        <Switch checked={enabled} disabled={saving || !config} onCheckedChange={(v) => void toggle(Boolean(v))} />
      </div>
      {enabled ? (
        <div className="flex justify-end border-t border-border px-4 py-3">
          <Button variant="outline" size="sm" onClick={() => navigate("/labs/leveraged")}>
            Open desk and rails →
          </Button>
        </div>
      ) : null}
    </div>
  )
}
