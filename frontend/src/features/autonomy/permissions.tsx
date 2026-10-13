import { useCallback, useEffect, useState } from "react"
import dayjs from "dayjs"
import { History, Pause, Plus, ShieldCheck, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

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
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Section } from "@/components/kit"
import { toastApiError } from "@/lib/api-error"
import { navigate } from "@/lib/router"
import { cn } from "@/lib/utils"
import { formatMoney } from "@/utils/format"

import {
  confirmPermissionLive,
  createPermission,
  listPermissionSuggestions,
  listPermissions,
  permissionHistory,
  revokePermission,
  setKillSwitch,
  updatePermission,
  type Permission,
  type PermissionEvent,
  type PermissionKind,
  type PermissionList,
} from "./api"

const gbp = (v: number) => formatMoney(v, "GBP", 0)

const STATUS_COPY: Record<string, { label: string; tone: string }> = {
  active: { label: "Active", tone: "bg-positive/15 text-positive" },
  paused: { label: "Off", tone: "bg-muted text-muted-foreground" },
  expired: { label: "Expired", tone: "bg-muted text-muted-foreground" },
  revoked: { label: "Revoked", tone: "bg-muted text-muted-foreground" },
  "all permissions paused": { label: "Paused (all)", tone: "bg-muted text-foreground" },
  "needs live confirmation": { label: "Needs live confirmation", tone: "bg-negative/10 text-negative" },
}

export type DraftPrefill = { kind: PermissionKind; per_trade_cap_gbp: number; daily_cap_gbp: number; from_suggestion_id?: string }

/** Settings → Permissions: the standing permissions that let Archie act on his own. */
export function PermissionsSettings({ suggestionId }: { suggestionId?: string | null }) {
  const [data, setData] = useState<PermissionList | null>(null)
  const [events, setEvents] = useState<PermissionEvent[]>([])
  const [editing, setEditing] = useState<Permission | "new" | null>(null)
  const [prefill, setPrefill] = useState<DraftPrefill | null>(null)
  const [revoking, setRevoking] = useState<Permission | null>(null)

  const load = useCallback(async () => {
    try {
      const [list, hist] = await Promise.all([listPermissions(), permissionHistory()])
      setData(list)
      setEvents(hist)
    } catch (err) {
      toastApiError(err, "Couldn't load permissions")
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Arriving from a suggestion card: open the form prefilled with its terms.
  useEffect(() => {
    if (!suggestionId) return
    void listPermissionSuggestions().then((rows) => {
      const s = rows.find((r) => r.id === suggestionId)
      if (!s) return
      setPrefill({ kind: s.kind, per_trade_cap_gbp: s.per_trade_cap_gbp, daily_cap_gbp: s.daily_cap_gbp, from_suggestion_id: s.id })
      setEditing("new")
    })
  }, [suggestionId])

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn()
      toast.success(ok)
    } catch (err) {
      toastApiError(err, "That didn't work")
    } finally {
      await load()
    }
  }

  if (!data) return <Skeleton className="h-48 w-full rounded-xl" />

  const live = data.broker_mode === "live"
  const current = data.permissions.filter((p) => !p.revoked_at)
  const revoked = data.permissions.filter((p) => p.revoked_at)

  return (
    <div className="space-y-8">
      <p className="text-sm leading-relaxed text-muted-foreground">
        Archie only proposes. A standing permission lets him place one narrow kind of trade on his own, inside caps you
        set — and every trade still passes the same risk rails as one you approve by hand. Anything new, outside a play,
        over a cap or in another category waits for you.
      </p>

      <div className="flex items-center justify-between gap-4 rounded-xl border border-border px-4 py-3">
        <div className="flex min-w-0 gap-3">
          <Pause className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <p className="text-sm font-medium">Pause all permissions</p>
            <p className="text-xs text-muted-foreground">A kill switch. While on, nothing is placed automatically.</p>
          </div>
        </div>
        <Switch
          checked={data.paused}
          aria-label="Pause all permissions"
          onCheckedChange={(v) => void act(() => setKillSwitch(Boolean(v)), v ? "All permissions paused" : "Permissions resumed")}
        />
      </div>

      {live ? (
        <div className="flex gap-3 rounded-xl border border-negative/30 bg-negative/5 px-4 py-3 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-negative" />
          <p>
            <span className="font-medium">Live mode.</span>{" "}
            <span className="text-muted-foreground">
              A permission only places real orders once you've confirmed it here while live. Editing it asks again.
            </span>
          </p>
        </div>
      ) : null}

      <Section
        title="Your permissions"
        action={
          <Button size="sm" variant="outline" onClick={() => { setPrefill(null); setEditing("new") }}>
            <Plus className="size-3.5" />
            New permission
          </Button>
        }
      >
        {current.length === 0 ? (
          <div className="flex items-start gap-3 rounded-xl border border-dashed border-border px-5 py-6 text-sm text-muted-foreground">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            <p>
              None yet — everything Archie proposes waits for you. When his track record in a category earns it, he may
              suggest one in Decisions.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border/70 rounded-xl border border-border">
            {current.map((p) => (
              <PermissionRow
                key={p.id}
                p={p}
                live={live}
                onToggle={(on) => void act(() => updatePermission(p.id, { enabled: on }), on ? "Permission on" : "Permission off")}
                onEdit={() => setEditing(p)}
                onRevoke={() => setRevoking(p)}
                onConfirmLive={() => void act(() => confirmPermissionLive(p.id), "Confirmed for live")}
              />
            ))}
          </div>
        )}
      </Section>

      <Section title="History" description="Every grant, change and trade placed under a permission.">
        <HistoryList events={events} />
        {revoked.length ? (
          <p className="pt-3 text-xs text-muted-foreground">
            Revoked: {revoked.map((p) => p.label).join(" · ")}
          </p>
        ) : null}
      </Section>

      {editing ? (
        <PermissionDialog
          kinds={data.kinds}
          live={live}
          editing={editing === "new" ? null : editing}
          prefill={prefill}
          onClose={() => {
            setEditing(null)
            if (suggestionId) navigate("/settings/safety?section=permissions", { replace: true })
          }}
          onSaved={() => void load()}
        />
      ) : null}

      <AlertDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke “{revoking?.label}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Archie stops placing these on his own straight away. Trades already placed stay placed. To use it again
              you'd grant a new one.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const p = revoking
                setRevoking(null)
                if (p) void act(() => revokePermission(p.id), "Permission revoked")
              }}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function StatusChip({ status }: { status: string }) {
  const s = STATUS_COPY[status] ?? { label: status, tone: "bg-muted text-muted-foreground" }
  return <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", s.tone)}>{s.label}</span>
}

function PermissionRow({
  p,
  live,
  onToggle,
  onEdit,
  onRevoke,
  onConfirmLive,
}: {
  p: Permission
  live: boolean
  onToggle: (on: boolean) => void
  onEdit: () => void
  onRevoke: () => void
  onConfirmLive: () => void
}) {
  const used = p.daily_cap_gbp > 0 ? Math.min(1, p.spent_today_gbp / p.daily_cap_gbp) : 0
  return (
    <div className="space-y-3 px-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium">{p.label}</p>
            <StatusChip status={p.status} />
          </div>
          <p className="text-xs text-muted-foreground">{p.kind_description}</p>
        </div>
        <Switch checked={p.enabled} aria-label={`Turn ${p.label} ${p.enabled ? "off" : "on"}`} onCheckedChange={(v) => onToggle(Boolean(v))} />
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Per trade</dt>
          <dd className="font-mono tabular-nums">up to {gbp(p.per_trade_cap_gbp)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Per day</dt>
          <dd className="font-mono tabular-nums">up to {gbp(p.daily_cap_gbp)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Used today</dt>
          <dd className="flex items-center gap-2">
            <span className="font-mono tabular-nums">{gbp(p.spent_today_gbp)}</span>
            <span className="relative h-1.5 w-14 overflow-hidden rounded-full bg-muted" title={`${Math.round(used * 100)}% of today's cap`}>
              <span className="absolute inset-y-0 left-0 rounded-full bg-foreground/60" style={{ width: `${used * 100}%` }} />
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Expires</dt>
          <dd>{p.expires_at ? dayjs(p.expires_at + "Z").format("D MMM YYYY") : "Never"}</dd>
        </div>
      </dl>
      <div className="flex flex-wrap items-center gap-2">
        {live && !p.confirmed_live_at ? (
          <Button size="sm" onClick={onConfirmLive}>
            Confirm for live
          </Button>
        ) : null}
        <Button size="sm" variant="outline" onClick={onEdit}>
          Edit
        </Button>
        <Button size="sm" variant="ghost" className="text-negative hover:text-negative" onClick={onRevoke}>
          Revoke
        </Button>
        <span className="ml-auto text-[11px] text-muted-foreground">Granted {dayjs(p.created_at + "Z").format("D MMM")}</span>
      </div>
    </div>
  )
}

const EVENT_COPY: Record<string, string> = {
  granted: "Granted",
  edited: "Changed",
  paused: "Turned off",
  resumed: "Turned on",
  revoked: "Revoked",
  confirmed_live: "Confirmed for live",
  used: "Placed",
  failed: "Couldn't place — back to you",
  kill_switch_on: "All permissions paused",
  kill_switch_off: "Permissions resumed",
}

function HistoryList({ events }: { events: PermissionEvent[] }) {
  const [open, setOpen] = useState(false)
  if (!events.length) return <p className="text-sm text-muted-foreground">Nothing yet.</p>
  const shown = open ? events : events.slice(0, 8)
  return (
    <div>
      <ul className="divide-y divide-border/60">
        {shown.map((e) => (
          <li key={e.id} className="flex items-baseline gap-3 py-2 text-sm">
            <span className="w-24 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
              {dayjs(e.created_at + "Z").format("MMM D HH:mm")}
            </span>
            <span className="shrink-0">{EVENT_COPY[e.kind] ?? e.kind}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {e.kind === "used" ? `${String(e.detail.side ?? "").toUpperCase()} ${e.detail.symbol ?? ""} · ${gbp(e.amount_gbp ?? 0)} · ` : ""}
              {e.kind === "failed" ? `${String(e.detail.error ?? "")} · ` : ""}
              {e.permission_label ?? ""}
            </span>
          </li>
        ))}
      </ul>
      {events.length > shown.length ? (
        <button type="button" onClick={() => setOpen(true)} className="flex items-center gap-1 pt-2 text-xs text-muted-foreground hover:text-foreground">
          <History className="size-3" /> Show all {events.length}
        </button>
      ) : null}
    </div>
  )
}

function PermissionDialog({
  kinds,
  live,
  editing,
  prefill,
  onClose,
  onSaved,
}: {
  kinds: PermissionList["kinds"]
  live: boolean
  editing: Permission | null
  prefill: DraftPrefill | null
  onClose: () => void
  onSaved: () => void
}) {
  const [kind, setKind] = useState<PermissionKind>(editing?.kind ?? prefill?.kind ?? "play_tranche")
  const [perTrade, setPerTrade] = useState(String(editing?.per_trade_cap_gbp ?? prefill?.per_trade_cap_gbp ?? 250))
  const [daily, setDaily] = useState(String(editing?.daily_cap_gbp ?? prefill?.daily_cap_gbp ?? 750))
  const [expiry, setExpiry] = useState(editing?.expires_at ? editing.expires_at.slice(0, 10) : "")
  const [confirm, setConfirm] = useState(false)
  const [saving, setSaving] = useState(false)

  const per = Number(perTrade)
  const day = Number(daily)
  const invalid = !(per > 0) || !(day > 0) || day < per
  const needsConfirm = live && !confirm

  async function save() {
    setSaving(true)
    try {
      const expires_at = expiry ? dayjs(expiry).endOf("day").toISOString() : null
      if (editing) {
        await updatePermission(editing.id, {
          per_trade_cap_gbp: per,
          daily_cap_gbp: day,
          ...(expires_at ? { expires_at } : { clear_expiry: true }),
          confirm_live: live && confirm,
        })
        toast.success("Permission updated")
      } else {
        await createPermission({
          kind,
          per_trade_cap_gbp: per,
          daily_cap_gbp: day,
          expires_at,
          confirm_live: live && confirm,
          from_suggestion_id: prefill?.from_suggestion_id ?? null,
        })
        toast.success("Permission granted")
      }
      onSaved()
      onClose()
    } catch (err) {
      toastApiError(err, "Couldn't save the permission")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? `Edit “${editing.label}”` : "New standing permission"}</DialogTitle>
          <DialogDescription>
            Trades that fit are placed without asking you, then shown in Activity as “auto”. Every risk rail still applies.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          {!editing ? (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1.5 text-sm font-medium">What Archie may do</legend>
              {kinds.map((k) => (
                <label
                  key={k.kind}
                  className={cn(
                    "flex cursor-pointer gap-3 rounded-lg border px-3 py-2.5 text-sm transition-colors",
                    kind === k.kind ? "border-foreground/40 bg-muted/60" : "border-border hover:bg-muted/30",
                  )}
                >
                  <input type="radio" name="kind" className="mt-1" checked={kind === k.kind} onChange={() => setKind(k.kind)} />
                  <span>
                    <span className="font-medium">{k.label}</span>
                    <span className="block text-xs text-muted-foreground">{k.description}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="perm-per">Per trade, up to (£)</Label>
              <Input id="perm-per" inputMode="decimal" value={perTrade} onChange={(e) => setPerTrade(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="perm-day">Per day, up to (£)</Label>
              <Input id="perm-day" inputMode="decimal" value={daily} onChange={(e) => setDaily(e.target.value)} />
            </div>
          </div>
          {day < per && per > 0 && day > 0 ? <p className="-mt-2 text-xs text-negative">The daily cap can't be lower than the per-trade cap.</p> : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perm-exp">Expires (optional)</Label>
            <Input id="perm-exp" type="date" value={expiry} min={dayjs().add(1, "day").format("YYYY-MM-DD")} onChange={(e) => setExpiry(e.target.value)} />
          </div>
          {live ? (
            <label className="flex items-start gap-2.5 rounded-lg border border-negative/30 bg-negative/5 px-3 py-2.5 text-sm">
              <Checkbox checked={confirm} onCheckedChange={(v) => setConfirm(Boolean(v))} className="mt-0.5" />
              <span>
                I understand this places <span className="font-medium">real orders</span> in my Trading 212 account without
                asking me, up to {gbp(per || 0)} a trade and {gbp(day || 0)} a day.
              </span>
            </label>
          ) : (
            <p className="text-xs text-muted-foreground">
              You're in paper mode. If you switch to live, you'll be asked to confirm this permission again.
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={saving || invalid || needsConfirm}>
            {editing ? "Save" : "Grant permission"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
