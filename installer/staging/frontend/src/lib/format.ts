// 数字与金额格式化。后端所有数值都是字符串，这里统一转换。

export function num(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === '') return 0
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function fmt(value: string | number | null | undefined, digits = 2): string {
  const n = num(value)
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 价格：按标的精度显示，不加千分位（外汇 5 位、股票 2 位） */
export function fmtPrice(value: string | number | null | undefined, precision: number): string {
  const n = num(value)
  return n.toFixed(precision)
}

export function fmtSigned(value: string | number | null | undefined, digits = 2): string {
  const n = num(value)
  const body = Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  if (n > 0) return `+${body}`
  if (n < 0) return `-${body}`
  return body
}

export function fmtQty(value: string | number | null | undefined): string {
  const n = num(value)
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  return n.toLocaleString('en-US', { maximumFractionDigits: 4 })
}

/** 日元：不带小数，千分位 */
export function fmtJpy(value: string | number | null | undefined): string {
  const n = num(value)
  return n.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/** 距今多久（用于行情新鲜度提示） */
export function ageLabel(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (seconds < 60) return `${seconds} 秒前`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} 小时前`
  return `${Math.round(hours / 24)} 天前`
}

export const pnlClass = (value: string | number | null | undefined): string => {
  const n = num(value)
  if (n > 0) return 'up'
  if (n < 0) return 'down'
  return 'flat'
}
