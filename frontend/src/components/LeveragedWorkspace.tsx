import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { ArrowRight, Inbox, Layers, RefreshCw, RotateCcw, Scan, TrendingUp } from 'lucide-react'

import {
  adoptLeveragedPosition,
  closeLeveragedPosition,
  closeLeveragedTrade,
  executeLeveragedSignal,
  getLeveragedPositions,
  getLeveragedSnapshot,
  getLeveragedUniverse,
  patchLeveragedPolicy,
  refreshInstrumentCache,
  runLeveragedCycle,
  runLeveragedScan,
  type UniverseResponse,
} from '../api/client'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Money, MoneyDelta, Pct, SectionCard, StatCard } from '@/components/kit'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { HeldLeveragedPosition, LeveragedConfig, LeveragedSnapshot } from '../types'

interface Props {
  onError: (message: string | null) => void
}

export function LeveragedWorkspace({ onError }: Props) {
  const [snapshot, setSnapshot] = useState<LeveragedSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const [policyDraft, setPolicyDraft] = useState<LeveragedConfig | null>(null)
  const [universe, setUniverse] = useState<UniverseResponse | null>(null)
  const [positions, setPositions] = useState<HeldLeveragedPosition[] | null>(null)
  const [positionsBusy, setPositionsBusy] = useState(false)

  async function loadAll() {
    setBusy(true)
    try {
      const snap = await getLeveragedSnapshot()
      setSnapshot(snap)
      setPolicyDraft(snap.policy)
      onError(null)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to load leveraged workspace')
    } finally {
      setBusy(false)
    }
  }

  async function loadPositions() {
    // Live T212 positions are a slower, rate-limited call — load independently so
    // it never blocks the desk, and surface failures softly (T212 may be down).
    setPositionsBusy(true)
    try {
      setPositions(await getLeveragedPositions())
    } catch {
      setPositions([])
    } finally {
      setPositionsBusy(false)
    }
  }

  useEffect(() => {
    void loadAll()
    // Regime + universe + held positions load independently (slower, market-data
    // backed) so they never block the core desk from rendering.
    void getLeveragedUniverse(8).then(setUniverse).catch(() => setUniverse(null))
    void loadPositions()
  }, [])

  const summary = snapshot?.summary
  const policy = policyDraft
  const openTrades = useMemo(() => snapshot?.open_trades ?? [], [snapshot])
  const signals = useMemo(() => (snapshot?.signals ?? []).filter((row) => row.status === 'proposed').slice(0, 16), [snapshot])

  async function savePolicy() {
    if (!policy) return
    setBusy(true)
    try {
      const updated = await patchLeveragedPolicy({
        enabled: policy.enabled,
        auto_execute_enabled: policy.auto_execute_enabled,
        per_position_notional: Number(policy.per_position_notional),
        max_total_exposure: Number(policy.max_total_exposure),
        max_open_positions: Number(policy.max_open_positions),
        take_profit_pct: Number(policy.take_profit_pct),
        stop_loss_pct: Number(policy.stop_loss_pct),
        close_time_uk: policy.close_time_uk,
        allow_overnight: policy.allow_overnight,
        max_hold_days: Number(policy.max_hold_days),
        scan_symbols: policy.scan_symbols,
        instrument_priority: policy.instrument_priority,
      })
      setPolicyDraft(updated)
      await loadAll()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update leveraged policy')
      setBusy(false)
    }
  }

  const regime = universe?.regime
  const regimeTone =
    regime?.regime === 'risk_on'
      ? 'border-emerald-500/30 bg-emerald-500/5'
      : regime?.regime === 'risk_off'
        ? 'border-rose-500/30 bg-rose-500/5'
        : 'border-border/60 bg-muted/20'
  const regimeDot =
    regime?.regime === 'risk_on' ? 'bg-emerald-500' : regime?.regime === 'risk_off' ? 'bg-rose-500' : 'bg-muted-foreground'

  return (
    <div className="space-y-6">
      {regime ? (
        <div className={cn('flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border px-4 py-3', regimeTone)}>
          <div className="flex items-center gap-2">
            <span className={cn('h-2.5 w-2.5 rounded-full', regimeDot)} />
            <span className="text-sm font-semibold">Market regime: {regime.label}</span>
            {regime.stale ? <Badge variant="outline" className="text-[10px]">degraded data</Badge> : null}
          </div>
          <span className="text-xs text-muted-foreground">score {regime.score >= 0 ? '+' : ''}{regime.score.toFixed(2)}</span>
          {regime.vix != null ? (
            <span className="text-xs text-muted-foreground">VIX {regime.vix.toFixed(1)} ({regime.vix_state})</span>
          ) : null}
          <span className="text-xs text-muted-foreground">
            tilt: {regime.regime === 'risk_on' ? '3x long favoured' : regime.regime === 'risk_off' ? '3x inverse favoured (ISA)' : 'no strong tilt'}
          </span>
          <span className="ml-auto hidden text-xs text-muted-foreground sm:inline">{regime.rationale}</span>
        </div>
      ) : null}

      {universe && universe.ranked.length > 0 ? (
        <SectionCard
          title="Today's Universe"
          description={`Regime-gated movers from ${universe.available_underlyings} live T212 leveraged products`}
        >
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Underlying</TableHead>
                  <TableHead>Direction</TableHead>
                  <TableHead>ETP</TableHead>
                  <TableHead className="text-right">Move vs 50d</TableHead>
                  <TableHead className="text-right">Momentum</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {universe.ranked.map((row) => (
                  <TableRow key={row.etp_ticker}>
                    <TableCell className="font-medium">
                      {row.underlying}
                      {row.underlying_name ? (
                        <span className="ml-2 hidden text-xs text-muted-foreground md:inline">{row.underlying_name}</span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={row.direction === 'long' ? 'text-emerald-500' : 'text-rose-500'}>
                        {row.direction === 'long' ? 'Long' : 'Inverse'} {row.factor ? `${row.factor}x` : ''}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.etp_ticker}</TableCell>
                    <TableCell className={cn('text-right tabular-nums', (row.move_pct ?? 0) >= 0 ? 'text-emerald-500' : 'text-rose-500')}>
                      {row.move_pct != null ? `${row.move_pct >= 0 ? '+' : ''}${(row.move_pct * 100).toFixed(1)}%` : `${row.move_score.toFixed(2)}`}
                    </TableCell>
                    <TableCell className="text-right text-xs capitalize text-muted-foreground">{row.trend}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </SectionCard>
      ) : null}

      <SectionCard
        title="Leveraged Desk"
        description="ISA leveraged positions tracked in SQLite + markdown logs"
        action={
          <>
            <Button variant="outline" size="sm" onClick={() => void loadAll()} disabled={busy} title="Refresh" className="px-2 sm:px-3">
              <RefreshCw />
              <span className="hidden sm:inline">Refresh</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void runLeveragedScan().then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'scan failed'))}
              disabled={busy}
              title="Scan"
              className="px-2 sm:px-3"
            >
              <Scan />
              <span className="hidden sm:inline">Scan</span>
            </Button>
            <Button
              size="sm"
              onClick={() => void runLeveragedCycle().then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'cycle failed'))}
              disabled={busy}
              title="Run Cycle"
              className="px-2 sm:px-3"
            >
              <TrendingUp />
              <span className="hidden sm:inline">Run Cycle</span>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void refreshInstrumentCache().then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'cache refresh failed'))}
              disabled={busy}
              title="Refresh Instruments"
              className="px-2 sm:px-3"
            >
              <RotateCcw />
              <span className="hidden sm:inline">Refresh Instruments</span>
            </Button>
          </>
        }
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Open Exposure"
            value={summary ? <Money value={summary.open_exposure} /> : '—'}
          />
          <StatCard
            label="Open Unrealised"
            value={summary ? <MoneyDelta value={summary.open_unrealized_pnl} /> : '—'}
          />
          <StatCard
            label="Realised P/L"
            value={summary ? <MoneyDelta value={summary.closed_realized_pnl} /> : '—'}
          />
          <StatCard
            label="Win Rate"
            value={summary ? <Pct value={summary.win_rate} /> : '—'}
          />
        </div>
      </SectionCard>

      <SectionCard
        title="Risk Rails"
        description="Guardrails for leveraged execution"
        action={
          <Button size="sm" onClick={() => void savePolicy()} disabled={!policy || busy}>
            Save
          </Button>
        }
      >
        {policy && (
          <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2.5">
                <Label htmlFor="lev-enabled" className="text-sm">Leveraged system enabled</Label>
                <Switch
                  id="lev-enabled"
                  checked={policy.enabled}
                  onCheckedChange={(checked) => setPolicyDraft({ ...policy, enabled: checked })}
                />
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2.5">
                <Label htmlFor="lev-auto-execute" className="text-sm">Auto execute within rails</Label>
                <Switch
                  id="lev-auto-execute"
                  checked={policy.auto_execute_enabled}
                  onCheckedChange={(checked) => setPolicyDraft({ ...policy, auto_execute_enabled: checked })}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="lev-per-position" className="text-xs text-muted-foreground">Per position (£)</Label>
                <Input
                  id="lev-per-position"
                  className="font-mono tabular-nums"
                  value={policy.per_position_notional}
                  onChange={(e) => setPolicyDraft({ ...policy, per_position_notional: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lev-max-exposure" className="text-xs text-muted-foreground">Max exposure (£)</Label>
                <Input
                  id="lev-max-exposure"
                  className="font-mono tabular-nums"
                  value={policy.max_total_exposure}
                  onChange={(e) => setPolicyDraft({ ...policy, max_total_exposure: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lev-max-open" className="text-xs text-muted-foreground">Max open positions</Label>
                <Input
                  id="lev-max-open"
                  className="font-mono tabular-nums"
                  value={policy.max_open_positions}
                  onChange={(e) => setPolicyDraft({ ...policy, max_open_positions: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lev-close-time" className="text-xs text-muted-foreground">Close time UK</Label>
                <Input
                  id="lev-close-time"
                  className="font-mono tabular-nums"
                  value={policy.close_time_uk}
                  onChange={(e) => setPolicyDraft({ ...policy, close_time_uk: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lev-take-profit" className="text-xs text-muted-foreground">Take profit (%)</Label>
                <Input
                  id="lev-take-profit"
                  className="font-mono tabular-nums"
                  value={(policy.take_profit_pct * 100).toFixed(2)}
                  onChange={(e) => setPolicyDraft({ ...policy, take_profit_pct: Number(e.target.value) / 100 })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lev-stop-loss" className="text-xs text-muted-foreground">Stop loss (%)</Label>
                <Input
                  id="lev-stop-loss"
                  className="font-mono tabular-nums"
                  value={(policy.stop_loss_pct * 100).toFixed(2)}
                  onChange={(e) => setPolicyDraft({ ...policy, stop_loss_pct: Number(e.target.value) / 100 })}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="lev-scan-symbols" className="text-xs text-muted-foreground">Scan symbols (comma separated)</Label>
                <Input
                  id="lev-scan-symbols"
                  value={policy.scan_symbols.join(', ')}
                  onChange={(e) => setPolicyDraft({ ...policy, scan_symbols: e.target.value.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean) })}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2.5">
                <Label htmlFor="lev-overnight" className="text-sm">Allow overnight holds</Label>
                <Switch
                  id="lev-overnight"
                  checked={policy.allow_overnight}
                  onCheckedChange={(checked) => setPolicyDraft({ ...policy, allow_overnight: checked })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lev-max-hold" className="text-xs text-muted-foreground">
                  Max hold (days) {policy.allow_overnight ? '' : '— inactive while overnight off'}
                </Label>
                <Input
                  id="lev-max-hold"
                  className="font-mono tabular-nums"
                  value={policy.max_hold_days}
                  disabled={!policy.allow_overnight}
                  onChange={(e) => setPolicyDraft({ ...policy, max_hold_days: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
        )}
      </SectionCard>

      <HeldPositionsCard
        positions={positions}
        busy={positionsBusy}
        policy={policy}
        onRefresh={loadPositions}
        onClose={async (code, qty, reason) => {
          try {
            await closeLeveragedPosition(code, qty, reason)
            await Promise.all([loadPositions(), loadAll()])
          } catch (e) {
            onError(e instanceof Error ? e.message : 'close failed')
          }
        }}
        onAdopt={async (code, sl, tp) => {
          try {
            await adoptLeveragedPosition(code, sl, tp)
            await Promise.all([loadPositions(), loadAll()])
          } catch (e) {
            onError(e instanceof Error ? e.message : 'adopt failed')
          }
        }}
      />

      <SectionCard
        title="Open Trades"
        description="Live leveraged positions"
        noPadding
      >
        {openTrades.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
            <Inbox className="size-5 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No open leveraged trades.</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="text-xs">Symbol</TableHead>
                <TableHead className="text-xs">Dir</TableHead>
                <TableHead className="text-right text-xs">Qty</TableHead>
                <TableHead className="text-right text-xs">Entry</TableHead>
                <TableHead className="text-right text-xs">Current</TableHead>
                <TableHead className="text-right text-xs">Notional</TableHead>
                <TableHead className="text-right text-xs">P/L</TableHead>
                <TableHead className="text-right text-xs"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {openTrades.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">{row.symbol}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{row.direction.toUpperCase()}</TableCell>
                  <TableCell className="text-right font-mono text-muted-foreground tabular-nums">
                    {row.quantity.toFixed(4)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Money value={row.entry_price} />
                  </TableCell>
                  <TableCell className="text-right">
                    {row.current_price ? <Money value={row.current_price} /> : '—'}
                  </TableCell>
                  <TableCell className="text-right">
                    <Money value={row.entry_notional} />
                  </TableCell>
                  <TableCell className="text-right">
                    {row.current_pnl_value !== null && row.current_pnl_value !== undefined ? (
                      <span className="font-mono tabular-nums">
                        <MoneyDelta value={row.current_pnl_value} />
                        <span className={(row.current_pnl_value || 0) >= 0 ? 'text-positive' : 'text-negative'}>
                          {' '}({((row.current_pnl_pct || 0) * 100).toFixed(2)}%)
                        </span>
                      </span>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void closeLeveragedTrade(row.id, 'manual').then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'close failed'))}
                    >
                      Close
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>

      <SectionCard
        title="Signal Queue"
        description="Archie proposals before execution"
        noPadding
      >
        {signals.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
            <Inbox className="size-5 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No proposed signals right now.</p>
          </div>
        ) : (
          <div className="divide-y divide-border/60">
            {signals.map((row) => (
              <div key={row.id} className="space-y-2 px-5 py-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-positive/10 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-positive">
                      {row.direction.toUpperCase()}
                    </span>
                    <span className="font-medium">{row.symbol}</span>
                    <span className="text-xs text-muted-foreground">{dayjs(row.created_at).format('MMM D HH:mm')}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
                      Conf <span className="font-mono tabular-nums text-foreground">{Math.round(row.confidence * 100)}%</span>
                    </span>
                    <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
                      Edge <span className="font-mono tabular-nums text-foreground">{(row.expected_edge * 100).toFixed(1)}%</span>
                    </span>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">{row.rationale}</p>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">
                    Target <Money value={row.target_notional} className="text-foreground" />
                  </span>
                  <Button
                    size="sm"
                    onClick={() => void executeLeveragedSignal(row.id).then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'execute failed'))}
                  >
                    Execute
                    <ArrowRight />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  )
}

function HeldPositionsCard({
  positions,
  busy,
  policy,
  onRefresh,
  onClose,
  onAdopt,
}: {
  positions: HeldLeveragedPosition[] | null
  busy: boolean
  policy: LeveragedConfig | null
  onRefresh: () => void
  onClose: (code: string, qty: number | undefined, reason: string) => void | Promise<void>
  onAdopt: (code: string, sl: number | undefined, tp: number | undefined) => void | Promise<void>
}) {
  const rows = positions ?? []
  return (
    <SectionCard
      title="Positions"
      description="Live leveraged ETPs held in Trading 212 — close or bring under engine management"
      noPadding
      action={
        <Button
          variant="outline"
          size="sm"
          onClick={() => onRefresh()}
          disabled={busy}
          title="Refresh positions"
          className="px-2 sm:px-3"
        >
          <RefreshCw />
          <span className="hidden sm:inline">Refresh</span>
        </Button>
      }
    >
      {positions === null ? (
        <div className="px-6 py-12 text-center text-sm text-muted-foreground">Loading positions…</div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
          <Layers className="size-5 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">No leveraged positions held (or T212 unavailable).</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="text-xs">Position</TableHead>
              <TableHead className="text-xs">Dir</TableHead>
              <TableHead className="text-right text-xs">Qty</TableHead>
              <TableHead className="text-right text-xs">Avg</TableHead>
              <TableHead className="text-right text-xs">Current</TableHead>
              <TableHead className="text-right text-xs">Notional</TableHead>
              <TableHead className="text-right text-xs">Unrealised</TableHead>
              <TableHead className="text-right text-xs">Held</TableHead>
              <TableHead className="text-xs">Status</TableHead>
              <TableHead className="text-right text-xs"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={`${row.account_kind}-${row.instrument_code}`}>
                <TableCell className="font-medium">
                  <div className="flex flex-col">
                    <span>{row.underlying || row.symbol}</span>
                    <span className="text-[11px] text-muted-foreground">{row.name}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={row.direction === 'inverse' ? 'text-rose-500' : 'text-emerald-500'}
                  >
                    {row.direction === 'inverse' ? 'Inverse' : 'Long'}
                    {row.factor ? ` ${row.factor}x` : ''}
                  </Badge>
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                  {row.quantity.toFixed(4)}
                </TableCell>
                <TableCell className="text-right">
                  <Money value={row.avg_price} />
                </TableCell>
                <TableCell className="text-right">
                  {row.current_price != null ? <Money value={row.current_price} /> : '—'}
                </TableCell>
                <TableCell className="text-right">
                  <Money value={row.notional} />
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">
                  <MoneyDelta value={row.unrealized_pnl_value} />
                  <span className={row.unrealized_pnl_value >= 0 ? 'text-positive' : 'text-negative'}>
                    {' '}({(row.unrealized_pnl_pct * 100).toFixed(2)}%)
                  </span>
                </TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {row.days_held != null ? `${row.days_held}d` : '—'}
                </TableCell>
                <TableCell>
                  {row.tracked ? (
                    <Badge variant="outline" className="text-[10px] text-sky-500">
                      tracked
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      untracked
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    {!row.tracked ? (
                      <AdoptPositionDialog position={row} policy={policy} onAdopt={onAdopt} disabled={busy} />
                    ) : null}
                    <ClosePositionDialog position={row} onClose={onClose} disabled={busy} />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </SectionCard>
  )
}

function ClosePositionDialog({
  position,
  onClose,
  disabled,
}: {
  position: HeldLeveragedPosition
  onClose: (code: string, qty: number | undefined, reason: string) => void | Promise<void>
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const full = Math.abs(position.quantity)
  const [qty, setQty] = useState(String(full))

  useEffect(() => {
    if (open) setQty(String(full))
  }, [open, full])

  const parsed = Number(qty)
  const valid = Number.isFinite(parsed) && parsed > 0 && parsed <= full + 1e-9
  const isPartial = valid && parsed < full - 1e-9

  return (
    <>
      <Button variant="ghost" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
        Close
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Close {position.underlying || position.symbol}</DialogTitle>
          <DialogDescription>
            {position.name} — holding {full} share{full === 1 ? '' : 's'}. Sells at market on Trading 212.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="close-qty" className="text-xs text-muted-foreground">
            Quantity to sell
          </Label>
          <Input
            id="close-qty"
            className="font-mono tabular-nums"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            {isPartial ? `Partial close — ${(full - parsed).toFixed(4)} will remain open.` : 'Full close.'}
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!valid || submitting}
            onClick={async () => {
              setSubmitting(true)
              await onClose(position.instrument_code, isPartial ? parsed : undefined, 'manual')
              setSubmitting(false)
              setOpen(false)
            }}
          >
            {isPartial ? 'Close part' : 'Close all'}
          </Button>
        </DialogFooter>
      </DialogContent>
      </Dialog>
    </>
  )
}

function AdoptPositionDialog({
  position,
  policy,
  onAdopt,
  disabled,
}: {
  position: HeldLeveragedPosition
  policy: LeveragedConfig | null
  onAdopt: (code: string, sl: number | undefined, tp: number | undefined) => void | Promise<void>
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [tp, setTp] = useState('')
  const [sl, setSl] = useState('')

  useEffect(() => {
    if (open) {
      setTp(((policy?.take_profit_pct ?? 0.4) * 100).toFixed(1))
      setSl(((policy?.stop_loss_pct ?? 0.05) * 100).toFixed(1))
    }
  }, [open, policy])

  return (
    <>
      <Button variant="ghost" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
        Adopt
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Adopt {position.underlying || position.symbol}</DialogTitle>
          <DialogDescription>
            Bring this position under engine management (stop / take-profit + age governance). No order is placed.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="adopt-sl" className="text-xs text-muted-foreground">
              Stop loss (%)
            </Label>
            <Input id="adopt-sl" className="font-mono tabular-nums" value={sl} onChange={(e) => setSl(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="adopt-tp" className="text-xs text-muted-foreground">
              Take profit (%)
            </Label>
            <Input id="adopt-tp" className="font-mono tabular-nums" value={tp} onChange={(e) => setTp(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={submitting}
            onClick={async () => {
              setSubmitting(true)
              await onAdopt(position.instrument_code, Number(sl) / 100, Number(tp) / 100)
              setSubmitting(false)
              setOpen(false)
            }}
          >
            Adopt
          </Button>
        </DialogFooter>
      </DialogContent>
      </Dialog>
    </>
  )
}
