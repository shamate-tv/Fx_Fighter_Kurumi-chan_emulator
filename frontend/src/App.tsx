import { useEffect, useRef, useState } from 'react'
import { CashPanel } from './components/CashPanel'
import { CandleChart } from './components/CandleChart'
import { ExchangePage } from './components/ExchangePage'
import { GameOverModal } from './components/GameOverModal'
import { LifePanel } from './components/LifePanel'
import { OrderPanel } from './components/OrderPanel'
import { PositionsTable } from './components/PositionsTable'
import { QuotePanel } from './components/QuotePanel'
import { ResumeModal } from './components/ResumeModal'
import { StaleBanner } from './components/StaleBanner'
import { SymbolTabs } from './components/SymbolTabs'
import { Toast } from './components/Toast'
import { TopBar } from './components/TopBar'
import { TradesTable } from './components/TradesTable'
import { AdvisorToast } from './components/AdvisorToast'
import { ApiFailure, api } from './lib/api'
import { historyKey, useStore } from './store/useStore'
import { useStream } from './ws/useStream'

export default function App() {
  useStream()

  const [bootError, setBootError] = useState<string | null>(null)
  const requestedHistory = useRef<Set<string>>(new Set())
  const snapshotsLoaded = useRef<Set<string>>(new Set())
  const markets = useStore((s) => s.markets)
  const market = useStore((s) => s.market)
  const symbol = useStore((s) => s.symbol)
  const interval = useStore((s) => s.interval)
  const view = useStore((s) => s.view)

  // 启动：一次性拉齐静态数据与初始状态
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [marketResp, health, account, orders, trades, life] = await Promise.all([
          api.markets(),
          api.health(),
          api.account(),
          api.orders(),
          api.trades(),
          api.life(),
        ])
        if (!alive) return
        const store = useStore.getState()
        store.setMarkets(marketResp.markets)
        store.setHealth(health)
        store.setAccount(account)
        store.setOrders(orders)
        store.setTrades(trades)
        store.setLife(life)
      } catch (error) {
        if (!alive) return
        setBootError(
          error instanceof ApiFailure ? error.message : '初始化失败，请检查后端是否已启动',
        )
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  // 每个市场首次进入时补一次报价快照（WS 不重放 last_quotes，静默行情下否则全是 "—"）
  // 注意：写入的是全局 store，不因组件卸载而丢弃结果——React StrictMode 会挂载两次，
  // 若在 cleanup 里丢弃，第二次又被 ref 去重跳过，报价就永远补不上。
  useEffect(() => {
    if (markets.length === 0) return
    if (snapshotsLoaded.current.has(market)) return
    const group = markets.find((g) => g.key === market)
    if (!group) return
    snapshotsLoaded.current.add(market)

    void Promise.allSettled(
      group.symbols.map((item) =>
        api.quote(item.symbol).then((quote) => useStore.getState().applyTick(quote)),
      ),
    ).then((results) => {
      // 整个市场都失败（后端未就绪）时允许下次重新尝试
      if (results.every((result) => result.status === 'rejected')) {
        snapshotsLoaded.current.delete(market)
      }
    })
  }, [market, markets])

  // 切换标的/周期时按需拉历史（用 ref 记录已请求的 key，避免失败后无限重试）
  useEffect(() => {
    if (!symbol || markets.length === 0) return
    const key = historyKey(symbol, interval)
    if (requestedHistory.current.has(key)) return
    requestedHistory.current.add(key)

    let alive = true
    const store = useStore.getState()
    store.setLoadingHistory(true)
    void api
      .history(symbol, interval)
      .then((bars) => {
        if (alive) useStore.getState().setHistory(key, bars)
      })
      .catch(() => {
        if (alive) useStore.getState().setHistory(key, [])
      })
      .finally(() => {
        if (alive) useStore.getState().setLoadingHistory(false)
      })
    return () => {
      alive = false
    }
  }, [symbol, interval, markets.length])

  if (bootError) {
    return (
      <div className="boot-error">
        <h3 style={{ marginTop: 0 }}>无法初始化交易台</h3>
        <p>{bootError}</p>
        <p className="muted">
          请确认后端已启动：<code>cd backend</code> 然后
          <code>.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000</code>
        </p>
      </div>
    )
  }

  return (
    <div className="app">
      <TopBar />
      {view === 'exchange' ? (
        <ExchangePage />
      ) : (
        <>
          <main className="main">
            <section className="col-left">
              <LifePanel />
              <SymbolTabs />
              <StaleBanner />
              <CandleChart />
              <QuotePanel />
            </section>
            <aside className="col-right">
              <CashPanel />
              <OrderPanel />
            </aside>
          </main>
          <div className="bottom">
            <PositionsTable />
            <TradesTable />
          </div>
        </>
      )}
      <ResumeModal />
      <GameOverModal />
      <AdvisorToast />
      <Toast />
    </div>
  )
}
