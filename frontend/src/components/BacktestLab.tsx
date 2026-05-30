import { useState } from 'react'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { runBacktest } from '../api/client'
import { Pct, SectionCard } from '@/components/kit'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatNumber } from '@/utils/format'
import type { BacktestResult } from '@/types'

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
    <SectionCard title="Strategy Lab" description="Fast/slow moving-average crossover">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bt-symbol" className="text-xs text-muted-foreground">
            Symbol
          </Label>
          <Input
            id="bt-symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="font-mono"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bt-lookback" className="text-xs text-muted-foreground">
            Lookback
          </Label>
          <Input
            id="bt-lookback"
            type="number"
            value={lookback}
            onChange={(e) => setLookback(Number(e.target.value))}
            className="font-mono tabular-nums"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bt-fast" className="text-xs text-muted-foreground">
            Fast
          </Label>
          <Input
            id="bt-fast"
            type="number"
            value={fast}
            onChange={(e) => setFast(Number(e.target.value))}
            className="font-mono tabular-nums"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bt-slow" className="text-xs text-muted-foreground">
            Slow
          </Label>
          <Input
            id="bt-slow"
            type="number"
            value={slow}
            onChange={(e) => setSlow(Number(e.target.value))}
            className="font-mono tabular-nums"
          />
        </div>
      </div>

      <div className="mt-4 flex justify-end">
        <Button onClick={submit} disabled={loading}>
          {loading ? 'Running…' : 'Run Backtest'}
        </Button>
      </div>

      {result && (
        <div className="mt-5 flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div className="flex flex-col gap-0.5 rounded-lg border border-border/60 bg-muted/20 p-3">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">CAGR</span>
              <Pct value={result.cagr} className="text-base" />
            </div>
            <div className="flex flex-col gap-0.5 rounded-lg border border-border/60 bg-muted/20 p-3">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Sharpe</span>
              <span className="font-mono text-base tabular-nums">{formatNumber(result.sharpe, 2)}</span>
            </div>
            <div className="flex flex-col gap-0.5 rounded-lg border border-border/60 bg-muted/20 p-3">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Max DD</span>
              <Pct value={result.max_drawdown} className="text-base text-negative" />
            </div>
            <div className="flex flex-col gap-0.5 rounded-lg border border-border/60 bg-muted/20 p-3">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Win Rate</span>
              <Pct value={result.win_rate} className="text-base" />
            </div>
            <div className="flex flex-col gap-0.5 rounded-lg border border-border/60 bg-muted/20 p-3">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Trades</span>
              <span className="font-mono text-base tabular-nums">{result.trades}</span>
            </div>
          </div>

          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={result.equity_curve}>
              <XAxis dataKey="date" hide />
              <YAxis
                domain={['auto', 'auto']}
                tick={{ fill: 'var(--color-muted-foreground)', fontSize: 11 }}
                stroke="var(--color-border)"
              />
              <Tooltip
                cursor={false}
                contentStyle={{
                  background: 'var(--color-popover)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 8,
                  color: 'var(--color-popover-foreground)',
                  fontSize: 12,
                }}
              />
              <Line type="monotone" dataKey="strategy" stroke="var(--color-positive)" dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="benchmark" stroke="var(--color-chart-2)" dot={false} strokeWidth={1.5} />
            </LineChart>
          </ResponsiveContainer>

          <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
            <span className="flex items-center gap-1.5">
              <i className="size-2 rounded-[3px] bg-positive" />
              <span className="text-foreground">Strategy</span>
            </span>
            <span className="flex items-center gap-1.5">
              <i className="size-2 rounded-[3px]" style={{ background: 'var(--color-chart-2)' }} />
              <span className="text-foreground">Benchmark</span>
            </span>
          </div>
        </div>
      )}
    </SectionCard>
  )
}
