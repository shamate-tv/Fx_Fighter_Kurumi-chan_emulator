// REST 封装。所有金额字段仍是字符串，交给调用方决定何时转数字。
// 单机演示（Toy 静态部署）时切到 demoApi，其它情况走真实后端。
import { demoApi, isDemo } from '../demo/engine'
import type {
  AccountSnapshot,
  ApiError,
  Bar,
  Health,
  LifeOut,
  Market,
  MarketGroup,
  Order,
  OrderType,
  PlanBResult,
  Position,
  Quote,
  Side,
  SymbolSpec,
  Trade,
} from '../types'

export class ApiFailure extends Error {
  readonly code: string
  readonly details?: Record<string, unknown>

  constructor(error: ApiError) {
    super(error.message)
    this.name = 'ApiFailure'
    this.code = error.code
    this.details = error.details
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (cause) {
    throw new ApiFailure({ code: 'NETWORK_ERROR', message: '无法连接后端服务，请确认 uvicorn 已启动' })
  }

  const text = await resp.text()
  const body = text ? JSON.parse(text) : null

  if (!resp.ok) {
    const err = body?.error as ApiError | undefined
    throw new ApiFailure(
      err ?? { code: 'HTTP_ERROR', message: `请求失败（HTTP ${resp.status}）` },
    )
  }
  return body as T
}

const httpApi = {
  health: () => request<Health>('/api/health'),
  markets: () => request<{ markets: MarketGroup[] }>('/api/markets'),
  account: () => request<AccountSnapshot>('/api/account'),
  positions: () => request<Position[]>('/api/positions'),
  orders: (status: 'all' | 'open' = 'all') => request<Order[]>(`/api/orders?status=${status}`),
  trades: (limit = 200) => request<Trade[]>(`/api/trades?limit=${limit}`),
  history: (symbol: string, interval: string, limit = 300) =>
    request<Bar[]>(`/api/history/${encodeURIComponent(symbol)}?interval=${interval}&limit=${limit}`),

  /**
   * 单标的报价快照。
   * WS 建立时后端只发 hello + account，不重放 last_quotes；
   * 行情静默期（周末 / 闭市 / 冷标的）若只依赖 WS，面板会一直是 "—"。
   * 所以前端在启动和切换市场时主动拉一次快照，之后由 tick 覆盖。
   */
  quote: (symbol: string) => request<Quote>(`/api/quote/${encodeURIComponent(symbol)}`),

  placeOrder: (payload: {
    symbol: string
    side: Side
    type: OrderType
    qty: string
    limit_price?: string | null
  }) =>
    request<Order>('/api/orders', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  cancelOrder: (id: string) =>
    request<Order>(`/api/orders/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  convert: (from_currency: string, to_currency: string, amount: string) =>
    request<{ ok: boolean; account: AccountSnapshot }>('/api/cash/convert', {
      method: 'POST',
      body: JSON.stringify({ from_currency, to_currency, amount }),
    }),

  reset: () => request<{ ok: boolean }>('/api/admin/reset', { method: 'POST' }),

  /** 生存状态快照（REST 兜底；WS 的 life 帧会持续覆盖） */
  life: () => request<LifeOut>('/api/life'),

  /** Plan B：B站求关注换 15000 日元，24 小时冷却 */
  claimPlanB: (uid?: string) =>
    request<PlanBResult>('/api/planb/claim', {
      method: 'POST',
      body: JSON.stringify({ uid }),
    }),

  advanceTime: (payload: { days?: number; hours?: number; minutes?: number }) =>
    request<{ ok: boolean; now: string; released_events: number }>('/api/admin/advance-time', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** 注册自定义标的：行情由后端 MockProvider 生成，仅需代码 / 市场 / 参考价。 */
  addSymbol: (payload: {
    symbol: string
    market: Market
    description?: string
    start_price: string
    lot_size?: number | null
  }) =>
    request<{ ok: boolean; symbol: SymbolSpec }>('/api/admin/symbols', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

export const api = isDemo ? (demoApi as unknown as typeof httpApi) : httpApi
