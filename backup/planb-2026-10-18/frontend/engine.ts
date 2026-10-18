// 单机演示引擎：没有后端也能玩的「久留美模拟器」。
// 行情是本地随机游走，账户/撮合/生存系统全部在内存里，纯静态部署即可运行。
import type {
  AccountSnapshot,
  Bar,
  Health,
  LifeOut,
  Market,
  MarketGroup,
  MarketRule,
  Order,
  OrderType,
  PlanBResult,
  Position,
  Quote,
  Side,
  SymbolSpec,
  Trade,
} from '../types'

export const USDJPY = 150
export const USDHKD = 7.8
const CONVERSION_SPREAD = 0.0006
const RENT_JPY = 35000
const FOOD_JPY = 15000
const WEEKLY_JPY = RENT_JPY + FOOD_JPY
const INITIAL_CASH_JPY = 100000
const PLANB_REWARD = 50000
const PLANB_COOLDOWN_MIN = 24 * 60
const MINUTES_PER_TICK = 120 // 现实 1 秒 = 游戏 2 小时
const SETTLE_DAYS = 7
const MINUTES_PER_DAY = 24 * 60
const SETTLE_EVERY = SETTLE_DAYS * MINUTES_PER_DAY
const ADVISOR_EVERY = 3 * MINUTES_PER_DAY

interface DemoSpec {
  symbol: string
  market: Market
  description: string
  price: number
  spread: number
  precision: number
  lot: number
  minQty: number
  step: number
  contract: number
  leverage: number | null
  currency: string
  quoteCurrency: string
}

const SPECS: DemoSpec[] = [
  { symbol: 'EURUSD', market: 'FX', description: '欧元 / 美元', price: 1.12396, spread: 0.00002, precision: 5, lot: 1, minQty: 0.01, step: 0.01, contract: 100000, leverage: 120, currency: 'USD', quoteCurrency: 'USD' },
  { symbol: 'GBPUSD', market: 'FX', description: '英镑 / 美元', price: 1.3313, spread: 0.00003, precision: 5, lot: 1, minQty: 0.01, step: 0.01, contract: 100000, leverage: 120, currency: 'USD', quoteCurrency: 'USD' },
  { symbol: 'XAUUSD', market: 'FX', description: '现货黄金 / 美元', price: 4164.27, spread: 0.3, precision: 2, lot: 1, minQty: 0.01, step: 0.01, contract: 100, leverage: 100, currency: 'USD', quoteCurrency: 'USD' },
  { symbol: 'AAPL', market: 'US', description: '苹果', price: 180.5, spread: 0.02, precision: 2, lot: 1, minQty: 1, step: 1, contract: 1, leverage: null, currency: 'USD', quoteCurrency: 'USD' },
  { symbol: 'NVDA', market: 'US', description: '英伟达', price: 900.3, spread: 0.1, precision: 2, lot: 1, minQty: 1, step: 1, contract: 1, leverage: null, currency: 'USD', quoteCurrency: 'USD' },
  { symbol: 'TSLA', market: 'US', description: '特斯拉', price: 250.6, spread: 0.05, precision: 2, lot: 1, minQty: 1, step: 1, contract: 1, leverage: null, currency: 'USD', quoteCurrency: 'USD' },
  { symbol: '00700.HK', market: 'HK', description: '腾讯控股', price: 384.74, spread: 0.2, precision: 2, lot: 100, minQty: 100, step: 100, contract: 1, leverage: null, currency: 'HKD', quoteCurrency: 'HKD' },
  { symbol: '09988.HK', market: 'HK', description: '阿里巴巴-W', price: 82.51, spread: 0.05, precision: 2, lot: 100, minQty: 100, step: 100, contract: 1, leverage: null, currency: 'HKD', quoteCurrency: 'HKD' },
]

function specOf(symbol: string): DemoSpec {
  const spec = SPECS.find((s) => s.symbol === symbol)
  if (!spec) throw new Error(`未知标的 ${symbol}`)
  return spec
}

function ruleOf(spec: DemoSpec): MarketRule {
  const isFx = spec.market === 'FX'
  const isHk = spec.market === 'HK'
  return {
    market: spec.market,
    label: spec.market === 'FX' ? '外汇' : spec.market === 'US' ? '美股' : '港股',
    trade_mode: isFx ? '保证金' : '现金',
    settlement: isFx ? 'INSTANT' : 'T_N',
    settlement_days: isFx ? 0 : isHk ? 2 : 1,
    sell_proceeds_release: isFx ? '即时' : isHk ? 'T+2' : 'T+1',
    allow_same_market_reinvest: true,
    cross_market_reinvest: '需先换汇',
    lot_policy: isHk ? '每手 100 股' : isFx ? '每手 0.01 手' : '每股',
    allowed_lot_sizes: null,
    price_limit: '无',
    flags: {},
    fees: isFx
      ? []
      : [{ name: '佣金', kind: 'PERCENT', value: '0.0005', side: 'BOTH', min_amount: '0', round_up_to_whole: false }],
  }
}

const GROUPS: MarketGroup[] = (['FX', 'US', 'HK'] as Market[]).map((market) => {
  const symbols = SPECS.filter((s) => s.market === market).map<SymbolSpec>((s) => ({
    symbol: s.symbol,
    description: s.description,
    market: s.market,
    currency: s.currency,
    quote_currency: s.quoteCurrency,
    lot_size: s.lot,
    min_qty: String(s.minQty),
    qty_step: String(s.step),
    qty_unit: s.market === 'FX' ? '手' : '股',
    price_precision: s.precision,
    contract_size: s.contract,
    leverage: s.leverage ? String(s.leverage) : null,
  }))
  return { key: market, label: market === 'FX' ? '外汇' : market === 'US' ? '美股' : '港股', rule: ruleOf(specOf(symbols[0].symbol)), symbols }
})

const money = (n: number) => n.toFixed(2)
const priceStr = (spec: DemoSpec, n: number) => n.toFixed(spec.precision)

interface Holding {
  qty: number
  avg: number
  margin: number
}

interface PendingItem {
  id: string
  market: Market
  currency: string
  amount: number
  releaseAt: number
}

interface DemoState {
  prices: Record<string, number>
  cash: Record<string, number>
  cashJpy: number
  holdings: Record<string, Holding>
  orders: Order[]
  trades: Trade[]
  pending: PendingItem[]
  realized: Record<string, number>
  feesPaid: Record<string, number>
  gameMinutes: number
  nextSettleMinute: number
  planbLastMinute: number | null
  planbClaims: number
  advisorSeq: number
  nextAdvisorMinute: number
  alive: boolean
  ending: string | null
  endingReason: string | null
  seq: number
  startedAt: number
}

function initialState(): DemoState {
  return {
    prices: Object.fromEntries(SPECS.map((s) => [s.symbol, s.price])),
    cash: { USD: 10000, HKD: 0 },
    cashJpy: INITIAL_CASH_JPY,
    holdings: {},
    orders: [],
    trades: [],
    pending: [],
    realized: { USD: 0, HKD: 0 },
    feesPaid: { USD: 0, HKD: 0 },
    gameMinutes: 0,
    nextSettleMinute: SETTLE_EVERY,
    planbLastMinute: null,
    planbClaims: 0,
    advisorSeq: 0,
    nextAdvisorMinute: 10,
    alive: true,
    ending: null,
    endingReason: null,
    seq: 0,
    startedAt: Date.now(),
  }
}

let state: DemoState = initialState()

const todayIso = () => new Date().toISOString().slice(0, 10)
const gameDateStr = () => {
  const date = new Date(state.startedAt + state.gameMinutes * 60_000)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
const dateAfterMinutes = (minutes: number) => {
  const date = new Date(state.startedAt + minutes * 60_000)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function quoteOf(symbol: string): Quote {
  const spec = specOf(symbol)
  const mid = state.prices[symbol]
  const half = spec.spread / 2
  return {
    symbol,
    market: spec.market,
    bid: priceStr(spec, mid - half),
    ask: priceStr(spec, mid + half),
    mid: priceStr(spec, mid),
    ts: new Date().toISOString(),
    stale: false,
    source: 'demo',
  }
}

function holdingPnl(spec: DemoSpec, holding: Holding): number {
  const price = state.prices[spec.symbol]
  return spec.market === 'FX' ? holding.qty * spec.contract * (price - holding.avg) : holding.qty * (price - holding.avg)
}

function equityOf(currency: string): number {
  let value = state.cash[currency] ?? 0
  for (const [symbol, holding] of Object.entries(state.holdings)) {
    const spec = specOf(symbol)
    if (spec.currency !== currency) continue
    value += spec.market === 'FX' ? holding.margin + holdingPnl(spec, holding) : holding.qty * state.prices[symbol]
  }
  return value
}

function pendingSum(currency: string): number {
  return state.pending.filter((p) => p.currency === currency).reduce((total, p) => total + p.amount, 0)
}

function snapshot(): AccountSnapshot {
  const balances: AccountSnapshot['balances'] = {}
  for (const ccy of ['USD', 'HKD']) {
    const pending = pendingSum(ccy)
    const available = state.cash[ccy] ?? 0
    balances[ccy] = {
      available: money(available),
      pending: money(pending),
      frozen: '0.00',
      total: money(available + pending),
    }
  }
  const positions: Position[] = Object.entries(state.holdings).map(([symbol, holding]) => {
    const spec = specOf(symbol)
    const last = state.prices[symbol]
    return {
      symbol,
      market: spec.market,
      qty: String(holding.qty),
      avg_cost: priceStr(spec, holding.avg),
      currency: spec.currency,
      last_price: priceStr(spec, last),
      market_value: money(holding.qty * last * (spec.market === 'FX' ? spec.contract : 1)),
      unrealized_pnl: money(holdingPnl(spec, holding)),
    }
  })
  return {
    balances,
    buying_power: {
      FX: money((state.cash.USD ?? 0) * 120),
      US: money(state.cash.USD ?? 0),
      HK: money(state.cash.HKD ?? 0),
    },
    withdrawable: { FX: money(state.cash.USD ?? 0), US: money(state.cash.USD ?? 0), HK: money(state.cash.HKD ?? 0) },
    positions,
    equity: { USD: money(equityOf('USD')), HKD: money(equityOf('HKD')) },
    realized_pnl: { USD: money(state.realized.USD ?? 0), HKD: money(state.realized.HKD ?? 0) },
    fees_paid: { USD: money(state.feesPaid.USD ?? 0), HKD: money(state.feesPaid.HKD ?? 0) },
    pending_items: state.pending.map((p) => ({
      market: p.market,
      currency: p.currency,
      amount: money(p.amount),
      trade_id: p.id,
      release_at: new Date(p.releaseAt).toISOString(),
    })),
  }
}

const planbReady = () => state.planbLastMinute === null || state.gameMinutes - state.planbLastMinute >= PLANB_COOLDOWN_MIN
const planbCooldownSeconds = () =>
  state.planbLastMinute === null ? 0 : Math.max(0, (PLANB_COOLDOWN_MIN - (state.gameMinutes - state.planbLastMinute)) * 60)

function lifeOut(): LifeOut {
  const equityUsd = equityOf('USD') + equityOf('HKD') / USDHKD
  return {
    day: Math.floor(state.gameMinutes / MINUTES_PER_DAY) + 1,
    game_date: gameDateStr(),
    game_time: new Date(state.startedAt + state.gameMinutes * 60_000).toISOString(),
    cash_jpy: money(state.cashJpy),
    rent_jpy: String(RENT_JPY),
    food_jpy: String(FOOD_JPY),
    weekly_cost_jpy: String(WEEKLY_JPY),
    next_settle_on: dateAfterMinutes(state.nextSettleMinute),
    sanity: '100',
    caution_level: 0,
    status: state.alive ? 'ALIVE' : 'GAME_OVER',
    ending: state.ending,
    ending_reason: state.endingReason,
    online: true,
    debt_jpy: '0.00',
    usdjpy: String(USDJPY),
    debt_usd: '0.00',
    account_equity_usd: money(equityUsd),
    net_equity_usd: money(equityUsd + state.cashJpy / USDJPY),
    forced_liquidations: 0,
    planb_ready: planbReady(),
    planb_cooldown_seconds: planbCooldownSeconds(),
    planb_claims: state.planbClaims,
    loans: [],
  }
}

export class DemoFailure extends Error {
  readonly code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = 'DemoFailure'
    this.code = code
  }
}

const feeRate = (spec: DemoSpec) => (spec.market === 'FX' ? 0 : 0.0005)
const roundPrice = (spec: DemoSpec, price: number) => Number(price.toFixed(spec.precision))

function liquidateAll(): void {
  for (const [symbol, holding] of Object.entries(state.holdings)) {
    const spec = specOf(symbol)
    const price = state.prices[symbol]
    const pnl = holdingPnl(spec, holding)
    if (spec.market === 'FX') {
      state.cash[spec.currency] = (state.cash[spec.currency] ?? 0) + holding.margin + pnl
    } else {
      state.cash[spec.currency] = (state.cash[spec.currency] ?? 0) + holding.qty * price
    }
    state.realized[spec.currency] = (state.realized[spec.currency] ?? 0) + pnl
    delete state.holdings[symbol]
  }
}

function settleWeek(): void {
  state.cashJpy -= WEEKLY_JPY
  if (state.cashJpy < 0) {
    // 先从交易账户提现（USD 按 150 换日元）
    const cashUsd = state.cash.USD ?? 0
    const neededUsd = Math.min(cashUsd, -state.cashJpy / USDJPY)
    state.cash.USD = cashUsd - neededUsd
    state.cashJpy += neededUsd * USDJPY
    if (state.cashJpy < 0) {
      // 再强平所有持仓
      liquidateAll()
      const afterUsd = state.cash.USD ?? 0
      const moreUsd = Math.min(afterUsd, -state.cashJpy / USDJPY)
      state.cash.USD = afterUsd - moreUsd
      state.cashJpy += moreUsd * USDJPY
    }
    if (state.cashJpy < 0) {
      state.alive = false
      state.ending = 'ROOFTOP'
      state.endingReason = `第 ${Math.floor(state.gameMinutes / MINUTES_PER_DAY) + 1} 天：交不起这一周的房租和伙食，你站在了天台上`
    }
  }
  state.nextSettleMinute += SETTLE_EVERY
}

function tick(): { quotes: Quote[]; events: { id: string; kind: string; title: string; body: string }[] } {
  state.seq += 1
  const events: { id: string; kind: string; title: string; body: string }[] = []
  if (state.alive) {
    state.gameMinutes += MINUTES_PER_TICK
    for (const spec of SPECS) {
      const drift = (Math.random() - 0.5) * 0.0012
      state.prices[spec.symbol] = Math.max(spec.price * 0.5, state.prices[spec.symbol] * (1 + drift))
    }
    // 在途资金释放（演示加速：10 秒）
    const now = Date.now()
    const released = state.pending.filter((p) => p.releaseAt <= now)
    if (released.length > 0) {
      state.pending = state.pending.filter((p) => p.releaseAt > now)
      for (const item of released) {
        state.cash[item.currency] = (state.cash[item.currency] ?? 0) + item.amount
      }
    }
    while (state.gameMinutes >= state.nextSettleMinute) settleWeek()
    // 萌智子
    if (state.gameMinutes >= state.nextAdvisorMinute) {
      state.advisorSeq += 1
      state.nextAdvisorMinute += ADVISOR_EVERY
      events.push({
        id: `advisor_${state.advisorSeq}`,
        kind: 'ADVISOR_TIP',
        title: '萌智子',
        body: '我跟你说，这波行情绝对暴涨，赶紧全仓梭哈！',
      })
    }
  }
  return { quotes: SPECS.map((s) => quoteOf(s.symbol)), events }
}

function placeOrder(payload: { symbol: string; side: Side; type: OrderType; qty: string; limit_price?: string | null }): Order {
  const spec = specOf(payload.symbol)
  const qty = Number(payload.qty)
  if (!Number.isFinite(qty) || qty <= 0) throw new DemoFailure('INVALID_QTY', '数量必须为正数')
  if (!state.alive) throw new DemoFailure('GAME_OVER', '这一局已经结束了')
  const quote = quoteOf(spec.symbol)
  const price = roundPrice(spec, Number(payload.side === 'BUY' ? quote.ask : quote.bid))
  const holding = state.holdings[spec.symbol]
  const held = holding?.qty ?? 0

  let realizedThisTrade: number | null = null
  if (payload.side === 'SELL' && qty > held + 1e-9) {
    throw new DemoFailure('INSUFFICIENT_POSITION', `可卖 ${held} ${spec.market === 'FX' ? '手' : '股'}，不能卖空`)
  }

  const gross = spec.market === 'FX' ? qty * spec.contract * price : qty * price
  const fee = gross * feeRate(spec)
  const order: Order = {
    id: `ord_${Math.random().toString(16).slice(2, 12)}`,
    symbol: payload.symbol,
    market: spec.market,
    side: payload.side,
    type: payload.type,
    qty: String(qty),
    filled_qty: String(qty),
    limit_price: payload.limit_price ?? null,
    status: 'FILLED',
    avg_fill_price: priceStr(spec, price),
    fee: money(fee),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    reject_reason: null,
    client_order_id: null,
  }

  if (payload.side === 'BUY') {
    if (spec.market === 'FX') {
      const margin = gross / (spec.leverage ?? 100)
      if (margin > (state.cash.USD ?? 0) + 1e-9) {
        throw new DemoFailure('INSUFFICIENT_CASH', `保证金不足：需要 ${money(margin)} USD，可用 ${money(state.cash.USD ?? 0)} USD`)
      }
      state.cash.USD = (state.cash.USD ?? 0) - margin
      const current = state.holdings[spec.symbol] ?? { qty: 0, avg: 0, margin: 0 }
      const newQty = current.qty + qty
      state.holdings[spec.symbol] = {
        qty: newQty,
        avg: (current.avg * current.qty + price * qty) / newQty,
        margin: current.margin + margin,
      }
    } else {
      const cost = gross + fee
      const ccy = spec.currency
      if (cost > (state.cash[ccy] ?? 0) + 1e-9) {
        throw new DemoFailure('INSUFFICIENT_CASH', `${ccy} 可用不足：需要 ${money(cost)}，可用 ${money(state.cash[ccy] ?? 0)}`)
      }
      state.cash[ccy] = (state.cash[ccy] ?? 0) - cost
      state.feesPaid[ccy] = (state.feesPaid[ccy] ?? 0) + fee
      const current = state.holdings[spec.symbol] ?? { qty: 0, avg: 0, margin: 0 }
      const newQty = current.qty + qty
      state.holdings[spec.symbol] = {
        qty: newQty,
        avg: (current.avg * current.qty + price * qty) / newQty,
        margin: 0,
      }
    }
  } else {
    const current = state.holdings[spec.symbol] as Holding
    const pnl = spec.market === 'FX' ? qty * spec.contract * (price - current.avg) : qty * (price - current.avg)
    realizedThisTrade = pnl
    const marginPart = current.margin * (qty / current.qty)
    const newQty = current.qty - qty
    state.realized[spec.currency] = (state.realized[spec.currency] ?? 0) + pnl
    if (spec.market === 'FX') {
      state.cash[spec.currency] = (state.cash[spec.currency] ?? 0) + marginPart + pnl
    } else {
      state.feesPaid[spec.currency] = (state.feesPaid[spec.currency] ?? 0) + fee
      state.pending.push({ id: `${spec.symbol}:${order.id}`, market: spec.market, currency: spec.currency, amount: gross - fee, releaseAt: Date.now() + 10_000 })
    }
    if (newQty <= 1e-9) delete state.holdings[spec.symbol]
    else state.holdings[spec.symbol] = { qty: newQty, avg: current.avg, margin: current.margin - marginPart }
  }

  state.orders.unshift(order)
  const pushOrder = order
  const trade: Trade = {
    id: `trd_${Math.random().toString(16).slice(2, 12)}`,
    order_id: order.id,
    symbol: payload.symbol,
    market: spec.market,
    side: payload.side,
    qty: String(qty),
    price: priceStr(spec, price),
    gross: money(gross),
    fees:
      fee > 0
        ? [{ name: '佣金', rate: '0.0005', amount: money(fee), currency: spec.currency }]
        : [],
    fee_total: money(fee),
    currency: spec.currency,
    trade_date: todayIso(),
    settles_on: payload.side === 'SELL' && spec.market !== 'FX' ? dateAfterMinutes(state.gameMinutes + (spec.market === 'HK' ? 2 : 1) * MINUTES_PER_DAY) : null,
    realized_pnl: realizedThisTrade === null ? null : money(realizedThisTrade),
    ts: new Date().toISOString(),
  }
  state.trades.unshift(trade)
  activeDispatch?.({ type: 'order', order: pushOrder })
  activeDispatch?.({ type: 'trade', trade })
  activeDispatch?.({ type: 'account', account: snapshot() })
  return order
}

function convert(from: string, to: string, amount: string): { ok: boolean; account: AccountSnapshot } {
  const value = Number(amount)
  if (!Number.isFinite(value) || value <= 0) throw new DemoFailure('INVALID_QTY', '换汇金额必须为正数')
  if ((state.cash[from] ?? 0) + 1e-9 < value) throw new DemoFailure('INSUFFICIENT_CASH', `${from} 可用余额不足`)
  if (from === 'USD' && to === 'HKD') {
    state.cash.USD = (state.cash.USD ?? 0) - value
    state.cash.HKD = (state.cash.HKD ?? 0) + value * USDHKD * (1 - CONVERSION_SPREAD)
  } else if (from === 'HKD' && to === 'USD') {
    state.cash.HKD = (state.cash.HKD ?? 0) - value
    state.cash.USD = (state.cash.USD ?? 0) + (value / USDHKD) * (1 - CONVERSION_SPREAD)
  } else {
    throw new DemoFailure('SAME_CURRENCY', '暂不支持这个币种对')
  }
  activeDispatch?.({ type: 'account', account: snapshot() })
  return { ok: true, account: snapshot() }
}

function claimPlanB(): PlanBResult {
  if (!state.alive) throw new DemoFailure('GAME_OVER', '这一局已经结束了')
  if (!planbReady()) throw new DemoFailure('PLANB_COOLDOWN', '同一批粉丝还没凉透，再等等')
  state.cashJpy += PLANB_REWARD
  state.planbLastMinute = state.gameMinutes
  state.planbClaims += 1
  activeDispatch?.({ type: 'life', life: lifeOut() })
  return { ok: true, gain_jpy: money(PLANB_REWARD), claims: state.planbClaims, life: lifeOut() }
}

function reset(): void {
  state = initialState()
}

function history(symbol: string, interval: string, limit = 300): Bar[] {
  const spec = specOf(symbol)
  const count = Math.max(1, Math.min(limit, 400))
  const bars: Bar[] = []
  let price = spec.price
  const stepMs = interval === '1d' ? 86_400_000 : interval === '1h' ? 3_600_000 : interval === '5m' ? 300_000 : interval === '15m' ? 900_000 : interval === '4h' ? 14_400_000 : 60_000
  const start = Date.now() - count * stepMs
  for (let i = 0; i < count; i += 1) {
    const open = price
    const close = open * (1 + (Math.random() - 0.5) * 0.004)
    const high = Math.max(open, close) * (1 + Math.random() * 0.0015)
    const low = Math.min(open, close) * (1 - Math.random() * 0.0015)
    price = close
    bars.push({
      symbol,
      interval,
      open_time: new Date(start + i * stepMs).toISOString(),
      open: priceStr(spec, open),
      high: priceStr(spec, high),
      low: priceStr(spec, low),
      close: priceStr(spec, close),
      volume: String(Math.round(100 + Math.random() * 900)),
    })
  }
  state.prices[symbol] = price
  return bars
}

function health(): Health {
  return {
    status: 'ok',
    clock: new Date().toISOString(),
    data_sources: { overall: 'demo' },
    provider_status: {},
    symbols: SPECS.length,
    usdhk_rate: String(USDHKD),
    market_data: 'demo',
    clock_mode: 'demo',
  }
}

export interface DemoDispatch {
  (msg: unknown): void
}

let timer: number | null = null
let activeDispatch: DemoDispatch | null = null

export function startDemoStream(dispatch: DemoDispatch): () => void {
  if (timer !== null) return () => undefined
  activeDispatch = dispatch
  // 开场先来一条萌智子的建议
  window.setTimeout(() => {
    if (!state.alive) return
    state.advisorSeq += 1
    dispatch({
      type: 'event',
      event: { id: `advisor_${state.advisorSeq}`, kind: 'ADVISOR_TIP', title: '萌智子', body: '我跟你说，这波行情绝对暴涨，赶紧全仓梭哈！' },
    })
  }, 1800)

  timer = window.setInterval(() => {
    const result = tick()
    for (const quote of result.quotes) {
      dispatch({ type: 'tick', ...quote })
    }
    dispatch({ type: 'account', account: snapshot() })
    dispatch({ type: 'life', life: lifeOut() })
    for (const event of result.events) dispatch({ type: 'event', event })
    if (!state.alive) {
      window.clearInterval(timer ?? 0)
      timer = null
    }
  }, 1000)
  return () => {
    if (timer !== null) window.clearInterval(timer)
    timer = null
  }
}

export const demoApi = {
  health: async () => health(),
  markets: async () => ({ markets: GROUPS }),
  account: async () => snapshot(),
  positions: async () => snapshot().positions,
  orders: async () => state.orders,
  trades: async () => state.trades,
  quote: async (symbol: string) => quoteOf(symbol),
  history: async (symbol: string, interval: string, limit = 300) => history(symbol, interval, limit),
  placeOrder: async (payload: { symbol: string; side: Side; type: OrderType; qty: string; limit_price?: string | null }) => placeOrder(payload),
  cancelOrder: async () => {
    throw new DemoFailure('NOT_FOUND', '演示模式不支持撤单')
  },
  convert: async (from: string, to: string, amount: string) => convert(from, to, amount),
  reset: async () => {
    reset()
    return { ok: true }
  },
  life: async () => lifeOut(),
  claimPlanB: async () => claimPlanB(),
  advanceTime: async () => ({ ok: true, now: new Date().toISOString(), released_events: 0 }),
  addSymbol: async () => {
    throw new DemoFailure('UNSUPPORTED', '演示模式暂不支持添加自定义标的')
  },
}

export const isDemo = import.meta.env.VITE_DEMO === '1'
