import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  ColorType,
  type IChartApi,
  type LogicalRange,
} from 'lightweight-charts'
import { fetchChartData, type ChartData, type MACDPoint, type IndicatorPoint } from '../api/charts'

interface Props {
  ticker: string
  period?: string
  interval?: string
  chartType?: 'candlestick' | 'line'
  indicators?: string[]
  height?: number
}

const OVERLAY_COLORS: Record<string, { color: string; style?: 'dashed' }> = {
  sma20: { color: '#2196F3' },
  sma50: { color: '#FF9800' },
  sma200: { color: '#9C27B0' },
  bollinger_upper: { color: '#9E9E9E', style: 'dashed' },
  bollinger_lower: { color: '#9E9E9E', style: 'dashed' },
  bollinger_middle: { color: '#9E9E9E' },
}

const INDICATOR_LABELS: Record<string, string> = {
  sma20: 'SMA 20',
  sma50: 'SMA 50',
  sma200: 'SMA 200',
  rsi: 'RSI',
  macd: 'MACD',
  atr: 'ATR',
  bbands: 'Bollinger',
  bollinger: 'Bollinger',
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
      textColor: '#999',
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
}: Props) {
  const mainRef = useRef<HTMLDivElement>(null)
  const panelRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const chartsRef = useRef<IChartApi[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<ChartData | null>(null)
  const [collapsed, setCollapsed] = useState(false)

  // Determine which panels we expect from the data
  const panelKeys = data ? Object.keys(data.panels) : []

  // Build indicator label chips
  const indicatorChips = indicators.map((ind) => INDICATOR_LABELS[ind.toLowerCase()] || ind.toUpperCase())

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
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
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
      const lineSeries = mainChart.addLineSeries({ color: '#2196F3', lineWidth: 2 })
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
              p.histogram >= 0 ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)',
          }))
        )

        // MACD line
        const macdLine = panelChart.addLineSeries({
          color: '#2196F3',
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
          color: '#FF9800',
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
          color: '#E91E63',
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
  }, [data, chartType, height, collapsed, panelKeys.join(',')])

  if (loading) {
    return (
      <div className="stock-chart-wrapper">
        <div className="stock-chart-loading">Loading chart for {ticker}...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="stock-chart-wrapper">
        <div className="stock-chart-error">Chart error: {error}</div>
      </div>
    )
  }

  return (
    <div className="stock-chart-wrapper">
      <button className="stock-chart-header" onClick={() => setCollapsed((c) => !c)}>
        <span className="stock-chart-ticker">{ticker}</span>
        <span className="stock-chart-meta">
          {period} · {chartType}
        </span>
        {indicatorChips.length > 0 && (
          <span className="stock-chart-indicators">
            {indicatorChips.map((chip) => (
              <span key={chip} className="stock-chart-chip">{chip}</span>
            ))}
          </span>
        )}
        <span className="stock-chart-collapse-icon">{collapsed ? '▸' : '▾'}</span>
      </button>
      {!collapsed && (
        <div className="stock-chart-body">
          <div ref={mainRef} style={{ width: '100%' }} />
          {panelKeys.map((key) => (
            <div key={key} className="stock-chart-panel">
              <span className="stock-chart-panel-label">{key}</span>
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
