import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { ArrowRight, Inbox, RefreshCw, RotateCcw, Scan, TrendingUp } from 'lucide-react'

import {
  closeLeveragedTrade,
  executeLeveragedSignal,
  getLeveragedSnapshot,
  patchLeveragedPolicy,
  refreshInstrumentCache,
  runLeveragedCycle,
  runLeveragedScan,
} from '../api/client'
import { Money, MoneyDelta, Pct, SectionCard, StatCard } from '@/components/kit'
import { Button } from '@/components/ui/button'
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
import type { LeveragedConfig, LeveragedSnapshot } from '../types'

interface Props {
  onError: (message: string | null) => void
}

export function LeveragedWorkspace({ onError }: Props) {
  const [snapshot, setSnapshot] = useState<LeveragedSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const [policyDraft, setPolicyDraft] = useState<LeveragedConfig | null>(null)

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

  useEffect(() => {
    void loadAll()
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

  return (
    <div className="space-y-6">
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

            <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2.5 sm:w-1/2">
              <Label htmlFor="lev-overnight" className="text-sm">Allow overnight holds</Label>
              <Switch
                id="lev-overnight"
                checked={policy.allow_overnight}
                onCheckedChange={(checked) => setPolicyDraft({ ...policy, allow_overnight: checked })}
              />
            </div>
          </div>
        )}
      </SectionCard>

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
