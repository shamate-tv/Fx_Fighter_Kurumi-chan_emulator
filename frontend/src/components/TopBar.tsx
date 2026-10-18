import { fmt, fmtSigned, num, pnlClass } from '../lib/format'
import { useStore } from '../store/useStore'
import type { Market } from '../types'

const MARKETS: Market[] = ['FX', 'US', 'HK']

export function TopBar() {
  const markets = useStore((s) => s.markets)
  const market = useStore((s) => s.market)
  const setMarket = useStore((s) => s.setMarket)
  const account = useStore((s) => s.account)
  const health = useStore((s) => s.health)
  const wsStatus = useStore((s) => s.wsStatus)
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)

  const rate = num(health?.usdhk_rate ?? '7.8') || 7.8
  const usd = num(account?.equity?.USD)
  const hkd = num(account?.equity?.HKD)
  const totalUsd = usd + hkd / rate
  const realizedUsd = num(account?.realized_pnl?.USD) + num(account?.realized_pnl?.HKD) / rate
  const feesUsd = num(account?.fees_paid?.USD) + num(account?.fees_paid?.HKD) / rate

  const statusText =
    wsStatus === 'open' ? '实时已连接' : wsStatus === 'connecting' ? '连接中…' : '已断开，重连中'

  return (
    <header className="topbar">
      <div className="brand">
        久留美模拟器
        <span className="tag">FX 战士 · 模拟交易</span>
      </div>

      <nav className="tabs">
        {MARKETS.map((key) => (
          <button
            key={key}
            type="button"
            className={`tab ${view === 'trade' && market === key ? 'active' : ''}`}
            onClick={() => {
              setView('trade')
              setMarket(key)
            }}
          >
            {markets.find((g) => g.key === key)?.label ?? key}
          </button>
        ))}
        <button
          type="button"
          className={`tab ${view === 'exchange' ? 'active' : ''}`}
          onClick={() => setView(view === 'exchange' ? 'trade' : 'exchange')}
        >
          兑换
        </button>
      </nav>

      <div className="spacer" />

      <div className="total-asset">
        <span className="label">总资产 · USD 折算</span>
        <span className="value">${fmt(totalUsd, 2)}</span>
        <span className={`sub ${pnlClass(realizedUsd)}`}>
          已实现 {fmtSigned(realizedUsd)} · 费用 {fmt(feesUsd, 2)}
        </span>
      </div>

      <div className="status" title={`数据源：${health ? JSON.stringify(health.data_sources) : '—'}`}>
        <span className={`dot ${wsStatus}`} />
        {statusText}
      </div>
    </header>
  )
}
