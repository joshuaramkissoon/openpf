import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  createChart,
  ColorType,
  type IChartApi,
  type LogicalRange,
} from 'lightweight-charts'
import {
  fetchChartData,
  fetchForecast,
  type ChartData,
  type ForecastData,
  type MACDPoint,
  type IndicatorPoint,
} from '../api/charts'

interface Props {
  ticker: string
  period?: string
  interval?: string
  chartType?: 'candlestick' | 'line'
  indicators?: string[]
  height?: number
  /** Overlay a Kronos probabilistic forecast cone (daily interval only). */
  forecast?: boolean
  forecastHorizon?: number
  forecastSamples?: number
  forecastLookback?: number
}

const FORECAST_COLOR = '#dcb45c'

const OVERLAY_COLORS: Record<string, { color: string; style?: 'dashed' }> = {
  sma20: { color: '#6ea8d4' },
  sma50: { color: '#d8a85c' },
  sma200: { color: '#a98fcf' },
  bollinger_upper: { color: '#9E9E9E', style: 'dashed' },
  bollinger_lower: { color: '#9E9E9E', style: 'dashed' },
  bollinger_middle: { color: '#9E9E9E' },
}

/** A single legend entry: a colour swatch plus a label. */
function LegendItem({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      {swatch}
      <span>{children}</span>
    </span>
  )
}

function isMACDData(arr: unknown[]): arr is MACDPoint[] {
  if (arr.length === 0) return false
  const first = arr[0] as Record<string, unknown>
  return 'macd' in first && 'signal' in first && 'histogram' in first
}

function isIndicatorData(arr: unknown[]): arr is IndicatorPoint[] {
  if (arr.length === 0) return false
  const first = arr[0] as Record<string, unknown>
  return 'value' in first && !('macd' in first)
}

function buildChartOptions(width: number, height: number) {
  return {
    width,
    height,
    layout: {
      background: { type: ColorType.Solid as const, color: 'transparent' },
      textColor: '#a3a09a',
      attributionLogo: false as const,
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,0.05)' },
      horzLines: { color: 'rgba(255,255,255,0.05)' },
    },
    crosshair: { mode: 0 as const },
    rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
    timeScale: { borderColor: 'rgba(255,255,255,0.1)' },
    // Disable mouse wheel so chat scroll works — click-drag still pans
    handleScroll: {
      mouseWheel: false,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      mouseWheel: false,
      pinch: false,
      axisPressedMouseMove: true,
      axisDoubleClickReset: true,
    },
  }
}

export function StockChart({
  ticker,
  period = '3mo',
  interval = '1d',
  chartType = 'candlestick',
  indicators = [],
  height = 300,
  forecast = false,
  forecastHorizon = 30,
  forecastSamples = 20,
  forecastLookback,
}: Props) {
  const mainRef = useRef<HTMLDivElement>(null)
  const panelRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const chartsRef = useRef<IChartApi[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<ChartData | null>(null)
  const [collapsed, setCollapsed] = useState(false)

  // Forecast cone state (only fetched when `forecast` is enabled).
  const forecastEnabled = forecast && interval === '1d'
  const [forecastData, setForecastData] = useState<ForecastData | null>(null)
  const [forecastError, setForecastError] = useState<string | null>(null)
  const [forecastLoading, setForecastLoading] = useState(false)

  // Determine which panels we expect from the data
  const panelKeys = data ? Object.keys(data.panels) : []

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    fetchChartData({
      ticker,
      period,
      interval,
      indicators: indicators.length > 0 ? indicators.join(',') : undefined,
    })
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'Failed to load chart data'
          setError(message)
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [ticker, period, interval, indicators.join(',')])

  // Fetch the Kronos forecast cone when enabled.
  useEffect(() => {
    if (!forecastEnabled) {
      setForecastData(null)
      setForecastError(null)
      return
    }
    let cancelled = false
    setForecastLoading(true)
    setForecastError(null)

    fetchForecast({ ticker, horizon: forecastHorizon, samples: forecastSamples, lookback: forecastLookback })
      .then((result) => {
        if (!cancelled) {
          setForecastData(result)
          setForecastLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'Failed to load forecast'
          setForecastError(message)
          setForecastData(null)
          setForecastLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [forecastEnabled, ticker, forecastHorizon, forecastSamples, forecastLookback])

  // Render charts once data is loaded
  useEffect(() => {
    if (!data || !mainRef.current || collapsed) return

    // Cleanup previous chart instances
    chartsRef.current.forEach((c) => c.remove())
    chartsRef.current = []

    const container = mainRef.current
    const containerWidth = container.clientWidth || 600

    // --- Main chart ---
    const mainPanelHeight = panelKeys.length > 0 ? Math.round(height * 0.65) : height
    const mainChart = createChart(container, buildChartOptions(containerWidth, mainPanelHeight))
    chartsRef.current.push(mainChart)

    // Primary series
    if (chartType === 'candlestick') {
      const candleSeries = mainChart.addCandlestickSeries({
        upColor: '#5fb98a',
        downColor: '#e0635a',
        borderUpColor: '#5fb98a',
        borderDownColor: '#e0635a',
        wickUpColor: '#5fb98a',
        wickDownColor: '#e0635a',
      })
      candleSeries.setData(
        data.candles.map((c) => ({
          time: c.time as string,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      )
    } else {
      const lineSeries = mainChart.addLineSeries({ color: '#6ea8d4', lineWidth: 2 })
      lineSeries.setData(
        data.candles.map((c) => ({
          time: c.time as string,
          value: c.close,
        }))
      )
    }

    // Overlay indicators on main chart
    if (data.overlays) {
      for (const [name, points] of Object.entries(data.overlays)) {
        const cfg = OVERLAY_COLORS[name] || { color: '#888' }
        const series = mainChart.addLineSeries({
          color: cfg.color,
          lineWidth: 1,
          lineStyle: cfg.style === 'dashed' ? 2 : 0,
        })
        series.setData(
          points.map((p) => ({
            time: p.time as string,
            value: p.value,
          }))
        )
      }
    }

    // --- Forecast cone (Kronos) ---
    if (forecastData && forecastData.forecast.length > 0 && data.candles.length > 0) {
      const lastCandle = data.candles[data.candles.length - 1]
      const anchorTime = lastCandle.time as string
      const anchorClose = lastCandle.close

      // Anchor each band at the last real close so the cone connects.
      const median = [
        { time: anchorTime, value: anchorClose },
        ...forecastData.forecast.map((p) => ({ time: p.date, value: p.p50 })),
      ]
      const upper = [
        { time: anchorTime, value: anchorClose },
        ...forecastData.forecast.map((p) => ({ time: p.date, value: p.p90 })),
      ]
      const lower = [
        { time: anchorTime, value: anchorClose },
        ...forecastData.forecast.map((p) => ({ time: p.date, value: p.p10 })),
      ]

      const upperSeries = mainChart.addLineSeries({
        color: FORECAST_COLOR,
        lineWidth: 1,
        lineStyle: 2, // dashed
        priceLineVisible: false,
        lastValueVisible: false,
      })
      upperSeries.setData(upper)

      const lowerSeries = mainChart.addLineSeries({
        color: FORECAST_COLOR,
        lineWidth: 1,
        lineStyle: 2, // dashed
        priceLineVisible: false,
        lastValueVisible: false,
      })
      lowerSeries.setData(lower)

      const medianSeries = mainChart.addLineSeries({
        color: FORECAST_COLOR,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      })
      medianSeries.setData(median)
    }

    mainChart.timeScale().fitContent()

    // --- Sub-panel charts ---
    const subPanelHeight = panelKeys.length > 0 ? Math.round((height * 0.35) / panelKeys.length) : 0
    const subCharts: IChartApi[] = []

    for (const panelName of panelKeys) {
      const el = panelRefs.current[panelName]
      if (!el) continue

      const panelChart = createChart(el, {
        ...buildChartOptions(containerWidth, subPanelHeight),
        timeScale: {
          borderColor: 'rgba(255,255,255,0.1)',
          visible: panelName === panelKeys[panelKeys.length - 1], // only last panel shows time axis
        },
      })
      chartsRef.current.push(panelChart)
      subCharts.push(panelChart)

      const panelData = data.panels[panelName]
      if (!panelData || panelData.length === 0) continue

      if (panelName === 'macd' && isMACDData(panelData)) {
        // MACD histogram as a histogram series
        const histSeries = panelChart.addHistogramSeries({
          priceLineVisible: false,
          lastValueVisible: false,
        })
        histSeries.setData(
          panelData.map((p) => ({
            time: p.time as string,
            value: p.histogram,
            color:
              p.histogram >= 0 ? 'rgba(95, 185, 138, 0.45)' : 'rgba(224, 99, 90, 0.45)',
          }))
        )

        // MACD line
        const macdLine = panelChart.addLineSeries({
          color: '#6ea8d4',
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        macdLine.setData(
          panelData.map((p) => ({
            time: p.time as string,
            value: p.macd,
          }))
        )

        // Signal line
        const signalLine = panelChart.addLineSeries({
          color: '#d8a85c',
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        signalLine.setData(
          panelData.map((p) => ({
            time: p.time as string,
            value: p.signal,
          }))
        )
      } else if (panelName === 'rsi' && isIndicatorData(panelData)) {
        // RSI line
        const rsiLine = panelChart.addLineSeries({
          color: '#cf6a98',
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        rsiLine.setData(
          panelData.map((p) => ({
            time: p.time as string,
            value: p.value,
          }))
        )

        // Reference lines at 30 and 70
        rsiLine.createPriceLine({
          price: 70,
          color: 'rgba(255,255,255,0.2)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: '',
        })
        rsiLine.createPriceLine({
          price: 30,
          color: 'rgba(255,255,255,0.2)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: '',
        })
      } else if (panelName === 'atr' && isIndicatorData(panelData)) {
        const atrLine = panelChart.addLineSeries({
          color: '#795548',
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        atrLine.setData(
          panelData.map((p) => ({
            time: p.time as string,
            value: p.value,
          }))
        )
      } else if (isIndicatorData(panelData)) {
        // Generic indicator panel
        const genLine = panelChart.addLineSeries({
          color: '#888',
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        genLine.setData(
          panelData.map((p) => ({
            time: p.time as string,
            value: p.value,
          }))
        )
      }

      panelChart.timeScale().fitContent()
    }

    // --- Sync time scales ---
    let isSyncing = false
    const allCharts = [mainChart, ...subCharts]

    for (const chart of allCharts) {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
        if (isSyncing || !range) return
        isSyncing = true
        for (const other of allCharts) {
          if (other !== chart) {
            other.timeScale().setVisibleLogicalRange(range)
          }
        }
        isSyncing = false
      })
    }

    // --- ResizeObserver ---
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = entry.contentRect.width
        if (w > 0) {
          mainChart.applyOptions({ width: w })
          for (const sc of subCharts) {
            sc.applyOptions({ width: w })
          }
        }
      }
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      chartsRef.current.forEach((c) => c.remove())
      chartsRef.current = []
    }
  }, [data, forecastData, chartType, height, collapsed, panelKeys.join(',')])

  if (loading) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
        Loading chart for {ticker}…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-negative">
        Chart error: {error}
      </div>
    )
  }

  const showSma20 = indicators.some((i) => i.toLowerCase() === 'sma20')
  const showSma50 = indicators.some((i) => i.toLowerCase() === 'sma50')

  return (
    <div className="w-full">
      <div className="mb-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-2 text-sm text-foreground transition-colors hover:text-foreground/80"
        >
          <span className="font-semibold tracking-tight">{ticker}</span>
          <span className="text-xs font-normal text-muted-foreground">
            {period} · {chartType}
          </span>
          <span className="text-xs text-muted-foreground">{collapsed ? '▸' : '▾'}</span>
        </button>

        {!collapsed && (
          <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-[11px] text-muted-foreground">
            <LegendItem
              swatch={
                <span className="flex gap-0.5">
                  <i className="h-2.5 w-1 rounded-[1px]" style={{ background: '#5fb98a' }} />
                  <i className="h-2.5 w-1 rounded-[1px]" style={{ background: '#e0635a' }} />
                </span>
              }
            >
              Price
            </LegendItem>
            {showSma20 && (
              <LegendItem swatch={<i className="h-0.5 w-3.5 rounded-full" style={{ background: '#6ea8d4' }} />}>
                SMA 20
              </LegendItem>
            )}
            {showSma50 && (
              <LegendItem swatch={<i className="h-0.5 w-3.5 rounded-full" style={{ background: '#d8a85c' }} />}>
                SMA 50
              </LegendItem>
            )}
            {forecastData && (
              <LegendItem
                swatch={
                  <span
                    className="inline-block w-3.5 border-t-2 border-dashed"
                    style={{ borderColor: FORECAST_COLOR }}
                  />
                }
              >
                Kronos cone (p10–p50–p90)
              </LegendItem>
            )}
          </div>
        )}
      </div>

      {!collapsed && (
        <div className="flex flex-col gap-2">
          {forecastEnabled && (
            <div className="text-xs">
              {forecastLoading && <span className="text-muted-foreground">Forecasting {ticker} (Kronos)…</span>}
              {forecastError && <span className="text-negative">Forecast unavailable: {forecastError}</span>}
              {forecastData && (
                <span className="text-muted-foreground">
                  <span className="font-medium" style={{ color: FORECAST_COLOR }}>
                    {forecastData.horizon}d median
                  </span>{' '}
                  <span className={forecastData.summary.expected_return_pct >= 0 ? 'text-positive' : 'text-negative'}>
                    {forecastData.summary.expected_return_pct >= 0 ? '+' : ''}
                    {forecastData.summary.expected_return_pct.toFixed(1)}%
                  </span>{' '}
                  · P(up) {(forecastData.summary.prob_up * 100).toFixed(0)}% · band ±
                  {(forecastData.summary.terminal_spread_pct / 2).toFixed(1)}%
                  <span className="ml-1 opacity-70">({forecastData.model.split('/').pop()})</span>
                </span>
              )}
            </div>
          )}
          <div ref={mainRef} style={{ width: '100%' }} />
          {panelKeys.map((key) => (
            <div key={key} className="border-t border-border/40 pt-1.5">
              <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{key}</span>
              <div
                ref={(el) => {
                  panelRefs.current[key] = el
                }}
                style={{ width: '100%' }}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
