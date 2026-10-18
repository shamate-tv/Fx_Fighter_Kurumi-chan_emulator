import { create } from 'zustand'
import type {
  AccountSnapshot,
  Bar,
  GameEvent,
  Health,
  LifeOut,
  Market,
  MarketGroup,
  Order,
  Quote,
  Trade,
  WsStatus,
} from '../types'

export interface Toast {
  id: number
  kind: 'ok' | 'err' | 'info'
  text: string
}

export interface ResumeNotice {
  offline_seconds: number
  crossed_weekend: boolean
  message: string | null
}

export type AppView = 'trade' | 'exchange'

const DEFAULT_SYMBOL: Record<Market, string> = {
  FX: 'EURUSD',
  US: 'AAPL',
  HK: '00700.HK',
}

export const historyKey = (symbol: string, interval: string): string => `${symbol}:${interval}`

interface AppState {
  markets: MarketGroup[]
  health: Health | null
  account: AccountSnapshot | null
  quotes: Record<string, Quote>
  history: Record<string, Bar[]>
  orders: Order[]
  trades: Trade[]
  market: Market
  symbol: string
  interval: string
  wsStatus: WsStatus
  serverTime: string
  loadingHistory: boolean
  toast: Toast | null
  life: LifeOut | null
  resumeNotice: ResumeNotice | null
  gameOver: { ending: string; reason: string; day: number } | null
  activeEvent: GameEvent | null
  view: AppView

  setMarkets: (groups: MarketGroup[]) => void
  setHealth: (health: Health) => void
  setAccount: (account: AccountSnapshot) => void
  setLife: (life: LifeOut) => void
  setResumeNotice: (notice: ResumeNotice) => void
  dismissResumeNotice: () => void
  setGameOver: (payload: { ending: string; reason: string; day: number }) => void
  setActiveEvent: (event: GameEvent | null) => void
  setView: (view: AppView) => void
  applyTick: (quote: Quote) => void
  setHistory: (key: string, bars: Bar[]) => void
  setLoadingHistory: (loading: boolean) => void
  setOrders: (orders: Order[]) => void
  upsertOrder: (order: Order) => void
  setTrades: (trades: Trade[]) => void
  prependTrade: (trade: Trade) => void
  setMarket: (market: Market) => void
  setSymbol: (symbol: string) => void
  setInterval: (interval: string) => void
  setWsStatus: (status: WsStatus) => void
  setServerTime: (time: string) => void
  notify: (kind: Toast['kind'], text: string) => void
  dismissToast: () => void
}

let toastSeq = 0

export const useStore = create<AppState>((set, get) => ({
  markets: [],
  health: null,
  account: null,
  quotes: {},
  history: {},
  orders: [],
  trades: [],
  market: 'FX',
  symbol: DEFAULT_SYMBOL.FX,
  interval: '1m',
  wsStatus: 'connecting',
  serverTime: '',
  loadingHistory: false,
  toast: null,
  life: null,
  resumeNotice: null,
  gameOver: null,
  activeEvent: null,
  view: 'trade',

  setMarkets: (groups) => {
    const current = get().market
    const group = groups.find((g) => g.key === current)
    const symbol = group?.symbols[0]?.symbol ?? get().symbol
    set({ markets: groups, symbol })
  },

  setHealth: (health) => set({ health }),

  setAccount: (account) => set({ account }),

  setLife: (life) => set({ life }),

  setResumeNotice: (notice) => set({ resumeNotice: notice }),

  dismissResumeNotice: () => set({ resumeNotice: null }),

  setGameOver: (payload) => set({ gameOver: payload }),

  setActiveEvent: (event) => set({ activeEvent: event }),

  setView: (view) => set({ view }),

  // REST 快照与 WS tick 可能交错到达：只接受更新的报价，避免旧快照盖掉新 tick。
  applyTick: (quote) =>
    set((state) => {
      const prev = state.quotes[quote.symbol]
      if (prev && Date.parse(prev.ts) > Date.parse(quote.ts)) return state
      return { quotes: { ...state.quotes, [quote.symbol]: quote } }
    }),

  setHistory: (key, bars) => set((state) => ({ history: { ...state.history, [key]: bars } })),

  setLoadingHistory: (loading) => set({ loadingHistory: loading }),

  setOrders: (orders) => set({ orders }),

  upsertOrder: (order) =>
    set((state) => {
      const exists = state.orders.some((o) => o.id === order.id)
      const next = exists
        ? state.orders.map((o) => (o.id === order.id ? order : o))
        : [order, ...state.orders]
      return { orders: next.slice(0, 300) }
    }),

  setTrades: (trades) => set({ trades }),

  prependTrade: (trade) =>
    set((state) => ({ trades: [trade, ...state.trades].slice(0, 200) })),

  setMarket: (market) => {
    const group = get().markets.find((g) => g.key === market)
    const fallback = group?.symbols[0]?.symbol ?? DEFAULT_SYMBOL[market]
    set({ market, symbol: fallback })
  },

  setSymbol: (symbol) => set({ symbol }),

  setInterval: (interval) => set({ interval }),

  setWsStatus: (status) => set({ wsStatus: status }),

  setServerTime: (time) => set({ serverTime: time }),

  notify: (kind, text) => {
    toastSeq += 1
    set({ toast: { id: toastSeq, kind, text } })
    window.setTimeout(() => {
      if (get().toast?.id === toastSeq) set({ toast: null })
    }, 4200)
  },

  dismissToast: () => set({ toast: null }),
}))

/**
 * 空的标的数组必须是一个稳定引用。
 * Zustand v5 用 useSyncExternalStore + Object.is 比较 selector 结果，
 * 若 selector 在“数据未加载”时每次返回新数组（`?? []`），React 会判定快照持续变化，
 * 抛出 "Maximum update depth exceeded" 并让整棵树崩溃（升级前的真实故障）。
 */
const EMPTY_SYMBOLS: AppState['markets'][number]['symbols'] = []

/** 当前市场下的标的列表 */
export const selectMarketSymbols = (state: AppState) =>
  state.markets.find((g) => g.key === state.market)?.symbols ?? EMPTY_SYMBOLS

/** 当前标的定义 */
export const selectSymbolSpec = (state: AppState): AppState['markets'][number]['symbols'][number] | undefined =>
  state.markets.flatMap((g) => g.symbols).find((s) => s.symbol === state.symbol)

/** 当前市场的规则 */
export const selectRule = (state: AppState) => state.markets.find((g) => g.key === state.market)?.rule ?? null

// 调试入口：便于在浏览器控制台/自动化里读取 store 状态
if (typeof window !== 'undefined') {
  ;(window as unknown as { __store?: typeof useStore }).__store = useStore
}
