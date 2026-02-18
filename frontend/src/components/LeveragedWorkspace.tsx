import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'

import {
  closeLeveragedTrade,
  executeLeveragedSignal,
  getLeveragedSnapshot,
  patchLeveragedPolicy,
  refreshInstrumentCache,
  runLeveragedCycle,
  runLeveragedScan,
} from '../api/client'
import type { LeveragedConfig, LeveragedSnapshot } from '../types'

interface Props {
  onError: (message: string | null) => void
}

function money(value: number, symbol = '\u00a3') {
  return `${symbol}${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
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
    <div className="leveraged-grid">
      <section className="glass-card leveraged-summary-card">
        <div className="section-heading-row">
          <h2>Leveraged Desk</h2>
          <div className="intent-actions">
            <button className="btn" onClick={() => void loadAll()} disabled={busy}>Refresh</button>
            <button className="btn" onClick={() => void runLeveragedScan().then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'scan failed'))} disabled={busy}>Scan</button>
            <button className="btn primary" onClick={() => void runLeveragedCycle().then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'cycle failed'))} disabled={busy}>Run Cycle</button>
            <button className="btn ghost" onClick={() => void refreshInstrumentCache().then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'cache refresh failed'))} disabled={busy}>Refresh Instruments</button>
          </div>
        </div>
        <div className="metric-grid leveraged-metrics">
          <article className="glass-card metric-card">
            <span className="metric-label">Open Exposure</span>
            <strong className="metric-value">{summary ? money(summary.open_exposure) : '\u2014'}</strong>
          </article>
          <article className="glass-card metric-card">
            <span className="metric-label">Open Unrealized</span>
            <strong className="metric-value">{summary ? money(summary.open_unrealized_pnl) : '\u2014'}</strong>
          </article>
          <article className="glass-card metric-card">
            <span className="metric-label">Realized P/L</span>
            <strong className="metric-value">{summary ? money(summary.closed_realized_pnl) : '\u2014'}</strong>
          </article>
          <article className="glass-card metric-card">
            <span className="metric-label">Win Rate</span>
            <strong className="metric-value">{summary ? `${(summary.win_rate * 100).toFixed(1)}%` : '\u2014'}</strong>
          </article>
        </div>
      </section>

      <section className="glass-card leveraged-policy-card">
        <div className="section-heading-row">
          <h2>Risk Rails</h2>
          <button className="btn" onClick={() => void savePolicy()} disabled={!policy || busy}>Save</button>
        </div>
        {policy && (
          <div className="leveraged-policy-form">
            <label className="check">
              <input
                type="checkbox"
                checked={policy.enabled}
                onChange={(e) => setPolicyDraft({ ...policy, enabled: e.target.checked })}
              />
              Leveraged system enabled
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={policy.auto_execute_enabled}
                onChange={(e) => setPolicyDraft({ ...policy, auto_execute_enabled: e.target.checked })}
              />
              Auto execute within rails
            </label>
            <label>
              Per position ({'\u00a3'})
              <input
                value={policy.per_position_notional}
                onChange={(e) => setPolicyDraft({ ...policy, per_position_notional: Number(e.target.value) })}
              />
            </label>
            <label>
              Max exposure ({'\u00a3'})
              <input
                value={policy.max_total_exposure}
                onChange={(e) => setPolicyDraft({ ...policy, max_total_exposure: Number(e.target.value) })}
              />
            </label>
            <label>
              Max open positions
              <input
                value={policy.max_open_positions}
                onChange={(e) => setPolicyDraft({ ...policy, max_open_positions: Number(e.target.value) })}
              />
            </label>
            <label>
              Take profit (%)
              <input
                value={(policy.take_profit_pct * 100).toFixed(2)}
                onChange={(e) => setPolicyDraft({ ...policy, take_profit_pct: Number(e.target.value) / 100 })}
              />
            </label>
            <label>
              Stop loss (%)
              <input
                value={(policy.stop_loss_pct * 100).toFixed(2)}
                onChange={(e) => setPolicyDraft({ ...policy, stop_loss_pct: Number(e.target.value) / 100 })}
              />
            </label>
            <label>
              Close time UK
              <input
                value={policy.close_time_uk}
                onChange={(e) => setPolicyDraft({ ...policy, close_time_uk: e.target.value })}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={policy.allow_overnight}
                onChange={(e) => setPolicyDraft({ ...policy, allow_overnight: e.target.checked })}
              />
              Allow overnight holds
            </label>
            <label>
              Scan symbols (comma separated)
              <input
                value={policy.scan_symbols.join(', ')}
                onChange={(e) => setPolicyDraft({ ...policy, scan_symbols: e.target.value.split(',').map((x) => x.trim().toUpperCase()).filter(Boolean) })}
              />
            </label>
          </div>
        )}
      </section>

      <section className="glass-card leveraged-open-trades">
        <div className="section-heading-row">
          <h2>Open Trades</h2>
          <span className="hint">ISA leveraged positions tracked in SQLite + markdown logs</span>
        </div>
        <div className="table-scroll leveraged-table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Dir</th>
                <th>Qty</th>
                <th>Entry</th>
                <th>Current</th>
                <th>Notional</th>
                <th>P/L</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {openTrades.map((row) => (
                <tr key={row.id}>
                  <td>{row.symbol}</td>
                  <td>{row.direction.toUpperCase()}</td>
                  <td>{row.quantity.toFixed(4)}</td>
                  <td>{money(row.entry_price)}</td>
                  <td>{row.current_price ? money(row.current_price) : '\u2014'}</td>
                  <td>{money(row.entry_notional)}</td>
                  <td className={(row.current_pnl_value || 0) >= 0 ? 'up' : 'down'}>
                    {row.current_pnl_value !== null && row.current_pnl_value !== undefined
                      ? `${money(row.current_pnl_value)} (${((row.current_pnl_pct || 0) * 100).toFixed(2)}%)`
                      : '\u2014'}
                  </td>
                  <td>
                    <button
                      className="btn ghost"
                      onClick={() => void closeLeveragedTrade(row.id, 'manual').then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'close failed'))}
                    >
                      Close
                    </button>
                  </td>
                </tr>
              ))}
              {openTrades.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">No open leveraged trades.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="glass-card leveraged-signals-card">
        <div className="section-heading-row">
          <h2>Signal Queue</h2>
          <span className="hint">Archie proposals before execution</span>
        </div>
        <div className="intents-list leveraged-signals-list">
          {signals.map((row) => (
            <article key={row.id} className="intent-item">
              <div className="intent-head">
                <div>
                  <span className="pill ok">{row.direction.toUpperCase()}</span>
                  <strong>{row.symbol}</strong>
                  <span className="muted">{dayjs(row.created_at).format('MMM D HH:mm')}</span>
                </div>
                <span className="status approved">{Math.round(row.confidence * 100)}%</span>
              </div>
              <p>{row.rationale}</p>
              <div className="intent-actions">
                <span className="muted">Target {money(row.target_notional)}</span>
                <button className="btn" onClick={() => void executeLeveragedSignal(row.id).then(loadAll).catch((e) => onError(e instanceof Error ? e.message : 'execute failed'))}>
                  Execute
                </button>
              </div>
            </article>
          ))}
          {signals.length === 0 && <p className="muted">No proposed signals right now.</p>}
        </div>
      </section>
    </div>
  )
}
