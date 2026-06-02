import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dayjs from 'dayjs'
import { toast } from 'sonner'
import { AlertTriangle, Ban, CheckCircle2, Copy, Inbox, Loader2, RefreshCw, ShieldAlert, Wifi } from 'lucide-react'

import {
  cancelOrder,
  getExecutionHealth,
  getOrderHistory,
  getPendingOrders,
  testExecutionKey,
  type AccountExecutionHealth,
  type ExecutionHealthResponse,
  type OrderAccount,
  type OrderItem,
  type OrderScope,
} from '@/api/orders'
import { toastApiError } from '@/lib/api-error'
import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { accountLabel, formatMoney, formatNumber } from '@/utils/format'
import { cn } from '@/lib/utils'

const POLL_MS = 20_000
const ACCOUNTS: OrderAccount[] = ['invest', 'stocks_isa']

interface Props {
  onError: (msg: string | null) => void
}

function statusBadgeVariant(status: string | null): 'default' | 'secondary' | 'outline' | 'destructive' {
  const s = (status || '').toUpperCase()
  if (s.includes('FILL') || s === 'EXECUTED') return 'default'
  if (s.includes('CANCEL') || s.includes('REJECT')) return 'destructive'
  if (s === 'NEW' || s.includes('SUBMIT') || s.includes('WORK') || s.includes('PEND')) return 'secondary'
  return 'outline'
}

function sideBadge(side: string | null) {
  if (!side) return null
  return (
    <Badge variant={side === 'buy' ? 'default' : 'destructive'} className="text-[10px]">
      {side.toUpperCase()}
    </Badge>
  )
}

const TEST_BADGE: Record<string, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  ok: { label: 'Working', cls: 'text-positive', Icon: CheckCircle2 },
  ip_restricted: { label: 'IP blocked', cls: 'text-warning', Icon: ShieldAlert },
  auth_failed: { label: 'Key rejected', cls: 'text-negative', Icon: Ban },
  error: { label: 'Error', cls: 'text-negative', Icon: AlertTriangle },
  not_configured: { label: 'Not set', cls: 'text-muted-foreground', Icon: AlertTriangle },
  untested: { label: 'Untested', cls: 'text-muted-foreground', Icon: Wifi },
}

export function OrdersWorkspace({ onError }: Props) {
  const [scope, setScope] = useState<OrderScope>('all')
  const [pending, setPending] = useState<OrderItem[]>([])
  const [history, setHistory] = useState<OrderItem[]>([])
  const [health, setHealth] = useState<ExecutionHealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [tickerFilter, setTickerFilter] = useState('')
  const [testing, setTesting] = useState<OrderAccount | null>(null)
  const [cancelTarget, setCancelTarget] = useState<OrderItem | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const loadId = useRef(0)

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      const id = ++loadId.current
      if (!opts.silent) setLoading(true)
      else setRefreshing(true)
      try {
        const [p, h, hist] = await Promise.all([
          getPendingOrders(scope),
          getExecutionHealth(),
          getOrderHistory(scope, tickerFilter.trim() || undefined, 50),
        ])
        if (id !== loadId.current) return
        setPending(p.orders)
        setHistory(hist.orders)
        setHealth(h)
        onError(null)
        // Per-account read failures (e.g. an IP-blocked or bad read key) surface inline.
        const errs = [...p.errors, ...hist.errors]
        if (errs.length) {
          const seen = new Set<string>()
          for (const e of errs) {
            const key = `${e.account_kind}:${e.code}`
            if (seen.has(key)) continue
            seen.add(key)
            toast.error(`${accountLabel(e.account_kind)}: ${e.code}`, { description: e.message })
          }
        }
      } catch (err) {
        if (id !== loadId.current) return
        onError(err instanceof Error ? err.message : 'Failed to load orders')
      } finally {
        if (id === loadId.current) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    },
    [scope, tickerFilter, onError],
  )

  useEffect(() => {
    void load()
  }, [load])

  // Poll pending orders + health while the tab is visible.
  useEffect(() => {
    function tick() {
      if (document.visibilityState === 'visible') void load({ silent: true })
    }
    const interval = window.setInterval(tick, POLL_MS)
    return () => window.clearInterval(interval)
  }, [load])

  async function handleTest(account: OrderAccount) {
    setTesting(account)
    try {
      const res = await testExecutionKey(account)
      setHealth((prev) =>
        prev
          ? {
              ...prev,
              egress_ip: res.egress_ip ?? prev.egress_ip,
              accounts: { ...prev.accounts, [account]: { ...prev.accounts[account], last_test: res.test } },
            }
          : prev,
      )
      const r = res.test.result
      if (r === 'ok') toast.success(`${accountLabel(account)} execution key works`, { description: res.test.message ?? undefined })
      else if (r === 'ip_restricted')
        toast.error(`${accountLabel(account)} key blocked`, {
          description: `${res.test.message ?? ''}${res.egress_ip ? ` Current IP: ${res.egress_ip}` : ''}`,
          duration: 12000,
        })
      else toast.error(`${accountLabel(account)} key test failed`, { description: res.test.message ?? undefined })
    } catch (err) {
      toastApiError(err, 'Test failed')
    } finally {
      setTesting(null)
    }
  }

  async function confirmCancel() {
    if (!cancelTarget?.order_id) return
    const account = cancelTarget.account_kind as OrderAccount
    setCancelling(true)
    try {
      await cancelOrder(cancelTarget.order_id, account)
      toast.success('Order cancelled', { description: `${cancelTarget.ticker ?? cancelTarget.order_id} on ${accountLabel(account)}` })
      setCancelTarget(null)
      await load({ silent: true })
    } catch (err) {
      toastApiError(err, 'Cancel failed')
    } finally {
      setCancelling(false)
    }
  }

  async function copyIp() {
    if (!health?.egress_ip) return
    try {
      await navigator.clipboard.writeText(health.egress_ip)
      toast.success('IP copied', { description: health.egress_ip })
    } catch {
      toast.error('Could not copy IP')
    }
  }

  const showAccountCol = scope === 'all'

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Select value={scope} onValueChange={(v) => setScope(v as OrderScope)}>
            <SelectTrigger size="sm" className="w-[150px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All accounts</SelectItem>
              <SelectItem value="invest">Invest</SelectItem>
              <SelectItem value="stocks_isa">Stocks ISA</SelectItem>
            </SelectContent>
          </Select>
          {health ? (
            <Badge variant={health.broker_mode === 'live' ? 'default' : 'outline'} className="uppercase">
              {health.broker_mode} · {health.base_env}
            </Badge>
          ) : null}
        </div>
        <Button variant="outline" size="sm" onClick={() => void load({ silent: true })} disabled={refreshing}>
          <RefreshCw className={cn('size-3.5', refreshing && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      <ExecutionHealthCard
        health={health}
        loading={loading}
        testing={testing}
        onTest={handleTest}
        onCopyIp={copyIp}
      />

      <SectionCard title="Open orders" description="Pending / working orders live at the broker" noPadding>
        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 rounded-lg" />
            ))}
          </div>
        ) : pending.length === 0 ? (
          <EmptyRow icon={Inbox} text="No open orders." />
        ) : (
          <OrdersTable orders={pending} showAccount={showAccountCol} showCancel onCancel={setCancelTarget} />
        )}
      </SectionCard>

      <SectionCard
        title="Order history"
        description="Recent fills and cancellations"
        noPadding
        action={
          <Input
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void load()
            }}
            placeholder="Filter ticker…"
            className="h-8 w-[160px] text-xs"
          />
        }
      >
        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 rounded-lg" />
            ))}
          </div>
        ) : history.length === 0 ? (
          <EmptyRow icon={Inbox} text="No order history for this filter." />
        ) : (
          <OrdersTable orders={history} showAccount={showAccountCol} history />
        )}
      </SectionCard>

      <Dialog open={!!cancelTarget} onOpenChange={(o) => !o && setCancelTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel order?</DialogTitle>
            <DialogDescription>
              {cancelTarget ? (
                <>
                  Cancel the {cancelTarget.side ?? ''} order for{' '}
                  <span className="font-medium text-foreground">{cancelTarget.ticker ?? cancelTarget.order_id}</span> on{' '}
                  <span className="font-medium text-foreground">{accountLabel(cancelTarget.account_kind)}</span>? This sends a
                  cancellation to Trading 212 using the execution key.
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setCancelTarget(null)} disabled={cancelling}>
              Keep order
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void confirmCancel()} disabled={cancelling}>
              {cancelling ? <Loader2 className="size-3.5 animate-spin" /> : <Ban className="size-3.5" />}
              Cancel order
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function EmptyRow({ icon: Icon, text }: { icon: typeof Inbox; text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      <Icon className="size-5 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">{text}</p>
    </div>
  )
}

function OrdersTable({
  orders,
  showAccount,
  showCancel = false,
  history = false,
  onCancel,
}: {
  orders: OrderItem[]
  showAccount: boolean
  showCancel?: boolean
  history?: boolean
  onCancel?: (o: OrderItem) => void
}) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            {showAccount ? <TableHead>Account</TableHead> : null}
            <TableHead>Instrument</TableHead>
            <TableHead>Side</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead className="text-right">{history ? 'Fill' : 'Limit / Stop'}</TableHead>
            <TableHead className="text-right">Value</TableHead>
            <TableHead>Time</TableHead>
            <TableHead>Status</TableHead>
            {showCancel ? <TableHead className="text-right">Action</TableHead> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.map((o, i) => (
            <TableRow key={`${o.order_id ?? 'o'}-${i}`}>
              {showAccount ? (
                <TableCell>
                  <Badge variant="outline" className="text-[10px]">
                    {o.account_kind === 'stocks_isa' ? 'ISA' : 'Invest'}
                  </Badge>
                </TableCell>
              ) : null}
              <TableCell className="min-w-0">
                <div className="flex flex-col">
                  <span className="font-medium">{o.ticker ?? '—'}</span>
                  {o.name ? <span className="truncate text-xs text-muted-foreground">{o.name}</span> : null}
                </div>
              </TableCell>
              <TableCell>{sideBadge(o.side)}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{o.type ?? '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {o.quantity != null ? formatNumber(Math.abs(o.quantity), 4) : '—'}
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {history
                  ? o.fill_price != null
                    ? formatNumber(o.fill_price, 2)
                    : '—'
                  : o.limit_price != null
                    ? formatNumber(o.limit_price, 2)
                    : o.stop_price != null
                      ? `stop ${formatNumber(o.stop_price, 2)}`
                      : '—'}
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {o.value != null ? formatMoney(Math.abs(o.value), 'GBP') : '—'}
              </TableCell>
              <TableCell className="font-mono text-xs tabular-nums text-muted-foreground">
                {o.created_at ? dayjs(o.created_at).format('MMM D HH:mm') : '—'}
              </TableCell>
              <TableCell>
                <Badge variant={statusBadgeVariant(o.status)} className="text-[10px]">
                  {o.status ?? '—'}
                </Badge>
              </TableCell>
              {showCancel ? (
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-negative hover:text-negative"
                    onClick={() => onCancel?.(o)}
                    disabled={!o.order_id}
                  >
                    <Ban className="size-3.5" />
                    Cancel
                  </Button>
                </TableCell>
              ) : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function ExecutionHealthCard({
  health,
  loading,
  testing,
  onTest,
  onCopyIp,
}: {
  health: ExecutionHealthResponse | null
  loading: boolean
  testing: OrderAccount | null
  onTest: (account: OrderAccount) => void
  onCopyIp: () => void
}) {
  const liveButPaper = useMemo(() => health?.broker_mode === 'paper', [health])
  return (
    <SectionCard
      title="Execution health"
      description="The IP-restricted write key — reads always use the unrestricted key"
      action={
        <div className="flex items-center gap-2 text-xs">
          <Wifi className="size-3.5 text-muted-foreground" />
          {loading && !health ? (
            <Skeleton className="h-4 w-24" />
          ) : (
            <>
              <span className="text-muted-foreground">IP</span>
              <span className="font-mono tabular-nums">{health?.egress_ip ?? 'unknown'}</span>
              {health?.egress_ip ? (
                <button type="button" onClick={onCopyIp} title="Copy IP" className="text-muted-foreground hover:text-foreground">
                  <Copy className="size-3.5" />
                </button>
              ) : null}
            </>
          )}
        </div>
      }
    >
      {liveButPaper ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          <AlertTriangle className="size-3.5 shrink-0" />
          Broker mode is <span className="font-semibold">PAPER</span> — orders are simulated, not sent to Trading 212.
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {ACCOUNTS.map((account) => (
          <ExecAccountRow
            key={account}
            account={account}
            data={health?.accounts?.[account]}
            testing={testing === account}
            onTest={() => onTest(account)}
          />
        ))}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        If a write is rejected for IP reasons, update the key's IP allowlist in Trading 212 to the IP shown above (or paste a
        fresh key in Settings → Credentials), then re-test.
      </p>
    </SectionCard>
  )
}

function ExecAccountRow({
  account,
  data,
  testing,
  onTest,
}: {
  account: OrderAccount
  data: AccountExecutionHealth | undefined
  testing: boolean
  onTest: () => void
}) {
  const test = data?.last_test
  const badge = TEST_BADGE[test?.result ?? 'untested'] ?? TEST_BADGE.untested
  const BadgeIcon = badge.Icon
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{accountLabel(account)}</span>
        <span className={cn('flex items-center gap-1 text-xs', badge.cls)}>
          <BadgeIcon className="size-3.5" />
          {badge.label}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        <Badge variant={data?.read_configured ? 'secondary' : 'outline'}>
          read {data?.read_configured ? 'set' : 'missing'}
        </Badge>
        <Badge variant={data?.exec_configured ? 'default' : 'outline'}>
          exec {data?.exec_configured ? 'set' : 'missing'}
        </Badge>
        {data && !data.exec_enabled ? <Badge variant="destructive">exec off</Badge> : null}
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] text-muted-foreground">
          {test?.checked_at ? `tested ${dayjs(test.checked_at).format('MMM D HH:mm')}` : 'not tested yet'}
        </span>
        <Button variant="outline" size="sm" className="h-7" onClick={onTest} disabled={testing || !data?.exec_configured}>
          {testing ? <Loader2 className="size-3.5 animate-spin" /> : null}
          Test key
        </Button>
      </div>
    </div>
  )
}
