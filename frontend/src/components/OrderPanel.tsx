import { useEffect, useMemo, useState } from 'react'
import { ApiFailure, api } from '../lib/api'
import { estimateFees, feesTotal, grossAmount } from '../lib/fees'
import { fmt, fmtPrice, fmtQty, num } from '../lib/format'
import { selectRule, selectSymbolSpec, useStore } from '../store/useStore'
import type { OrderType, Side } from '../types'

export function OrderPanel() {
  const spec = useStore(selectSymbolSpec)
  const rule = useStore(selectRule)
  const market = useStore((s) => s.market)
  const symbol = useStore((s) => s.symbol)
  const quote = useStore((s) => s.quotes[s.symbol])
  const account = useStore((s) => s.account)
  const notify = useStore((s) => s.notify)

  const [side, setSide] = useState<Side>('BUY')
  const [type, setType] = useState<OrderType>('MARKET')
  const [qty, setQty] = useState('')
  const [limit, setLimit] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setQty('')
    setLimit('')
    setType('MARKET')
  }, [symbol])

  const minQty = num(spec?.min_qty)
  const step = num(spec?.qty_step)
  const lotSize = spec?.lot_size ?? 1
  const precision = spec?.price_precision ?? 2
  const qtyNum = Number(qty)
  const effectiveQty = Number.isFinite(qtyNum) && qtyNum > 0 ? qtyNum : 0

  const qtyError = useMemo(() => {
    if (!spec) return '未选择标的'
    if (!qty.trim()) return null
    if (!Number.isFinite(qtyNum) || qtyNum <= 0) return '数量必须为正数'
    if (qtyNum < minQty - 1e-9) return `最小 ${minQty} ${spec.qty_unit}`
    if (lotSize > 1 && Math.abs(qtyNum % lotSize) > 1e-9) {
      return `${spec.symbol} 每手 ${lotSize} 股，数量必须是 ${lotSize} 的整数倍`
    }
    const scaled = Math.round(qtyNum * 1e6)
    const stepScaled = Math.round(step * 1e6)
    const minScaled = Math.round(minQty * 1e6)
    if (stepScaled > 0 && (scaled - minScaled) % stepScaled !== 0) {
      return `数量必须是 ${step} ${spec.qty_unit} 的整数倍`
    }
    return null
  }, [qty, qtyNum, spec, minQty, step, lotSize])

  const priceNum =
    type === 'LIMIT' ? Number(limit) : side === 'BUY' ? num(quote?.ask) : num(quote?.bid)
  const priceValid = Number.isFinite(priceNum) && priceNum > 0

  const gross =
    spec && priceValid
      ? grossAmount({
          market: spec.market,
          qty: effectiveQty,
          price: priceNum,
          quoteCurrency: spec.quote_currency,
          settleCurrency: spec.currency,
          contractSize: spec.contract_size,
        })
      : 0

  const feeLines = rule ? estimateFees(rule, side, gross, effectiveQty) : []
  const feeTotal = feesTotal(feeLines)
  const isFx = spec?.market === 'FX'
  const leverage = Number(spec?.leverage ?? 20) || 20
  const margin = isFx ? gross / leverage : 0
  const payCash = isFx ? margin : gross + feeTotal

  const buyingPower = num(account?.buying_power?.[market])
  const position = account?.positions.find((p) => p.symbol === symbol)
  const sellable = num(position?.qty)

  const insufficientCash = side === 'BUY' && effectiveQty > 0 && payCash > buyingPower + 1e-9
  const insufficientPosition = side === 'SELL' && effectiveQty > sellable + 1e-9
  const stale = quote?.stale ?? false
  const marketOrderBlocked = stale && type === 'MARKET'
  const noQuote = !quote

  const disabled =
    busy ||
    !spec ||
    !qty.trim() ||
    !!qtyError ||
    insufficientCash ||
    insufficientPosition ||
    marketOrderBlocked ||
    noQuote ||
    (type === 'LIMIT' && !priceValid)

  function switchType(next: OrderType) {
    setType(next)
    if (next === 'LIMIT' && !limit && quote) {
      setLimit(side === 'BUY' ? quote.ask : quote.bid)
    }
  }

  function switchSide(next: Side) {
    setSide(next)
    if (type === 'LIMIT' && quote) {
      setLimit(next === 'BUY' ? quote.ask : quote.bid)
    }
  }

  function bump(direction: 1 | -1) {
    const base = effectiveQty > 0 ? effectiveQty : 0
    const next = base + direction * step
    if (next < minQty - 1e-9) {
      setQty(String(minQty))
      return
    }
    setQty(String(Number(next.toFixed(6))))
  }

  function fillMax() {
    if (!spec || !priceValid) return
    const perUnit = isFx ? (spec.contract_size * priceNum) / leverage : priceNum
    if (perUnit <= 0) return
    const raw = (buyingPower / perUnit) * 0.995
    const aligned = Math.floor((raw - minQty) / step + 1e-9) * step + minQty
    const safe = Number.isFinite(aligned) && aligned >= minQty ? aligned : minQty
    setQty(String(Number(safe.toFixed(6))))
  }

  async function submit() {
    if (disabled || !spec) return
    setBusy(true)
    try {
      const order = await api.placeOrder({
        symbol: spec.symbol,
        side,
        type,
        qty: String(effectiveQty),
        limit_price: type === 'LIMIT' ? String(priceNum) : null,
      })
      const verb = side === 'BUY' ? '买入' : '卖出'
      const filled = order.status === 'FILLED'
      notify(
        'ok',
        `${filled ? '已成交' : '已挂单'}：${verb} ${fmtQty(effectiveQty)} ${spec.qty_unit} ${spec.symbol}${
          order.avg_fill_price ? ` @ ${fmtPrice(order.avg_fill_price, precision)}` : ''
        }`,
      )
      setQty('')
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : '下单失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  const blockedReason = noQuote
    ? '尚未收到报价'
    : marketOrderBlocked
      ? '行情已冻结，市价单不可用（可改用限价单挂单等开市）'
      : insufficientCash
        ? isFx
          ? '可用保证金不足'
          : '可用买力不足'
        : insufficientPosition
          ? `持仓不足（可卖 ${fmtQty(sellable)}）`
          : null

  return (
    <section className="panel order-panel">
      <div className="panel-head">
        <span className="panel-title">下单</span>
        <div className="spacer" />
        <span className="hint">
          {spec ? `${spec.symbol} · ${spec.qty_unit}` : '—'}
          {spec?.leverage ? ` · 杠杆 1:${spec.leverage}` : ''}
        </span>
      </div>

      <div className="panel-body">
        <div className="seg">
          <button
            type="button"
            className={`buy ${side === 'BUY' ? 'active' : ''}`}
            onClick={() => switchSide('BUY')}
          >
            买入 BUY
          </button>
          <button
            type="button"
            className={`sell ${side === 'SELL' ? 'active' : ''}`}
            onClick={() => switchSide('SELL')}
          >
            卖出 SELL
          </button>
        </div>

        <div className="seg" style={{ marginTop: 6 }}>
          <button
            type="button"
            className={`plain ${type === 'MARKET' ? 'active' : ''}`}
            onClick={() => switchType('MARKET')}
          >
            市价
          </button>
          <button
            type="button"
            className={`plain ${type === 'LIMIT' ? 'active' : ''}`}
            onClick={() => switchType('LIMIT')}
          >
            限价
          </button>
        </div>

        {type === 'LIMIT' ? (
          <div className="field">
            <label>限价（{spec?.currency ?? '—'}）</label>
            <input
              type="text"
              inputMode="decimal"
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
              placeholder={quote?.ask ?? ''}
            />
          </div>
        ) : null}

        <div className="field">
          <label>
            数量（{spec?.qty_unit ?? '—'}）
            {lotSize > 1 ? ` · 每手 ${lotSize}` : ''}
          </label>
          <div className="input-row">
            <input
              type="text"
              inputMode="decimal"
              className={qtyError ? 'invalid' : ''}
              value={qty}
              onChange={(event) => setQty(event.target.value)}
              placeholder={String(minQty)}
            />
            <div className="stepper">
              <button type="button" onClick={() => bump(-1)}>
                −
              </button>
              <button type="button" onClick={() => bump(1)}>
                ＋
              </button>
              <button type="button" onClick={fillMax} title="按可用买力填满">
                MAX
              </button>
            </div>
          </div>
          {qtyError ? (
            <span className="hint err">{qtyError}</span>
          ) : (
            <span className="hint">
              最小 {minQty} {spec?.qty_unit}，步长 {step}
            </span>
          )}
        </div>

        <div className="summary">
          <div className="summary-row">
            <span className="k">参考价</span>
            <span className="v">{priceValid ? fmtPrice(priceNum, precision) : '—'}</span>
          </div>
          <div className="summary-row">
            <span className="k">{isFx ? '名义价值' : '成交额'}</span>
            <span className="v">
              {fmt(gross)} {spec?.currency ?? ''}
            </span>
          </div>
          {feeLines.map((line) => (
            <div className="summary-row" key={line.name}>
              <span className="k">{line.label}</span>
              <span className="v">{fmt(line.amount)}</span>
            </div>
          ))}
          {isFx ? (
            <div className="summary-row">
              <span className="k">占用保证金（1:{leverage}）</span>
              <span className="v">{fmt(margin)}</span>
            </div>
          ) : (
            <div className="summary-row">
              <span className="k">费用合计</span>
              <span className="v">{fmt(feeTotal)}</span>
            </div>
          )}
          <div className="summary-row">
            <span className="k">
              {side === 'BUY' ? (isFx ? '需保证金' : '预计支付') : '预计回收'}
            </span>
            <span className="v">
              {fmt(side === 'BUY' ? payCash : gross - feeTotal)} {spec?.currency ?? ''}
            </span>
          </div>
          {rule && rule.settlement === 'T_N' && side === 'SELL' ? (
            <div className="summary-row">
              <span className="k">卖出资金</span>
              <span className="v">T+{rule.settlement_days} 到账</span>
            </div>
          ) : null}
        </div>

        {blockedReason ? <div className="hint err" style={{ marginTop: 8 }}>{blockedReason}</div> : null}
      </div>

      <div className="order-footer">
        <button
          type="button"
          className={`submit ${side === 'BUY' ? 'buy' : 'sell'}`}
          disabled={disabled}
          onClick={submit}
        >
          {busy ? '提交中…' : `${side === 'BUY' ? '买入' : '卖出'} ${spec?.symbol ?? ''}`}
        </button>
      </div>
    </section>
  )
}
