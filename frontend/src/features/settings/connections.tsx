import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowUpRight,
  Eye,
  KeyRound,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react"
import { toast } from "sonner"

import {
  addConnection,
  getConnections,
  getProviders,
  reconnectConnection,
  removeConnection,
  snaptradeLink,
  syncConnection,
  type Connection,
  type ConnectionMode,
  type Provider,
  type ProviderCatalog,
} from "@/api/connections"
import { Money, Section, Segmented } from "@/components/kit"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { reloadAccounts } from "@/lib/accounts"
import { parseApiError } from "@/lib/api-error"
import { useAppActions } from "@/lib/app-actions"
import { reloadCryptoStatus } from "@/lib/crypto-status"
import { cn } from "@/lib/utils"

/* ── small helpers ─────────────────────────────────────────────────────── */

function ago(iso: string | null | undefined): string | null {
  if (!iso) return null
  const t = Date.parse(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`)
  if (!Number.isFinite(t)) return null
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

/** A quiet monogram tile — the provider's initials on a tint derived from its id. */
function Monogram({ id, label, className }: { id: string; label: string; className?: string }) {
  const hue = useMemo(() => [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7), [id])
  const initials = label
    .replace(/\(.*\)/, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-9 shrink-0 place-items-center rounded-lg font-mono text-[11px] font-semibold tracking-tight",
        className,
      )}
      style={{
        background: `oklch(0.62 0.09 ${hue} / 0.16)`,
        color: `oklch(0.58 0.11 ${hue})`,
        boxShadow: `inset 0 0 0 1px oklch(0.6 0.08 ${hue} / 0.22)`,
      }}
    >
      {initials || "?"}
    </span>
  )
}

function CapabilityPills({ c }: { c: Connection }) {
  const trades = c.capabilities.includes("trade")
  return (
    <span className="inline-flex items-center gap-1">
      <span className="rounded-full border border-border px-2 py-px text-[10px] font-medium text-muted-foreground">Reads</span>
      {trades ? (
        <span className="rounded-full border border-positive/30 bg-positive/5 px-2 py-px text-[10px] font-medium text-positive">
          Trades on approval
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-px text-[10px] font-medium text-muted-foreground">
          <Eye className="size-2.5" />
          Read-only
        </span>
      )}
    </span>
  )
}

function StatusLine({ c }: { c: Connection }) {
  const { state, message, last_sync } = c.status
  const when = ago(last_sync)
  const tone = state === "ok" ? "bg-positive" : state === "error" ? "bg-negative" : "bg-warning"
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      <span className={cn("size-1.5 shrink-0 rounded-full", tone, state === "pending" && "animate-pulse")} />
      <span className="truncate">
        {!c.enabled
          ? "Paused"
          : state === "error"
            ? message || "Last sync failed"
            : state === "pending" && !when
              ? message || "Waiting for the first sync"
              : when
                ? `Synced ${when}`
                : "Synced"}
        {state === "error" && when ? ` · last good sync ${when}` : ""}
      </span>
    </span>
  )
}

/* ── a connected row ───────────────────────────────────────────────────── */

function ConnectionRow({
  c,
  busy,
  onSync,
  onReconnect,
  onRemove,
  onLink,
}: {
  c: Connection
  busy: boolean
  onSync: () => void
  onReconnect: (mode: ConnectionMode) => void
  onRemove: () => void
  onLink: () => void
}) {
  const { navigate } = useAppActions()
  const total = c.accounts.reduce((s, a) => s + (a.value_gbp ?? 0), 0)
  const canUpgrade = c.can_trade_provider && c.mode === "read" && c.type === "crypto" && !c.sandbox
  return (
    <li className="group rounded-xl border border-border bg-card/40 transition-colors hover:border-foreground/15">
      <div className="flex flex-wrap items-start gap-3 p-4">
        <Monogram id={c.provider} label={c.label} />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="text-sm font-medium">{c.label}</p>
            <CapabilityPills c={c} />
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
            <StatusLine c={c} />
            {c.detail ? <span className="font-mono text-[11px] text-muted-foreground/80">{c.detail}</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {c.managed_in ? (
            <Button variant="outline" size="sm" onClick={() => navigate(c.managed_in!)}>
              <KeyRound className="size-3.5" />
              Manage keys
            </Button>
          ) : (
            <>
              {c.provider === "snaptrade" && !c.sandbox ? (
                <Button variant="ghost" size="sm" disabled={busy} onClick={onLink} title="Link another broker on SnapTrade">
                  <Link2 className="size-3.5" />
                  Link broker
                </Button>
              ) : null}
              {canUpgrade ? (
                <Button variant="outline" size="sm" disabled={busy} onClick={() => onReconnect("trade")}>
                  Enable trading
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => onReconnect(c.mode)}>
                Reconnect
              </Button>
            </>
          )}
          <Button variant="ghost" size="icon-sm" disabled={busy} onClick={onSync} aria-label={`Sync ${c.label} now`} title="Sync now">
            <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />
          </Button>
          {c.removable && !c.managed_in ? (
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={busy}
              onClick={onRemove}
              aria-label={`Remove ${c.label}`}
              title="Remove"
              className="text-muted-foreground hover:text-negative"
            >
              <Trash2 className="size-3.5" />
            </Button>
          ) : null}
        </div>
      </div>
      {c.accounts.length ? (
        <ul className="divide-y divide-border/70 border-t border-border/70">
          {c.accounts.map((a) => (
            <li key={a.account_kind} className="flex items-center gap-3 px-4 py-2 text-sm">
              <span className="w-9 shrink-0" />
              <span className="min-w-0 flex-1 truncate">
                {a.institution && a.institution !== c.label ? (
                  <span className="text-foreground">{a.institution} · </span>
                ) : null}
                <span className="text-muted-foreground">{a.name}</span>
              </span>
              <span className="hidden font-mono text-[11px] text-muted-foreground/70 sm:inline">{a.account_kind}</span>
              {a.value_gbp != null ? (
                <Money value={a.value_gbp} decimals={0} className="w-24 text-right text-sm tabular-nums" />
              ) : (
                <span className="w-24 text-right text-xs text-muted-foreground">—</span>
              )}
            </li>
          ))}
          {c.accounts.length > 1 ? (
            <li className="flex items-center gap-3 px-4 py-2 text-xs text-muted-foreground">
              <span className="w-9 shrink-0" />
              <span className="flex-1">Total</span>
              <Money value={total} decimals={0} className="w-24 text-right tabular-nums" />
            </li>
          ) : null}
        </ul>
      ) : null}
      {c.trade_note && c.mode === "read" ? (
        <p className="border-t border-border/70 px-4 py-2 text-xs text-muted-foreground">
          <span className="pl-12">{c.trade_note}</span>
        </p>
      ) : null}
    </li>
  )
}

/* ── the add / reconnect panel ─────────────────────────────────────────── */

function GuidePanel({
  provider,
  reconnect,
  initialMode,
  sandbox,
  onDone,
  onCancel,
}: {
  provider: Provider
  reconnect?: Connection | null
  initialMode: ConnectionMode
  sandbox: boolean
  onDone: (msg: string, link?: string | null) => void
  onCancel: () => void
}) {
  const [mode, setMode] = useState<ConnectionMode>(provider.modes.includes(initialMode) ? initialMode : "read")
  const [values, setValues] = useState<Record<string, string>>(() => ({ ...(sandbox ? provider.sandbox_values ?? {} : {}) }))
  const [working, setWorking] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [provider.id])

  const steps = mode === "trade" ? provider.trade_steps ?? [] : provider.read_steps
  const ready = provider.fields.every((f) => f.options || values[f.key]?.trim())
  const isT212 = provider.id === "t212"

  async function submit() {
    setWorking(true)
    setRefusal(null)
    try {
      const fields = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()]))
      const res = reconnect
        ? await reconnectConnection(reconnect.id, mode, fields)
        : await addConnection(provider.id, mode, fields)
      onDone(
        mode === "trade"
          ? `${provider.label} connected for trading — orders still need your approval`
          : `${provider.label} connected read-only`,
        res.link_url,
      )
    } catch (e) {
      setRefusal(parseApiError(e).message)
    } finally {
      setWorking(false)
    }
  }

  return (
    <div ref={ref} className="scroll-mt-6 overflow-hidden rounded-xl border border-foreground/15 bg-card/60">
      <div className="flex items-start gap-3 border-b border-border p-4">
        <Monogram id={provider.id} label={provider.label} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {reconnect ? `Reconnect ${provider.label}` : `Connect ${provider.label}`}
            <span className="font-normal text-muted-foreground"> · {provider.product}</span>
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">{provider.summary}</p>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onCancel} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <div className="grid gap-6 p-4 md:grid-cols-[1.1fr_1fr]">
        <div className="space-y-4">
          {provider.modes.length > 1 && !isT212 ? (
            <div className="space-y-2">
              <Segmented<ConnectionMode>
                ariaLabel="Connection type"
                value={mode}
                onChange={(m) => {
                  setMode(m)
                  setRefusal(null)
                }}
                options={[
                  { value: "read", label: "Read-only" },
                  { value: "trade", label: "Trade" },
                ]}
              />
              <p className="text-xs leading-relaxed text-muted-foreground">
                {mode === "read"
                  ? "Start here. OpenPF sees balances and holdings; nothing can be bought, sold or moved. You can add a trade key later."
                  : "A trade key lets OpenPF place the orders you approve. It must never be able to withdraw — OpenPF checks and refuses one that can."}
              </p>
            </div>
          ) : null}

          <div
            className={cn(
              "flex items-start gap-2.5 rounded-lg p-3 text-sm leading-relaxed",
              mode === "trade" ? "bg-warning/10" : "bg-positive/5",
            )}
          >
            {mode === "trade" ? (
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" />
            ) : (
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-positive" />
            )}
            <p>
              {mode === "trade" ? (
                <>
                  <span className="font-medium">Trade, never withdraw.</span> Tick only the permissions below. {provider.notes}
                </>
              ) : (
                <>
                  <span className="font-medium">Read-only key.</span> Tick only the permissions in these steps — anything
                  else stays off.
                </>
              )}
            </p>
          </div>

          <ol className="relative space-y-3 pl-8">
            <span aria-hidden className="absolute bottom-2 left-[11px] top-2 w-px bg-border" />
            {steps.map((s, i) => (
              <li key={s} className="relative text-sm leading-relaxed">
                <span className="absolute -left-8 top-0 grid size-[23px] place-items-center rounded-full border border-border bg-background font-mono text-[11px] text-muted-foreground">
                  {i + 1}
                </span>
                {s}
              </li>
            ))}
          </ol>
          {provider.key_url ? (
            <a
              href={provider.key_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 pl-8 text-xs text-primary hover:underline"
            >
              Open {provider.label} <ArrowUpRight className="size-3" />
            </a>
          ) : null}
          <p className="text-[11px] leading-relaxed text-muted-foreground">{provider.availability}</p>
        </div>

        {isT212 ? (
          <div className="space-y-3 self-start rounded-lg border border-dashed border-border p-4 text-sm">
            <p className="leading-relaxed text-muted-foreground">
              Trading 212 keys live under Broker &amp; keys, with a separate read key and IP-restricted execution key per account.
            </p>
            <ManageT212 />
          </div>
        ) : (
          <form
            className="space-y-3 self-start"
            onSubmit={(e) => {
              e.preventDefault()
              void submit()
            }}
          >
            {provider.fields.map((f) =>
              f.options ? (
                <div key={f.key} className="space-y-1.5">
                  <Label>{f.label}</Label>
                  <Segmented<string>
                    ariaLabel={f.label}
                    value={values[f.key] || f.options[0][0]}
                    onChange={(v) => setValues((cur) => ({ ...cur, [f.key]: v }))}
                    options={f.options.map(([value, label]) => ({ value, label }))}
                  />
                </div>
              ) : (
                <div key={f.key} className="space-y-1.5">
                  <Label htmlFor={`conn-${f.key}`}>{f.label}</Label>
                  {f.multiline ? (
                    <Textarea
                      id={`conn-${f.key}`}
                      spellCheck={false}
                      value={values[f.key] ?? ""}
                      onChange={(e) => setValues((cur) => ({ ...cur, [f.key]: e.target.value }))}
                      placeholder={f.placeholder}
                      className="min-h-24 font-mono text-xs"
                    />
                  ) : (
                    <Input
                      id={`conn-${f.key}`}
                      type={f.secret ? "password" : "text"}
                      autoComplete="off"
                      spellCheck={false}
                      value={values[f.key] ?? ""}
                      onChange={(e) => setValues((cur) => ({ ...cur, [f.key]: e.target.value }))}
                      placeholder={f.placeholder}
                      className="font-mono text-xs"
                    />
                  )}
                </div>
              ),
            )}
            <p className="text-xs leading-relaxed text-muted-foreground">
              Encrypted in this app's database, never shown again, never given to Archie.
              {sandbox ? " Sandbox: these are fake fixture keys — nothing real is contacted." : ""}
            </p>
            {refusal ? (
              <p role="alert" className="flex items-start gap-2 rounded-lg border border-negative/30 bg-negative/5 p-3 text-sm">
                <ShieldAlert className="mt-0.5 size-4 shrink-0 text-negative" />
                <span>{refusal}</span>
              </p>
            ) : null}
            <Button type="submit" size="sm" disabled={working || !ready}>
              {working ? <Loader2 className="size-3.5 animate-spin" /> : <KeyRound className="size-3.5" />}
              {working ? "Checking with " + provider.label + "…" : "Verify & connect"}
            </Button>
          </form>
        )}
      </div>
    </div>
  )
}

function ManageT212() {
  const { navigate } = useAppActions()
  return (
    <Button variant="outline" size="sm" onClick={() => navigate("/settings/broker")}>
      <KeyRound className="size-3.5" />
      Open Broker &amp; keys
    </Button>
  )
}

/* ── provider picker ───────────────────────────────────────────────────── */

function ProviderPicker({
  catalog,
  connected,
  picked,
  onPick,
}: {
  catalog: ProviderCatalog
  connected: Set<string>
  picked: string | null
  onPick: (p: Provider) => void
}) {
  return (
    <div className="grid gap-5 md:grid-cols-3">
      {catalog.groups.map((g) => {
        const items = catalog.providers.filter((p) => p.type === g.id)
        const missing = catalog.unavailable.filter((u) => u.type === g.id)
        return (
          <div key={g.id} className="min-w-0 space-y-1.5">
            <p className="px-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{g.label}</p>
            <ul className="space-y-1">
              {items.map((p) => {
                const isConnected = connected.has(p.id)
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => onPick(p)}
                      aria-pressed={picked === p.id}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-colors",
                        picked === p.id ? "border-foreground/40 bg-muted/60" : "border-transparent hover:bg-muted/50",
                      )}
                    >
                      <Monogram id={p.id} label={p.label} className="size-7 rounded-md text-[10px]" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm">{p.label}</span>
                        <span className="block truncate text-[11px] text-muted-foreground">
                          {p.modes.includes("trade") ? "Read · trade on approval" : "Read-only"}
                        </span>
                      </span>
                      {isConnected ? <span className="text-[10px] text-positive">Connected</span> : null}
                    </button>
                  </li>
                )
              })}
              {missing.map((u) => (
                <li key={u.id} className="rounded-lg px-2.5 py-2 opacity-70" title={u.why}>
                  <span className="flex items-center gap-2 text-sm text-muted-foreground">
                    {u.label}
                    <span className="rounded-full border border-border px-1.5 text-[10px]">not built in</span>
                  </span>
                  <span className="block text-[11px] leading-snug text-muted-foreground">{u.why}</span>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}

/* ── the page ──────────────────────────────────────────────────────────── */

/** Settings → Connections: every broker, exchange and aggregator in one list. */
export function ConnectionsSettings() {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null)
  const [connections, setConnections] = useState<Connection[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [picked, setPicked] = useState<{ provider: Provider; reconnect: Connection | null; mode: ConnectionMode } | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Connection | null>(null)

  const load = useCallback(async () => {
    try {
      const [cat, conns] = await Promise.all([getProviders(), getConnections()])
      setCatalog(cat)
      setConnections(conns.connections)
    } catch (e) {
      toast.error(parseApiError(e).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const afterChange = (list: Connection[]) => {
    setConnections(list)
    void reloadAccounts()
    void reloadCryptoStatus()
  }

  const providerById = (id: string) => catalog?.providers.find((p) => p.id === id)
  const connectedProviders = new Set((connections ?? []).map((c) => c.provider))
  const accountsCount = (connections ?? []).reduce((n, c) => n + c.accounts.length, 0)
  const readOnly = (connections ?? []).filter((c) => !c.capabilities.includes("trade")).length
  const total = (connections ?? []).reduce((s, c) => s + c.accounts.reduce((t, a) => t + (a.value_gbp ?? 0), 0), 0)

  async function run(id: string, fn: () => Promise<{ connections: Connection[] }>, ok?: string) {
    setBusy(id)
    try {
      const res = await fn()
      afterChange(res.connections)
      if (ok) toast.success(ok)
    } catch (e) {
      toast.error(parseApiError(e).message)
    } finally {
      setBusy(null)
    }
  }

  async function openLink(id: string) {
    setBusy(id)
    try {
      const url = await snaptradeLink(id)
      window.open(url, "_blank", "noopener,noreferrer")
    } catch (e) {
      toast.error(parseApiError(e).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-8">
      {connections ? (
        <p className="text-sm text-muted-foreground">
          {connections.length === 0 ? (
            "Nothing connected yet."
          ) : (
            <>
              <span className="text-foreground">{connections.length}</span> connection{connections.length === 1 ? "" : "s"} ·{" "}
              <span className="text-foreground">{accountsCount}</span> account{accountsCount === 1 ? "" : "s"} ·{" "}
              <Money value={total} decimals={0} className="text-foreground" /> in one book
              {readOnly ? ` · ${readOnly} read-only` : ""}
            </>
          )}
        </p>
      ) : null}

      <Section
        title="Connected"
        description="Holdings from every account flow into one book. Archie analyses all of them and can only trade where it says Trades on approval."
        action={
          <Button
            size="sm"
            variant={adding ? "ghost" : "default"}
            onClick={() => {
              setAdding((v) => !v)
              setPicked(null)
            }}
          >
            {adding ? <X className="size-3.5" /> : <Plus className="size-3.5" />}
            {adding ? "Close" : "Add connection"}
          </Button>
        }
      >
        {connections === null ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading connections…
          </div>
        ) : connections.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
            Connect your first account. Read-only keys are enough to see everything in one place.
          </div>
        ) : (
          <ul className="space-y-2.5">
            {connections.map((c) => (
              <ConnectionRow
                key={c.id}
                c={c}
                busy={busy === c.id}
                onSync={() => void run(c.id, () => syncConnection(c.id), `${c.label} synced`)}
                onReconnect={(mode) => {
                  const p = providerById(c.provider)
                  if (!p) return
                  setAdding(true)
                  setPicked({ provider: p, reconnect: c, mode })
                }}
                onRemove={() => setRemoving(c)}
                onLink={() => void openLink(c.id)}
              />
            ))}
          </ul>
        )}
      </Section>

      {adding && catalog ? (
        <Section title="Add a connection" description="Pick where your money is. Every provider starts read-only.">
          <div className="space-y-5">
            <ProviderPicker
              catalog={catalog}
              connected={connectedProviders}
              picked={picked?.provider.id ?? null}
              onPick={(p) => {
                const existing = (connections ?? []).find((c) => c.provider === p.id && c.type === "crypto")
                setPicked({ provider: p, reconnect: existing ?? null, mode: "read" })
              }}
            />
            {picked ? (
              <GuidePanel
                key={`${picked.provider.id}:${picked.reconnect?.id ?? "new"}:${picked.mode}`}
                provider={picked.provider}
                reconnect={picked.reconnect}
                initialMode={picked.mode}
                sandbox={catalog.sandbox}
                onCancel={() => setPicked(null)}
                onDone={(msg, link) => {
                  toast.success(msg)
                  setPicked(null)
                  setAdding(false)
                  void load()
                  void reloadAccounts()
                  void reloadCryptoStatus()
                  if (link) window.open(link, "_blank", "noopener,noreferrer")
                }}
              />
            ) : null}
          </div>
        </Section>
      ) : null}

      <Section title="How this stays safe">
        <ul className="grid gap-x-6 gap-y-2 text-sm leading-relaxed text-muted-foreground sm:grid-cols-2">
          <li>Keys are encrypted at rest, shown back only as the last few characters, and never reach Archie.</li>
          <li>Keys that can withdraw or transfer are refused — for every provider, in every mode.</li>
          <li>Read-only accounts can't execute: a suggestion there says “Place it in &lt;broker&gt;”.</li>
          <li>Every order, anywhere, still needs your approval (or a standing permission you set).</li>
        </ul>
      </Section>

      <AlertDialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {removing?.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              OpenPF deletes its stored key and stops syncing these accounts. Past snapshots stay in your history. Revoke the key
              at {removing?.label} too.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const c = removing
                setRemoving(null)
                if (c) void run(c.id, () => removeConnection(c.id), `${c.label} removed`)
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
