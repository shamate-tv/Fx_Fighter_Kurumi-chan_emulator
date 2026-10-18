import { useState } from 'react'
import { ApiFailure, api } from '../lib/api'
import { fmt, num } from '../lib/format'
import { selectSymbolSpec, useStore } from '../store/useStore'
import type { SymbolSpec } from '../types'

/** 满仓数量：FX 按保证金、股票按现金 + 手续费缓冲，再按步长/手数取整。 */
function maxQty(spec: SymbolSpec | undefined, price: number, buyingPower: number): number {
  if (!spec || !price || buyingPower <= 0) return 0
  const isFx = spec.market === 'FX'
  const leverage = Number(spec.leverage ?? 20) || 20
  const lot = spec.lot_size ?? 1
  const step = num(spec.qty_step)
  const minQty = num(spec.min_qty)
  const contract = Number(spec.contract_size || 1)
  let raw = isFx ? (buyingPower * leverage) / (price * contract) : buyingPower / (price * 1.005)
  if (lot > 1) raw = Math.floor(raw / lot) * lot
  if (step > 0) raw = Math.floor(raw / step + 1e-9) * step
  raw = Math.floor(raw * 1e6) / 1e6
  return raw >= minQty ? raw : 0
}

/** 萌智子的建议：像 QQ 消息一样从右下角滑入，久留美照片做半透明背景。 */
export function AdvisorToast() {
  const event = useStore((s) => s.activeEvent)
  const setActiveEvent = useStore((s) => s.setActiveEvent)
  const spec = useStore(selectSymbolSpec)
  const symbol = useStore((s) => s.symbol)
  const quote = useStore((s) => s.quotes[s.symbol])
  const account = useStore((s) => s.account)
  const notify = useStore((s) => s.notify)
  const [busy, setBusy] = useState(false)

  if (!event) return null

  const ask = num(quote?.ask)
  const buyingPower = num(account?.buying_power?.[spec?.market ?? 'FX'])
  const qty = maxQty(spec, ask, buyingPower)
  const stale = quote?.stale ?? false
  const disabled = busy || !spec || qty <= 0 || stale || !quote

  async function goAllIn() {
    if (!spec) return
    setBusy(true)
    try {
      const order = await api.placeOrder({ symbol, side: 'BUY', type: 'MARKET', qty: String(qty) })
      notify('ok', `听萌智子的：${symbol} 全仓买入 ${fmt(order.filled_qty || order.qty, 2)} ${spec.qty_unit}`)
      setActiveEvent(null)
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : '下单失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="advisor-toast" role="alertdialog" aria-live="polite">
      <div className="advisor-toast-head">
        <span className="advisor-avatar-wrap">
          <img className="advisor-avatar" src="/art/mochiko.png" alt="萌智子" />
        </span>
        <span className="advisor-toast-name">萌智子</span>
        <span className="advisor-toast-tag">第 {event.id.replace('advisor_', '')} 条建议</span>
        <button className="advisor-close" onClick={() => setActiveEvent(null)} title="关闭">
          ×
        </button>
      </div>
      <p className="advisor-toast-body">“{event.body}”</p>
      <div className="advisor-toast-hint">
        {spec ? (
          <>
            {spec.symbol}
            {quote ? ` · 市价 ${fmt(ask, Number(spec.price_precision ?? 2))}` : ' · 无行情'}
            {qty > 0 ? ` · 满仓 ${fmt(qty, 2)} ${spec.qty_unit}` : ''}
            {stale ? <span className="err"> · 行情已冻结</span> : null}
          </>
        ) : (
          '未选择标的'
        )}
      </div>
      <div className="advisor-toast-actions">
        <button className="plain-btn" onClick={() => setActiveEvent(null)} disabled={busy}>
          自己分析（关闭）
        </button>
        <button className="danger-btn" onClick={() => void goAllIn()} disabled={disabled}>
          {busy ? <span className="spinner" /> : '听她的（全仓市价买入当前标的）'}
        </button>
      </div>
    </div>
  )
}
