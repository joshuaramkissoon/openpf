import { useEffect, useMemo, useRef, useState } from "react"
import { TriangleAlert } from "lucide-react"

import { updateBroker, updateRisk } from "@/api/client"
import { Segmented } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { PermissionsSettings } from "@/features/autonomy/permissions"
import type { AppConfig, RiskConfig } from "@/types"
import { AutoNumberField, Disclosure, SettingRow, SettingsGroup, useSaver } from "./ui"

const MAX_NOTIONAL = 10_000_000 // mirrors backend schemas/config.py

const gbp = (v: number) => `£${Math.round(v).toLocaleString("en-GB")}`
const pct = (v: number) => `${+(v * 100).toFixed(2)}%`

const DEFAULT_RISK: RiskConfig = {
  max_single_order_notional: 500,
  max_daily_notional: 1500,
  max_position_weight: 0.25,
  duplicate_order_window_seconds: 90,
  max_crypto_pct_of_book: 0.05,
}

function money(raw: string): number | string {
  const v = Number(raw.replace(/[£,\s]/g, ""))
  if (raw.trim() === "" || !Number.isFinite(v) || v < 0 || v > MAX_NOTIONAL) return `Enter an amount from £0 to ${gbp(MAX_NOTIONAL)}.`
  return v
}

function percent(raw: string): number | string {
  const v = Number(raw.replace(/[%\s]/g, ""))
  if (raw.trim() === "" || !Number.isFinite(v) || v < 0 || v > 100) return "Enter a percentage from 0 to 100."
  return Math.round(v * 100) / 10_000
}

/** Settings → Safety: the hard limits on every order, paper vs live, and standing permissions. */
export function SafetySettings({
  config,
  onConfigChanged,
  focus,
  section,
  suggestionId,
}: {
  config: AppConfig | null
  onConfigChanged: () => void
  focus: string | null
  section: string | null
  suggestionId: string | null
}) {
  const risk: RiskConfig = { ...DEFAULT_RISK, ...(config?.risk ?? {}) }
  const spot = useMemo(() => new Set((focus ?? "").split(",").filter(Boolean)), [focus])
  const saver = useSaver()
  const [savingField, setSavingField] = useState<string | null>(null)
  const permsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const first = (focus ?? "").split(",").find(Boolean)
    if (!first) return
    const t = window.setTimeout(() => {
      const el = document.getElementById(first)
      el?.scrollIntoView({ block: "center", behavior: "smooth" })
      ;(el as HTMLInputElement | null)?.focus?.({ preventScroll: true })
    }, 80)
    return () => window.clearTimeout(t)
  }, [focus, Boolean(config)])
  useEffect(() => {
    if (section === "permissions" || suggestionId) permsRef.current?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [section, suggestionId])

  async function save(field: keyof RiskConfig, value: number) {
    const next = { ...risk, [field]: value }
    if (next.max_daily_notional < next.max_single_order_notional) {
      saver.fail("The daily limit can't be lower than the per-order limit.")
      setSavingField(field)
      return
    }
    setSavingField(field)
    if (await saver.run(() => updateRisk(next))) onConfigChanged()
  }
  const statusFor = (field: string) => (savingField === field ? saver.status : undefined)

  return (
    <div className="space-y-10">
      <LimitsSentence risk={risk} />

      <SettingsGroup title="Limits" description="Checked on every order — ones you approve, and ones a permission places. Only you can change them.">
        <SettingRow
          label="Per order"
          htmlFor="max_single_order_notional"
          help="The most a single order can be. A bigger proposal is held back for you to resize."
          status={statusFor("max_single_order_notional")}
          highlight={spot.has("max_single_order_notional")}
          control={
            <AutoNumberField
              id="max_single_order_notional"
              prefix="£"
              className="w-32"
              value={risk.max_single_order_notional}
              parse={money}
              onCommit={(v) => void save("max_single_order_notional", v)}
            />
          }
        />
        <SettingRow
          label="Per day"
          htmlFor="max_daily_notional"
          help="Everything placed in one day, added up. Resets at midnight UK time."
          status={statusFor("max_daily_notional")}
          highlight={spot.has("max_daily_notional")}
          control={
            <AutoNumberField
              id="max_daily_notional"
              prefix="£"
              className="w-32"
              value={risk.max_daily_notional}
              parse={money}
              onCommit={(v) => void save("max_daily_notional", v)}
            />
          }
        />
        <SettingRow
          label="Largest holding"
          htmlFor="max_position_weight"
          help="No buy may take one holding above this share of your book."
          status={statusFor("max_position_weight")}
          highlight={spot.has("max_position_weight")}
          control={
            <AutoNumberField
              id="max_position_weight"
              suffix="%"
              className="w-32"
              value={+(risk.max_position_weight * 100).toFixed(2)}
              parse={percent}
              onCommit={(v) => void save("max_position_weight", v)}
            />
          }
        />
        <SettingRow
          label="Crypto cap"
          htmlFor="max_crypto_pct"
          help="Crypto buys that would take crypto above this share of your whole book are refused."
          status={statusFor("max_crypto_pct_of_book")}
          highlight={spot.has("max_crypto_pct")}
          control={
            <AutoNumberField
              id="max_crypto_pct"
              suffix="%"
              className="w-32"
              value={+((risk.max_crypto_pct_of_book ?? 0.05) * 100).toFixed(2)}
              parse={percent}
              onCommit={(v) => void save("max_crypto_pct_of_book", v)}
            />
          }
        />
      </SettingsGroup>

      <Disclosure label="More limits" hint="duplicate-order guard" defaultOpen={spot.has("duplicate_order_window_seconds")}>
        <SettingsGroup>
          <SettingRow
            label="Duplicate guard"
            htmlFor="duplicate_order_window_seconds"
            help="The same order twice inside this window is blocked — it catches double clicks and repeated alerts."
            status={statusFor("duplicate_order_window_seconds")}
            highlight={spot.has("duplicate_order_window_seconds")}
            control={
              <AutoNumberField
                id="duplicate_order_window_seconds"
                suffix="sec"
                className="w-32"
                value={risk.duplicate_order_window_seconds}
                parse={(raw) => {
                  const v = Number(raw.trim())
                  return Number.isInteger(v) && v >= 15 && v <= 86_400 ? v : "Whole seconds from 15 to 86400."
                }}
                onCommit={(v) => void save("duplicate_order_window_seconds", v)}
              />
            }
          />
        </SettingsGroup>
      </Disclosure>

      <OrderMode config={config} onConfigChanged={onConfigChanged} highlight={spot.has("broker_mode")} />

      <div ref={permsRef} id="permissions" className="scroll-mt-20 space-y-4">
        <h3 className="text-sm font-semibold tracking-tight">Standing permissions</h3>
        <PermissionsSettings suggestionId={suggestionId} />
      </div>
    </div>
  )
}

/** The limits in one plain sentence, so the numbers mean something. */
function LimitsSentence({ risk }: { risk: RiskConfig }) {
  const n = (s: string) => <span className="font-mono font-medium tabular-nums text-foreground">{s}</span>
  return (
    <div className="rounded-xl border border-border bg-muted/30 px-5 py-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">In plain words</p>
      <p className="mt-1.5 text-[15px] leading-relaxed text-muted-foreground">
        Archie can propose up to {n(gbp(risk.max_single_order_notional))} per order and {n(gbp(risk.max_daily_notional))} a
        day in total. No holding grows past {n(pct(risk.max_position_weight))} of your book, and crypto stays under{" "}
        {n(pct(risk.max_crypto_pct_of_book ?? 0.05))}. Nothing is placed without your approval unless you grant a permission
        below.
      </p>
    </div>
  )
}

function OrderMode({ config, onConfigChanged, highlight }: { config: AppConfig | null; onConfigChanged: () => void; highlight: boolean }) {
  const saver = useSaver()
  const [confirmLive, setConfirmLive] = useState(false)
  const broker = config?.broker
  const mode = broker?.broker_mode ?? "paper"
  const sandbox = (broker?.t212_base_env as string | undefined) === "sandbox"

  async function set(next: "paper" | "live") {
    if (!broker) return
    setConfirmLive(false)
    if (await saver.run(() => updateBroker({ ...broker, broker_mode: next }))) onConfigChanged()
  }

  return (
    <SettingsGroup title="Orders">
      <SettingRow
        label="When you approve an order"
        help={
          mode === "live"
            ? sandbox
              ? "Live: approved orders go to the sandbox broker — fake money, real flow."
              : "Live: approved orders are sent to your broker with real money."
            : "Paper: approved orders fill in a simulation. Nothing reaches your broker."
        }
        status={saver.status}
        highlight={highlight}
        control={
          <div id="broker_mode" tabIndex={-1} className="outline-none">
            <Segmented<"paper" | "live">
              ariaLabel="Order mode"
              value={confirmLive ? "live" : mode}
              onChange={(v) => (v === "live" && mode !== "live" && !sandbox ? setConfirmLive(true) : void set(v))}
              options={[
                { value: "paper", label: "Paper" },
                { value: "live", label: "Live" },
              ]}
            />
          </div>
        }
      >
        {confirmLive ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2.5 text-sm">
            <TriangleAlert className="size-4 shrink-0 text-warning" />
            <p className="min-w-0 flex-1">Approved orders will use real money at your broker.</p>
            <div className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={() => setConfirmLive(false)}>
                Stay on paper
              </Button>
              <Button size="sm" onClick={() => void set("live")}>
                Go live
              </Button>
            </div>
          </div>
        ) : null}
      </SettingRow>
      {mode === "live" && !sandbox ? (
        <p className="px-4 py-2.5 text-xs text-muted-foreground">
          Trading also needs a trade key on the account (Accounts). Read-only accounts only ever get suggestions.
        </p>
      ) : null}
    </SettingsGroup>
  )
}
