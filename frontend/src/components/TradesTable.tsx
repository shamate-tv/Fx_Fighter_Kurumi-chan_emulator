import { fmt, fmtPrice, fmtQty, fmtSigned, fmtTime, num, pnlClass } from '../lib/format'
import { useStore } from '../store/useStore'
import type { Trade } from '../types'

const EMPTY_TRADES: Trade[] = []

export function TradesTable() {
  const trades = useStore((s) => s.trades ?? EMPTY_TRADES)
  const markets = useStore((s) => s.markets)
  const market = useStore((s) => s.market)

  const precisionOf = (symbol: string): number => {
    for (const group of markets) {
      const spec = group.symbols.find((s) => s.symbol === symbol)
      if (spec) return spec.price_precision
    }
    return 2
  }

  const current = trades.filter((t) => t.market === market)
  const rule = markets.find((g) => g.key === market)?.rule
  // 交收制度按市场规则展示；Trade.settles_on 只对"卖出资金到账"有值，买入单不能据此显示"即时"
  const settlementLabel =
    rule?.settlement === 'INSTANT' ? '即时' : `T+${rule?.settlement_days ?? 'N'}`

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="panel-title">成交记录</span>
        <div className="spacer" />
        <span className="hint">{current.length} 笔</span>
      </div>
      <div className="panel-body flush scroll" style={{ maxHeight: 180 }}>
        {current.length === 0 ? (
          <div className="empty">当前市场暂无成交</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>标的</th>
                <th>方向</th>
                <th>数量</th>
                <th>价格</th>
                <th>成交额</th>
                <th>费用</th>
                <th>交收</th>
                <th>已实现</th>
              </tr>
            </thead>
            <tbody>
              {current.map((trade) => (
                <tr key={trade.id}>
                  <td>{fmtTime(trade.ts)}</td>
                  <td>{trade.symbol}</td>
                  <td>
                    <span className={`badge ${trade.side === 'BUY' ? 'buy' : 'sell'}`}>
                      {trade.side === 'BUY' ? '买' : '卖'}
                    </span>
                  </td>
                  <td>{fmtQty(trade.qty)}</td>
                  <td>{fmtPrice(trade.price, precisionOf(trade.symbol))}</td>
                  <td>{fmt(trade.gross)}</td>
                  <td>{num(trade.fee_total) > 0 ? fmt(trade.fee_total) : '—'}</td>
                  <td>{settlementLabel}</td>
                  <td className={pnlClass(trade.realized_pnl)}>
                    {trade.realized_pnl === null ? '—' : fmtSigned(trade.realized_pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
