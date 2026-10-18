// 与后端 Pydantic 模型一一对应的类型。
// 注意：后端所有 Decimal 在 JSON 里都是**字符串**（保精度），前端用 num() 转数字。

export type Market = 'FX' | 'US' | 'HK'
export type Side = 'BUY' | 'SELL'
export type OrderType = 'MARKET' | 'LIMIT'
export type OrderStatus = 'PENDING' | 'FILLED' | 'CANCELLED' | 'REJECTED'
export type WsStatus = 'connecting' | 'open' | 'closed'

export interface SymbolSpec {
  symbol: string
  description: string
  market: Market
  currency: string
  quote_currency: string
  lot_size: number | null
  min_qty: string
  qty_step: string
  qty_unit: string
  price_precision: number
  contract_size: number
  leverage: string | null
}

export interface FeeRulePublic {
  name: string
  kind: 'PERCENT' | 'PER_SHARE' | 'FLAT'
  value: string
  side: 'BUY' | 'SELL' | 'BOTH'
  min_amount: string
  round_up_to_whole: boolean
}

export interface MarketRule {
  market: Market
  label: string
  trade_mode: string
  settlement: 'INSTANT' | 'T_N'
  settlement_days: number
  sell_proceeds_release: string
  allow_same_market_reinvest: boolean
  cross_market_reinvest: string
  lot_policy: string
  allowed_lot_sizes: number[] | null
  price_limit: string
  flags: Record<string, boolean>
  fees: FeeRulePublic[]
}

export interface MarketGroup {
  key: Market
  label: string
  rule: MarketRule
  symbols: SymbolSpec[]
}

export interface Quote {
  symbol: string
  market: Market
  bid: string
  ask: string
  mid: string
  ts: string
  stale: boolean
  source: string
}

export interface Bar {
  symbol: string
  interval: string
  open_time: string
  open: string
  high: string
  low: string
  close: string
  volume: string
}

export interface Balances {
  available: string
  pending: string
  frozen: string
  total: string
}

export interface Position {
  symbol: string
  market: Market
  qty: string
  avg_cost: string
  currency: string
  last_price: string | null
  market_value: string | null
  unrealized_pnl: string | null
}

export interface PendingItem {
  market: Market
  currency: string
  amount: string
  trade_id: string
  release_at: string
}

export interface AccountSnapshot {
  balances: Record<string, Balances>
  buying_power: Record<string, string>
  withdrawable: Record<string, string>
  positions: Position[]
  equity: Record<string, string>
  realized_pnl: Record<string, string>
  fees_paid: Record<string, string>
  pending_items: PendingItem[]
}

export interface LoanOut {
  id: string
  lender: string
  principal_jpy: string
  balance_jpy: string
  daily_rate: string
  accrued_interest_jpy: string
  taken_on: string
  due_date: string | null
  status: 'ACTIVE' | 'REPAID' | 'DEFAULTED'
}

export interface LifeOut {
  day: number
  game_date: string
  game_time: string
  cash_jpy: string
  rent_jpy: string
  food_jpy: string
  weekly_cost_jpy: string
  next_settle_on: string | null
  sanity: string
  caution_level: number
  status: 'ALIVE' | 'GAME_OVER'
  ending: string | null
  ending_reason: string | null
  online: boolean
  debt_jpy: string
  usdjpy: string
  debt_usd: string
  account_equity_usd: string
  net_equity_usd: string
  forced_liquidations: number
  planb_ready: boolean
  planb_cooldown_seconds: number
  planb_claims: number
  loans: LoanOut[]
}

export interface PlanBResult {
  ok: boolean
  gain_jpy: string
  claims: number
  life: LifeOut
}

export interface GameEvent {
  id: string
  kind: string
  title: string
  body: string
}

export interface FeeComponent {
  name: string
  rate: string
  amount: string
  currency: string
}

export interface Order {
  id: string
  symbol: string
  market: Market
  side: Side
  type: OrderType
  qty: string
  filled_qty: string
  limit_price: string | null
  status: OrderStatus
  avg_fill_price: string | null
  fee: string
  created_at: string
  updated_at: string
  reject_reason: string | null
  client_order_id: string | null
}

export interface Trade {
  id: string
  order_id: string
  symbol: string
  market: Market
  side: Side
  qty: string
  price: string
  gross: string
  fees: FeeComponent[]
  fee_total: string
  currency: string
  trade_date: string
  settles_on: string | null
  realized_pnl: string | null
  ts: string
}

export interface ProviderStatus {
  name: string
  enabled: boolean
  mode: string
  last_error: string | null
  feed?: string
}

export interface Health {
  status: string
  clock: string
  data_sources: Record<string, string>
  provider_status: Record<string, ProviderStatus>
  symbols: number
  usdhk_rate: string
  market_data: string
  clock_mode: string
}

export type WsMessage =
  | { type: 'hello'; server_time: string; data_sources: Record<string, string>; channels: string[] }
  | {
      type: 'tick'
      symbol: string
      market: Market
      bid: string
      ask: string
      mid: string
      ts: string
      stale: boolean
      source: string
    }
  | { type: 'order'; order: Order }
  | { type: 'trade'; trade: Trade }
  | { type: 'account'; account: AccountSnapshot }
  | { type: 'life'; life: LifeOut }
  | { type: 'event'; event: GameEvent }
  | { type: 'session_resumed'; offline_seconds: number; crossed_weekend: boolean; message: string | null }
  | { type: 'game_over'; ending: string; reason: string; day: number }
  | { type: 'settlement'; item: { id: string; amount: string; currency: string; market: Market; trade_id: string; release_at: string } }
  | { type: 'subscribed'; symbols: string[]; channels: string[] }
  | { type: 'unsubscribed'; symbols: string[] }
  | { type: 'pong' }

export interface ApiError {
  code: string
  message: string
  details?: Record<string, unknown>
}
