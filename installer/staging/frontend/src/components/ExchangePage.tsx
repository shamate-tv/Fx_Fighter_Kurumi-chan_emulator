import { useState } from 'react'
import { ApiFailure, api } from '../lib/api'
import { fmt, num } from '../lib/format'
import { useStore } from '../store/useStore'

/** 独立兑换页面：不依赖右键/浏览器菜单，纯按钮操作。 */
export function ExchangePage() {
  const account = useStore((s) => s.account)
  const health = useStore((s) => s.health)
  const setAccount = useStore((s) => s.setAccount)
  const setView = useStore((s) => s.setView)
  const notify = useStore((s) => s.notify)

  const [from, setFrom] = useState<'USD' | 'HKD'>('USD')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)

  const to = from === 'USD' ? 'HKD' : 'USD'
  const available = num(account?.balances?.[from]?.available)
  const rate = num(health?.usdhk_rate ?? '7.8') || 7.8
  const pairRate = from === 'USD' ? rate : 1 / rate
  const value = Number(amount)
  const receive = Number.isFinite(value) && value > 0 ? value * pairRate : 0
  const invalid = !Number.isFinite(value) || value <= 0 || value > available + 1e-9

  async function submit() {
    if (invalid) return
    setBusy(true)
    try {
      const result = await api.convert(from, to, amount)
      setAccount(result.account)
      notify('ok', `已兑换 ${fmt(value, 2)} ${from} → ${fmt(receive, 2)} ${to}`)
      setAmount('')
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : '兑换失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="exchange-page">
      <div className="exchange-head">
        <div>
          <h2>货币兑换</h2>
          <p className="hint">USD ⇄ HKD · 换汇后可在对应市场下单（外汇/美股用 USD，港股用 HKD）</p>
        </div>
        <button className="plain-btn" onClick={() => setView('trade')}>
          ← 返回交易台
        </button>
      </div>

      <div className="exchange-card">
        <div className="exchange-balance">
          <div>
            <span className="k">USD 可用</span>
            <span className="v">${fmt(num(account?.balances?.USD?.available))}</span>
          </div>
          <div>
            <span className="k">HKD 可用</span>
            <span className="v">HK${fmt(num(account?.balances?.HKD?.available))}</span>
          </div>
        </div>

        <div className="exchange-swap">
          <div className="exchange-side">
            <span className="k">从</span>
            <select value={from} onChange={(event) => setFrom(event.target.value as 'USD' | 'HKD')}>
              <option value="USD">USD</option>
              <option value="HKD">HKD</option>
            </select>
            <span className="avail">可用 {fmt(available)}</span>
          </div>
          <button className="swap-btn" type="button" onClick={() => setFrom(to)} title="交换方向">
            ⇄
          </button>
          <div className="exchange-side">
            <span className="k">到</span>
            <strong>{to}</strong>
          </div>
        </div>

        <label className="exchange-input">
          <span>兑换金额</span>
          <div className="exchange-input-row">
            <input
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              placeholder={`最多 ${fmt(available, 2)} ${from}`}
            />
            <button type="button" className="max-btn" onClick={() => setAmount(String(available))}>
              全部
            </button>
          </div>
        </label>

        <div className="exchange-preview">
          <div>
            <span>参考汇率</span>
            <span>
              1 {from} ≈ {pairRate.toFixed(4)} {to}
            </span>
          </div>
          <div>
            <span>预计到账</span>
            <span>
              {fmt(receive, 2)} {to}
            </span>
          </div>
        </div>

        <button className="primary-btn exchange-submit" onClick={() => void submit()} disabled={invalid || busy}>
          {busy ? '兑换中…' : `确认兑换 ${from} → ${to}`}
        </button>
        <p className="hint" style={{ marginTop: 10 }}>
          实际到账以服务端点差后的成交价为准。
        </p>
      </div>
    </section>
  )
}
