import { ageLabel, fmtPrice, num } from '../lib/format'
import { selectSymbolSpec, useStore } from '../store/useStore'

export function QuotePanel() {
  const quote = useStore((s) => s.quotes[s.symbol])
  const spec = useStore(selectSymbolSpec)
  const health = useStore((s) => s.health)
  const market = useStore((s) => s.market)

  const precision = spec?.price_precision ?? 2
  const bid = num(quote?.bid)
  const ask = num(quote?.ask)
  const mid = num(quote?.mid)
  const spread = ask - bid
  const spreadBp = mid > 0 ? (spread / mid) * 10_000 : 0
  const provider = health?.provider_status?.[market]
  const source = provider?.name ?? quote?.source ?? '—'

  return (
    <section className="panel">
      <div className="panel-body">
        <div className="quote-grid">
          <div className="quote-cell">
            <span className="k">BID · 你卖出</span>
            <span className="v down">{quote ? fmtPrice(bid, precision) : '—'}</span>
          </div>
          <div className="quote-cell">
            <span className="k">MID · 中间价</span>
            <span className="v">{quote ? fmtPrice(mid, precision) : '—'}</span>
          </div>
          <div className="quote-cell">
            <span className="k">ASK · 你买入</span>
            <span className="v up">{quote ? fmtPrice(ask, precision) : '—'}</span>
          </div>
        </div>
        <div className="quote-meta">
          <span>
            点差 {quote ? fmtPrice(spread, precision) : '—'}（{spreadBp.toFixed(1)} bp）
          </span>
          <span>
            来源 {source}
            {provider?.mode ? ` · ${provider.mode}` : ''}
          </span>
          <span>最后 tick {ageLabel(quote?.ts)}</span>
          {quote?.stale ? <span style={{ color: 'var(--warn)' }}>· 行情已冻结</span> : null}
        </div>
      </div>
    </section>
  )
}
