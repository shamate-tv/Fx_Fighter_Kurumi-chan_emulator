// tick → K 线聚合。历史来自 REST，之后每个 tick 就地更新"最后一根"。
import type { Bar, Quote } from '../types'

export const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
export type Interval = (typeof INTERVALS)[number]

const INTERVAL_SECONDS: Record<string, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '4h': 14400,
  '1d': 86400,
}

export function intervalSeconds(interval: string): number {
  return INTERVAL_SECONDS[interval] ?? 60
}

/** 把 ISO 时间对齐到所属 bar 的起始秒 */
export function bucketOf(iso: string, interval: string): number {
  const ts = Math.floor(new Date(iso).getTime() / 1000)
  const step = intervalSeconds(interval)
  return Math.floor(ts / step) * step
}

const dec = (value: number): string => value.toFixed(8)

/**
 * 用一条 tick 更新最后一根 bar。
 * 同一时间桶内 → 更新 high/low/close；跨桶 → 开一根新的。
 */
export function mergeTick(last: Bar | null, quote: Quote, interval: string, precision: number): Bar {
  const bucket = bucketOf(quote.ts, interval)
  const price = Number(quote.mid)
  const rounded = Number(price.toFixed(precision))

  if (last && bucketOf(last.open_time, interval) === bucket) {
    const high = Math.max(Number(last.high), rounded)
    const low = Math.min(Number(last.low), rounded)
    return { ...last, high: dec(high), low: dec(low), close: dec(rounded) }
  }

  return {
    symbol: quote.symbol,
    interval,
    open_time: new Date(bucket * 1000).toISOString(),
    open: dec(rounded),
    high: dec(rounded),
    low: dec(rounded),
    close: dec(rounded),
    volume: '0',
  }
}
