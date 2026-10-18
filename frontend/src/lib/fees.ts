// 费用预估：规则完全来自后端的 /api/markets，前端只做"按规则算一遍"，不硬编码费率。
import type { FeeRulePublic, MarketRule, Side } from '../types'

export interface FeeLine {
  name: string
  label: string
  amount: number
}

const LABELS: Record<string, string> = {
  stamp_duty: '印花税',
  transaction_levy: '交易征费',
  trading_fee: '交易费',
  settlement_fee: '结算费',
  commission: '佣金',
}

export function feeLabel(name: string): string {
  return LABELS[name] ?? name
}

function applies(rule: FeeRulePublic, side: Side): boolean {
  return rule.side === 'BOTH' || rule.side === side
}

/**
 * @param gross 成交额（标的结算币种）
 * @param qty   数量（股票用股数；PER_SHARE 类规则用得到）
 */
export function estimateFees(rule: MarketRule, side: Side, gross: number, qty: number): FeeLine[] {
  const lines: FeeLine[] = []
  for (const fee of rule.fees) {
    if (!applies(fee, side)) continue
    const value = Number(fee.value)
    if (!Number.isFinite(value) || value === 0) continue

    let amount: number
    if (fee.kind === 'PERCENT') amount = gross * value
    else if (fee.kind === 'PER_SHARE') amount = qty * value
    else amount = value

    if (fee.round_up_to_whole) amount = Math.ceil(amount)
    const floor = Number(fee.min_amount)
    if (Number.isFinite(floor) && floor > 0 && amount < floor) amount = floor

    lines.push({ name: fee.name, label: feeLabel(fee.name), amount: Math.round(amount * 100) / 100 })
  }
  return lines
}

export function feesTotal(lines: FeeLine[]): number {
  return Math.round(lines.reduce((sum, line) => sum + line.amount, 0) * 100) / 100
}

/**
 * 前端成交额口径（与后端 SymbolSpec.trade_value 对齐）：
 *  - 外汇：手数 × 合约乘数，报价币种≠结算币种时用除法
 *  - 股票：股数 × 价格
 * 费用只对股票有意义（外汇成本体现在点差），这里对 FX 直接返回 0。
 */
export function grossAmount(params: {
  market: string
  qty: number
  price: number
  quoteCurrency: string
  settleCurrency: string
  contractSize?: number
}): number {
  const { market, qty, price, quoteCurrency, settleCurrency } = params
  if (market === 'FX') {
    const units = qty * (params.contractSize ?? 100000)
    return quoteCurrency === settleCurrency ? units * price : units / price
  }
  return qty * price
}
