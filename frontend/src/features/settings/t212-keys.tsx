import { useEffect, useRef, useState } from "react"
import { KeyRound, Loader2, X } from "lucide-react"
import { toast } from "sonner"

import { updateAccountCredentials, updateBroker } from "@/api/client"
import { testExecutionKey } from "@/api/orders"
import { Segmented } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { parseApiError } from "@/lib/api-error"
import { cn } from "@/lib/utils"
import type { AccountCredentialView, AppConfig } from "@/types"
import { SettingRow, SettingsGroup, useSaver } from "./ui"

type Kind = "invest" | "stocks_isa"
const LABEL: Record<Kind, string> = { invest: "Invest", stocks_isa: "Stocks ISA" }

/** Trading 212 keys, inline under Accounts: a read key and an optional trade key per account. */
export function T212KeysPanel({
  config,
  onConfigChanged,
  onClose,
}: {
  config: AppConfig | null
  onConfigChanged: () => void
  onClose: () => void
}) {
  const env = useSaver()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => ref.current?.scrollIntoView({ block: "start", behavior: "smooth" }), [])
  const broker = config?.broker
  const sandbox = (broker?.t212_base_env as string | undefined) === "sandbox"

  return (
    <div ref={ref} className="scroll-mt-6 space-y-6 rounded-xl border border-foreground/15 bg-card/60 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Trading 212 keys</p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Each account takes a <span className="text-foreground">read key</span> (to see holdings) and, if you want OpenPF to place the
            orders you approve, a separate <span className="text-foreground">trade key</span> restricted to this machine's IP.
          </p>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close Trading 212 keys">
          <X className="size-4" />
        </Button>
      </div>

      <SettingsGroup>
        <SettingRow
          label="Environment"
          status={env.status}
          help={
            sandbox
              ? "Sandbox: a mock Trading 212 with fake money."
              : broker?.t212_base_env === "live"
                ? "Your real Trading 212 account."
                : "Trading 212's practice account (demo money)."
          }
          control={
            sandbox ? (
              <span className="rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 font-mono text-[11px] text-warning">
                sandbox
              </span>
            ) : (
              <Segmented<"demo" | "live">
                ariaLabel="Trading 212 environment"
                value={broker?.t212_base_env === "live" ? "live" : "demo"}
                onChange={(v) => broker && void env.run(() => updateBroker({ ...broker, t212_base_env: v })).then((ok) => ok && onConfigChanged())}
                options={[
                  { value: "demo", label: "Practice" },
                  { value: "live", label: "Real" },
                ]}
              />
            )
          }
        />
      </SettingsGroup>

      <div className="grid gap-6 lg:grid-cols-2">
        {(["invest", "stocks_isa"] as Kind[]).map((kind) => (
          <AccountKeys
            key={kind}
            kind={kind}
            view={config?.credentials?.[kind]}
            sandbox={sandbox}
            onSaved={onConfigChanged}
          />
        ))}
      </div>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Create keys in the Trading 212 app → Settings → API (Beta). Keys are stored on this machine and never reach Archie.
      </p>
    </div>
  )
}

function AccountKeys({
  kind,
  view,
  sandbox,
  onSaved,
}: {
  kind: Kind
  view: AccountCredentialView | undefined
  sandbox: boolean
  onSaved: () => void
}) {
  const sync = useSaver()
  const live = useSaver()
  const [testing, setTesting] = useState(false)
  const enabled = view?.enabled ?? true

  async function test() {
    setTesting(true)
    try {
      const res = await testExecutionKey(kind)
      if (res.test.result === "ok") toast.success(`${LABEL[kind]} trade key works`, { description: res.test.message ?? undefined })
      else if (res.test.result === "ip_restricted")
        toast.error(`${LABEL[kind]} trade key blocked`, {
          description: `${res.test.message ?? ""}${res.egress_ip ? ` This machine's IP: ${res.egress_ip}` : ""}`,
          duration: 12000,
        })
      else toast.error(`${LABEL[kind]} trade key: ${res.test.result}`, { description: res.test.message ?? undefined })
      onSaved()
    } catch (e) {
      toast.error(parseApiError(e).message || "Couldn't test the key")
    } finally {
      setTesting(false)
    }
  }

  return (
    <SettingsGroup title={LABEL[kind]}>
      <SettingRow
        label="Sync this account"
        htmlFor={`${kind}-sync`}
        status={sync.status}
        control={
          <Switch
            id={`${kind}-sync`}
            checked={enabled}
            disabled={!view}
            onCheckedChange={(v) =>
              void sync.run(() => updateAccountCredentials(kind, { enabled: Boolean(v) })).then((ok) => ok && onSaved())
            }
          />
        }
      />
      <KeyPairRow
        label="Read key"
        help="Account data, Portfolio, History and Metadata permissions. Leave Orders execute off."
        set={Boolean(view?.configured)}
        sandbox={sandbox}
        onSave={(key, secret) => updateAccountCredentials(kind, { t212_api_key: key, t212_api_secret: secret, enabled })}
        onSaved={onSaved}
      />
      <KeyPairRow
        label="Trade key"
        help="A second key with Orders execute ticked, restricted to this machine's IP. Only used for orders you approve."
        set={Boolean(view?.exec_configured)}
        sandbox={sandbox}
        onSave={(key, secret) => updateAccountCredentials(kind, { exec_api_key: key, exec_api_secret: secret, enabled })}
        onSaved={onSaved}
        extra={
          view?.exec_configured ? (
            <Button size="sm" variant="ghost" disabled={testing} onClick={() => void test()}>
              {testing ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Test
            </Button>
          ) : null
        }
      />
      {view?.exec_configured ? (
        <SettingRow
          label="Live orders on this account"
          htmlFor={`${kind}-exec`}
          status={live.status}
          help="Off pauses trading here without deleting the key."
          control={
            <Switch
              id={`${kind}-exec`}
              checked={view.exec_enabled}
              onCheckedChange={(v) =>
                void live.run(() => updateAccountCredentials(kind, { exec_enabled: Boolean(v), enabled })).then((ok) => ok && onSaved())
              }
            />
          }
        />
      ) : null}
    </SettingsGroup>
  )
}

function KeyPairRow({
  label,
  help,
  set,
  sandbox,
  onSave,
  onSaved,
  extra,
}: {
  label: string
  help: string
  set: boolean
  sandbox: boolean
  onSave: (key: string, secret: string) => Promise<unknown>
  onSaved: () => void
  extra?: React.ReactNode
}) {
  const saver = useSaver()
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState("")
  const [secret, setSecret] = useState("")

  return (
    <SettingRow
      label={label}
      help={help}
      status={saver.status}
      control={
        <>
          <span className={cn("text-xs", set ? "text-positive" : "text-muted-foreground")}>{set ? "Saved" : "Not set"}</span>
          {extra}
          {!sandbox ? (
            <Button size="sm" variant="outline" onClick={() => setOpen((o) => !o)}>
              <KeyRound className="size-3.5" />
              {set ? "Replace" : "Add"}
            </Button>
          ) : null}
        </>
      }
    >
      {open ? (
        <form
          className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]"
          onSubmit={async (e) => {
            e.preventDefault()
            if (!key.trim() || !secret.trim()) return saver.fail("Paste both the key and the secret.")
            if (await saver.run(() => onSave(key.trim(), secret.trim()))) {
              setKey("")
              setSecret("")
              setOpen(false)
              onSaved()
            }
          }}
        >
          <Input type="password" autoComplete="off" aria-label={`${label} — API key`} placeholder="API key" value={key} onChange={(e) => setKey(e.target.value)} className="h-8 font-mono text-xs" />
          <Input type="password" autoComplete="off" aria-label={`${label} — secret`} placeholder="Secret" value={secret} onChange={(e) => setSecret(e.target.value)} className="h-8 font-mono text-xs" />
          <Button type="submit" size="sm">
            Save
          </Button>
        </form>
      ) : null}
    </SettingRow>
  )
}
