import { useState } from 'react'
import { ApiFailure, api } from '../lib/api'
import { fmtPrice } from '../lib/format'
import { selectMarketSymbols, useStore } from '../store/useStore'
import type { Market } from '../types'

const MARKET_OPTIONS: { key: Market; label: string }[] = [
  { key: 'FX', label: '外汇' },
  { key: 'US', label: '美股' },
  { key: 'HK', label: '港股' },
]

const HK_LOT_SIZES = [1, 50, 100, 500, 1000, 2000, 5000, 10000]

export function SymbolTabs() {
  const symbols = useStore(selectMarketSymbols)
  const symbol = useStore((s) => s.symbol)
  const quotes = useStore((s) => s.quotes)
  const market = useStore((s) => s.market)
  const setSymbol = useStore((s) => s.setSymbol)
  const notify = useStore((s) => s.notify)

  const [open, setOpen] = useState(false)
  const [code, setCode] = useState('')
  const [targetMarket, setTargetMarket] = useState<Market>(market)
  const [price, setPrice] = useState('')
  const [desc, setDesc] = useState('')
  const [lot, setLot] = useState('100')
  const [busy, setBusy] = useState(false)

  const canSubmit = code.trim().length > 0 && price.trim().length > 0 && !busy

  function toggleForm() {
    setTargetMarket(market)
    setOpen((value) => !value)
  }

  async function submit() {
    if (!canSubmit) return
    setBusy(true)
    try {
      const resp = await api.addSymbol({
        symbol: code.trim(),
        market: targetMarket,
        description: desc.trim(),
        start_price: price.trim(),
        lot_size: targetMarket === 'HK' ? Number(lot) : null,
      })
      // 重新拉 markets：新标的会进入 tabs / 下单面板 / 行情订阅
      const marketResp = await api.markets()
      const store = useStore.getState()
      store.setMarkets(marketResp.markets)
      store.setMarket(targetMarket)
      store.setSymbol(resp.symbol.symbol)
      notify('ok', `已添加 ${resp.symbol.symbol}（模拟行情）`)
      setOpen(false)
      setCode('')
      setPrice('')
      setDesc('')
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : '添加失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="symbol-bar scroll">
        {symbols.map((spec) => {
          const quote = quotes[spec.symbol]
          return (
            <button
              key={spec.symbol}
              type="button"
              className={`symbol-chip ${symbol === spec.symbol ? 'active' : ''}`}
              onClick={() => setSymbol(spec.symbol)}
              title={spec.description}
            >
              <span className="code">{spec.symbol}</span>
              <span className="desc">{spec.description}</span>
              <span className="price">
                {quote ? fmtPrice(quote.mid, spec.price_precision) : '—'}
              </span>
            </button>
          )
        })}
        <button type="button" className="symbol-chip add" onClick={toggleForm} title="添加自定义股票代码">
          <span className="code">＋ 添加代码</span>
          <span className="desc">自定义标的</span>
        </button>
      </div>

      {open ? (
        <div className="add-symbol-bar">
          <label>
            代码
            <input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder={targetMarket === 'HK' ? '如 01810' : targetMarket === 'FX' ? '如 USDCNH' : '如 TSLA'}
            />
          </label>
          <label>
            市场
            <select
              value={targetMarket}
              onChange={(event) => setTargetMarket(event.target.value as Market)}
            >
              {MARKET_OPTIONS.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            参考价
            <input
              value={price}
              inputMode="decimal"
              onChange={(event) => setPrice(event.target.value)}
              placeholder="如 150.00"
            />
          </label>
          <label>
            名称（可选）
            <input
              value={desc}
              onChange={(event) => setDesc(event.target.value)}
              placeholder="如 小米集团"
            />
          </label>
          {targetMarket === 'HK' ? (
            <label>
              每手股数
              <select value={lot} onChange={(event) => setLot(event.target.value)}>
                {HK_LOT_SIZES.map((size) => (
                  <option key={size} value={String(size)}>
                    {size}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="actions">
            <span className="hint">行情由本地模拟生成</span>
            <button type="button" className="plain-btn" onClick={() => setOpen(false)}>
              取消
            </button>
            <button type="button" className="primary-btn" disabled={!canSubmit} onClick={submit}>
              {busy ? '添加中…' : '添加'}
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}
