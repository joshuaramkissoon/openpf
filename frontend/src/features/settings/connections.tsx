import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowLeftRight,
  ArrowUpRight,
  Check,
  Eye,
  Globe,
  KeyRound,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
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
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { reloadAccounts } from "@/lib/accounts"
import { parseApiError } from "@/lib/api-error"
import { reloadCryptoStatus } from "@/lib/crypto-status"
import { navigate } from "@/lib/router"
import { cn } from "@/lib/utils"
import type { AppConfig } from "@/types"
import { T212KeysPanel } from "./t212-keys"
import { Disclosure } from "./ui"

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
        <span className="whitespace-nowrap rounded-full border border-positive/30 bg-positive/5 px-2 py-px text-[10px] font-medium text-positive">
          Trades on approval
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-dashed border-border px-2 py-px text-[10px] font-medium text-muted-foreground">
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
  upgradeNote,
  onManage,
  onSync,
  onReconnect,
  onRemove,
  onLink,
}: {
  c: Connection
  busy: boolean
  upgradeNote: string | null
  onManage: () => void
  onSync: () => void
  onReconnect: (mode: ConnectionMode) => void
  onRemove: () => void
  onLink: () => void
}) {
  const total = c.accounts.reduce((s, a) => s + (a.value_gbp ?? 0), 0)
  const readOnlyCrypto = c.can_trade_provider && c.mode === "read" && c.type === "crypto"
  const canUpgrade = readOnlyCrypto && !c.sandbox
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
            <Button variant="outline" size="sm" onClick={onManage}>
              <KeyRound className="size-3.5" />
              Keys
            </Button>
          ) : (
            <>
              {c.provider === "snaptrade" && !c.sandbox ? (
                <Button variant="ghost" size="sm" disabled={busy} onClick={onLink} title="Link another broker on SnapTrade">
                  <Link2 className="size-3.5" />
                  Link broker
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
      {readOnlyCrypto ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border/70 bg-muted/30 px-4 py-3">
          <p className="min-w-0 flex-1 text-xs leading-relaxed text-muted-foreground sm:pl-12">
            <span className="font-medium text-foreground">Read-only.</span>{" "}
            {c.sandbox
              ? "Archie can suggest trades here but can't place them. In the sandbox only the mock Kraken account trades."
              : `Archie can suggest trades here but can't buy or sell. Add a trade key to let him place the orders you approve. ${upgradeNote ?? ""}`}
          </p>
          {canUpgrade ? (
            <Button size="sm" disabled={busy} onClick={() => onReconnect("trade")}>
              <ArrowLeftRight className="size-3.5" />
              Enable trading
            </Button>
          ) : null}
        </div>
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

const MODE_COPY: Record<ConnectionMode, { title: string; body: string; icon: typeof Eye }> = {
  read: {
    title: "Read only",
    body: "OpenPF sees balances and holdings. Archie can suggest trades; you place them on the exchange.",
    icon: Eye,
  },
  trade: {
    title: "Read + trade",
    body: "Archie can buy and sell here — only the orders you approve, inside your limits. The key can never withdraw.",
    icon: ArrowLeftRight,
  },
}

/** Read only vs Read + trade, as two equal, explicit choices. */
function ModeChoice({ value, onChange }: { value: ConnectionMode; onChange: (m: ConnectionMode) => void }) {
  return (
    <div role="radiogroup" aria-label="What OpenPF can do with this account" className="grid gap-2 sm:grid-cols-2">
      {(["read", "trade"] as ConnectionMode[]).map((m) => {
        const { title, body, icon: Icon } = MODE_COPY[m]
        const on = value === m
        return (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(m)}
            className={cn(
              "flex items-start gap-3 rounded-lg border p-3 text-left transition-colors",
              on ? "border-foreground/40 bg-muted/60" : "border-border hover:border-foreground/20 hover:bg-muted/30",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border",
                on ? "border-foreground" : "border-muted-foreground/50",
              )}
            >
              {on ? <span className="size-2 rounded-full bg-foreground" /> : null}
            </span>
            <span className="min-w-0 space-y-0.5">
              <span className="flex items-center gap-1.5 text-sm font-medium">
                <Icon className="size-3.5 text-muted-foreground" />
                {title}
              </span>
              <span className="block text-xs leading-relaxed text-muted-foreground">{body}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** The exchange's key boxes as a checklist: what to tick, what to leave off. */
function PermissionChecklist({ provider, mode }: { provider: Provider; mode: ConnectionMode }) {
  const rows = provider.permissions?.[mode]
  if (!rows?.length) return null
  // "Funds · Query Funds" → grouped under "Funds", as on the exchange's page.
  const groups: [string, typeof rows][] = []
  for (const r of rows) {
    const group = r.label.includes(" · ") ? r.label.split(" · ")[0] : ""
    const last = groups[groups.length - 1]
    if (last && last[0] === group) last[1].push(r)
    else groups.push([group, [r]])
  }
  return (
    <div className="rounded-lg border border-border">
      <p className="border-b border-border/70 px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        On {provider.label}'s key page
      </p>
      {groups.map(([group, items]) => (
        <div key={group || "all"}>
          {group ? <p className="bg-muted/30 px-3 pb-1 pt-2 text-[11px] font-medium text-muted-foreground">{group}</p> : null}
          <ul className="divide-y divide-border/50">
            {items.map((r) => {
              const name = r.label.includes(" · ") ? r.label.split(" · ").slice(1).join(" · ") : r.label
              const never = r.state === "off" && /withdraw/i.test(r.label)
              return (
                <li key={r.label} className="flex items-center gap-2.5 px-3 py-1.5 text-sm">
                  <span
                    aria-hidden
                    className={cn(
                      "grid size-4 shrink-0 place-items-center rounded-[4px] border",
                      r.state === "on" ? "border-positive bg-positive text-background" : "border-muted-foreground/40",
                      r.state === "optional" && "border-dashed",
                    )}
                  >
                    {r.state === "on" ? <Check className="size-3" strokeWidth={3} /> : null}
                  </span>
                  <span className={cn("min-w-0 flex-1", r.state === "off" && "text-muted-foreground")}>{name}</span>
                  <span
                    className={cn(
                      "shrink-0 text-[11px]",
                      r.state === "on" && "font-medium text-positive",
                      r.state === "off" && (never ? "font-medium text-negative" : "text-muted-foreground"),
                      r.state === "optional" && "text-muted-foreground",
                    )}
                  >
                    {r.state === "on" ? "Tick" : r.state === "optional" ? "Optional" : never ? "Never" : "Leave off"}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </div>
  )
}

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
  const canTrade = provider.modes.includes("trade")
  const sandboxValues = (m: ConnectionMode) =>
    sandbox ? { ...((m === "trade" ? provider.sandbox_trade_values : null) ?? provider.sandbox_values ?? {}) } : {}
  const [mode, setMode] = useState<ConnectionMode>(canTrade && initialMode === "trade" ? "trade" : "read")
  const [values, setValues] = useState<Record<string, string>>(() => sandboxValues(mode))
  const [working, setWorking] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [provider.id])

  const steps = mode === "trade" ? provider.trade_steps ?? [] : provider.read_steps
  const ready = provider.fields.every((f) => f.options || values[f.key]?.trim())
  const upgrading = Boolean(reconnect && reconnect.mode === "read" && mode === "trade")
  const sandboxNoTrade = sandbox && mode === "trade" && !(provider.id === "kraken" && reconnect)

  function pick(m: ConnectionMode) {
    setMode(m)
    setRefusal(null)
    if (sandbox) setValues(sandboxValues(m))
  }

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
          ? `${provider.label} connected for trading — Archie can now buy and sell there, on your approval`
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
            {upgrading ? `Enable trading on ${provider.label}` : reconnect ? `Reconnect ${provider.label}` : `Connect ${provider.label}`}
            <span className="font-normal text-muted-foreground"> · {provider.product}</span>
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">{provider.summary}</p>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onCancel} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <div className="space-y-5 p-4">
        {canTrade ? (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">What should OpenPF be able to do?</p>
            <ModeChoice value={mode} onChange={pick} />
          </div>
        ) : provider.trade_note ? (
          <p className="flex items-start gap-2 rounded-lg bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground">
            <Eye className="mt-0.5 size-3.5 shrink-0" />
            {provider.trade_note}
          </p>
        ) : null}

        <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
          <Globe className="mt-0.5 size-3.5 shrink-0" />
          {provider.availability}
        </p>

        <div className="grid gap-6 md:grid-cols-[1.15fr_1fr]">
          <div className="space-y-4">
            {upgrading && provider.upgrade ? (
              <p className="rounded-lg border border-border bg-muted/40 p-3 text-sm leading-relaxed">{provider.upgrade}</p>
            ) : null}

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
                Open {provider.label}'s API page <ArrowUpRight className="size-3" />
              </a>
            ) : null}
          </div>

          <div className="space-y-4 self-start">
            <PermissionChecklist provider={provider} mode={mode} />
            <form
              className="space-y-3"
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
                {mode === "trade" ? " A key that can withdraw is refused." : ""}
                {sandbox ? " Sandbox: fake fixture keys — nothing real is contacted." : ""}
              </p>
              {sandboxNoTrade ? (
                <p className="rounded-lg bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground">
                  In the sandbox only the mock Kraken account trades — try buying there. Connect this one read-only.
                </p>
              ) : null}
              {refusal ? (
                <p role="alert" className="flex items-start gap-2 rounded-lg border border-negative/30 bg-negative/5 p-3 text-sm">
                  <ShieldAlert className="mt-0.5 size-4 shrink-0 text-negative" />
                  <span>{refusal}</span>
                </p>
              ) : null}
              <Button type="submit" size="sm" disabled={working || !ready}>
                {working ? <Loader2 className="size-3.5 animate-spin" /> : <KeyRound className="size-3.5" />}
                {working ? `Checking with ${provider.label}…` : mode === "trade" ? "Verify & connect for trading" : "Verify & connect read-only"}
              </Button>
            </form>
          </div>
        </div>
      </div>
    </div>
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
                          {p.modes.includes("trade") ? "Read only, or read + trade" : "Read only"}
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

/**
 * Settings → Accounts: every broker, exchange and aggregator in one list.
 * `?manage=t212` opens the Trading 212 keys; `?add=<provider>` opens that guide.
 */
export function AccountsSettings({
  config,
  onConfigChanged,
  manage,
  add,
}: {
  config: AppConfig | null
  onConfigChanged: () => void
  manage: string | null
  add: string | null
}) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null)
  const [connections, setConnections] = useState<Connection[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [picked, setPicked] = useState<{ provider: Provider; reconnect: Connection | null; mode: ConnectionMode } | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Connection | null>(null)
  const t212Open = manage === "t212"

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

  const setT212 = (open: boolean) => navigate(open ? "/settings/accounts?manage=t212" : "/settings/accounts", { replace: true })

  /** Open a provider's guide. A connected one reopens in its CURRENT mode, so reconnecting never downgrades a trade key. */
  const openProvider = useCallback(
    (p: Provider) => {
      if (p.id === "t212") {
        setAdding(false)
        setPicked(null)
        navigate("/settings/accounts?manage=t212", { replace: true })
        return
      }
      const existing = (connections ?? []).find((c) => c.provider === p.id && c.type === "crypto") ?? null
      setAdding(true)
      setPicked({ provider: p, reconnect: existing, mode: existing?.mode ?? "read" })
    },
    [connections],
  )

  // Deep link: ?add=binance opens that guide.
  useEffect(() => {
    if (!add || !catalog || connections === null) return
    const p = catalog.providers.find((x) => x.id === add)
    if (p) openProvider(p)
    navigate(t212Open ? "/settings/accounts?manage=t212" : "/settings/accounts", { replace: true })
  }, [add, catalog, connections, openProvider, t212Open])

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
      <Section
        title={
          connections && connections.length ? (
            <span className="font-normal text-muted-foreground">
              <span className="font-medium text-foreground">{connections.length}</span> connection{connections.length === 1 ? "" : "s"} ·{" "}
              <span className="font-medium text-foreground">{accountsCount}</span> account{accountsCount === 1 ? "" : "s"} ·{" "}
              <Money value={total} decimals={0} className="font-medium text-foreground" />
              {readOnly ? ` · ${readOnly} read-only` : ""}
            </span>
          ) : (
            "Connected"
          )
        }
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
            {adding ? "Close" : "Add account"}
          </Button>
        }
      >
        {connections === null ? (
          <div className="space-y-2.5">
            <Skeleton className="h-24 w-full rounded-xl" />
            <Skeleton className="h-16 w-full rounded-xl" />
          </div>
        ) : connections.length === 0 ? (
          <div className="flex items-start gap-3 rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
            <Link2 className="mt-0.5 size-4 shrink-0" />
            <p>Connect your first account. Read-only is enough to see everything in one place; add trading where you want it.</p>
          </div>
        ) : (
          <ul className="space-y-2.5">
            {connections.map((c) => (
              <Fragment key={c.id}>
              <ConnectionRow
                key={c.id}
                c={c}
                busy={busy === c.id}
                upgradeNote={providerById(c.provider)?.upgrade ?? null}
                onManage={() => setT212(!t212Open)}
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
              {c.provider === "t212" && t212Open ? (
                <li>
                  <T212KeysPanel config={config} onConfigChanged={onConfigChanged} onClose={() => setT212(false)} />
                </li>
              ) : null}
              </Fragment>
            ))}
          </ul>
        )}
      </Section>

      {adding && catalog ? (
        <Section title="Add an account" description="Pick where your money is. You choose read-only or trading for each one.">
          <div className="space-y-5">
            <ProviderPicker catalog={catalog} connected={connectedProviders} picked={picked?.provider.id ?? null} onPick={(p) => openProvider(p)} />
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

      <div className="space-y-4">
        <Disclosure label="How your keys stay safe">
          <ul className="grid gap-x-6 gap-y-2 text-sm leading-relaxed text-muted-foreground sm:grid-cols-2">
            <li>Keys are encrypted at rest, shown back only as the last few characters, and never reach Archie.</li>
            <li>Keys that can withdraw or transfer are refused — for every provider, in every mode.</li>
            <li>On a read-only account a suggestion says “Place it in &lt;broker&gt;” — OpenPF can't execute there.</li>
            <li>Every order, anywhere, still needs your approval (or a standing permission you set) and passes your limits.</li>
          </ul>
        </Disclosure>
        <Disclosure label="Before you buy crypto (UK)">
          <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground">
            <li>
              Crypto held on an exchange is <span className="text-foreground">not covered by the FSCS</span>. If the exchange fails or is
              hacked, you may not get it back.
            </li>
            <li>
              Selling, swapping or spending crypto is a disposal for <span className="text-foreground">Capital Gains Tax</span>. Keep a
              record of every buy and sell.
            </li>
            <li>UK investors can also get crypto exposure through exchange-traded notes (ETNs) at a regular broker — a different product with its own fees and risks.</li>
            <li>Crypto prices are far more volatile than shares; falls of more than 70% have happened more than once.</li>
          </ul>
        </Disclosure>
      </div>

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
