import { useMemo } from 'react'
import { fmt, fmtPrice, fmtQty, fmtSigned, num, pnlClass } from '../lib/format'
import { useStore } from '../store/useStore'
import type { Position, SymbolSpec } from '../types'

const EMPTY_POSITIONS: Position[] = []

export function PositionsTable() {
  const positions = useStore((s) => s.account?.positions ?? EMPTY_POSITIONS)
  const quotes = useStore((s) => s.quotes)
  const markets = useStore((s) => s.markets)

  const specs = useMemo(() => {
    const map = new Map<string, SymbolSpec>()
    for (const group of markets) {
      for (const spec of group.symbols) map.set(spec.symbol, spec)
    }
    return map
  }, [markets])

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="panel-title">持仓</span>
        <div className="spacer" />
        <span className="hint">{positions.length} 个标的</span>
      </div>
      <div className="panel-body flush scroll" style={{ maxHeight: 180 }}>
        {positions.length === 0 ? (
          <div className="empty">暂无持仓</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>标的</th>
                <th>数量</th>
                <th>成本</th>
                <th>现价</th>
                <th>市值</th>
                <th>浮动盈亏</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const spec = specs.get(pos.symbol)
                const quote = quotes[pos.symbol]
                const last = quote ? num(quote.mid) : num(pos.last_price)
                const avg = num(pos.avg_cost)
                const qty = num(pos.qty)
                const precision = spec?.price_precision ?? 2

                let value: number
                let pnl: number
                if (pos.market === 'FX') {
                  const units = qty * (spec?.contract_size ?? 100_000)
                  const raw = units * (last - avg)
                  pnl =
                    spec && spec.quote_currency !== spec.currency && last > 0 ? raw / last : raw
                  value = units * last
                } else {
                  value = qty * last
                  pnl = (last - avg) * qty
                }

                return (
                  <tr key={pos.symbol}>
                    <td>
                      <div className="cell-symbol">
                        <span className="code">{pos.symbol}</span>
                        <span className="sub">{spec?.description ?? pos.market}</span>
                      </div>
                    </td>
                    <td>{fmtQty(pos.qty)}</td>
                    <td>{fmtPrice(avg, precision)}</td>
                    <td>{fmtPrice(last, precision)}</td>
                    <td>{fmt(value)}</td>
                    <td className={pnlClass(pnl)}>{fmtSigned(pnl)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
