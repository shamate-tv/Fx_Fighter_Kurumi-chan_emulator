import { ageLabel } from '../lib/format'
import { useStore } from '../store/useStore'

/** 行情冻结提示：stale=true 时显眼地告诉用户"看到的价不能用来成交"。 */
export function StaleBanner() {
  const quote = useStore((s) => s.quotes[s.symbol])
  const market = useStore((s) => s.market)
  const health = useStore((s) => s.health)
  const wsStatus = useStore((s) => s.wsStatus)

  if (wsStatus !== 'open') {
    return (
      <div className="banner warn">
        <span>⚡</span>
        <span>行情连接已断开，正在自动重连…（此时不会收到新报价）</span>
      </div>
    )
  }

  if (!quote?.stale) {
    const source = health?.data_sources?.[market] ?? quote?.source ?? '—'
    return (
      <div className="banner live">
        <span>●</span>
        <span>
          实时行情 · 来源 {source} · 最后更新 {ageLabel(quote?.ts)}
        </span>
      </div>
    )
  }

  return (
    <div className="banner warn">
      <span>⏸</span>
      <span>
        <strong>周末休市，行情已冻结</strong> —— 当前展示的是最后一条报价（{ageLabel(quote.ts)}）。
        陈旧价格不会参与撮合，市价单会被拒绝；限价单可以挂，但要等开市后新 tick 到达才会触发。
      </span>
    </div>
  )
}
