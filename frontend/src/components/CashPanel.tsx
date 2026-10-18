import { fmt, fmtDateTime, num } from '../lib/format'
import { selectRule, selectSymbolSpec, useStore } from '../store/useStore'

/**
 * 分桶账本：
 *   Available = 真正可自由支配（可提现、可跨市场）
 *   Pending   = 本市场卖出所得但尚未交收（同市场可再买，不能跨市场/提现）
 * 这两个数必须分列，否则看不出"T+1/T+2 交收"到底卡住了什么。
 */
export function CashPanel() {
  const account = useStore((s) => s.account)
  const spec = useStore(selectSymbolSpec)
  const market = useStore((s) => s.market)
  const rule = useStore(selectRule)

  const currency = spec?.currency ?? (market === 'HK' ? 'HKD' : 'USD')
  const balances = account?.balances?.[currency]
  const buyingPower = num(account?.buying_power?.[market])
  const withdrawable = num(account?.withdrawable?.[market])
  const pendingItems = (account?.pending_items ?? []).filter((item) => item.currency === currency)
  const nextRelease = pendingItems
    .slice()
    .sort((a, b) => new Date(a.release_at).getTime() - new Date(b.release_at).getTime())[0]

  const settlementLabel =
    rule?.settlement === 'INSTANT' ? '即时交收' : `T+${rule?.settlement_days ?? '?'} 交收（跳过周末）`

  return (
    <section className="panel cash-panel">
      <div className="panel-head">
        <span className="panel-title">{currency} 现金</span>
        <div className="spacer" />
        <span className="hint">{settlementLabel}</span>
      </div>
      <div className="panel-body">
        <div className="cash-grid">
          <div className="cash-cell">
            <span className="k">可用 AVAILABLE</span>
            <span className="v">{fmt(balances?.available)}</span>
            <span className="ccy">{currency} · 可提现 / 可跨市场</span>
          </div>
          <div className="cash-cell pending">
            <span className="k">在途 PENDING</span>
            <span className="v">{fmt(balances?.pending)}</span>
            <span className="ccy">
              {currency} · {pendingItems.length} 笔待交收
            </span>
          </div>
        </div>

        <div className="cash-row">
          <span className="k">可买力（可用 + 本市场在途）</span>
          <span className="v">{fmt(buyingPower)}</span>
        </div>
        <div className="cash-row">
          <span className="k">可提现</span>
          <span className="v">{fmt(withdrawable)}</span>
        </div>
        <div className="cash-row">
          <span className="k">冻结（挂单占用）</span>
          <span className="v">{fmt(balances?.frozen)}</span>
        </div>
        <div className="cash-row">
          <span className="k">总额</span>
          <span className="v">{fmt(balances?.total)}</span>
        </div>

        {nextRelease ? (
          <div className="hint" style={{ marginTop: 6 }}>
            下一笔 {fmt(nextRelease.amount)} {currency} 将于 {fmtDateTime(nextRelease.release_at)} 到账
          </div>
        ) : null}
      </div>
    </section>
  )
}
