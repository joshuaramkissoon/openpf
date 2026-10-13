import { useEffect, useRef } from "react"
import { FlaskConical } from "lucide-react"

import { patchLeveragedPolicy, setLeveragedAutoExecute, updateBroker } from "@/api/client"
import { RuntimeDiagnosticsPanel } from "@/components/RuntimeDiagnosticsPanel"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { useAppActions } from "@/lib/app-actions"
import type { AppConfig } from "@/types"
import { Disclosure, SettingRow, SettingsGroup, useSaver } from "./ui"

/** Settings → Advanced: background jobs, diagnostics and Labs (off by default). */
export function AdvancedSettings({
  config,
  onConfigChanged,
  section,
  onError,
}: {
  config: AppConfig | null
  onConfigChanged: () => void
  section: string | null
  onError: (m: string | null) => void
}) {
  const { navigate } = useAppActions()
  const scheduler = useSaver()
  const desk = useSaver()
  const auto = useSaver()
  const labsRef = useRef<HTMLDivElement>(null)
  const diagRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = section === "labs" ? labsRef.current : section === "diagnostics" ? diagRef.current : null
    el?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [section])

  const broker = config?.broker
  const leveraged = config?.leveraged
  const deskOn = Boolean(leveraged?.enabled)

  async function run(saver: ReturnType<typeof useSaver>, fn: () => Promise<unknown>) {
    if (await saver.run(fn)) onConfigChanged()
  }

  return (
    <div className="space-y-10">
      <SettingsGroup title="Background">
        <SettingRow
          label="Run scheduled jobs"
          htmlFor="scheduler_enabled"
          help="Morning briefings, monitors and your recipes' schedules. Off means nothing runs until you ask."
          status={scheduler.status}
          control={
            <Switch
              id="scheduler_enabled"
              checked={Boolean(broker?.scheduler_enabled)}
              disabled={!broker}
              onCheckedChange={(v) => broker && void run(scheduler, () => updateBroker({ ...broker, scheduler_enabled: Boolean(v) }))}
            />
          }
        />
      </SettingsGroup>

      <div ref={labsRef} id="labs" className="scroll-mt-20">
        <SettingsGroup title="Labs" description="Experimental. Off by default, and not part of the everyday app.">
          <SettingRow
            label={
              <span className="inline-flex items-center gap-1.5">
                <FlaskConical className="size-3.5 text-muted-foreground" />
                Leveraged desk
              </span>
            }
            htmlFor="leveraged_enabled"
            status={desk.status}
            help="Short-term trades in 3x ETPs under hard daily rails. High risk: leveraged products can lose value fast. Adds a Labs entry to the sidebar."
            control={
              <Switch
                id="leveraged_enabled"
                checked={deskOn}
                disabled={!config}
                onCheckedChange={(v) => void run(desk, () => patchLeveragedPolicy({ enabled: Boolean(v) }))}
              />
            }
          />
          {deskOn ? (
            <>
              <SettingRow
                label="Let the desk place its own trades"
                htmlFor="auto_execute_enabled"
                status={auto.status}
                help="Within its rails only, and only in Live mode with a trade key. Off means it proposes like Archie."
                control={
                  <Switch
                    id="auto_execute_enabled"
                    checked={Boolean(leveraged?.auto_execute_enabled)}
                    onCheckedChange={(v) => void run(auto, () => setLeveragedAutoExecute(Boolean(v)))}
                  />
                }
              />
              <div className="flex justify-end px-4 py-3">
                <Button variant="outline" size="sm" onClick={() => navigate("/labs/leveraged")}>
                  Open desk and rails →
                </Button>
              </div>
            </>
          ) : null}
        </SettingsGroup>
      </div>

      <div ref={diagRef} id="diagnostics" className="scroll-mt-20 space-y-3">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">Diagnostics</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">Runtime, tool servers and capabilities — for when something looks off.</p>
        </div>
        <Disclosure label="Show diagnostics" defaultOpen={section === "diagnostics"}>
          <RuntimeDiagnosticsPanel onError={onError} />
        </Disclosure>
      </div>
    </div>
  )
}
