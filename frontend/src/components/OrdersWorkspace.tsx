import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dayjs from 'dayjs'
import { toast } from 'sonner'
import { AlertTriangle, Ban, CheckCircle2, Copy, Inbox, Loader2, RefreshCw, Search, ShieldAlert, ShieldCheck, Wifi } from 'lucide-react'

import {
  cancelOrder,
  getExecutionHealth,
  getOrderHistory,
  getPendingOrders,
  testExecutionKey,
  type AccountError,
  type AccountExecutionHealth,
  type ExecutionHealthResponse,
  type OrderAccount,
  type OrderItem,
  type OrderScope,
} from '@/api/orders'
import { toastApiError } from '@/lib/api-error'
import { copyToClipboard } from '@/lib/clipboard'
import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { accountLabel, formatMoney, formatNumber } from '@/utils/format'
import { cn } from '@/lib/utils'

// Pending orders + health are cheap to poll. Order HISTORY is NOT polled — the
// T212 history endpoint is aggressively rate-limited, so it loads on demand only
// (mount, account change, debounced filter, manual refresh).
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
  const [pendingErrors, setPendingErrors] = useState<AccountError[]>([])
  const [history, setHistory] = useState<OrderItem[]>([])
  const [historyErrors, setHistoryErrors] = useState<AccountError[]>([])
  const [health, setHealth] = useState<ExecutionHealthResponse | null>(null)
  const [loadingLive, setLoadingLive] = useState(true)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [tickerFilter, setTickerFilter] = useState('')
  const [testing, setTesting] = useState<OrderAccount | null>(null)
  const [cancelTarget, setCancelTarget] = useState<OrderItem | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const liveLoadId = useRef(0)
  const histLoadId = useRef(0)

  // Pending orders + execution health (safe to poll).
  const loadLive = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      const id = ++liveLoadId.current
      if (!opts.silent) setLoadingLive(true)
      try {
        const [p, h] = await Promise.all([getPendingOrders(scope), getExecutionHealth()])
        if (id !== liveLoadId.current) return
        setPending(p.orders)
        setPendingErrors(p.errors)
        setHealth(h)
        onError(null)
      } catch (err) {
        if (id !== liveLoadId.current) return
        onError(err instanceof Error ? err.message : 'Failed to load orders')
      } finally {
        if (id === liveLoadId.current) setLoadingLive(false)
      }
    },
    [scope, onError],
  )

  // Order history — fetched on-demand (never polled), then filtered CLIENT-SIDE.
  // We don't pass the ticker to T212's instrumentCode filter (it needs the exact
  // full code, e.g. NVDA_US_EQ) — substring matching here is what users expect.
  const loadHistory = useCallback(async () => {
    const id = ++histLoadId.current
    setLoadingHistory(true)
    try {
      const res = await getOrderHistory(scope, undefined, 50)
      if (id !== histLoadId.current) return
      setHistory(res.orders)
      setHistoryErrors(res.errors)
    } catch (err) {
      if (id !== histLoadId.current) return
      setHistoryErrors([
        { account_kind: scope, code: 'unknown', message: err instanceof Error ? err.message : 'Failed to load history' },
      ])
    } finally {
      if (id === histLoadId.current) setLoadingHistory(false)
    }
  }, [scope])

  const filteredHistory = useMemo(() => {
    const q = tickerFilter.trim().toLowerCase()
    if (!q) return history
    return history.filter(
      (o) => (o.ticker ?? '').toLowerCase().includes(q) || (o.name ?? '').toLowerCase().includes(q),
    )
  }, [history, tickerFilter])

  // Live data: load on scope change + poll while visible.
  useEffect(() => {
    void loadLive()
    function tick() {
      if (document.visibilityState === 'visible') void loadLive({ silent: true })
    }
    const interval = window.setInterval(tick, POLL_MS)
    return () => window.clearInterval(interval)
  }, [loadLive])

  // History loads on mount + account change; the ticker filter is client-side.
  useEffect(() => {
    void loadHistory()
  }, [loadHistory])

  async function handleRefresh() {
    setRefreshing(true)
    await Promise.all([loadLive({ silent: true }), loadHistory()])
    setRefreshing(false)
  }

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
      toast.success('Order cancelled', {
        description: `${cancelTarget.name ?? cancelTarget.ticker ?? cancelTarget.order_id} on ${accountLabel(account)}`,
      })
      setCancelTarget(null)
      await Promise.all([loadLive({ silent: true }), loadHistory()])
    } catch (err) {
      toastApiError(err, 'Cancel failed')
    } finally {
      setCancelling(false)
    }
  }

  async function copyIp() {
    if (!health?.egress_ip) return
    const ok = await copyToClipboard(health.egress_ip)
    if (ok) toast.success('IP copied', { description: health.egress_ip })
    else toast.message('Copy unavailable here', { description: `Select it manually: ${health.egress_ip}` })
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
            <>
              <Badge
                variant={health.broker_mode === 'live' ? 'default' : 'outline'}
                title={
                  health.broker_mode === 'live'
                    ? 'Orders are placed for real via the execution key.'
                    : 'Orders are simulated locally — nothing is sent to Trading 212.'
                }
              >
                {health.broker_mode === 'live' ? 'Live' : 'Paper (simulated)'}
              </Badge>
              {/* Only surface the environment when it's the unusual demo account —
                  no redundant "Live · live". */}
              {health.base_env === 'demo' ? (
                <Badge variant="outline" title="Reading from Trading 212's practice (demo) account.">
                  demo account
                </Badge>
              ) : null}
            </>
          ) : null}
        </div>
        <Button variant="outline" size="sm" onClick={() => void handleRefresh()} disabled={refreshing}>
          <RefreshCw className={cn('size-3.5', refreshing && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      <ExecutionHealthBar
        health={health}
        loading={loadingLive && !health}
        testing={testing}
        onTest={handleTest}
        onCopyIp={copyIp}
      />

      <SectionCard title="Open orders" description="Pending / working orders live at the broker" noPadding>
        {loadingLive && pending.length === 0 ? (
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
        <AccountErrorNote errors={pendingErrors} />
      </SectionCard>

      <SectionCard
        title="Order history"
        description="Recent fills and cancellations, newest first"
        noPadding
        action={
          <InputGroup className="w-[190px]">
            <InputGroupAddon align="inline-start">
              {loadingHistory ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
            </InputGroupAddon>
            <InputGroupInput
              value={tickerFilter}
              onChange={(e) => setTickerFilter(e.target.value)}
              placeholder="Filter ticker…"
              className="text-xs"
            />
          </InputGroup>
        }
      >
        {loadingHistory && history.length === 0 ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 rounded-lg" />
            ))}
          </div>
        ) : filteredHistory.length === 0 ? (
          <EmptyRow
            icon={Inbox}
            text={tickerFilter.trim() ? `No recent orders match “${tickerFilter.trim()}”.` : 'No order history.'}
          />
        ) : (
          <OrdersTable orders={filteredHistory} showAccount={showAccountCol} history />
        )}
        <AccountErrorNote errors={historyErrors} />
      </SectionCard>

      <Dialog open={!!cancelTarget} onOpenChange={(o) => !o && setCancelTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel order?</DialogTitle>
            <DialogDescription>
              {cancelTarget ? (
                <>
                  Cancel the {cancelTarget.side ?? ''} order for{' '}
                  <span className="font-medium text-foreground">{cancelTarget.name ?? cancelTarget.ticker ?? cancelTarget.order_id}</span> on{' '}
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

function AccountErrorNote({ errors }: { errors: AccountError[] }) {
  if (!errors.length) return null
  return (
    <div className="space-y-1 border-t border-border/60 px-4 py-2.5">
      {errors.map((e, i) => (
        <p key={`${e.account_kind}-${i}`} className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertTriangle className="size-3 shrink-0 text-warning" />
          <span className="font-medium text-foreground/80">{accountLabel(e.account_kind)}</span>
          <span className="truncate">· {e.message}</span>
        </p>
      ))}
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
              <TableCell className="min-w-0 max-w-[220px]">
                <div className="flex flex-col">
                  <span className="truncate font-medium">{o.name ?? o.ticker ?? '—'}</span>
                  {o.name && o.ticker ? (
                    <span className="truncate font-mono text-xs text-muted-foreground">{o.ticker}</span>
                  ) : null}
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

function chipStatus(data: AccountExecutionHealth | undefined): { dotCls: string; label: string; labelCls: string } {
  if (!data?.exec_configured) return { dotCls: 'bg-muted-foreground/40', label: 'no key', labelCls: 'text-muted-foreground' }
  if (!data.exec_enabled) return { dotCls: 'bg-negative', label: 'off', labelCls: 'text-muted-foreground' }
  switch (data.last_test?.result) {
    case 'ok':
      return { dotCls: 'bg-positive', label: 'ready', labelCls: 'text-muted-foreground' }
    case 'ip_restricted':
      return { dotCls: 'bg-warning', label: 'IP blocked', labelCls: 'text-warning' }
    case 'auth_failed':
      return { dotCls: 'bg-negative', label: 'rejected', labelCls: 'text-negative' }
    case 'error':
      return { dotCls: 'bg-negative', label: 'error', labelCls: 'text-negative' }
    default:
      return { dotCls: 'bg-muted-foreground/60', label: 'untested', labelCls: 'text-muted-foreground' }
  }
}

// A condensed, always-on strip: per-account status dots + egress IP. Hover an
// account for full detail + the Test-key action (replaces the big health card).
function ExecutionHealthBar({
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
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-border/60 bg-card/40 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 font-medium text-muted-foreground">
          <ShieldCheck className="size-3.5" />
          Execution
        </span>
        {loading ? (
          <Skeleton className="h-4 w-40" />
        ) : (
          ACCOUNTS.map((account) => (
            <AccountStatusChip
              key={account}
              account={account}
              data={health?.accounts?.[account]}
              testing={testing === account}
              onTest={() => onTest(account)}
            />
          ))
        )}
      </div>
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <Wifi className="size-3.5" />
        <span>IP</span>
        <span className="font-mono tabular-nums text-foreground">{health?.egress_ip ?? 'unknown'}</span>
        {health?.egress_ip ? (
          <button type="button" onClick={onCopyIp} title="Copy IP" className="hover:text-foreground">
            <Copy className="size-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  )
}

function AccountStatusChip({
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
  const status = chipStatus(data)
  const test = data?.last_test
  const badge = TEST_BADGE[test?.result ?? 'untested'] ?? TEST_BADGE.untested
  const BadgeIcon = badge.Icon
  return (
    <HoverCard>
      <HoverCardTrigger className="flex cursor-default items-center gap-1.5 rounded-md px-1.5 py-0.5 outline-none transition-colors hover:bg-muted/60">
        <span className={cn('size-2 rounded-full', status.dotCls)} />
        <span className="font-medium text-foreground">{account === 'stocks_isa' ? 'ISA' : 'Invest'}</span>
        <span className={status.labelCls}>{status.label}</span>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-64">
        <div className="space-y-2.5">
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
          {test?.message ? <p className="text-[11px] text-muted-foreground">{test.message}</p> : null}
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[11px] text-muted-foreground">
              {test?.checked_at ? `tested ${dayjs(test.checked_at).format('MMM D HH:mm')}` : 'not tested yet'}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              onClick={onTest}
              disabled={testing || !data?.exec_configured}
            >
              {testing ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Test key
            </Button>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
