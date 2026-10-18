import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { INTERVALS, mergeTick } from '../lib/bars'
import { fmtPrice, num } from '../lib/format'
import { historyKey, selectSymbolSpec, useStore } from '../store/useStore'
import type { Bar } from '../types'

const EMPTY_BARS: Bar[] = []

function toCandle(bar: Bar): CandlestickData {
  return {
    time: Math.floor(new Date(bar.open_time).getTime() / 1000) as UTCTimestamp,
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
  }
}

export function CandleChart() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const lastBarRef = useRef<Bar | null>(null)
  const lastTimeRef = useRef(0)

  const [direction, setDirection] = useState<'up' | 'down' | 'flat'>('flat')
  const prevPriceRef = useRef<number | null>(null)

  const interval = useStore((s) => s.interval)
  const setInterval = useStore((s) => s.setInterval)
  const bars = useStore((s) => s.history[historyKey(s.symbol, s.interval)] ?? EMPTY_BARS)
  const quote = useStore((s) => s.quotes[s.symbol])
  const spec = useStore(selectSymbolSpec)
  const loading = useStore((s) => s.loadingHistory)

  // 建图（只建一次）
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'rgba(19, 25, 34, 0.45)' },
        textColor: '#8b98a8',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: '#1a2330' },
        horzLines: { color: '#1a2330' },
      },
      rightPriceScale: {
        borderColor: '#222c39',
        scaleMargins: { top: 0.14, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#222c39',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        barSpacing: 7,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(79, 140, 255, 0.45)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#2b3a52',
        },
        horzLine: {
          color: 'rgba(79, 140, 255, 0.45)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#2b3a52',
        },
      },
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#1fbf8f',
      downColor: '#f2525f',
      borderVisible: false,
      wickUpColor: '#1fbf8f',
      wickDownColor: '#f2525f',
      priceLineVisible: true,
      priceLineColor: 'rgba(139, 152, 168, 0.45)',
      lastValueVisible: true,
    })

    chartRef.current = chart
    seriesRef.current = series

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      lastBarRef.current = null
      lastTimeRef.current = 0
    }
  }, [])

  // 历史数据：换标的 / 换周期 / 首次加载
  useEffect(() => {
    const series = seriesRef.current
    const chart = chartRef.current
    if (!series || !chart) return

    if (bars.length === 0) {
      series.setData([])
      lastBarRef.current = null
      lastTimeRef.current = 0
      return
    }

    const data = bars.map(toCandle)
    series.setData(data)
    lastBarRef.current = bars[bars.length - 1] ?? null
    lastTimeRef.current = Number(data[data.length - 1].time)
    chart.timeScale().fitContent()
  }, [bars])

  // 实时 tick：就地更新最后一根，跨周期时新开一根
  useEffect(() => {
    const series = seriesRef.current
    if (!series || !quote || !spec) return
    const base = lastBarRef.current
    if (!base) return

    const merged = mergeTick(base, quote, interval, spec.price_precision)
    const time = Math.floor(new Date(merged.open_time).getTime() / 1000)
    if (time < lastTimeRef.current) return

    lastBarRef.current = merged
    lastTimeRef.current = time
    try {
      series.update({
        time: time as UTCTimestamp,
        open: Number(merged.open),
        high: Number(merged.high),
        low: Number(merged.low),
        close: Number(merged.close),
      })
    } catch {
      /* 单帧异常不影响后续更新 */
    }
  }, [quote, interval, spec])

  // 价格方向（用于顶部价格闪色）
  useEffect(() => {
    const price = num(quote?.mid)
    const prev = prevPriceRef.current
    if (prev !== null && price !== prev) {
      setDirection(price > prev ? 'up' : 'down')
    }
    prevPriceRef.current = price
  }, [quote?.mid])

  const price = quote ? fmtPrice(quote.mid, spec?.price_precision ?? 2) : '—'

  return (
    <section className="chart-shell">
      <div className="chart-head">
        <span className="sym">{spec?.symbol ?? '—'}</span>
        <span className={`price ${direction === 'up' ? 'up' : direction === 'down' ? 'down' : ''}`}>
          {price}
        </span>
        <span className="hint">{spec?.description ?? ''}</span>
        <div className="intervals">
          {INTERVALS.map((item) => (
            <button
              key={item}
              type="button"
              className={`interval-btn ${interval === item ? 'active' : ''}`}
              onClick={() => setInterval(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-canvas" ref={containerRef} />
      {loading && bars.length === 0 ? (
        <div className="empty" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
          加载 K 线…
        </div>
      ) : null}
      {!loading && bars.length === 0 ? (
        <div className="empty" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
          暂无 K 线数据
        </div>
      ) : null}
    </section>
  )
}
