import { useState } from 'react'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { runBacktest } from '../api/client'
import type { BacktestResult } from '../types'

interface Props {
  onError: (message: string) => void
}

export function BacktestLab({ onError }: Props) {
  const [symbol, setSymbol] = useState('AAPL')
  const [lookback, setLookback] = useState(365)
  const [fast, setFast] = useState(20)
  const [slow, setSlow] = useState(100)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)

  async function submit() {
    setLoading(true)
    try {
      const data = await runBacktest({
        symbol,
        lookback_days: lookback,
        fast_window: fast,
        slow_window: slow,
      })
      setResult(data)
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Backtest failed'
      onError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="glass-card backtest-card">
      <div className="section-heading-row">
        <h2>Strategy Lab</h2>
        <span className="hint">Fast/Slow moving-average crossover</span>
      </div>

      <div className="backtest-form">
        <label>
          Symbol
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        </label>
        <label>
          Lookback
          <input type="number" value={lookback} onChange={(e) => setLookback(Number(e.target.value))} />
        </label>
        <label>
          Fast
          <input type="number" value={fast} onChange={(e) => setFast(Number(e.target.value))} />
        </label>
        <label>
          Slow
          <input type="number" value={slow} onChange={(e) => setSlow(Number(e.target.value))} />
        </label>
        <button className="btn primary" onClick={submit} disabled={loading}>
          {loading ? 'Running...' : 'Run Backtest'}
        </button>
      </div>

      {result && (
        <>
          <div className="bt-stats">
            <span>CAGR {((result.cagr || 0) * 100).toFixed(1)}%</span>
            <span>Sharpe {(result.sharpe || 0).toFixed(2)}</span>
            <span>Max DD {((result.max_drawdown || 0) * 100).toFixed(1)}%</span>
            <span>Win Rate {((result.win_rate || 0) * 100).toFixed(1)}%</span>
            <span>Trades {result.trades}</span>
          </div>
          <div className="bt-chart-wrap">
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={result.equity_curve}>
                <XAxis dataKey="date" hide />
                <YAxis domain={['auto', 'auto']} />
                <Tooltip />
                <Line type="monotone" dataKey="strategy" stroke="#3ad98f" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="benchmark" stroke="#88a2ff" dot={false} strokeWidth={1.5} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </section>
  )
}
