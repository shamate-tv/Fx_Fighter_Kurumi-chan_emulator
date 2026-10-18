import { useState } from 'react'
import { ApiFailure, api } from '../lib/api'
import { useStore } from '../store/useStore'

/** 天台结局全屏界面：带「重开一局」按钮。 */
export function GameOverModal() {
  const life = useStore((s) => s.life)
  const notify = useStore((s) => s.notify)
  const [busy, setBusy] = useState(false)

  if (!life || life.status !== 'GAME_OVER') return null

  async function restart() {
    setBusy(true)
    try {
      await api.reset()
      const [account, nextLife, orders, trades] = await Promise.all([
        api.account(),
        api.life(),
        api.orders(),
        api.trades(),
      ])
      const store = useStore.getState()
      store.setAccount(account)
      store.setLife(nextLife)
      store.setOrders(orders)
      store.setTrades(trades)
      notify('ok', '新的一局开始了')
    } catch (error) {
      notify('err', error instanceof ApiFailure ? error.message : '重置失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop gameover-backdrop" role="dialog" aria-modal="true">
      <div className="gameover-card">
        <div className="gameover-title">这一局结束了</div>
        <p className="gameover-reason">{life.ending_reason ?? '你站在了天台上，风有点冷。'}</p>
        <p className="gameover-sub">久留美，再来一次吧。</p>
        <button className="primary-btn gameover-btn" onClick={() => void restart()} disabled={busy}>
          {busy ? '重开中…' : '重开一局'}
        </button>
      </div>
    </div>
  )
}
