import { useState } from 'react'
import { ApiFailure, api } from '../lib/api'
import { fmtJpy, num } from '../lib/format'
import { useStore } from '../store/useStore'

/** 生存状态横条：天数 / 交租倒计时 / 生活现金 / 负债 / Plan B / 重开。 */
export function LifePanel() {
  const life = useStore((s) => s.life)
  const setLife = useStore((s) => s.setLife)
  const notify = useStore((s) => s.notify)

  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [coins, setCoins] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)

  if (!life) return null

  const cash = num(life.cash_jpy)
  const weekly = num(life.weekly_cost_jpy)
  const lowCash = cash < weekly
  const finished = life.status === 'GAME_OVER'
  const debt = num(life.debt_jpy)
  const daysToSettle = life.next_settle_on
    ? Math.max(0, Math.round((Date.parse(life.next_settle_on) - Date.parse(life.game_date)) / 86_400_000))
    : null
  const cooldownHours = Math.ceil(life.planb_cooldown_seconds / 3600)

  async function claim() {
    setBusy(true)
    try {
      const result = await api.claimPlanB('123456')
      setLife(result.life)
      setConfirmOpen(false)
      setCoins(true)
      window.setTimeout(() => setCoins(false), 1700)
      notify('ok', `Plan B 到账 ¥${fmtJpy(result.gain_jpy)}`)
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : 'Plan B 失败')
    } finally {
      setBusy(false)
    }
  }

  async function resetGame() {
    setResetBusy(true)
    try {
      await api.reset()
      const [account, life, orders, trades] = await Promise.all([
        api.account(),
        api.life(),
        api.orders(),
        api.trades(),
      ])
      const store = useStore.getState()
      store.setAccount(account)
      store.setLife(life)
      store.setOrders(orders)
      store.setTrades(trades)
      notify('ok', '新的一局开始了')
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : '重置失败')
    } finally {
      setResetBusy(false)
    }
  }

  return (
    <>
      <section className={`panel life-panel ${lowCash ? 'danger' : ''}`}>
        <div className="life-line">
          <span className="life-title">生存</span>
          <span className="life-item">
            <span className="k">第 {life.day} 天</span>
          </span>
          <span className="life-item">
            <span className="k">距交租</span>
            <b>{daysToSettle === null ? '—' : daysToSettle <= 0 ? '今天' : `${daysToSettle} 天`}</b>
          </span>
          <span className="life-item">
            <span className="k">生活现金</span>
            <b className={lowCash ? 'warn' : ''}>¥{fmtJpy(life.cash_jpy)}</b>
          </span>
          <span className="life-item">
            <span className="k">负债</span>
            <b className={debt > 0 ? 'debt' : ''}>¥{fmtJpy(life.debt_jpy)}</b>
          </span>
          <span className="life-item">
            <span className="k">周支出</span>
            <b className="muted">¥{fmtJpy(life.weekly_cost_jpy)}</b>
          </span>
          {finished ? (
            <span className="life-item">
              <span className="k">结局</span>
              <b className="debt">{life.ending_reason ?? '天台'}</b>
            </span>
          ) : null}
          <span className="spacer" />
          <button
            type="button"
            className={`planb-btn ${lowCash && !finished ? 'urgent' : ''}`}
            disabled={!life.planb_ready || busy || finished}
            onClick={() => setConfirmOpen(true)}
          >
            {finished
              ? '这一局已经结束了'
              : busy
                ? '正在求关注…'
                : life.planb_ready
                  ? 'Plan B：求关注 +¥50,000'
                  : `Plan B 冷却中（${cooldownHours} 小时）`}
          </button>
          {finished ? (
            <button className="reset-btn" onClick={() => void resetGame()} disabled={resetBusy}>
              {resetBusy ? '重置中…' : '重开一局（Reset）'}
            </button>
          ) : null}
        </div>
        {coins ? (
          <div className="coin-burst" aria-hidden>
            {Array.from({ length: 16 }, (_, index) => (
              <span
                key={index}
                className="coin"
                style={{ left: `${(index * 41) % 100}%`, animationDelay: `${index * 45}ms` }}
              >
                ¥
              </span>
            ))}
          </div>
        ) : null}
      </section>

      {confirmOpen ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal planb-modal">
            <div className="modal-title">Plan B · B站求关注</div>
            <p className="modal-line">“没办法了...只能去 B站 骗几个粉丝关注了，求求你关注我吧！”</p>
            <p className="modal-line muted">—— 久留美</p>
            <div className="modal-actions">
              <button className="plain-btn" onClick={() => setConfirmOpen(false)} disabled={busy}>
                再想想
              </button>
              <button className="primary-btn" onClick={() => void claim()} disabled={busy}>
                {busy ? <span className="spinner" /> : '求求你了'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}
