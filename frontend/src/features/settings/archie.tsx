import { useEffect, useRef } from "react"

import { CostsWorkspace } from "@/components/CostsWorkspace"
import { DataProvidersSection, ModelsSection } from "./models"
import { Disclosure } from "./ui"

/** Settings → Archie: which model does what, where his data comes from, and what he costs. */
export function ArchieSettings({ section, onError }: { section: string | null; onError: (m: string | null) => void }) {
  const providersRef = useRef<HTMLDivElement>(null)
  const usageRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = section === "providers" ? providersRef.current : section === "usage" ? usageRef.current : null
    el?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [section])

  return (
    <div className="space-y-10">
      <ModelsSection />

      <div ref={providersRef} className="scroll-mt-20 space-y-3">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">Data providers</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            Optional API keys for news, macro data and quick jobs. Archie works without them; each one adds a source. Saved keys
            are never shown again.
          </p>
        </div>
        <Disclosure label="API keys" hint="news, macro, fast models" defaultOpen={section === "providers"}>
          <DataProvidersSection />
        </Disclosure>
      </div>

      <div ref={usageRef} className="scroll-mt-20 space-y-3">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">Usage</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">Estimated model usage on your Claude account.</p>
        </div>
        <Disclosure label="Show usage" defaultOpen={section === "usage"}>
          <CostsWorkspace onError={onError} />
        </Disclosure>
      </div>
    </div>
  )
}
