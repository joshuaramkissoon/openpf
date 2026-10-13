import { useEffect } from "react"

import { Segmented } from "@/components/kit"
import { privacyModeDescription, type PrivacyMode } from "@/lib/privacy"
import { useTheme, type ThemePreference } from "@/lib/theme"
import { SettingRow, SettingsGroup } from "./ui"

/** Settings → Appearance & privacy: theme, privacy mode and company logos. */
export function AppearanceSettings({
  privacyMode,
  onPrivacyModeChange,
  focus,
}: {
  privacyMode: PrivacyMode
  onPrivacyModeChange: (m: PrivacyMode) => void
  focus: string | null
}) {
  const { preference, setPreference } = useTheme()
  useEffect(() => {
    if (focus) document.getElementById(`setting-${focus}`)?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [focus])

  return (
    <div className="space-y-10">
      <SettingsGroup title="Appearance">
        <SettingRow
          label="Theme"
          help="System follows your device."
          control={
            <Segmented<ThemePreference>
              ariaLabel="Theme"
              value={preference}
              onChange={setPreference}
              options={[
                { value: "system", label: "System" },
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark" },
              ]}
            />
          }
        />
      </SettingsGroup>

      <div id="setting-logos" className="-mt-6 scroll-mt-24 empty:hidden">
        {/* LOGOS-SETTING-SLOT: render <LogosSetting /> (features/settings/logos-setting.tsx, built on the
            logos branch — a self-contained card) right here. Wire it up at merge. */}
      </div>

      <SettingsGroup title="Privacy" description="For screen sharing and working in public. Press P anywhere to cycle.">
        <div id="setting-privacy" className="scroll-mt-24">
          <SettingRow
            label="Privacy mode"
            help={privacyModeDescription(privacyMode)}
            highlight={focus === "privacy"}
            control={
              <Segmented<PrivacyMode>
                ariaLabel="Privacy mode"
                value={privacyMode}
                onChange={onPrivacyModeChange}
                options={[
                  { value: "off", label: "Off" },
                  { value: "blur", label: "Blur" },
                  { value: "scramble", label: "Scramble" },
                ]}
              />
            }
          />
        </div>
      </SettingsGroup>
    </div>
  )
}
